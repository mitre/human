import asyncio
import glob
import json
import os
import re
import socket
import traceback
from datetime import datetime
from pathlib import Path

from aiohttp_jinja2 import template, web

from app.service.auth_svc import for_all_public_methods, check_authorization

# Cloud carrier support: when meta.json declares `transport.kind ==
# "ssh-tunnel"` we open an `ssh -L` to forward the remote operator UDS
# back to a local path. SSHTunnelManager is shared across cloud
# providers (AWS bare-metal + Azure nested). One instance per Human
# plugin process is fine.
try:
    from plugins.human.app.ssh_tunnel import SSHTunnelManager
    _SSH_TUNNEL_MGR = SSHTunnelManager()
except Exception:  # noqa: BLE001 — module-load defensive
    _SSH_TUNNEL_MGR = None

# Where the SSE handler writes recorded MP4s. One subdir per VM keeps
# operator-side cleanup straightforward (and matches the convention
# used elsewhere in the runtime tree).
RECORDINGS_BASE = os.environ.get(
    'TIMESTONE_RECORDINGS_BASE', '/var/lib/timestone/recordings'
)

# Filenames the MP4-serving endpoint will accept. Defensive: enforce
# .mp4 suffix and reject anything containing a path separator or '..'
# component before we touch the filesystem.
_SAFE_MP4_FILENAME_RE = re.compile(r'^[A-Za-z0-9._-]+\.mp4$')


