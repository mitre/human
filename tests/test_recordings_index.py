"""Tests for the Recordings catalog endpoints.

Covers:

  1. ``GET /plugin/human/api/recordings`` returns the catalog of MP4
     files under ``RECORDINGS_BASE``, parsed from filenames of shape
     ``<YYYYMMDD-HHMMSS>-<ability>.mp4`` and sorted newest-first.
  2. Empty / nonexistent recordings dir returns ``{"recordings": []}``
     (200 OK), not a 500.
  3. Malformed filenames are skipped from the index (defensive).
  4. ``DELETE /plugin/human/api/recording/<vm>/<file>`` removes the
     MP4 and prunes empty VM dirs; 404 when the file is gone; 400
     for traversal / non-mp4 names.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

CALDERA_ROOT = '/home/caldera/Desktop/CalderaVENV/caldera'
if CALDERA_ROOT not in sys.path:
    sys.path.insert(0, CALDERA_ROOT)


# --- aiohttp wiring ------------------------------------------------------

class _BaseRecordingsTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from plugins.human.app import human_api
        self.human_api_mod = human_api

        self.recordings_root = tempfile.mkdtemp(prefix='test-recordings-idx-')
        self._orig_recordings = human_api.RECORDINGS_BASE
        human_api.RECORDINGS_BASE = self.recordings_root

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
            'GET', '/plugin/human/api/recordings',
            self.api.api_recordings_index)
        self.app.router.add_route(
            'DELETE', '/plugin/human/api/recording/{vm_name}/{filename}',
            self.api.api_recording_delete)
        self.app.router.add_route(
            'GET', '/plugin/human/api/recording/{vm_name}/{filename}',
            self.api.api_recording)

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
        self.human_api_mod.RECORDINGS_BASE = self._orig_recordings

    def _drop(self, vm_name: str, filename: str,
              payload: bytes = b'\x00\x00\x00\x18ftypmp42 fake') -> Path:
        """Drop a synthetic MP4 under <recordings_root>/<vm>/<filename>
        and return the absolute path."""
        vm_dir = Path(self.recordings_root) / vm_name
        vm_dir.mkdir(parents=True, exist_ok=True)
        path = vm_dir / filename
        path.write_bytes(payload)
        return path


# --- Index endpoint -----------------------------------------------------

class RecordingsIndexTests(_BaseRecordingsTest):

    async def test_empty_dir_returns_empty_list(self):
        # Default state: tmp dir created but empty.
        resp = await self.client.get('/plugin/human/api/recordings')
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body, {'recordings': []})

    async def test_nonexistent_base_returns_empty_list(self):
        # Point RECORDINGS_BASE at a path that doesn't exist; the
        # endpoint should still 200 with an empty list (mkdir is lazy
        # and only happens when the recorder is actually spawned).
        bogus = os.path.join(self.recordings_root, 'does-not-exist')
        self.human_api_mod.RECORDINGS_BASE = bogus
        try:
            resp = await self.client.get('/plugin/human/api/recordings')
            self.assertEqual(resp.status, 200)
            body = await resp.json()
            self.assertEqual(body, {'recordings': []})
        finally:
            self.human_api_mod.RECORDINGS_BASE = self.recordings_root

    async def test_lists_and_parses_filenames(self):
        # Two recordings on the same VM, one on another, in mixed
        # timestamp order to verify newest-first sorting.
        self._drop('windows-victim',
                   '20260509-122100-surf-the-web.mp4')
        self._drop('windows-victim',
                   '20260509-100500-open-default-browser.mp4')
        self._drop('linux-victim',
                   '20260509-130000-noop.mp4')

        resp = await self.client.get('/plugin/human/api/recordings')
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        recs = body['recordings']
        self.assertEqual(len(recs), 3)
        # Newest first.
        self.assertEqual(recs[0]['vm_name'], 'linux-victim')
        self.assertEqual(recs[0]['ability'], 'noop')
        self.assertEqual(recs[0]['timestamp'], '2026-05-09T13:00:00')
        self.assertEqual(
            recs[0]['url'],
            '/plugin/human/api/recording/linux-victim/'
            '20260509-130000-noop.mp4')
        # Timestamps decreasing.
        self.assertEqual(recs[1]['ability'], 'surf-the-web')
        self.assertEqual(recs[2]['ability'], 'open-default-browser')
        # Size populated.
        for r in recs:
            self.assertGreater(r['size_bytes'], 0)

    async def test_skips_malformed_filenames_in_index(self):
        # Filename without timestamp prefix: still listed (size + url),
        # but ability/timestamp are None and it sorts to the bottom.
        # Filename failing the SAFE regex (e.g. embedded space) is
        # dropped entirely.
        self._drop('windows-victim',
                   '20260509-122100-surf-the-web.mp4')
        self._drop('windows-victim',
                   'no-timestamp-prefix.mp4')
        # Embedded space: violates _SAFE_MP4_FILENAME_RE.
        bad = Path(self.recordings_root) / 'windows-victim' / 'has space.mp4'
        bad.write_bytes(b'x')

        resp = await self.client.get('/plugin/human/api/recordings')
        self.assertEqual(resp.status, 200)
        recs = (await resp.json())['recordings']
        names = [r['filename'] for r in recs]
        self.assertIn('20260509-122100-surf-the-web.mp4', names)
        self.assertIn('no-timestamp-prefix.mp4', names)
        self.assertNotIn('has space.mp4', names)
        # Malformed filename has no parsed metadata.
        bad_rec = next(r for r in recs
                       if r['filename'] == 'no-timestamp-prefix.mp4')
        self.assertIsNone(bad_rec['timestamp'])
        self.assertIsNone(bad_rec['ability'])

    async def test_skips_dotted_vm_dirs(self):
        # A hidden directory shouldn't be indexable (defensive — the
        # api_recording handler also rejects dot-prefix vm_names).
        hidden_dir = Path(self.recordings_root) / '.hidden'
        hidden_dir.mkdir()
        (hidden_dir / '20260509-122100-x.mp4').write_bytes(b'x')

        resp = await self.client.get('/plugin/human/api/recordings')
        recs = (await resp.json())['recordings']
        self.assertEqual(recs, [])

    async def test_ignores_non_mp4_files(self):
        # A .txt sibling shouldn't break the listing.
        self._drop('windows-victim', '20260509-122100-x.mp4')
        sibling = (Path(self.recordings_root) / 'windows-victim'
                   / 'README.txt')
        sibling.write_text('notes')

        resp = await self.client.get('/plugin/human/api/recordings')
        recs = (await resp.json())['recordings']
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['filename'], '20260509-122100-x.mp4')


# --- DELETE endpoint ----------------------------------------------------

class RecordingDeleteTests(_BaseRecordingsTest):

    async def test_delete_removes_file(self):
        path = self._drop('windows-victim',
                          '20260509-122100-surf-the-web.mp4')
        self.assertTrue(path.is_file())

        resp = await self.client.delete(
            '/plugin/human/api/recording/windows-victim/'
            '20260509-122100-surf-the-web.mp4')
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body['status'], 'ok')
        self.assertFalse(path.is_file())
        # Empty VM dir was pruned.
        self.assertFalse(path.parent.is_dir())

    async def test_delete_keeps_other_files_in_vm_dir(self):
        a = self._drop('windows-victim',
                       '20260509-100000-a.mp4')
        b = self._drop('windows-victim',
                       '20260509-200000-b.mp4')
        resp = await self.client.delete(
            '/plugin/human/api/recording/windows-victim/'
            '20260509-100000-a.mp4')
        self.assertEqual(resp.status, 200)
        self.assertFalse(a.is_file())
        self.assertTrue(b.is_file())
        # Sibling file -> dir not pruned.
        self.assertTrue(b.parent.is_dir())

    async def test_delete_404_when_missing(self):
        resp = await self.client.delete(
            '/plugin/human/api/recording/windows-victim/'
            'never-existed.mp4')
        self.assertEqual(resp.status, 404)
        body = await resp.json()
        self.assertEqual(body['status'], 'error')

    async def test_delete_400_for_non_mp4(self):
        resp = await self.client.delete(
            '/plugin/human/api/recording/windows-victim/run.mov')
        self.assertEqual(resp.status, 400)

    async def test_delete_400_for_dot_prefix_vm(self):
        resp = await self.client.delete(
            '/plugin/human/api/recording/.hidden/run.mp4')
        self.assertEqual(resp.status, 400)


# --- Default RECORDINGS_BASE (post-move) --------------------------------

class DefaultRecordingsBaseTests(unittest.TestCase):

    def test_default_base_is_under_plugin_data_dir(self):
        # Reload the module fresh in a subprocess-style sentinel: we
        # check the module-level constant resolves to a path under
        # plugins/human/data/recordings rather than /var/lib/timestone.
        from plugins.human.app import human_api
        # The patched value is restored by other test setUp/tearDown,
        # so we look at the default we'd compute right now (the env
        # var fallback).
        plugin_root = Path(human_api.__file__).resolve().parent.parent
        expected = plugin_root / 'data' / 'recordings'
        # ``RECORDINGS_BASE`` may have been monkey-patched by an earlier
        # test that didn't run under unittest's isolation; assert the
        # *default* the module computes against the env var instead.
        # Re-derive the default by checking _DEFAULT_RECORDINGS_BASE.
        self.assertEqual(
            Path(human_api._DEFAULT_RECORDINGS_BASE),
            expected)
        # Also assert the sentinel /var/lib path is NOT what we default
        # to anymore.
        self.assertNotIn(
            '/var/lib/timestone',
            human_api._DEFAULT_RECORDINGS_BASE)


if __name__ == '__main__':
    unittest.main()
