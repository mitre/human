import traceback

from aiohttp_jinja2 import template


class HumanApi:

    def __init__(self, services, human_svc):
        self.auth_svc = services.get('auth_svc')
        self.human_svc = human_svc

    @template('human.html')
    async def splash(self, request):
        await self.auth_svc.check_permissions(request)
        return dict(modules=self.human_svc.modules)

    async def rest_api(self, request):
        try:
            data = dict(await request.json())
            index = data.pop('index')
            options = dict(
                POST=dict(
                    human=lambda d: self.human_svc.build_human(d),
                )
            )
            return await options[request.method][index](data)
        except Exception as e:
            traceback.print_exc()
