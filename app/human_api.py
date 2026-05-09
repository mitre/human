import asyncio
import glob
import json
import os
import socket
import traceback
from pathlib import Path

from aiohttp_jinja2 import template, web

from app.service.auth_svc import for_all_public_methods, check_authorization

# Canonical microVM runtime base. Mirrors the Range provider constant
# (plugins/range/app/cdktf/providers/onprem_microvm_provider.py:83
#  DEFAULT_CARRIER_RUNTIME_BASE = "/tmp/timestone-microvms").
# The Range provider's A3 agent writes meta.json under
# <BASE>/<host_id>-<suffix>/meta.json once the input/gpu daemons are up.
MICROVM_RUNTIME_BASE = os.environ.get(
    'TIMESTONE_MICROVM_RUNTIME_BASE', '/tmp/timestone-microvms'
)

# Canonical atomic-abilities directory for HID profiles. Hard-coded for
# now (Phase C); when more profile collections land we can lift this to
# a config knob.
HUMAN_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ATOMIC_ABILITIES_DIR = HUMAN_PLUGIN_ROOT / 'data' / 'abilities' \
    / 'benign-human-activity' / 'atomic'
ADVERSARIES_DIR = HUMAN_PLUGIN_ROOT / 'data' / 'adversaries'


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
        try:
            payload = await self.human_svc.list_range_hosts()
            return web.json_response(payload)
        except Exception:
            traceback.print_exc()
            return web.json_response({'hosts': [], 'profile': ''}, status=500)

    async def api_workflows(self, request):
        try:
            payload = await self.human_svc.list_live_workflows()
            return web.json_response(payload)
        except Exception:
            traceback.print_exc()
            return web.json_response({'workflows': []}, status=500)

    async def api_run(self, request):
        """Legacy ad-hoc / shell-cradle dispatch (echo for now)."""
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

    # --- Phase C: profile -> input-daemon dispatch --------------------------

    async def api_run_profile(self, request):
        """Materialize a profile and stream OperatorMessages to the host's
        input daemon over its operator UDS. Body or query string carries
        ``host_id`` and ``profile_id``; ``args`` (optional) is a JSON dict
        merged into every materialized message's call-args context.

        Profile lookup: ``profile_id`` matches either the YAML's ``id:``
        field (UUID) OR the filename stem (e.g. ``surf-the-web``). UUID
        is preferred — filenames may collide once multiple collections
        land — but the filename fallback keeps demo scripts terse.

        Streams Server-Sent Events: one ``data: {<msg>}`` per dispatched
        OperatorMessage and a final ``data: {"event":"done","count":N}``.
        Errors before the stream starts return JSON 4xx/5xx; errors mid-
        stream emit ``data: {"event":"error","error":"..."}`` and close.
        """
        # Accept GET (query string, easy for EventSource) or POST (body).
        if request.method == 'GET':
            host_id = request.query.get('host_id')
            profile_id = request.query.get('profile_id')
            args_raw = request.query.get('args')
            try:
                call_args = json.loads(args_raw) if args_raw else {}
            except Exception:
                return web.json_response(
                    {'status': 'error', 'error': 'args must be a JSON object'},
                    status=400)
        else:
            try:
                body = dict(await request.json())
            except Exception:
                return web.json_response(
                    {'status': 'error', 'error': 'invalid JSON body'},
                    status=400)
            host_id = body.get('host_id')
            profile_id = body.get('profile_id')
            call_args = body.get('args') or {}

        if not host_id or not profile_id:
            return web.json_response(
                {'status': 'error',
                 'error': 'host_id and profile_id are required'},
                status=400)

        # Resolve the operator socket path from meta.json BEFORE we open
        # the SSE stream — we want to surface "no GUI session" as a clean
        # 400, not as a half-open SSE connection.
        try:
            sock_path = self._resolve_operator_socket(host_id)
        except (FileNotFoundError, KeyError) as e:
            # KeyError repr-quotes the message; use .args[0] to get the
            # raw human-readable string we constructed.
            msg = e.args[0] if e.args else str(e)
            return web.json_response(
                {'status': 'error', 'error': msg}, status=400)

        # Resolve and load the profile.
        try:
            profile, abilities = self._load_profile_and_abilities(profile_id)
        except FileNotFoundError as e:
            return web.json_response(
                {'status': 'error', 'error': str(e)}, status=404)

        # Materialize the whole stream up-front. The materializer is
        # pure / fast (no I/O per message); doing it eagerly means we
        # can report the total step count to the UI before the first
        # SSE event lands.
        try:
            from plugins.human.pyhuman.profile_materializer import (
                materialize_profile,
            )
            messages = materialize_profile(profile, abilities)
        except Exception as e:
            traceback.print_exc()
            return web.json_response(
                {'status': 'error',
                 'error': f'materialize failed: {e}'},
                status=500)

        # If the caller passed an `args` envelope, treat it as a per-call
        # default override layered on top of the profile's static args.
        # We honor it by merging into every dict-typed message field that
        # the materializer left as a placeholder. (The current materializer
        # already substitutes args at materialize-time, so this is a no-op
        # for vanilla profile invocations; left as a hook for future
        # operator overrides.)
        _ = call_args  # reserved for future use

        # Open SSE response.
        resp = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            },
        )
        await resp.prepare(request)

        sock = None
        try:
            # Connect once, stream all messages, close.
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.setblocking(False)
                loop = asyncio.get_event_loop()
                await loop.sock_connect(sock, sock_path)
            except (OSError, FileNotFoundError) as e:
                err = (f'failed to connect to operator socket {sock_path}: '
                       f'{e}')
                await resp.write(
                    f'data: {json.dumps({"event": "error", "error": err})}\n\n'
                    .encode())
                return resp

            count = 0
            for msg in messages:
                line = json.dumps(msg) + '\n'
                try:
                    await loop.sock_sendall(sock, line.encode())
                except OSError as e:
                    err = f'operator socket send failed at step {count}: {e}'
                    await resp.write(
                        f'data: {json.dumps({"event": "error", "error": err})}\n\n'
                        .encode())
                    return resp
                # SSE event for the UI: include the index so it can drive
                # the step-preview highlight.
                event = dict(msg)
                event['_idx'] = count
                await resp.write(
                    f'data: {json.dumps(event)}\n\n'.encode())
                count += 1

            await resp.write(
                f'data: {json.dumps({"event": "done", "count": count})}\n\n'
                .encode())
            return resp
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _resolve_operator_socket(host_id: str) -> str:
        """Find <BASE>/<host_id>-*/meta.json and read input_daemon.operator_socket.

        Raises FileNotFoundError if no runtime dir exists for host_id.
        Raises KeyError if meta.json is missing the input_daemon block
        (i.e. the host has no GUI session — the GPU/input daemons were
        never launched, so there is nowhere to send OperatorMessages).
        """
        pattern = os.path.join(MICROVM_RUNTIME_BASE, f'{host_id}-*')
        matches = sorted(glob.glob(pattern))
        # Also accept an exact-match dir (no suffix) for hand-rolled tests.
        exact = os.path.join(MICROVM_RUNTIME_BASE, host_id)
        if os.path.isdir(exact):
            matches.append(exact)
        if not matches:
            raise FileNotFoundError(
                f'no microVM runtime dir for host_id={host_id!r} '
                f'under {MICROVM_RUNTIME_BASE}')
        meta_path = os.path.join(matches[0], 'meta.json')
        if not os.path.isfile(meta_path):
            raise FileNotFoundError(
                f'meta.json missing at {meta_path} (host not fully booted?)')
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception as e:
            raise FileNotFoundError(
                f'failed to parse {meta_path}: {e}') from e
        idaemon = meta.get('input_daemon') or {}
        sock_path = idaemon.get('operator_socket')
        if not sock_path:
            raise KeyError(
                f'host {host_id!r} has no GUI session: meta.json has no '
                f'input_daemon.operator_socket (the vhost-user-input '
                f'daemon was not started for this microVM)')
        return sock_path

    @staticmethod
    def _load_profile_and_abilities(profile_id: str):
        """Resolve profile_id (UUID or filename stem) into (profile_dict,
        abilities_index). Imports the materializer's helpers lazily so
        that hook.py-time import errors don't kill plugin enable."""
        from plugins.human.pyhuman.profile_materializer import (
            load_atomic_index, load_yaml_list,
        )

        # First try filename stem (cheap, common).
        stem_path = ADVERSARIES_DIR / f'{profile_id}.yml'
        candidate_paths = []
        if stem_path.is_file():
            candidate_paths.append(stem_path)
        else:
            # Fall back to scanning every adversary YAML for an `id:` match.
            candidate_paths = sorted(ADVERSARIES_DIR.glob('*.yml'))

        for path in candidate_paths:
            # Some adversary YAMLs (legacy format) are top-level dicts;
            # the materializer's loader rejects those. Tolerate them by
            # falling through — they can't match `profile_id` anyway.
            try:
                entries = load_yaml_list(path)
            except ValueError:
                continue
            if not entries:
                continue
            entry = entries[0]
            if path == stem_path or entry.get('id') == profile_id:
                abilities = load_atomic_index(ATOMIC_ABILITIES_DIR)
                return entry, abilities

        raise FileNotFoundError(
            f'profile_id {profile_id!r} not found (looked for filename '
            f'{profile_id}.yml and id-match under {ADVERSARIES_DIR})')
