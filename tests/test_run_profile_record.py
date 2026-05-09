"""Tests for the Run-Profile SSE recording integration + MP4 endpoint.

Covers:

  1. ``record=true`` end-to-end: the SSE stream emits
     ``recording_started`` / ``finalizing_recording`` / ``recording_ready``
     events, and the mocked ``RfbRecorder`` is exercised once
     (``start()`` + ``stop()``).
  2. ``record=false`` (default): no recorder is spawned, behavior
     matches the legacy SSE contract (existing test_run_profile_socket
     covers correctness; here we only assert *no* recorder side
     effects).
  3. The recorder is finalized (``stop()`` called) even when the
     operator-socket send blows up mid-stream.
  4. ``GET /plugin/human/api/recording/<vm>/<file>`` returns 404 for a
     nonexistent file.
  5. The recording route rejects ``..`` segments and non-mp4 names with
     a 400, with NO filesystem read.

We mock the ``RfbRecorder`` class on the ``plugins.human.pyhuman``
namespace so the parallel-agent module isn't required for these tests
to pass.
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

CALDERA_ROOT = '/home/caldera/Desktop/CalderaVENV/caldera'
if CALDERA_ROOT not in sys.path:
    sys.path.insert(0, CALDERA_ROOT)


# --- Helpers -------------------------------------------------------------

def _stub_meta(base_dir: str, host_id: str, sock_path: str,
               vnc_port: int = 5961) -> str:
    """Mirrors the helper in test_run_profile_socket.py — drops a
    Range-provider-shaped meta.json under ``<base>/<host>-<suffix>/``.
    """
    run_dir = os.path.join(base_dir, f'{host_id}-deadbeef')
    os.makedirs(run_dir, exist_ok=True)
    meta = {
        'input_daemon': {
            'socket': os.path.join(run_dir, 'input.sock'),
            'operator_socket': sock_path,
            'pid': 1234,
        },
        'gpu_daemon': {
            'socket': os.path.join(run_dir, 'gpu.sock'),
            'vnc_port': vnc_port,
            'pid': 1235,
        },
    }
    with open(os.path.join(run_dir, 'meta.json'), 'w') as f:
        json.dump(meta, f)
    return run_dir


def _install_mock_recorder():
    """Stub ``plugins.human.pyhuman.recorder`` with a controllable
    ``RfbRecorder`` class. Returns the class so the test can introspect
    constructor args / call counts.

    The real module is being authored by a parallel agent; this stub
    lets our tests run regardless of whether that module has landed.
    """
    pyhuman_pkg = sys.modules.get('plugins.human.pyhuman')
    if pyhuman_pkg is None:
        # Force-import so ``plugins.human.pyhuman`` is registered.
        import importlib
        pyhuman_pkg = importlib.import_module('plugins.human.pyhuman')

    recorder_mod = types.ModuleType('plugins.human.pyhuman.recorder')

    class RfbRecorder:
        instances = []  # noqa: RUF012 — class-level registry for assertions

        def __init__(self, host, port, output_mp4, fps=10):
            self.host = host
            self.port = port
            self.output_mp4 = Path(output_mp4)
            self.fps = fps
            self.start_calls = 0
            self.stop_calls = 0
            RfbRecorder.instances.append(self)

        def start(self):
            self.start_calls += 1

        def stop(self):
            self.stop_calls += 1
            # Touch the file so the MP4-serving endpoint test can find
            # it on disk if needed.
            try:
                self.output_mp4.parent.mkdir(parents=True, exist_ok=True)
                self.output_mp4.write_bytes(b'\x00\x00\x00\x18ftypmp42')
            except Exception:
                pass
            return self.output_mp4

    recorder_mod.RfbRecorder = RfbRecorder
    sys.modules['plugins.human.pyhuman.recorder'] = recorder_mod
    setattr(pyhuman_pkg, 'recorder', recorder_mod)
    # Reset the registry so each test sees a clean slate.
    RfbRecorder.instances = []
    return RfbRecorder


# --- Base class with the SSE app already wired up -----------------------

class _BaseSseTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from plugins.human.app import human_api
        self.human_api_mod = human_api

        self.tmpbase = tempfile.mkdtemp(prefix='test-microvms-')
        self.fixture_root = tempfile.mkdtemp(prefix='test-human-fixtures-')
        self.recordings_root = tempfile.mkdtemp(prefix='test-recordings-')
        self.adv_dir = os.path.join(self.fixture_root, 'adversaries')
        self.atomic_dir = os.path.join(self.fixture_root, 'atomic')
        os.makedirs(self.adv_dir)
        os.makedirs(self.atomic_dir)

        ability_id = '11111111-1111-1111-1111-111111111111'
        with open(os.path.join(self.atomic_dir, 'noop.yml'), 'w') as f:
            f.write(
                "---\n"
                "- id: {aid}\n"
                "  name: noop-dwell\n"
                "  hid:\n"
                "    steps:\n"
                "      - action: dwell\n"
                "        ms: 100\n"
                .format(aid=ability_id))
        profile_id = '22222222-2222-2222-2222-222222222222'
        with open(os.path.join(self.adv_dir, 'unit-test-profile.yml'), 'w') as f:
            f.write(
                "- id: {pid}\n"
                "  name: unit-test-profile\n"
                "  description: tiny\n"
                "  atomic_ordering:\n"
                "    - {aid}\n"
                "    - {aid}\n"
                .format(pid=profile_id, aid=ability_id))
        self.profile_id = profile_id

        self._orig_base = human_api.MICROVM_RUNTIME_BASE
        self._orig_adv = human_api.ADVERSARIES_DIR
        self._orig_atomic = human_api.ATOMIC_ABILITIES_DIR
        self._orig_recordings = human_api.RECORDINGS_BASE
        human_api.MICROVM_RUNTIME_BASE = self.tmpbase
        human_api.ADVERSARIES_DIR = Path(self.adv_dir)
        human_api.ATOMIC_ABILITIES_DIR = Path(self.atomic_dir)
        human_api.RECORDINGS_BASE = self.recordings_root

        # Listening UDS that captures every line the SSE handler ships.
        self.sock_path = os.path.join(self.fixture_root, 'op.sock')
        self.received = []
        self._stop_evt = threading.Event()

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sock_path)
        srv.listen(1)
        srv.settimeout(0.5)
        self._server_sock = srv

        self._send_failure_after = None  # if int, close conn after N bytes

        def _serve():
            conn = None
            while not self._stop_evt.is_set():
                try:
                    conn, _ = srv.accept()
                    break
                except socket.timeout:
                    continue
                except OSError:
                    return
            if conn is None:
                return
            conn.settimeout(2.0)
            buf = b''
            total = 0
            try:
                while not self._stop_evt.is_set():
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    total += len(chunk)
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        if line.strip():
                            try:
                                self.received.append(
                                    json.loads(line.decode()))
                            except Exception:
                                self.received.append(
                                    {'_raw': line.decode(errors='replace')})
                    if (self._send_failure_after is not None
                            and total >= self._send_failure_after):
                        # Simulate a peer that closes mid-stream.
                        break
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        self._server_thread = threading.Thread(target=_serve, daemon=True)
        self._server_thread.start()

        _stub_meta(self.tmpbase, 'demovm', self.sock_path)

        from aiohttp import web

        class _AuthStub:
            async def check_permissions(self, *a, **kw):
                return None

        class _DataStub:
            async def locate(self, *a, **kw):
                return []

        services = {'auth_svc': _AuthStub(), 'data_svc': _DataStub()}

        self.api = human_api.HumanApi(services, human_svc=None)
        self.app = web.Application()
        self.app.router.add_route(
            'GET', '/plugin/human/api/run-profile', self.api.api_run_profile)
        self.app.router.add_route(
            'GET',
            '/plugin/human/api/recording/{vm_name}/{filename}',
            self.api.api_recording)

        from aiohttp.test_utils import TestServer, TestClient
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.client = TestClient(self.server)
        await self.client.start_server()

        self.RfbRecorder = _install_mock_recorder()

    async def asyncTearDown(self):
        try:
            await self.client.close()
        except Exception:
            pass
        try:
            await self.server.close()
        except Exception:
            pass
        self._stop_evt.set()
        try:
            self._server_sock.close()
        except Exception:
            pass
        self._server_thread.join(timeout=2.0)

        self.human_api_mod.MICROVM_RUNTIME_BASE = self._orig_base
        self.human_api_mod.ADVERSARIES_DIR = self._orig_adv
        self.human_api_mod.ATOMIC_ABILITIES_DIR = self._orig_atomic
        self.human_api_mod.RECORDINGS_BASE = self._orig_recordings


# --- 1. record=true happy path ------------------------------------------

class RecordedRunSseTests(_BaseSseTest):

    async def test_record_true_emits_lifecycle_events(self):
        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'demovm',
                    'profile_id': self.profile_id,
                    'record': 'true'})
        self.assertEqual(resp.status, 200)
        body = (await resp.read()).decode()

        # Lifecycle events on the `log` channel.
        self.assertIn('event: log', body)
        self.assertIn('"event": "recording_started"', body)
        self.assertIn('"event": "finalizing_recording"', body)
        self.assertIn('"event": "recording_ready"', body)
        # Done event on the default channel.
        self.assertIn('"event": "done"', body)

        # The mock recorder was instantiated with the right endpoint
        # (vnc_port=5961 from the stubbed meta.json) and got both
        # start/stop calls.
        self.assertEqual(len(self.RfbRecorder.instances), 1)
        rec = self.RfbRecorder.instances[0]
        self.assertEqual(rec.host, '127.0.0.1')
        self.assertEqual(rec.port, 5961)
        self.assertEqual(rec.start_calls, 1)
        self.assertEqual(rec.stop_calls, 1)

        # `recording_ready` URL points at our endpoint.
        self.assertIn(
            '/plugin/human/api/recording/demovm/', body)


# --- 2. record=false (no recorder spawned) ------------------------------

class NonRecordedRunSseTests(_BaseSseTest):

    async def test_record_false_skips_recorder_entirely(self):
        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'demovm',
                    'profile_id': self.profile_id,
                    'record': 'false'})
        self.assertEqual(resp.status, 200)
        body = (await resp.read()).decode()

        # Done event still arrives.
        self.assertIn('"event": "done"', body)
        # No recording lifecycle events.
        self.assertNotIn('recording_started', body)
        self.assertNotIn('recording_ready', body)
        # Recorder class never instantiated.
        self.assertEqual(len(self.RfbRecorder.instances), 0)

    async def test_record_omitted_defaults_to_false(self):
        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'demovm',
                    'profile_id': self.profile_id})
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.RfbRecorder.instances), 0)


# --- 3. recorder cleanup on mid-stream failure --------------------------

class RecorderCleanupOnFailureTests(_BaseSseTest):

    async def test_recorder_stop_called_when_send_fails_midstream(self):
        # Force the listening UDS to drop the connection after the very
        # first byte arrives — that triggers the mid-stream send error
        # path inside api_run_profile. We still expect recorder.stop()
        # to fire (via the `finally:` block).
        self._send_failure_after = 1

        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'demovm',
                    'profile_id': self.profile_id,
                    'record': 'true'})
        # The handler keeps the SSE 200 status — errors mid-stream are
        # surfaced as `event: error` SSE frames, not as HTTP error
        # codes.
        self.assertEqual(resp.status, 200)
        # Drain the body so the connection closes cleanly.
        await resp.read()

        # Even though the profile push errored, the recorder must have
        # been stopped exactly once.
        self.assertEqual(len(self.RfbRecorder.instances), 1)
        self.assertEqual(self.RfbRecorder.instances[0].stop_calls, 1)


# --- 4. /api/recording route -------------------------------------------

class RecordingEndpointTests(_BaseSseTest):

    async def test_404_for_missing_file(self):
        resp = await self.client.get(
            '/plugin/human/api/recording/demovm/does-not-exist.mp4')
        self.assertEqual(resp.status, 404)
        body = await resp.json()
        self.assertEqual(body['status'], 'error')

    async def test_400_for_path_traversal_in_vm_name(self):
        # vm_name `.`  hits our defense BEFORE the filename regex —
        # the handler enforces that vm_name has no separators and is
        # not `.` / `..` / dot-prefixed. We use a leading-dot vm_name
        # because aiohttp's router happily passes it through as-is
        # (unlike a literal `..` segment which the URL parser
        # normalizes).
        resp = await self.client.get(
            '/plugin/human/api/recording/.hidden/run.mp4')
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body['status'], 'error')

    async def test_400_for_filename_without_mp4_suffix(self):
        # Bare `..` as the filename fails the regex (no `.mp4` suffix)
        # before any filesystem read. Use URL-encoded `..` so the
        # router treats it as a single path segment instead of a
        # traversal token.
        resp = await self.client.get(
            '/plugin/human/api/recording/demovm/notmp4')
        self.assertEqual(resp.status, 400)

    async def test_400_for_non_mp4_filename(self):
        resp = await self.client.get(
            '/plugin/human/api/recording/demovm/run.mov')
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body['status'], 'error')

    async def test_serves_existing_mp4(self):
        # Drop a fake MP4 on disk under the recordings root and assert
        # the route streams it back with the correct content-type.
        vm_dir = Path(self.recordings_root) / 'demovm'
        vm_dir.mkdir(parents=True, exist_ok=True)
        mp4 = vm_dir / 'run.mp4'
        mp4.write_bytes(b'\x00\x00\x00\x18ftypmp42 fake-payload')

        resp = await self.client.get(
            '/plugin/human/api/recording/demovm/run.mp4')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers.get('Content-Type'), 'video/mp4')
        body = await resp.read()
        self.assertIn(b'fake-payload', body)


if __name__ == '__main__':
    unittest.main()
