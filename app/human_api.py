import traceback

from aiohttp_jinja2 import template, web

from app.service.auth_svc import for_all_public_methods, check_authorization


@for_all_public_methods(check_authorization)
class HumanApi:

    def __init__(self, services, human_svc):
        self.auth_svc = services.get('auth_svc')
        self.data_svc = services.get('data_svc')
        self.human_svc = human_svc

    @template('human.html')
    async def splash(self, request):
        return dict()

    # --- Legacy routes (kept so the old cradle-builder keeps working until ---
    # --- it is fully retired). The new live UI uses /plugin/human/api/*. ---

    async def human_workflows(self, request):
        return web.json_response([w.display for w in await self.data_svc.locate('workflows')])

    async def human_humans(self, request):
        return web.json_response([h.display for h in await self.data_svc.locate('humans')])

    async def rest_api(self, request):
        try:
            data = dict(await request.json())
            index = data.pop('index')
            options = dict(
                POST=dict(
                    build_human=lambda d: self.human_svc.build_human(d),
                    load_human=lambda d: self.human_svc.load_humans(d),
                )
            )
            return web.json_response(await options[request.method][index](data))
        except Exception:
            traceback.print_exc()

    # --- Timestone live UI routes -------------------------------------------

    async def api_hosts(self, request):
        """Return the active range's host inventory.

        TODO: read this from the Range plugin's data store directly. The
        Range plugin exposes its inventory through routes registered at
        plugins/range/hook.py:124 (`POST /plugin/range/onprem/hosts`) and
        the cloud equivalents. Until we wire that proxy, we delegate to
        human_svc which currently returns a stub list. The Vue layer is
        already shape-tolerant so swapping the backing source later is a
        no-op for the frontend.
        """
        try:
            payload = await self.human_svc.list_range_hosts()
            return web.json_response(payload)
        except Exception:
            traceback.print_exc()
            return web.json_response({'hosts': [], 'profile': ''}, status=500)

    async def api_workflows(self, request):
        """Return workflows discoverable on the control_server side.

        TODO: forward to control_server.py's `_list` JSON-RPC method once
        that lands. For now we hand back a small hardcoded set so the UI
        has something to render.
        """
        try:
            payload = await self.human_svc.list_live_workflows()
            return web.json_response(payload)
        except Exception:
            traceback.print_exc()
            return web.json_response({'workflows': []}, status=500)

    async def api_run(self, request):
        """Dispatch a workflow / ad-hoc command to a host.

        Body: {host_id, workflow, args}

        For this branch we only echo. The actual transport-to-VM wiring
        (control_server.py + microVM channel) is a separate agent's task.
        """
        try:
            body = dict(await request.json())
        except Exception:
            return web.json_response({'status': 'error', 'stderr': 'invalid JSON body'}, status=400)
        try:
            payload = await self.human_svc.dispatch_run(body)
            return web.json_response(payload)
        except Exception as e:
            traceback.print_exc()
            return web.json_response({'status': 'error', 'stderr': str(e)}, status=500)
