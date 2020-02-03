import inspect
import json
import os
import sys
from aiohttp import web
from importlib import import_module

from app.utility.base_service import BaseService


class HumanService(BaseService):

    def __init__(self, services):
        self.file_svc = services.get('file_svc')
        self.log = self.add_service('human_svc', self)
        self.human_dir = os.path.relpath(os.path.join('plugins', 'human'))
        self.pyhuman_path = os.path.abspath(os.path.join(self.human_dir, 'pyhuman'))
        sys.path.insert(0, self.pyhuman_path)
        self.modules = self._find_available_workflows()

    async def build_human(self, data):
        try:
            await self._select_modules_and_compress(modules=data.pop('tasks'), name=data.pop('name'), platform=data.pop('platform'))
            return json.dumps('Human successfully built')
        except Exception as e:
            self.log.error('Error building human. %s' % e)
            return json.dumps('Could not build human.')

    """ PRIVATE """

    def _find_available_workflows(self):
        extensions = []
        for root, dirs, files in os.walk(os.path.join(self.human_dir, 'pyhuman', 'app', 'workflows')):
            files = [f for f in files if not f[0] == '.' and not f[0] == '_']
            dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == '_']
            for file in files:
                extensions.append(self._load_workflow_module(root, file))
        return extensions

    def _load_workflow_module(self, root, file):
        module = os.path.join(root, file.split('.')[0]).replace(os.path.sep, '.')
        try:
            return getattr(import_module(module), 'load')(driver=None)
        except Exception as e:
            self.log.error('Error loading extension=%s, %s' % (module, e))

    async def _select_modules_and_compress(self, modules, name, platform):
        algo, args, ext = await self._get_compression_params(platform=platform)
        chdir = 'cd {} && '.format(self.pyhuman_path)
        compress = '{} {} {}.{} human.py data/* app/utility/* requirements.txt'.format(
            algo, args, os.path.abspath(os.path.join(self.human_dir, 'payloads', name)), ext)
        compress = await self._append_module_paths(modules, compress)
        self.log.debug('Compressing new human: %s' % name)
        os.system(chdir + compress)

    @staticmethod
    async def _get_compression_params(platform):
        if platform == 'windows-psh':
            return 'zip', '-qq', 'zip'
        return 'tar', 'zcf', 'tar.gz'

    async def _append_module_paths(self, modules, compress):
        for m in self.modules:
            for sm in modules:
                if m.name == sm:
                    compress += ' {}'.format(os.path.join('app', 'workflows', os.path.split(inspect.getmodule(m).__file__)[1]))
        return compress
