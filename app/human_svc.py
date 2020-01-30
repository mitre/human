import os
import sys
from aiohttp_jinja2 import template
from importlib import import_module

from app.utility.base_service import BaseService


class HumanService(BaseService):

    def __init__(self, services):
        self.services = services
        self.file_svc = services.get('file_svc')
        self.auth_svc = services.get('auth_svc')
        self.log = self.create_logger('human_svc')
        self.human_dir = os.path.relpath(os.path.join('plugins', 'human'))
        sys.path.insert(0, os.path.abspath(os.path.join(self.human_dir, 'pyhuman', 'pyhuman')))

    @template('human.html')
    async def splash(self, request):
        await self.auth_svc.check_permissions(request)
        modules = self._find_available_workflows()
        return dict(modules=modules)

    """ PRIVATE """

    def _find_available_workflows(self):
        extensions = []
        for root, dirs, files in os.walk(os.path.join(self.human_dir, 'pyhuman', 'pyhuman', 'app', 'workflows')):
            files = [f for f in files if not f[0] == '.' and not f[0] == "_"]
            dirs[:] = [d for d in dirs if not d[0] == '.' and not d[0] == "_"]
            for file in files:
                extensions.append(self._load_workflow_module(root, file))
        return extensions

    def _load_workflow_module(self, root, file):
        module = os.path.join(root, file.split('.')[0]).replace(os.path.sep, '.')
        try:
            return getattr(import_module(module), 'load')(driver=None)
        except Exception as e:
            self.log.error('Error loading extension=%s, %s' % (module, e))
