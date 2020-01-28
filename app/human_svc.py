from aiohttp_jinja2 import template


class HumanService:

    def __init__(self, services):
        self.file_svc = services.get('file_svc')
        self.auth_svc = services.get('auth_svc')

    @template('human.html')
    async def splash(self, request):
        await self.auth_svc.check_permissions(request)
        return
