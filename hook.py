from plugins.human.app.human_svc import HumanService

name = 'Human'
description = 'Emulate human behavior on a system'
address = '/plugin/human/gui'


async def enable(services):
    app = services.get('app_svc').application
    human_svc = HumanService(services=services)
    app.router.add_route('GET', '/plugin/human/gui', human_svc.splash)
    app.router.add_static('/human', 'plugins/human/static', append_version=True)