import os
from importlib import import_module

from app.utility.base_service import BaseService
from plugins.human.app.c_human import Human
from plugins.human.app.c_workflow import Workflow


class HumanService(BaseService):

    def __init__(self, services):
        self.file_svc = services.get('file_svc')
        self.data_svc = services.get('data_svc')
        self.log = self.add_service('human_svc', self)
        self.human_dir = os.path.relpath(os.path.join('plugins', 'human'))
        self.pyhuman_path = os.path.abspath(os.path.join(self.human_dir, 'pyhuman', 'pyhuman'))

    async def build_human(self, data):
        try:
            name = data.pop('name')
            await self._select_modules_and_compress(modules=data.pop('tasks'), name=name, platform=data.pop('platform'),
                                                    task_interval=data.pop('task_interval'), tasks_per_cluster=data.pop('task_count'),
                                                    task_cluster_interval=data.pop('task_cluster_interval'))
            return (await self.data_svc.locate('humans', match=dict(name=name)))[0].display
        except Exception as e:
            self.log.error('Error building human. %s' % e)

    async def load_humans(self, data):
        return [h.display for h in await self.data_svc.locate('humans', match=dict(name=data.get('name')))]

    async def load_available_workflows(self):
        for root, dirs, files in os.walk(os.path.join(self.human_dir, 'pyhuman', 'app', 'workflows')):
            files = [f for f in files if not f[0] == '.' and not f[0] == '_']
            dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == '_']
            for file in files:
                await self._load_workflow_module(root, file)

    """ PRIVATE """

    async def _load_workflow_module(self, root, file):
        module = os.path.join(root, file.split('.')[0]).replace(os.path.sep, '.')
        try:
            loaded = getattr(import_module(module), 'load')(driver=None)
            await self.data_svc.store(Workflow(name=loaded.name, description=loaded.description, file=file))
        except Exception as e:
            self.log.error('Error loading extension=%s, %s' % (module, e))

    async def _select_modules_and_compress(self, modules, name, platform, task_interval, task_cluster_interval, tasks_per_cluster):
        algo, args, ext = await self._get_compression_params(platform=platform)
        chdir = 'cd {} && '.format(self.pyhuman_path)
        compress = '{} {} {}.{} human.py data/* app/utility/* requirements.txt'.format(
            algo, args, os.path.abspath(os.path.join(self.human_dir, 'payloads', name)), ext)
        compress, workflows = await self._append_module_paths(modules, compress)
        self.log.debug('Compressing new human: %s' % name)
        os.system(chdir + compress)
        await self.data_svc.store(Human(name=name, task_interval=task_interval, task_cluster_interval=task_cluster_interval,
                                        tasks_per_cluster=tasks_per_cluster, platform=platform, workflows=workflows))

    @staticmethod
    async def _get_compression_params(platform):
        if platform == 'windows-psh':
            return 'zip', '-qq', 'zip'
        return 'tar', 'zcf', 'tar.gz'

    async def _append_module_paths(self, modules, compress):
        workflows = []
        for sm in modules:
            workflow = await self.data_svc.locate('workflows', match=dict(name=sm))
            compress += ' {}'.format(os.path.join('app', 'workflows', workflow[0].file))
            workflows.append(workflow[0])
        return compress, workflows