def _parse_bool(value) -> bool:
    """Coerce query-string / JSON ``record`` field to bool.

    Browsers stringify ``record=true`` from the EventSource URL, so we
    need to treat ``"true"`` / ``"1"`` / ``"yes"`` as truthy. Anything
    else (including missing / None / empty string) is False so the
    feature is opt-in.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return False

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

        If the caller passes ``record=true`` (query string or body
        field), an :class:`RfbRecorder` is spawned for the duration of
        the profile run; on completion the SSE stream emits a
        ``recording_started`` event up front and a ``recording_ready``
        event with the playback URL once the MP4 is finalized.
        """
        # Accept GET (query string, easy for EventSource) or POST (body).
        os_override = None
        if request.method == 'GET':
            host_id = request.query.get('host_id')
            profile_id = request.query.get('profile_id')
            args_raw = request.query.get('args')
            record = _parse_bool(request.query.get('record'))
            os_override = request.query.get('os')
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
            record = _parse_bool(body.get('record'))
            os_override = body.get('os')

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

        # Resolve target OS for platform-aware ability dispatch. The
        # new HID atomic abilities (commit 8129246) use
        # `platforms.{windows,linux,darwin}.steps` and the materializer
        # raises if no OS is provided. Order of precedence:
        #   1. explicit query/body `os` (lets a tester force a branch)
        #   2. meta.json `os` field for the target host
        # Fall back to '' which preserves the legacy hid.steps path for
        # any older single-platform abilities still in flight.
        try:
            target_os = self._resolve_target_os(host_id, os_override)
        except Exception:  # noqa: BLE001 — best-effort, fall through to ''
            target_os = ''

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
            messages = materialize_profile(
                profile, abilities, target_os=target_os,
            )
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

        # --- optional framebuffer recording ---------------------------
        # When `record=true` we resolve the GPU daemon's RFB endpoint
        # (local TCP for on-prem, ssh-tunnelled local TCP for cloud
        # carriers), spawn a recorder for the duration of the profile
        # run, and emit `recording_started` / `recording_ready` SSE
        # events the UI can splice into its inline player.
        recorder = None
        recorder_mp4_path = None
        recorder_url = None
        if record:
            try:
                vnc_host, vnc_port = self._resolve_vnc_endpoint(host_id)
                recordings_dir = Path(RECORDINGS_BASE) / host_id
                recordings_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d-%H%M%S')
                recorder_mp4_path = recordings_dir / f'{ts}-{profile_id}.mp4'
                recorder_url = (
                    f'/plugin/human/api/recording/{host_id}/'
                    f'{recorder_mp4_path.name}'
                )
                # Lazy import: lets the rest of the plugin still load
                # cleanly if the recorder module is missing on a host
                # that won't need recording.
                from plugins.human.pyhuman.recorder import RfbRecorder
                recorder = RfbRecorder(
                    host=vnc_host, port=vnc_port,
                    output_mp4=recorder_mp4_path, fps=10,
                )
                recorder.start()
                await resp.write(
                    ('event: log\ndata: ' + json.dumps({
                        'event': 'recording_started',
                        'path': str(recorder_mp4_path),
                    }) + '\n\n').encode()
                )
            except Exception as e:  # noqa: BLE001 — recording is best-effort
                traceback.print_exc()
                await resp.write(
                    ('event: log\ndata: ' + json.dumps({
                        'event': 'recording_error',
                        'error': f'failed to start recorder: {e}',
                    }) + '\n\n').encode()
                )
                recorder = None  # don't try to .stop() something that never started

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
            # Stop the recorder regardless of how the profile run
            # exited — we always want a usable MP4 (or a clean stop)
            # even if the operator-socket send blew up mid-stream.
            if recorder is not None:
                try:
                    await resp.write(
                        ('event: log\ndata: ' + json.dumps(
                            {'event': 'finalizing_recording'}) + '\n\n'
                         ).encode())
                except Exception:
                    pass
                try:
                    final_path = recorder.stop()
                    final_path = Path(final_path) if final_path else recorder_mp4_path
                    payload = {
                        'event': 'recording_ready',
                        'url': recorder_url,
                        'path': str(final_path),
                    }
                    try:
                        await resp.write(
                            ('event: log\ndata: ' + json.dumps(payload)
                             + '\n\n').encode())
                    except Exception:
                        pass
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    try:
                        await resp.write(
                            ('event: log\ndata: ' + json.dumps({
                                'event': 'recording_error',
                                'error': f'recorder.stop failed: {e}',
                            }) + '\n\n').encode())
                    except Exception:
                        pass

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _resolve_target_os(host_id: str, override: str | None = None) -> str:
        """Return the normalized target OS for ``host_id``.

        Order of precedence:
          1. ``override`` if non-empty (caller-supplied query/body field).
          2. ``os`` (or ``platform``) field in the host's meta.json.
          3. '' (caller falls back to legacy hid.steps).

        Reuses the same ``<BASE>/<host_id>-*/meta.json`` discovery as
        ``_resolve_operator_socket`` so the two stay in lockstep. Lazy-
        imports the materializer's ``normalize_os`` helper to keep the
        synonym map (mac/macos/osx -> darwin, win -> windows) in one
        place.
        """
        from plugins.human.pyhuman.profile_materializer import normalize_os
        if override:
            return normalize_os(override)
        pattern = os.path.join(MICROVM_RUNTIME_BASE, f'{host_id}-*')
        matches = sorted(glob.glob(pattern))
        exact = os.path.join(MICROVM_RUNTIME_BASE, host_id)
        if os.path.isdir(exact):
            matches.append(exact)
        if not matches:
            return ''
        meta_path = os.path.join(matches[0], 'meta.json')
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            return ''
        return normalize_os(meta.get('os') or meta.get('platform') or '')

    @staticmethod
    def _resolve_operator_socket(host_id: str) -> str:
        """Find <BASE>/<host_id>-*/meta.json and read input_daemon.operator_socket.

        Raises FileNotFoundError if no runtime dir exists for host_id.
        Raises KeyError if meta.json is missing the input_daemon block
        (i.e. the host has no GUI session — the GPU/input daemons were
        never launched, so there is nowhere to send OperatorMessages).

        Cloud-carrier support: if meta.json carries a `transport` block
        with `kind == "ssh-tunnel"`, we open (or reuse) an `ssh -L`
        forwarding the remote UDS to a local path and return that
        local path. The downstream caller treats it as a normal AF_UNIX
        path — no other code change needed.
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

        # Cloud-carrier dispatch: a `ssh-tunnel://<vm_id>/...` sentinel
        # path means the operator socket lives in a cloud VM and must
        # be reached via local-forward.
        if isinstance(sock_path, str) and sock_path.startswith('ssh-tunnel://'):
            transport = meta.get('transport') or {}
            if transport.get('kind') != 'ssh-tunnel':
                raise KeyError(
                    f'host {host_id!r} operator_socket has ssh-tunnel:// '
                    f'sentinel but meta.json transport block is missing '
                    f'or wrong kind: {transport!r}')
            if _SSH_TUNNEL_MGR is None:
                raise RuntimeError(
                    'SSHTunnelManager not available; cannot resolve '
                    'cloud-carrier operator socket. Check '
                    'plugins/human/app/ssh_tunnel.py imports.')
            handle = _SSH_TUNNEL_MGR.get(host_id)
            if handle is None:
                handle = _SSH_TUNNEL_MGR.open_tunnel(transport, host_id)
            return handle.local_path

        return sock_path

    @staticmethod
    def _resolve_vnc_endpoint(host_id: str):
        """Return ``(host, port)`` for the GPU daemon's RFB endpoint.

        For local UDS-transport carriers, the GPU daemon listens on
        ``127.0.0.1:<vnc_port>`` directly — we just return that.

        For ssh-tunnel cloud carriers, we (re)use the SSHTunnelManager
        to publish a local TCP port forwarded to the remote VNC port,
        and return that local port. Reusing one tunnel per VM means
        repeated runs share the same forward.

        Raises FileNotFoundError / KeyError on the same conditions as
        ``_resolve_operator_socket``.
        """
        pattern = os.path.join(MICROVM_RUNTIME_BASE, f'{host_id}-*')
        matches = sorted(glob.glob(pattern))
        exact = os.path.join(MICROVM_RUNTIME_BASE, host_id)
        if os.path.isdir(exact):
            matches.append(exact)
        if not matches:
            raise FileNotFoundError(
                f'no microVM runtime dir for host_id={host_id!r} '
                f'under {MICROVM_RUNTIME_BASE}')
        meta_path = os.path.join(matches[0], 'meta.json')
        with open(meta_path) as f:
            meta = json.load(f)
        gpu = meta.get('gpu_daemon') or {}
        vnc_port = gpu.get('vnc_port')
        if not vnc_port:
            raise KeyError(
                f'host {host_id!r} has no GUI session: meta.json has no '
                f'gpu_daemon.vnc_port (recording requires the framebuffer '
                f'daemon to be running)')

        transport = meta.get('transport') or {}
        if transport.get('kind') == 'ssh-tunnel':
            if _SSH_TUNNEL_MGR is None:
                raise RuntimeError(
                    'SSHTunnelManager not available; cannot resolve '
                    'cloud-carrier VNC endpoint.')
            # Reuse an existing tunnel if it's already a TCP one for
            # this VM; otherwise open a fresh TCP -L for the VNC port.
            handle = _SSH_TUNNEL_MGR.get(f'{host_id}-vnc')
            if handle is None:
                tcp_transport = dict(transport)
                tcp_transport['remote_vnc_port'] = vnc_port
                # Drop the UDS field so open_tunnel picks the TCP path.
                tcp_transport.pop('remote_input_op_socket', None)
                handle = _SSH_TUNNEL_MGR.open_tunnel(
                    tcp_transport, f'{host_id}-vnc')
            return ('127.0.0.1', int(handle.local_port))

        return ('127.0.0.1', int(vnc_port))

    async def api_recording(self, request):
        """Serve an MP4 recording at
        ``/plugin/human/api/recording/<vm_name>/<filename>``.

        Defensive: rejects path traversal (``..`` / separators in
        either segment) and non-mp4 filenames before touching the
        filesystem; returns 404 when the file does not exist.
        """
        vm_name = request.match_info.get('vm_name', '')
        filename = request.match_info.get('filename', '')

        # Reject path-traversal characters in vm_name. The Range
        # provider's vm_names are simple identifiers (no slashes).
        if (not vm_name
                or '/' in vm_name
                or '\\' in vm_name
                or vm_name in ('.', '..')
                or vm_name.startswith('.')):
            return web.json_response(
                {'status': 'error', 'error': 'invalid vm_name'},
                status=400)

        if not _SAFE_MP4_FILENAME_RE.match(filename):
            return web.json_response(
                {'status': 'error',
                 'error': 'filename must be a simple .mp4 name'},
                status=400)

        target = (Path(RECORDINGS_BASE) / vm_name / filename).resolve()
        base = Path(RECORDINGS_BASE).resolve()
        # Resolve-then-prefix-check defends against symlink escapes.
        try:
            target.relative_to(base)
        except ValueError:
            return web.json_response(
                {'status': 'error',
                 'error': 'recording path escapes recordings root'},
                status=400)

        if not target.is_file():
            return web.json_response(
                {'status': 'error',
                 'error': f'recording {filename!r} not found for {vm_name!r}'},
                status=404)

        # FileResponse streams with sendfile() where the kernel + aiohttp
        # support it; explicit Content-Type ensures the inline <video>
        # element treats it as MP4.
        return web.FileResponse(
            target,
            headers={'Content-Type': 'video/mp4'},
        )

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
