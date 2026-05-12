import asyncio
import os
import shutil
import sys
import tarfile
import tempfile
import unittest


CALDERA_ROOT = '/home/caldera/Desktop/CalderaVENV/caldera'
if CALDERA_ROOT not in sys.path:
    sys.path.insert(0, CALDERA_ROOT)


class _DataStore:
    def __init__(self):
        self.collections = {'humans': [], 'workflows': []}

    async def locate(self, collection, match=None):
        rows = list(self.collections.get(collection, []))
        if not match:
            return rows
        out = []
        for row in rows:
            keep = True
            for key, value in match.items():
                if value is None:
                    continue
                if getattr(row, key, None) != value:
                    keep = False
                    break
            if keep:
                out.append(row)
        return out

    async def store(self, obj):
        collection = 'humans' if obj.__class__.__name__ == 'Human' else 'workflows'
        self.collections.setdefault(collection, []).append(obj)
        return obj


class _Services(dict):
    def __init__(self, data_svc):
        super().__init__()
        self['file_svc'] = None
        self['data_svc'] = data_svc

    def get(self, key):
        return super().get(key)


class _Log:
    def debug(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _write_pyh_root(root):
    pyhuman = os.path.join(root, 'pyhuman')
    workflows_dir = os.path.join(pyhuman, 'app', 'workflows')
    utility_dir = os.path.join(pyhuman, 'app', 'utility')
    os.makedirs(workflows_dir)
    os.makedirs(utility_dir)
    os.makedirs(os.path.join(pyhuman, 'data'))
    with open(os.path.join(pyhuman, 'human.py'), 'w') as f:
        f.write('print("human")\n')
    with open(os.path.join(pyhuman, 'requirements.txt'), 'w') as f:
        f.write('selenium\n')
    with open(os.path.join(utility_dir, 'base_driver.py'), 'w') as f:
        f.write('# utility\n')
    with open(os.path.join(workflows_dir, 'browse_web.py'), 'w') as f:
        f.write(
            'import dependency_that_should_not_be_imported\n'
            'WORKFLOW_NAME = \"browse_web\"\n'
            'WORKFLOW_DESCRIPTION = \"Browse the web\"\n'
        )
    return pyhuman


def _make_service(root, data_svc):
    from plugins.human.app import human_svc as svc_mod
    inst = svc_mod.HumanService.__new__(svc_mod.HumanService)
    inst.services = _Services(data_svc)
    inst.file_svc = None
    inst.data_svc = data_svc
    inst.log = _Log()
    inst.human_dir = root
    inst.pyhuman_path = os.path.join(root, 'pyhuman')
    return inst


class LegacyBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='human-legacy-builder-')
        _write_pyh_root(self.tmpdir)
        self.data_svc = _DataStore()
        self.svc = _make_service(self.tmpdir, self.data_svc)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lists_legacy_workflows_without_importing_modules(self):
        out = _run(self.svc.list_legacy_workflows())
        self.assertEqual(out['workflows'], [{
            'name': 'browse_web',
            'description': 'Browse the web',
            'file': 'browse_web.py',
        }])

    def test_builds_unix_archive_and_registers_human(self):
        built = _run(self.svc.build_human({
            'name': '../demo-human',
            'platform': 'linux',
            'tasks': ['browse_web'],
            'task_interval': 11,
            'task_count': 3,
            'task_cluster_interval': 222,
            'extra': ['echo hello'],
        }))

        self.assertEqual(built['name'], 'demo-human')
        self.assertEqual(built['platform'], 'linux')
        self.assertEqual(built['task_interval'], 11)
        self.assertEqual(built['tasks_per_cluster'], 3)
        self.assertEqual(built['extra'], ['echo hello'])

        archive = os.path.join(self.tmpdir, 'payloads', 'demo-human.tar.gz')
        self.assertTrue(os.path.exists(archive))
        with tarfile.open(archive, 'r:gz') as tf:
            names = set(tf.getnames())
        self.assertIn('human.py', names)
        self.assertIn('requirements.txt', names)
        self.assertIn('app/workflows/browse_web.py', names)


if __name__ == '__main__':
    unittest.main()
