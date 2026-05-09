"""Tests for ``human_svc.list_range_hosts()``.

The Human plugin's host dropdown is populated by ``list_range_hosts``.
The Range plugin does not (yet) publish hosts through
``data_svc.locate('range_instances')``; instead its A3 microvm-launch
agent writes a ``meta.json`` per VM under
``<MICROVM_RUNTIME_BASE>/<vm_name>-<vm_id>/meta.json``. These tests
exercise the meta.json-scan path.

Acceptance:
  - empty base dir -> stubs (back-compat)
  - one meta.json  -> one real host with the right fields
  - many meta.json -> all returned
  - malformed meta -> skipped + others returned
  - fields propagate (vm_name -> id/name, ip -> ip, etc.)
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest

# Same sys.path bootstrap as test_run_profile_socket.py: Caldera mounts
# Human as ``plugins.human.*``.
CALDERA_ROOT = '/home/caldera/Desktop/CalderaVENV/caldera'
if CALDERA_ROOT not in sys.path:
    sys.path.insert(0, CALDERA_ROOT)


def _write_meta(base_dir: str, vm_name: str, vm_id: str, **overrides) -> str:
    """Write a Range-provider-shaped meta.json. Returns the run dir."""
    run_dir = os.path.join(base_dir, f'{vm_name}-{vm_id}')
    os.makedirs(run_dir, exist_ok=True)
    meta = {
        'vm_name': vm_name,
        'os': 'windows',
        'ip': '192.168.66.97',
        'ch_pid': 12345,
        'tap': f'tap-ts-{vm_id}',
        'input_daemon': {
            'socket': os.path.join(run_dir, 'input.sock'),
            'operator_socket': os.path.join(run_dir, 'input-op.sock'),
            'pid': 12346,
        },
        'gpu_daemon': {
            'socket': os.path.join(run_dir, 'gpu.sock'),
            'vnc_port': 5901,
            'pid': 12347,
            'log': os.path.join(run_dir, 'gpu.log'),
        },
    }
    meta.update(overrides)
    with open(os.path.join(run_dir, 'meta.json'), 'w') as f:
        json.dump(meta, f)
    return run_dir


class _DataStub:
    """data_svc.locate() that always raises (mirrors current Range plugin
    behavior — no `range_instances` collection registered)."""

    async def locate(self, *a, **kw):
        raise KeyError('range_instances')


class _DataStubEmpty:
    """data_svc.locate() that returns []. Should fall through to the
    meta.json scan path identically."""

    async def locate(self, *a, **kw):
        return []


class _DataStubReturning:
    """data_svc.locate() that returns a real list. Used to verify the
    forward-compat preference for data_svc when it ever publishes."""

    def __init__(self, hosts):
        self._hosts = hosts

    async def locate(self, *a, **kw):
        return self._hosts


class _Services(dict):
    """Minimum services dict HumanService can stomach."""

    def __init__(self, data_svc):
        super().__init__()
        self['file_svc'] = None
        self['data_svc'] = data_svc

    def get(self, key):
        return super().get(key)


def _make_svc(data_svc):
    """Build a HumanService with stubbed deps. We bypass __init__ to
    avoid sys.path mutation and the ``add_service`` registry side-effect
    (which BaseService inherits and which assumes a running Caldera)."""
    from plugins.human.app import human_svc as svc_mod
    inst = svc_mod.HumanService.__new__(svc_mod.HumanService)
    inst.file_svc = None
    inst.data_svc = data_svc
    inst.log = svc_mod._log
    inst.human_dir = '.'
    inst.pyhuman_path = '.'
    return inst


class ListRangeHostsTests(unittest.TestCase):

    def setUp(self):
        from plugins.human.app import human_api
        self.human_api = human_api
        self.tmpbase = tempfile.mkdtemp(prefix='test-list-range-hosts-')
        self._orig_base = human_api.MICROVM_RUNTIME_BASE
        human_api.MICROVM_RUNTIME_BASE = self.tmpbase

    def tearDown(self):
        self.human_api.MICROVM_RUNTIME_BASE = self._orig_base
        shutil.rmtree(self.tmpbase, ignore_errors=True)

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro) \
            if False else asyncio.new_event_loop().run_until_complete(coro)

    # 1. No meta.json -> stubs.
    def test_no_meta_returns_stubs(self):
        svc = _make_svc(_DataStub())
        out = self._run(svc.list_range_hosts())
        self.assertEqual(out['profile'], '(stub)')
        ids = [h['id'] for h in out['hosts']]
        self.assertEqual(ids, ['host-stub-1', 'host-stub-2'])

    # 1b. data_svc returns empty list -> still falls through to scan/stub.
    def test_empty_data_svc_returns_stubs(self):
        svc = _make_svc(_DataStubEmpty())
        out = self._run(svc.list_range_hosts())
        self.assertEqual(out['profile'], '(stub)')
        self.assertEqual(len(out['hosts']), 2)

    # 2. One meta.json -> one real host with right fields.
    def test_single_meta_returns_one_real_host(self):
        _write_meta(self.tmpbase, 'windows-victim', '01afadfd',
                    ip='192.168.66.97', os='windows')
        svc = _make_svc(_DataStub())
        out = self._run(svc.list_range_hosts())
        self.assertEqual(out['profile'], '(active range)')
        self.assertEqual(len(out['hosts']), 1)
        h = out['hosts'][0]
        self.assertEqual(h['id'], 'windows-victim')
        self.assertEqual(h['name'], 'windows-victim')
        self.assertEqual(h['ip'], '192.168.66.97')
        self.assertEqual(h['os'], 'windows')
        self.assertEqual(h['status'], 'running')
        self.assertEqual(h['provider'], 'microvm')

    # 3. Multiple meta.json -> all returned.
    def test_multiple_meta_returns_all(self):
        _write_meta(self.tmpbase, 'vm-a', 'aaaa1111', ip='10.0.0.1')
        _write_meta(self.tmpbase, 'vm-b', 'bbbb2222', ip='10.0.0.2',
                    os='linux')
        _write_meta(self.tmpbase, 'vm-c', 'cccc3333', ip='10.0.0.3')
        svc = _make_svc(_DataStub())
        out = self._run(svc.list_range_hosts())
        self.assertEqual(out['profile'], '(active range)')
        self.assertEqual(len(out['hosts']), 3)
        names = sorted(h['name'] for h in out['hosts'])
        self.assertEqual(names, ['vm-a', 'vm-b', 'vm-c'])
        ips = {h['name']: h['ip'] for h in out['hosts']}
        self.assertEqual(ips['vm-a'], '10.0.0.1')
        self.assertEqual(ips['vm-b'], '10.0.0.2')
        self.assertEqual(ips['vm-c'], '10.0.0.3')

    # 4. Malformed meta.json -> skipped, others returned.
    def test_malformed_meta_is_skipped(self):
        _write_meta(self.tmpbase, 'good-vm', 'aaaa1111', ip='10.0.0.5')
        # Junk file that can't be parsed as JSON.
        bad_dir = os.path.join(self.tmpbase, 'broken-vm-bbbb2222')
        os.makedirs(bad_dir, exist_ok=True)
        with open(os.path.join(bad_dir, 'meta.json'), 'w') as f:
            f.write('this is not json {[}')
        # meta.json missing required fields (no vm_name / no ip).
        partial_dir = os.path.join(self.tmpbase, 'partial-vm-cccc3333')
        os.makedirs(partial_dir, exist_ok=True)
        with open(os.path.join(partial_dir, 'meta.json'), 'w') as f:
            json.dump({'os': 'linux'}, f)

        svc = _make_svc(_DataStub())
        out = self._run(svc.list_range_hosts())
        self.assertEqual(len(out['hosts']), 1)
        self.assertEqual(out['hosts'][0]['name'], 'good-vm')

    # 5. ip + vm_name propagate to the right output fields.
    def test_field_propagation(self):
        _write_meta(self.tmpbase, 'my-special-vm', 'deadbeef',
                    ip='172.16.99.42', os='ubuntu-22')
        svc = _make_svc(_DataStub())
        out = self._run(svc.list_range_hosts())
        h = out['hosts'][0]
        # vm_name -> both id and name
        self.assertEqual(h['id'], 'my-special-vm')
        self.assertEqual(h['name'], 'my-special-vm')
        # ip -> ip
        self.assertEqual(h['ip'], '172.16.99.42')
        # os -> os
        self.assertEqual(h['os'], 'ubuntu-22')
        # provider/status are constants for the microvm path.
        self.assertEqual(h['provider'], 'microvm')
        self.assertEqual(h['status'], 'running')

    # Forward-compat: when data_svc actually returns range_instances we
    # prefer it over the meta.json scan.
    def test_data_svc_preferred_when_populated(self):
        # Even with a meta.json present, the data_svc result wins.
        _write_meta(self.tmpbase, 'meta-vm', 'aaaa', ip='10.0.0.99')
        fake_host = {'id': 'data-svc-host', 'name': 'data-svc-host',
                     'ip': '10.0.0.1', 'status': 'running'}
        svc = _make_svc(_DataStubReturning([fake_host]))
        out = self._run(svc.list_range_hosts())
        self.assertEqual(out['profile'], '(active range)')
        self.assertEqual(len(out['hosts']), 1)
        self.assertEqual(out['hosts'][0]['id'], 'data-svc-host')


if __name__ == '__main__':
    unittest.main()
