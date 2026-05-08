"""Smoke test for pyhuman's keep-alive control server.

Boots `python -m pyhuman.human --mode control` in a subprocess on a
test-scoped Unix domain socket, drives it through a few requests, then asks
it to quit and reaps the process.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Legacy human.py uses `import_module('app.workflows.foo')`, which means it
# must be launched with cwd = pyhuman/. We mirror that here.
PYHUMAN_DIR = os.path.join(REPO_ROOT, 'pyhuman')


def _wait_for_socket(path, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            # Confirm we can actually connect (server might still be binding).
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(1.0)
                s.connect(path)
                s.close()
                return True
            except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
                pass
        time.sleep(0.1)
    return False


def _request(sock_path, payload, recv_timeout=60.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(recv_timeout)
    s.connect(sock_path)
    try:
        s.sendall((json.dumps(payload) + '\n').encode('utf-8'))
        f = s.makefile('rb')
        line = f.readline()
        if not line:
            raise RuntimeError('server closed connection without responding')
        return json.loads(line.decode('utf-8'))
    finally:
        s.close()


@pytest.fixture
def control_server():
    tmpdir = tempfile.mkdtemp(prefix='pyhuman-test-')
    sock_path = os.path.join(tmpdir, 'pyhuman.sock')
    env = dict(os.environ)
    env['PYHUMAN_CONTROL_SOCK'] = sock_path
    # Make sure the server can import the `pyhuman` package.
    env['PYTHONPATH'] = REPO_ROOT + os.pathsep + env.get('PYTHONPATH', '')

    proc = subprocess.Popen(
        [sys.executable, 'human.py', '--mode', 'control', '--sock', sock_path],
        cwd=PYHUMAN_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if not _wait_for_socket(sock_path, timeout=20.0):
            proc.kill()
            out, err = proc.communicate(timeout=5)
            raise RuntimeError(
                'control server never opened socket {}\nstdout:\n{}\nstderr:\n{}'.format(
                    sock_path, out.decode(errors='replace'),
                    err.decode(errors='replace')))
        yield sock_path, proc
    finally:
        if proc.poll() is None:
            try:
                _request(sock_path, {'id': 'final-quit', 'workflow': '_quit'},
                         recv_timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        try:
            if os.path.exists(sock_path):
                os.unlink(sock_path)
            os.rmdir(tmpdir)
        except OSError:
            pass


def test_list_returns_workflows(control_server):
    sock_path, _proc = control_server
    resp = _request(sock_path, {'id': 'list-1', 'workflow': '_list'})
    assert resp['id'] == 'list-1'
    assert resp['status'] == 'ok'
    assert isinstance(resp['workflows'], list)
    assert len(resp['workflows']) > 0
    # spawn_shell registers under the name 'ListFiles'.
    assert 'ListFiles' in resp['workflows']


def test_unknown_workflow_errors(control_server):
    sock_path, _proc = control_server
    resp = _request(sock_path, {'id': 'bad-1', 'workflow': 'does_not_exist'})
    assert resp['id'] == 'bad-1'
    assert resp['status'] == 'error'
    assert 'unknown workflow' in resp.get('error', '')


def test_run_spawn_shell(control_server):
    sock_path, _proc = control_server
    resp = _request(sock_path, {'id': 'run-1', 'workflow': 'ListFiles',
                                'args': {}}, recv_timeout=30.0)
    assert resp['id'] == 'run-1'
    assert resp['status'] == 'ok', 'stderr was: {!r}'.format(resp.get('stderr'))
    assert isinstance(resp['duration_ms'], int)
    assert resp['duration_ms'] >= 0


def test_quit_shuts_down(control_server):
    sock_path, proc = control_server
    resp = _request(sock_path, {'id': 'q-1', 'workflow': '_quit'})
    assert resp['status'] == 'ok'
    # Server should exit on its own shortly after replying.
    for _ in range(50):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None, 'control server did not exit after _quit'
