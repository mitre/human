"""Bridge test for the Run-Profile SSE endpoint -> per-VM operator socket.

Covers two layers:

1. ``_resolve_operator_socket(host_id)`` correctly maps a VM name to the
   ``input_daemon.operator_socket`` path written by the Range provider's
   A3 microvm-launch agent into ``<BASE>/<vm_name>-<vm_id>/meta.json``.

2. The full ``/plugin/human/api/run-profile`` SSE handler, given a
   stubbed meta.json and a tiny on-disk profile, opens the resolved UDS
   and writes one line of JSON per materialized OperatorMessage. We
   listen on the UDS in a background thread and assert what arrived.

The test stubs the ``MICROVM_RUNTIME_BASE`` module global to point at a
tmp dir, stubs ``ATOMIC_ABILITIES_DIR`` / ``ADVERSARIES_DIR`` at module
scope to point at fixture dirs, and uses ``aiohttp.test_utils`` to drive
the handler without standing up the full Caldera server.
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import unittest

# The Human plugin imports as ``plugins.human.*`` because Caldera mounts
# its plugin dirs under that namespace. Add the Caldera root to sys.path
# so the test can ``import plugins.human.app.human_api``.
CALDERA_ROOT = '/home/caldera/Desktop/CalderaVENV/caldera'
if CALDERA_ROOT not in sys.path:
    sys.path.insert(0, CALDERA_ROOT)


def _stub_meta(base_dir: str, host_id: str, sock_path: str) -> str:
    """Write a Range-provider-shaped meta.json under
    ``<base_dir>/<host_id>-<suffix>/`` and return the run_dir path."""
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
            'pid': 1235,
        },
    }
    with open(os.path.join(run_dir, 'meta.json'), 'w') as f:
        json.dump(meta, f)
    return run_dir


# --- 1. resolution unit tests -------------------------------------------

class ResolveOperatorSocketTests(unittest.TestCase):

    def setUp(self):
        from plugins.human.app import human_api
        self.human_api_mod = human_api
        self.HumanApi = human_api.HumanApi
        self.tmpbase = tempfile.mkdtemp(prefix='test-microvms-')
        self._orig_base = human_api.MICROVM_RUNTIME_BASE
        human_api.MICROVM_RUNTIME_BASE = self.tmpbase

    def tearDown(self):
        self.human_api_mod.MICROVM_RUNTIME_BASE = self._orig_base
        # Best-effort cleanup; tests run in /tmp so leaks aren't fatal.
        for root, dirs, files in os.walk(self.tmpbase, topdown=False):
            for f in files:
                try:
                    os.unlink(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(self.tmpbase)
        except OSError:
            pass

    def test_resolves_per_vm_socket_from_meta_json(self):
        sock = '/tmp/test-op-resolve.sock'
        _stub_meta(self.tmpbase, 'vm1', sock)
        self.assertEqual(self.HumanApi._resolve_operator_socket('vm1'), sock)

    def test_missing_run_dir_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            self.HumanApi._resolve_operator_socket('nope')
        self.assertIn('nope', str(ctx.exception))

    def test_missing_input_daemon_block_raises_keyerror(self):
        host = 'vm-no-gui'
        run_dir = os.path.join(self.tmpbase, f'{host}-aaaa')
        os.makedirs(run_dir, exist_ok=True)
        # meta.json with NO input_daemon block (headless VM).
        with open(os.path.join(run_dir, 'meta.json'), 'w') as f:
            json.dump({'gpu_daemon': {}}, f)
        with self.assertRaises(KeyError):
            self.HumanApi._resolve_operator_socket(host)


# --- 2. end-to-end SSE handler test -------------------------------------

class RunProfileSseTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from plugins.human.app import human_api
        self.human_api_mod = human_api

        # Per-test isolated dirs.
        self.tmpbase = tempfile.mkdtemp(prefix='test-microvms-')
        self.fixture_root = tempfile.mkdtemp(prefix='test-human-fixtures-')
        self.adv_dir = os.path.join(self.fixture_root, 'adversaries')
        self.atomic_dir = os.path.join(self.fixture_root, 'atomic')
        os.makedirs(self.adv_dir)
        os.makedirs(self.atomic_dir)

        # Tiny profile + ability so the materializer has something to chew on.
        # Two atomic-ordering steps -> we'll see two OperatorMessages on the
        # socket.
        ability_id = '11111111-1111-1111-1111-111111111111'
        # Shape mirrors data/abilities/benign-human-activity/atomic/*.yml:
        # a top-level list with one entry; `hid.steps` is the list the
        # materializer expands into OperatorMessages.
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

        # Patch module-level paths.
        from pathlib import Path
        self._orig_base = human_api.MICROVM_RUNTIME_BASE
        self._orig_adv = human_api.ADVERSARIES_DIR
        self._orig_atomic = human_api.ATOMIC_ABILITIES_DIR
        human_api.MICROVM_RUNTIME_BASE = self.tmpbase
        human_api.ADVERSARIES_DIR = Path(self.adv_dir)
        human_api.ATOMIC_ABILITIES_DIR = Path(self.atomic_dir)

        # Listening UDS in a background thread; appends each received line
        # (decoded JSON) to ``self.received``.
        self.sock_path = os.path.join(self.fixture_root, 'op.sock')
        self.received = []
        self._stop_evt = threading.Event()

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sock_path)
        srv.listen(1)
        srv.settimeout(0.5)
        self._server_sock = srv

        def _serve():
            # accept() loop with timeout: lets asyncTearDown signal exit
            # without leaving the thread blocked on a closed fd.
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
            try:
                while not self._stop_evt.is_set():
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        if line.strip():
                            try:
                                self.received.append(
                                    json.loads(line.decode()))
                            except Exception:
                                self.received.append(
                                    {'_raw': line.decode(errors='replace')})
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        self._server_thread = threading.Thread(target=_serve, daemon=True)
        self._server_thread.start()

        _stub_meta(self.tmpbase, 'demovm', self.sock_path)

        # Build the aiohttp app. The HumanApi class auto-wraps public
        # methods with the auth decorator, so we need a minimal services
        # dict that satisfies ``self.auth_svc.check_permissions``.
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
            'POST', '/plugin/human/api/input', self.api.api_input)

        from aiohttp.test_utils import TestServer, TestClient
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.client = TestClient(self.server)
        await self.client.start_server()

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

    async def test_sse_run_profile_writes_to_per_vm_socket(self):
        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'demovm', 'profile_id': self.profile_id})
        self.assertEqual(resp.status, 200)
        body = (await resp.read()).decode()
        # Stream must end with the done event.
        self.assertIn('"event": "done"', body)
        self.assertIn('"count": 2', body)

        # Give the server thread a moment to flush.
        for _ in range(40):
            if len(self.received) >= 2:
                break
            await asyncio.sleep(0.05)

        self.assertEqual(len(self.received), 2,
                         f'expected 2 OperatorMessages on socket, got '
                         f'{len(self.received)}: {self.received!r}')
        # Each message must look like a materialized OperatorMessage:
        # the materializer emits dicts whose discriminator is `action`
        # (matches the Rust OperatorMessage enum's serde shape).
        for msg in self.received:
            self.assertIsInstance(msg, dict)
            self.assertIn('action', msg)
            self.assertEqual(msg['action'], 'dwell')
            self.assertEqual(msg['ms'], 100)

    async def test_sse_run_profile_unknown_host_returns_400(self):
        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'no-such-vm', 'profile_id': self.profile_id})
        self.assertEqual(resp.status, 400)
        body = await resp.json()
        self.assertEqual(body['status'], 'error')
        self.assertIn('no-such-vm', body['error'])

    async def test_api_input_normalizes_and_writes_to_operator_socket(self):
        resp = await self.client.post(
            '/plugin/human/api/input',
            json={
                'host_id': 'demovm',
                'messages': [
                    {
                        'action': 'move',
                        'target': {
                            'kind': 'absolute',
                            'x': 512,
                            'y': 384,
                        },
                    },
                    {'action': 'click', 'button': 'left'},
                    {'action': 'scroll', 'direction': 'down', 'ticks': 2},
                ],
            })
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body['status'], 'success')
        self.assertEqual(body['sent'], 3)

        for _ in range(40):
            if len(self.received) >= 3:
                break
            await asyncio.sleep(0.05)

        self.assertEqual(len(self.received), 3, self.received)
        self.assertEqual(self.received[0]['action'], 'move')
        self.assertEqual(self.received[0]['target']['kind'], 'abs')
        self.assertEqual(self.received[0]['duration_ms'], 0)
        self.assertEqual(self.received[1], {'action': 'click', 'button': 'left'})
        self.assertEqual(
            self.received[2],
            {'action': 'scroll', 'wheel': 'down', 'ticks': 2})


# --- 3. platform-aware abilities (surf-the-web) regression --------------
#
# Regression for the bug where ``api_run_profile`` invoked
# ``materialize_profile`` with no ``target_os``, which caused every
# platform-shaped HID atomic ability (commit 8129246) to bomb with
# ``KeyError: ability requires an OS to pick a platform branch ...``.
# The endpoint now reads ``meta.json``'s ``os`` field (or accepts an
# ``os=`` query override) and forwards it to the materializer. This
# test wires the *real* surf-the-web profile and the *real* atomic
# ability dir against a stub UDS and confirms the stream is pure
# OperatorMessages — no shell-cradle ``cmd`` / ``sh`` payloads.

class RunProfileSurfTheWebTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from pathlib import Path

        from plugins.human.app import human_api
        self.human_api_mod = human_api

        self.tmpbase = tempfile.mkdtemp(prefix='test-microvms-surf-')
        self.fixture_root = tempfile.mkdtemp(prefix='test-human-surf-')
        self.sock_path = os.path.join(self.fixture_root, 'op.sock')

        # meta.json with os=windows so the platform branch resolves.
        run_dir = os.path.join(self.tmpbase, 'windows-victim-deadbeef')
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, 'meta.json'), 'w') as f:
            json.dump({
                'vm_name': 'windows-victim',
                'os': 'windows',
                'input_daemon': {
                    'socket': os.path.join(run_dir, 'input.sock'),
                    'operator_socket': self.sock_path,
                    'pid': 1234,
                },
            }, f)

        # Point the plugin at the *shipped* surf-the-web profile + the
        # real atomic ability dir.
        plugin_root = (Path(__file__).resolve().parent.parent
                       / 'data')
        self._orig_base = human_api.MICROVM_RUNTIME_BASE
        self._orig_adv = human_api.ADVERSARIES_DIR
        self._orig_atomic = human_api.ATOMIC_ABILITIES_DIR
        human_api.MICROVM_RUNTIME_BASE = self.tmpbase
        human_api.ADVERSARIES_DIR = plugin_root / 'adversaries'
        human_api.ATOMIC_ABILITIES_DIR = (
            plugin_root / 'abilities' / 'benign-human-activity' / 'atomic')

        # UDS listener in a background thread.
        self.received = []
        self._stop_evt = threading.Event()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sock_path)
        srv.listen(1)
        srv.settimeout(0.5)
        self._server_sock = srv

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
            try:
                while not self._stop_evt.is_set():
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        if line.strip():
                            try:
                                self.received.append(
                                    json.loads(line.decode()))
                            except Exception:
                                self.received.append(
                                    {'_raw': line.decode(errors='replace')})
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        self._server_thread = threading.Thread(target=_serve, daemon=True)
        self._server_thread.start()

        # Bare aiohttp app wrapping the handler, no auth.
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

        from aiohttp.test_utils import TestServer, TestClient
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.client = TestClient(self.server)
        await self.client.start_server()

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

    async def test_surf_the_web_emits_only_hid_operator_messages(self):
        """surf-the-web on a windows host must produce a pure HID stream.

        Asserts:
          - SSE response 200
          - every received message has an `action` field
          - every action is in the OperatorMessage vocabulary
          - no message contains a shell-cradle key (cmd/sh/payload/workflow)
        """
        # OS resolves from meta.json (windows), so no `os=` override needed.
        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'windows-victim',
                    'profile_id': 'surf-the-web'})
        self.assertEqual(resp.status, 200,
                         f'unexpected status {resp.status}: '
                         f'{await resp.text()}')
        body = (await resp.read()).decode()
        self.assertIn('"event": "done"', body,
                      'SSE stream did not finish cleanly')

        # Drain the UDS listener.
        for _ in range(60):
            if len(self.received) >= 30:
                break
            await asyncio.sleep(0.05)

        self.assertGreater(len(self.received), 20,
                           f'expected many HID OperatorMessages, got '
                           f'{len(self.received)}')

        op_actions = {
            'move', 'click', 'press', 'keydown', 'keyup',
            'type', 'dwell', 'wait_for', 'scroll', 'chord', 'raw',
        }
        shell_cradle_keys = {'cmd', 'sh', 'payload', 'workflow', 'command'}
        for msg in self.received:
            self.assertIsInstance(msg, dict)
            self.assertIn('action', msg,
                          f'message missing `action`: {msg!r}')
            self.assertIn(msg['action'], op_actions,
                          f'non-OperatorMessage action {msg["action"]!r} '
                          f'in stream: {msg!r}')
            for k in shell_cradle_keys:
                self.assertNotIn(k, msg,
                                 f'shell-cradle key {k!r} leaked into '
                                 f'OperatorMessage: {msg!r}')

        # First two messages must be the open-default-browser launcher
        # chord on Windows (LeftMeta+r), proving the platform branch
        # resolved correctly.
        self.assertEqual(self.received[0]['action'], 'chord')
        self.assertEqual(self.received[0]['keys'], ['LeftMeta', 'r'])
        self.assertEqual(self.received[1]['action'], 'dwell')

    async def test_os_query_override_picks_platform_branch(self):
        """Caller may force a platform branch via ?os=darwin."""
        resp = await self.client.get(
            '/plugin/human/api/run-profile',
            params={'host_id': 'windows-victim',
                    'profile_id': 'surf-the-web',
                    'os': 'darwin'})
        self.assertEqual(resp.status, 200)
        for _ in range(60):
            if len(self.received) >= 1:
                break
            await asyncio.sleep(0.05)
        # On macOS the open-default-browser launcher is Cmd+Space.
        self.assertGreater(len(self.received), 0)
        self.assertEqual(self.received[0]['action'], 'chord')
        self.assertEqual(self.received[0]['keys'], ['LeftMeta', 'space'])


# --- 4. _resolve_target_os unit tests -----------------------------------

class ResolveTargetOsTests(unittest.TestCase):

    def setUp(self):
        from plugins.human.app import human_api
        self.human_api_mod = human_api
        self.HumanApi = human_api.HumanApi
        self.tmpbase = tempfile.mkdtemp(prefix='test-microvms-os-')
        self._orig_base = human_api.MICROVM_RUNTIME_BASE
        human_api.MICROVM_RUNTIME_BASE = self.tmpbase

    def tearDown(self):
        self.human_api_mod.MICROVM_RUNTIME_BASE = self._orig_base
        for root, dirs, files in os.walk(self.tmpbase, topdown=False):
            for f in files:
                try:
                    os.unlink(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(self.tmpbase)
        except OSError:
            pass

    def _write_meta(self, host_id, payload):
        run_dir = os.path.join(self.tmpbase, f'{host_id}-aaaa')
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, 'meta.json'), 'w') as f:
            json.dump(payload, f)

    def test_reads_os_from_meta(self):
        self._write_meta('vm1', {'os': 'windows'})
        self.assertEqual(self.HumanApi._resolve_target_os('vm1'), 'windows')

    def test_normalizes_macos_synonym(self):
        self._write_meta('vm2', {'os': 'macOS'})
        self.assertEqual(self.HumanApi._resolve_target_os('vm2'), 'darwin')

    def test_override_wins(self):
        self._write_meta('vm3', {'os': 'windows'})
        self.assertEqual(
            self.HumanApi._resolve_target_os('vm3', override='linux'),
            'linux')

    def test_missing_meta_returns_empty(self):
        self.assertEqual(self.HumanApi._resolve_target_os('nope'), '')


if __name__ == '__main__':
    unittest.main()
