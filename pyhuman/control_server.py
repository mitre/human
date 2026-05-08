"""Keep-alive command server for pyhuman.

Listens on a Unix domain socket and accepts JSON-line job requests from an
operator. Each request names a registered workflow and (optionally) args; the
server runs that workflow synchronously, captures its stdout/stderr, and
replies with the result. The process stays alive between jobs.

The server is transport-agnostic: anything that can connect to the UDS (a
local client, a vsock<->UDS bridge, an Ansible connection plugin, etc.) can
drive it.

Wire format
-----------
Requests and responses are newline-delimited JSON ("JSON lines"). One request
per line, one response per line.

Request:
    {"id": "<opaque>", "workflow": "<name>", "args": {...}}

Special workflow names:
    "_list" -> returns {"workflows": [...]}
    "_quit" -> graceful shutdown after replying

Response:
    {"id": "<opaque>", "status": "ok"|"error",
     "stdout": "...", "stderr": "...", "duration_ms": <int>,
     "error": "<message, only on status=error>"}
"""

import io
import json
import os
import socket
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

DEFAULT_SOCK_PATH = '/tmp/pyhuman.sock'


def _recv_line(conn_file):
    """Read one newline-terminated line from a socket file. Returns None on EOF."""
    line = conn_file.readline()
    if not line:
        return None
    return line.rstrip(b'\r\n')


def _send_line(conn, payload):
    """Send a single JSON line to the connected peer."""
    data = json.dumps(payload, default=str).encode('utf-8') + b'\n'
    conn.sendall(data)


def _build_registry(workflows):
    """Map workflow display-name -> workflow instance.

    We register under both the class name and the BaseWorkflow.name attribute
    so callers can address workflows however they like; we also expose the
    lowercased forms.
    """
    registry = {}
    for wf in workflows:
        keys = set()
        cls_name = type(wf).__name__
        keys.add(cls_name)
        keys.add(cls_name.lower())
        if getattr(wf, 'name', None):
            keys.add(wf.name)
            keys.add(wf.name.lower())
        for k in keys:
            registry.setdefault(k, wf)
    return registry


def _run_workflow(workflow, args):
    """Execute workflow.action(extra), capturing stdout/stderr.

    Returns (status, stdout, stderr, duration_ms, err_msg).
    """
    extra = []
    if isinstance(args, dict):
        # If caller provided 'extra' explicitly, honor it; otherwise pass the
        # dict's values as a positional list (mirrors the existing CLI shape
        # of `--extra a b c`).
        if 'extra' in args:
            extra = args['extra']
        else:
            extra = args
    elif isinstance(args, list):
        extra = args

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    started = time.time()
    status = 'ok'
    err_msg = None
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            workflow.action(extra)
    except SystemExit as e:  # workflows shouldn't, but guard anyway
        status = 'error'
        err_msg = 'SystemExit({})'.format(e.code)
        traceback.print_exc(file=err_buf)
    except BaseException as e:  # noqa: BLE001 - we want to report anything
        status = 'error'
        err_msg = '{}: {}'.format(type(e).__name__, e)
        traceback.print_exc(file=err_buf)
    duration_ms = int((time.time() - started) * 1000)
    return status, out_buf.getvalue(), err_buf.getvalue(), duration_ms, err_msg


def _handle_request(req, registry):
    """Dispatch one parsed request dict. Returns (response_dict, should_quit)."""
    req_id = req.get('id')
    name = req.get('workflow')
    args = req.get('args', {})

    if name == '_list':
        # Deduplicate by identity for a clean listing.
        seen = []
        names = []
        for wf in registry.values():
            if wf in seen:
                continue
            seen.append(wf)
            names.append(getattr(wf, 'name', type(wf).__name__))
        return {'id': req_id, 'status': 'ok', 'workflows': names,
                'stdout': '', 'stderr': '', 'duration_ms': 0}, False

    if name == '_quit':
        return {'id': req_id, 'status': 'ok', 'stdout': '', 'stderr': '',
                'duration_ms': 0, 'message': 'shutting down'}, True

    if not name:
        return {'id': req_id, 'status': 'error',
                'error': 'missing "workflow" field',
                'stdout': '', 'stderr': '', 'duration_ms': 0}, False

    workflow = registry.get(name) or registry.get(name.lower())
    if workflow is None:
        return {'id': req_id, 'status': 'error',
                'error': 'unknown workflow: {}'.format(name),
                'stdout': '', 'stderr': '', 'duration_ms': 0}, False

    status, stdout, stderr, duration_ms, err_msg = _run_workflow(workflow, args)
    resp = {'id': req_id, 'status': status, 'stdout': stdout,
            'stderr': stderr, 'duration_ms': duration_ms}
    if err_msg:
        resp['error'] = err_msg
    return resp, False


def serve(workflows, sock_path=None, log=None):
    """Run the control server.

    Args:
        workflows: iterable of loaded BaseWorkflow instances.
        sock_path: UDS path. Falls back to env PYHUMAN_CONTROL_SOCK or
            DEFAULT_SOCK_PATH.
        log: callable(str) for human-readable status messages. Defaults to
            print.

    Blocks until a client sends `{"workflow": "_quit"}` or the process is
    interrupted.
    """
    if sock_path is None:
        sock_path = os.environ.get('PYHUMAN_CONTROL_SOCK', DEFAULT_SOCK_PATH)
    if log is None:
        log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731

    registry = _build_registry(workflows)

    # Clean up any stale socket from a prior run.
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError as e:
        log('warning: could not unlink stale socket {}: {}'.format(sock_path, e))

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    try:
        os.chmod(sock_path, 0o600)
    except OSError:
        pass
    srv.listen(8)
    log('pyhuman control server listening on {}'.format(sock_path))
    log('registered workflows: {}'.format(
        sorted({getattr(w, 'name', type(w).__name__) for w in registry.values()})))

    should_quit = False
    try:
        while not should_quit:
            try:
                conn, _ = srv.accept()
            except (KeyboardInterrupt, SystemExit):
                break
            with conn:
                conn_file = conn.makefile('rb')
                # Handle sequential requests on a single connection. One job
                # at a time per process — Selenium / pyautogui aren't
                # thread-safe, and accept() is the natural serializer.
                while True:
                    line = _recv_line(conn_file)
                    if line is None:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line.decode('utf-8'))
                    except json.JSONDecodeError as e:
                        _send_line(conn, {'id': None, 'status': 'error',
                                          'error': 'invalid json: {}'.format(e),
                                          'stdout': '', 'stderr': '',
                                          'duration_ms': 0})
                        continue
                    resp, should_quit = _handle_request(req, registry)
                    try:
                        _send_line(conn, resp)
                    except OSError as e:
                        log('warning: failed to send response: {}'.format(e))
                        break
                    if should_quit:
                        break
    finally:
        try:
            srv.close()
        finally:
            try:
                if os.path.exists(sock_path):
                    os.unlink(sock_path)
            except OSError:
                pass
            for wf in {id(w): w for w in registry.values()}.values():
                try:
                    wf.cleanup()
                except Exception as e:  # noqa: BLE001
                    log('warning: cleanup failed for {}: {}'.format(
                        type(wf).__name__, e))
        log('pyhuman control server stopped')
