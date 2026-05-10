from app.utility.base_world import BaseWorld
from plugins.human.app.human_api import HumanApi
from plugins.human.app.human_svc import HumanService

name = 'Human'
description = 'Emulate human behavior on a system'
address = '/plugin/human/gui'
access = BaseWorld.Access.APP

# NOTE (timestone-human-rewrite): Caldera's data_svc walks each plugin's
# `data/` directory automatically (see app/service/data_svc.py: it builds the
# plugin list and reads `plugin.data_dir`). That means the new
# `data/abilities/benign-human-activity/*.yml` and
# `data/adversaries/office-worker.yml` files are discovered without any
# explicit registration here - they show up as normal abilities / adversaries
# in the Caldera UI and can be dropped into operations alongside red-team
# adversary plans. The pyhuman/ python runtime is deprecated; see
# pyhuman/DEPRECATED.md.


async def enable(services):
    app = services.get('app_svc').application
    await services.get('data_svc').apply('humans')
    await services.get('data_svc').apply('workflows')
    human_svc = HumanService(services=services)
    await human_svc.load_available_workflows()
    human_api = HumanApi(services=services, human_svc=human_svc)

    # Splash + legacy cradle-builder routes (kept for backwards compat)
    app.router.add_route('GET', '/plugin/human/gui', human_api.splash)
    app.router.add_route('*',   '/plugin/human/api', human_api.rest_api)
    app.router.add_route('GET', '/plugin/human/workflows', human_api.human_workflows)
    app.router.add_route('GET', '/plugin/human/humans', human_api.human_humans)

    # Timestone live-UI routes
    app.router.add_route('GET',  '/plugin/human/api/hosts',     human_api.api_hosts)
    app.router.add_route('GET',  '/plugin/human/api/workflows', human_api.api_workflows)
    app.router.add_route('POST', '/plugin/human/api/run',       human_api.api_run)

    # Chord-palette dispatcher (overnight-stabilization 2026-05-10).
    # Body: {host_id, keys: [...], hold_ms?}. Backend for the
    # tier-1/tier-2/tier-3 chord buttons in human.vue.
    app.router.add_route('POST', '/plugin/human/api/chord',     human_api.api_chord)

    # Phase C: profile -> input-daemon dispatch. SSE-streamed; supports
    # both POST (JSON body) and GET (query string, friendly to the
    # browser's EventSource API which is GET-only).
    app.router.add_route('POST', '/plugin/human/api/run-profile', human_api.api_run_profile)
    app.router.add_route('GET',  '/plugin/human/api/run-profile', human_api.api_run_profile)

    # MP4 playback for runs the operator opted to record. Filename and
    # vm_name are validated inside the handler; we never trust the URL.
    app.router.add_route(
        'GET', '/plugin/human/api/recording/{vm_name}/{filename}',
        human_api.api_recording)
    # Catalog of recorded MP4s — feeds the "Recordings" collapsible
    # section in human.vue. JSON list, sorted newest-first.
    app.router.add_route(
        'GET', '/plugin/human/api/recordings',
        human_api.api_recordings_index)
    # Operator-initiated cleanup of a single MP4 (delete button next
    # to each entry in the Recordings dropdown).
    app.router.add_route(
        'DELETE', '/plugin/human/api/recording/{vm_name}/{filename}',
        human_api.api_recording_delete)

    app.router.add_static('/human', 'plugins/human/static', append_version=True)
