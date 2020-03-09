from app.utility.base_world import BaseWorld
from plugins.human.app.human_gui_api import HumanApi
from plugins.human.app.human_svc import HumanService

name = 'Human'
description = 'Emulate human behavior on a system'
address = '/plugin/human/gui'
access = BaseWorld.Access.BLUE


async def enable(services):
    app = services.get('app_svc').application
    await services.get('data_svc').apply('humans')
    await services.get('data_svc').apply('workflows')
    human_svc = HumanService(services=services)
    human_api = HumanApi(services=services, human_svc=human_svc)
    app.router.add_route('GET', '/plugin/human/gui', human_api.splash)
    app.router.add_route('*', '/plugin/human/api', human_api.rest_api)
    app.router.add_static('/human', 'plugins/human/static', append_version=True)
    await human_svc.install_pyhuman()
