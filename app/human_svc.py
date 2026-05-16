import ast
import glob
import json
import logging
import os
import sys
import tarfile
import time
import zipfile

from importlib import import_module

from app.utility.base_service import BaseService
from plugins.human.app.c_human import Human
from plugins.human.app.c_workflow import Workflow

# Import the canonical runtime base from human_api so the dropdown
# scans the same directory the SSE handler / _resolve_operator_socket
# read from. Tests patch human_api.MICROVM_RUNTIME_BASE; we re-read
# it dynamically below to honor that.
from plugins.human.app import human_api as _human_api

_log = logging.getLogger(__name__)


# Per-process TTL caches for two hot paths exercised by every Vue
# refresh of the Human picker:
#
#   * _scan_microvm_meta:  glob + json.load on every GET /api/hosts.
#   * _discover_profiles:  YAML parse on every GET /api/workflows.
#
# Both are read-only inventory views: a 3-second staleness window is
# imperceptible to an operator but eliminates ~99% of the disk work
# on rapid dropdown refreshes. mtime checks guarantee a write (Range
# provider writing a new meta.json; operator dropping a new profile
# YAML) is seen on the very next request.
#
# Module-level (not instance-level) so tests can clear via
# `_scan_meta_cache_clear()` and so the cache survives a single
# instance throwaway in CI. Threading is fine: aiohttp drives all
# call sites from the same event loop, no concurrent mutation.
_META_CACHE_TTL_S = 3.0
_PROFILE_CACHE_TTL_S = 3.0
# {(base,): (timestamp, fingerprint, result_list)}
_META_CACHE: dict = {}
# {(adv_dir,): (timestamp, fingerprint, result_list)}
_PROFILE_CACHE: dict = {}


def _meta_cache_clear():
    """Test hook: drop the _scan_microvm_meta TTL cache."""
    _META_CACHE.clear()


def _profile_cache_clear():
    """Test hook: drop the _discover_profiles TTL cache."""
    _PROFILE_CACHE.clear()


class HumanService(BaseService):

    def __init__(self, services):
        self.services = services
        self.file_svc = services.get('file_svc')
        self.data_svc = services.get('data_svc')
        self.log = self.add_service('human_svc', self)
        self.human_dir = os.path.relpath(os.path.join('plugins', 'human'))
        self.pyhuman_path = os.path.join(self.human_dir, 'pyhuman')
        sys.path.insert(0, self.pyhuman_path)  # needed to load relative module paths in pyhuman for workflows

    async def load_available_workflows(self):
        # The pyhuman/* python workflows are the deprecated pre-HID runtime
        # (see pyhuman/DEPRECATED.md). They drag in selenium / pyautogui /
        # PERSONAS etc., which are not installed in the current dev env;
        # importing them at startup spams ~12 'No module named X' errors
        # per Caldera restart and adds ~5s of import-time overhead.
        #
        # Default behavior: skip them. Operators who still need the legacy
        # cradle-builder workflows can opt in with HUMAN_LEGACY_PYHUMAN=1.
        if os.environ.get('HUMAN_LEGACY_PYHUMAN', '').lower() not in ('1', 'true', 'yes'):
            self.log.debug(
                'Skipping legacy pyhuman workflow discovery '
                '(set HUMAN_LEGACY_PYHUMAN=1 to re-enable)'
            )
            return

        root = os.path.join(self.pyhuman_path, 'app', 'workflows')
        for f in os.listdir(root):
            if os.path.isfile(os.path.join(root, f)) and not f[0] == '_':
                await self._load_workflow_module(root, f)

    async def list_legacy_workflows(self):
        """Return pyhuman workflow metadata for the legacy builder UI.

        ``load_available_workflows`` intentionally skips importing the
        legacy modules unless HUMAN_LEGACY_PYHUMAN=1 because those imports
        pull optional browser automation dependencies into server startup.
        The builder still needs a workflow picker, so this path reads the
        module-level WORKFLOW_* constants with ``ast`` instead of importing.
        """
        return {'workflows': self._legacy_workflow_catalog()}

    async def build_human(self, data):
        """Build a legacy pyhuman archive and register it in data_svc."""
        try:
            payload = dict(data or {})
            _, name = os.path.split(str(payload.pop('name', '')).strip())
            if not name:
                raise ValueError('name is required')

            platform = payload.pop('platform', '')
            if platform not in ('darwin', 'linux', 'windows-psh'):
                raise ValueError('platform must be darwin, linux, or windows-psh')

            tasks = payload.pop('tasks', [])
            if isinstance(tasks, str):
                tasks = [tasks]
            if not isinstance(tasks, list) or not tasks:
                raise ValueError('at least one workflow is required')

            extra = payload.pop('extra', [])
            if isinstance(extra, str):
                extra = [extra]
            if not isinstance(extra, list):
                extra = []

            task_interval = int(payload.pop('task_interval', 10) or 10)
            task_count = int(payload.pop('task_count', 5) or 5)
            task_cluster_interval = int(
                payload.pop('task_cluster_interval', 500) or 500)

            await self._select_modules_and_compress(
                modules=tasks,
                name=name,
                platform=platform,
                task_interval=task_interval,
                tasks_per_cluster=task_count,
                task_cluster_interval=task_cluster_interval,
                extra=extra,
            )
            humans = await self.data_svc.locate(
                'humans', match=dict(name=name))
            if humans:
                return humans[0].display
            return Human(
                name=name,
                task_interval=task_interval,
                task_cluster_interval=task_cluster_interval,
                tasks_per_cluster=task_count,
                platform=platform,
                extra=extra,
                workflows=[],
            ).display
        except Exception as e:
            self.log.error('Error building legacy human. %s' % e)
            raise

    async def load_humans(self, data=None):
        data = data or {}
        return [
            h.display
            for h in await self.data_svc.locate(
                'humans', match=dict(name=data.get('name')))
        ]

    # ------------------------------------------------------------------ #
    # Timestone live-UI helpers                                           #
    # ------------------------------------------------------------------ #

    async def list_range_hosts(self, profile=None):
        """Return the active range's host inventory.

        Resolution order:
          1. If the Range plugin ever publishes hosts through
             ``data_svc.locate('range_instances')`` we prefer that
             (forward-compat — Range doesn't currently publish here).
          2. Otherwise, scan ``<MICROVM_RUNTIME_BASE>/*/meta.json`` —
             this is what the Range provider's A3 microvm-launch agent
             writes once a microVM is up. Mirrors the lookup pattern
             in ``HumanApi._resolve_operator_socket``.
          3. If no meta.json files exist (fresh dev box), fall back to
             a deterministic stub so the Vue layer still renders.

        When ``profile`` is supplied AND resolves to a known Range
        profile, the scan is narrowed to that profile's
        ``carrier_runtime_base`` and the VM names are intersected with
        the profile's deployed_config catalog. Unknown / empty profile
        names fall through to the legacy union behavior so callers that
        don't pass a profile (tests, MCP probes, the initial fetch
        before the dropdown is wired) keep working.
        """
        # Resolve profile-scoped runtime base + catalog (if any) up
        # front so all three resolution branches honor the filter.
        scoped = self._resolve_profile_scope(profile) if profile else None

        # 1. Forward-compat: prefer the Range plugin's in-process data
        # store if it ever publishes there.
        try:
            range_hosts = await self.data_svc.locate('range_instances')
            if range_hosts:
                normalized = [self._normalize_range_host(h) for h in range_hosts]
                if scoped and scoped.get('catalog') is not None:
                    catalog = scoped['catalog']
                    normalized = [
                        h for h in normalized
                        if h.get('name') in catalog or h.get('id') in catalog
                    ]
                label = scoped['profile'] if scoped else '(active range)'
                return {'profile': label, 'hosts': normalized}
        except Exception:
            # data_svc.locate raises for unknown collections; that's fine,
            # fall through to the meta.json scan.
            pass

        # 2. Scan meta.json files written by the Range provider. If a
        # profile was resolved, restrict the scan to its private base.
        if scoped:
            bases = [scoped['runtime_base']]
        else:
            bases = self._runtime_bases()
        hosts = self._scan_microvm_meta(bases)

        # Intersect with the profile's deployed_config VM catalog so a
        # stale meta.json from a previous profile that happened to share
        # the same carrier_runtime_base (e.g. operator changed the base
        # mid-flight) is hidden. Only applied when we actually have a
        # catalog to match against.
        if scoped and scoped.get('catalog') is not None:
            catalog = scoped['catalog']
            hosts = [
                h for h in hosts
                if h.get('name') in catalog or h.get('id') in catalog
            ]

        if hosts:
            label = scoped['profile'] if scoped else '(active range)'
            return {'profile': label, 'hosts': hosts}

        # 3. No microVMs running — return an empty list so the UI shows
        # a clean empty state instead of fake hosts. Earlier behavior
        # returned two stub entries (microvm-1 10.0.0.11, microvm-2
        # 10.0.0.12) to populate the dropdown on a fresh dev box, but
        # that masked real "no range deployed yet" status and confused
        # users who tried to click the stubs.
        label = scoped['profile'] if scoped else '(no range)'
        return {'profile': label, 'hosts': []}

    def _resolve_profile_scope(self, profile_name):
        """Look up a Range profile and return its scan scope.

        Returns ``None`` when the name is empty / unknown / can't be
        resolved (so the caller falls back to the union behavior).
        Otherwise returns a dict::

            {
              'profile':       <profile name>,
              'runtime_base':  <carrier_runtime_base path>,
              'catalog':       set[str] of VM names from
                               deployed_config.json, or None if
                               no catalog is available yet (in which
                               case the runtime_base filter alone
                               is applied).
            }

        Looks up ``services['onprem_svc']`` first, then ``range_svc``
        (the same fallback order ``_configured_runtime_bases`` uses).
        The deployed_config.json catalog is the source of truth for
        which VMs belong to a profile — we read it instead of the
        profile YAML because YAML profiles don't carry an inline
        image list in this codebase; VMs are added at deploy time and
        persisted under cdktf.out/stacks/<stack_id>/deployed_config.json.
        """
        name = (profile_name or '').strip()
        if not name:
            return None

        services = getattr(self, 'services', None)
        if services is None or not hasattr(services, 'get'):
            return None

        svc = None
        for key in ('onprem_svc', 'range_svc'):
            try:
                candidate = services.get(key)
            except Exception:
                continue
            if candidate is not None:
                svc = candidate
                break
        if svc is None:
            return None

        # Find the profile dict + its provider (carrier_runtime_base
        # is set on the provider instance, not on the YAML dict that
        # may have omitted it and inherited DEFAULT_CARRIER_RUNTIME_BASE).
        profiles = getattr(svc, 'profiles', None) or []
        profile_dict = None
        for p in profiles:
            if isinstance(p, dict) and p.get('profile') == name:
                profile_dict = p
                break
        if profile_dict is None:
            return None

        providers = getattr(svc, 'providers', None) or {}
        provider = providers.get(name)
        runtime_base = getattr(provider, 'carrier_runtime_base', None)
        if not runtime_base:
            # Fall back to the YAML value (may still be missing —
            # the default base is shared with every other profile
            # that didn't override it, so a filter would be useless).
            runtime_base = profile_dict.get('carrier_runtime_base')
        if not runtime_base:
            return None

        # Catalog = VM names from deployed_config.json for this
        # profile's stack. Best-effort: if the helper isn't available
        # or the file is missing (profile defined but never deployed)
        # we return catalog=None so the caller skips the name-match
        # filter and relies on the runtime_base scan alone.
        catalog = None
        try:
            load = getattr(svc, '_load_deployed_config', None)
            provider_type = profile_dict.get('provider')
            if load and provider_type:
                # sanitize_stack_id lives in plugins.range.app.cdktf.
                from plugins.range.app.cdktf.cdktf_utilities import (
                    sanitize_stack_id,
                )
                stack_id = sanitize_stack_id(provider_type, name)
                deployed = load(stack_id) or {}
                vms = deployed.get('vms') or {}
                if vms:
                    catalog = set(vms.keys())
        except Exception as e:
            _log.debug(
                'profile catalog lookup failed for %s: %s', name, e)
            catalog = None

        return {
            'profile': name,
            'runtime_base': runtime_base,
            'catalog': catalog,
        }

    def _runtime_bases(self):
        services = getattr(self, 'services', None)
        return _human_api._configured_runtime_bases(services)

    @staticmethod
    def _scan_microvm_meta(runtime_bases=None):
        """Glob ``<BASE>/*/meta.json`` and return UI-shaped host dicts.

        Re-reads ``human_api.MICROVM_RUNTIME_BASE`` on every call so
        tests that patch the module global pick up the new value, and
        so an env-var change at runtime is honored.

        Malformed / unreadable meta.json files are skipped + logged
        (one bad file should not blank the dropdown).

        TTL cache: results are reused for up to ``_META_CACHE_TTL_S``
        seconds, but only when the (sorted-path, mtime) fingerprint
        across every matched meta.json is unchanged. A new microVM
        being launched (new file) or an existing meta.json being
        rewritten (mtime bump) invalidates immediately.
        """
        bases = _human_api._configured_runtime_bases(runtime_bases=runtime_bases)
        paths = sorted({
            p
            for base in bases
            for p in glob.glob(os.path.join(base, '*', 'meta.json'))
        })

        # Build a fingerprint = tuple of (path, mtime_ns). Stat is
        # much cheaper than open+json.load. If any file vanishes
        # between glob and stat we treat it as a cache miss.
        try:
            fingerprint = tuple((p, os.stat(p).st_mtime_ns) for p in paths)
        except OSError:
            fingerprint = None

        now = time.monotonic()
        cache_key = tuple(bases)
        cached = _META_CACHE.get(cache_key)
        if (cached is not None
                and fingerprint is not None
                and cached[1] == fingerprint
                and (now - cached[0]) < _META_CACHE_TTL_S):
            # Return a shallow copy so callers can't mutate the cache.
            return [dict(h) for h in cached[2]]

        out = []
        for meta_path in paths:
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except Exception as e:
                _log.warning('skipping unreadable meta.json at %s: %s',
                             meta_path, e)
                continue
            vm_name = meta.get('vm_name')
            ip = meta.get('ip') or ''
            if not vm_name:
                _log.warning('skipping meta.json at %s: missing vm_name',
                             meta_path)
                continue
            ch_pid = meta.get('ch_pid') or meta.get('pid')
            if ch_pid and not HumanService._pid_alive(ch_pid):
                _log.debug('skipping stale microVM meta at %s: ch_pid=%s',
                           meta_path, ch_pid)
                continue
            # NOTE: empty `ip` is acceptable. CLI VMs talk to the host via
            # serial.sock (a host-local AF_UNIX socket exposed by the
            # range_console_proxy); guest-side DHCP failure should NOT
            # hide the host from the operator's inventory — they still
            # need the console viewer to debug why DHCP failed.
            # MicroVMs deliberately do not advertise guest-network
            # viewer/bootstrap transports. The AE-clean path is host-side only:
            # Human drives input through operator sockets and control
            # uses the Range special sockets (Linux vsock, Windows SAC).
            # GUI framebuffer export is a host-side UDS exposed through
            # Range's special frame WebSocket, not VNC.
            gpu_daemon = meta.get('gpu_daemon') or {}
            has_gpu = isinstance(gpu_daemon, dict) and bool(
                gpu_daemon.get('socket'))
            gpu_pid = (
                gpu_daemon.get('pid')
                if isinstance(gpu_daemon, dict) else None
            )
            if has_gpu and gpu_pid and not HumanService._pid_alive(gpu_pid):
                _log.debug('ignoring stale GPU daemon for %s: pid=%s',
                           vm_name, gpu_pid)
                has_gpu = False
            frame_sock = (
                gpu_daemon.get('frame_socket')
                if has_gpu and isinstance(gpu_daemon, dict) else None
            )
            frame_ws = (
                f'/plugin/range/api/frame/{vm_name}/ws'
                if frame_sock else None
            )
            # Resolve session_type with this precedence:
            #   1. meta.json's persisted `session_type` (newest writes;
            #      onprem_microvm_provider now stores the catalog value
            #      for both Linux + Windows spawns).
            #   2. presence of a future frame_ws stream → 'gui'.
            #   3. presence of a vsock.sock / serial.sock sibling in the
            #      run_dir → 'cli'. This covers microVMs spawned BEFORE
            #      meta.json carried session_type (the already-running
            #      linux-nogui at install time) so the operator doesn't
            #      have to redeploy to get the xterm.js viewer.
            #   4. fallback 'shell' (= unknown stub).
            persisted_st = (meta.get('session_type') or '').lower()
            if persisted_st:
                session_type = persisted_st
                if session_type == 'gui' and not frame_ws:
                    session_type = 'shell'
            elif has_gpu and frame_ws:
                session_type = 'gui'
            else:
                # CLI fallback only when there is a serial.sock — vsock.sock
                # is the timestone-shim RPC channel and is NOT a tty, so it
                # must not light up an xterm.js viewer. Without this gate
                # legacy/no-session_type images (e.g. timestone-linux-victim)
                # render as 'cli' but the console proxy 404s, leaving the
                # operator with a permanently-spinning viewer.
                run_dir = os.path.dirname(meta_path)
                if os.path.exists(os.path.join(run_dir, 'serial.sock')):
                    session_type = 'cli'
                else:
                    session_type = 'shell'
            # Console WS URL — populated for cli hosts so the frontend
            # can mount xterm.js without re-deriving the route. Served
            # by plugins/range/app/range_console_proxy.py.
            console_ws = (
                f'/plugin/range/onprem/console/{vm_name}'
                if session_type == 'cli' else None)
            out.append({
                'id':           vm_name,
                'name':         vm_name,
                'ip':           ip,
                'os':           meta.get('os', 'unknown'),
                'status':       'running',
                'provider':     'microvm',
                'frame_ws':     frame_ws,
                'special_socket': (
                    frame_sock or (
                        gpu_daemon.get('socket')
                        if has_gpu and isinstance(gpu_daemon, dict) else None
                    )
                ),
                'console_ws':   console_ws,
                # Surface session_type so the frontend (LiveEndpointViewer)
                # can pick between a special-socket framebuffer (gui)
                # and xterm.js (cli) without an extra round-trip. 'shell'
                # is the legacy stub label.
                'session_type': session_type,
            })

        if fingerprint is not None:
            _META_CACHE[cache_key] = (now, fingerprint, out)
        return out

    @staticmethod
    def _pid_alive(pid):
        try:
            return os.path.exists(f'/proc/{int(pid)}')
        except Exception:
            return False

    async def list_live_workflows(self):
        """Return workflows that the control_server can execute.

        Mixes two sources:
          1. HID profile adversaries under data/adversaries/*.yml — these
             carry a `profile_id` so the frontend dispatches them through
             /plugin/human/api/run-profile (the input-daemon SSE pipe).
          2. Anything Caldera's data_svc has loaded as a `workflows`
             collection from the legacy pyhuman modules (gracefully empty
             when HUMAN_LEGACY_PYHUMAN is unset — see
             ``load_available_workflows``).
        Profiles surface first because they're the new canonical path.

        The three pre-HID stub workflow entries (idle_browse / office_open
        / shell_noop) that used to be appended here were dropdown noise —
        they had ``is_hid: False`` and dispatched nowhere — so they were
        removed in chore/human-efficiency-cleanup.
        """
        live = []

        # 1) HID profiles: every YAML in data/adversaries/ becomes an
        # ability the operator can pick. The materializer is the source
        # of truth for the step list, but we expose the raw `hid` block
        # too so human.vue's step preview can render without round-tripping.
        live.extend(self._discover_profiles())

        # 2) Legacy pyhuman workflows loaded into data_svc (opt-in via
        # HUMAN_LEGACY_PYHUMAN=1). Empty in the default config.
        try:
            legacy = await self.data_svc.locate('workflows')
            for w in legacy:
                disp = getattr(w, 'display', None) or {}
                name = disp.get('name') or getattr(w, 'name', None)
                if not name:
                    continue
                live.append({
                    'id': name,
                    'name': name,
                    'description': disp.get('description')
                                   or getattr(w, 'description', '') or '',
                    'is_hid': False,
                })
        except Exception:
            pass
        return {'workflows': live}

    def _discover_profiles(self):
        """Walk data/adversaries/*.yml and return picker-shaped dicts.

        The dict carries `profile_id` so the frontend knows to take the
        SSE/run-profile path; legacy entries lack that key and fall
        through to the cradle-builder run path.

        We also classify each profile as HID vs legacy shell-cradle so
        the UI can hide the "Legacy shell-cradle ability (no HID step-
        list)" warning for profiles that ARE driven through the input
        daemon. Detection rules (any one is sufficient):

          * top-level ``steps:`` list (the "new" profile shape — what
            data/adversaries/surf-the-web.yml uses).
          * top-level ``platforms.<os>.steps`` (platform-aware shape).
          * legacy ``hid.steps`` list (the very first profile shape).

        Anything else is legacy ``atomic_ordering``-based (a list of
        ability UUIDs that resolve to shell-cradle abilities) and the
        warning correctly applies. The frontend reads ``hid.steps`` to
        render the step-preview, so for HID profiles we surface the
        steps under that key — even when the YAML keeps them at the
        top level — so the existing v-if logic Just Works without
        having to refactor the picker template.

        TTL cache: reuses results for up to ``_PROFILE_CACHE_TTL_S``
        seconds when the (filename, mtime) fingerprint across every
        adversary YAML is unchanged. Dropping a new profile in
        data/adversaries/ or editing an existing one invalidates the
        cache on the next request.
        """
        out = []
        adv_dir = os.path.join(self.human_dir, 'data', 'adversaries')
        if not os.path.isdir(adv_dir):
            return out

        # Fingerprint = (filename, mtime_ns) tuple. Cheaper than
        # yaml.safe_load() per file on every call.
        yml_names = sorted(n for n in os.listdir(adv_dir)
                           if n.endswith('.yml'))
        try:
            fingerprint = tuple(
                (n, os.stat(os.path.join(adv_dir, n)).st_mtime_ns)
                for n in yml_names
            )
        except OSError:
            fingerprint = None

        now = time.monotonic()
        cached = _PROFILE_CACHE.get((adv_dir,))
        if (cached is not None
                and fingerprint is not None
                and cached[1] == fingerprint
                and (now - cached[0]) < _PROFILE_CACHE_TTL_S):
            # Shallow copy so callers can't mutate the cache list.
            return [dict(p) for p in cached[2]]

        import yaml
        for name in yml_names:
            path = os.path.join(adv_dir, name)
            try:
                with open(path) as f:
                    entries = yaml.safe_load(f) or []
            except Exception:
                continue
            # Legacy adversary YAMLs are top-level dicts; HID profiles
            # are top-level lists. Coerce to a list so both render.
            if isinstance(entries, dict):
                entries = [entries]
            if not entries:
                continue
            entry = entries[0]
            pid = entry.get('id') or os.path.splitext(name)[0]

            # ---- HID classification --------------------------------
            top_steps = entry.get('steps')
            platforms = entry.get('platforms') or {}
            platform_steps = None
            if isinstance(platforms, dict):
                # Pick any platform branch with a steps list — the
                # picker only needs ONE for the preview; the
                # materializer picks the right one at run-time.
                for branch in platforms.values():
                    if isinstance(branch, dict) and isinstance(
                            branch.get('steps'), list):
                        platform_steps = branch['steps']
                        break
            hid_block = entry.get('hid') or {}
            legacy_hid_steps = hid_block.get('steps') \
                if isinstance(hid_block, dict) else None

            if isinstance(top_steps, list):
                resolved_steps = top_steps
            elif isinstance(platform_steps, list):
                resolved_steps = platform_steps
            elif isinstance(legacy_hid_steps, list):
                resolved_steps = legacy_hid_steps
            else:
                resolved_steps = None

            is_hid = resolved_steps is not None

            # Surface the steps under hid.steps so human.vue's existing
            # `wf.hid.steps` lookup renders the preview for ALL HID
            # profile shapes (top-level steps:, platforms.<os>.steps,
            # or legacy hid.steps). Preserve any other hid keys
            # (estimated_duration_s, args defaults, etc).
            hid_out = dict(hid_block) if isinstance(hid_block, dict) else {}
            if is_hid:
                hid_out['steps'] = resolved_steps
                # Surface duration so the "~Ns" hint in the step-preview
                # header matches the YAML's estimate when present.
                if 'estimated_duration_s' not in hid_out \
                        and entry.get('duration_estimate_s') is not None:
                    hid_out['estimated_duration_s'] = \
                        entry.get('duration_estimate_s')

            out.append({
                'id': pid,                       # used by the picker key
                'profile_id': pid,               # marks it as run-profile path
                'name': entry.get('name') or os.path.splitext(name)[0],
                'description': (entry.get('description') or '').strip(),
                'is_hid': is_hid,                # frontend gates legacy warning
                'hid': hid_out,                  # populated for HID profiles
            })

        if fingerprint is not None:
            _PROFILE_CACHE[(adv_dir,)] = (now, fingerprint, out)
        return out

    @staticmethod
    def _normalize_range_host(h):
        """Coerce whatever the Range plugin gives us into the shape the UI wants."""
        disp = getattr(h, 'display', h) if not isinstance(h, dict) else h
        return {
            'id':      disp.get('id')    or disp.get('uuid') or disp.get('name'),
            'name':    disp.get('name')  or disp.get('hostname') or disp.get('id'),
            'ip':      disp.get('ip')    or disp.get('private_ip') or disp.get('public_ip') or '',
            'status':  disp.get('status') or 'unknown',
            'vnc_ws':  disp.get('vnc_ws') or None,
        }

    """ PRIVATE """

    async def _load_workflow_module(self, root, workflow_file):
        module_path = os.path.join(root, workflow_file.split('.')[0]).replace(os.path.sep, '.')
        try:
            module = import_module(module_path)
            workflow_name = getattr(module, 'WORKFLOW_NAME')
            workflow_description = getattr(module, 'WORKFLOW_DESCRIPTION')
            await self.data_svc.store(Workflow(name=workflow_name, description=workflow_description, file=workflow_file))
        except Exception as e:
            self.log.error('Error loading extension=%s, %s' % (module_path, e))

    def _legacy_workflow_catalog(self):
        root = os.path.join(self.pyhuman_path, 'app', 'workflows')
        if not os.path.isdir(root):
            return []

        out = []
        for workflow_file in sorted(os.listdir(root)):
            if workflow_file.startswith('_') or not workflow_file.endswith('.py'):
                continue
            path = os.path.join(root, workflow_file)
            meta = self._read_workflow_metadata(path)
            if not meta.get('name'):
                continue
            out.append({
                'name': meta['name'],
                'description': meta.get('description') or '',
                'file': workflow_file,
            })
        return sorted(out, key=lambda w: w['name'].lower())

    @staticmethod
    def _read_workflow_metadata(path):
        try:
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=path)
        except Exception:
            return {}

        values = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            if not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
        return {
            'name': values.get('WORKFLOW_NAME'),
            'description': values.get('WORKFLOW_DESCRIPTION'),
        }

    async def _create_windows_archive(self, payload_path, behaviors, name):
        os.makedirs(payload_path, exist_ok=True)
        file_name = name + '.zip'
        with zipfile.ZipFile(os.path.join(payload_path, file_name), 'w') as win_zip:
            for behavior in behaviors:
                arc_name = os.path.join('app', 'workflows', os.path.basename(behavior))
                win_zip.write(self.pyhuman_path + behavior, arc_name)
            for root, dirs, files in os.walk(os.path.join(self.pyhuman_path, 'data')):
                for file in files:
                    arc_name = os.path.join('data', os.path.basename(file))
                    win_zip.write(os.path.join(root, file), arc_name)
            for root, dirs, files in os.walk(os.path.join(self.pyhuman_path, 'app', 'utility')):
                for file in files:
                    arc_name = os.path.join('app', 'utility', os.path.basename(file))
                    win_zip.write(os.path.join(root, file), arc_name)
            win_zip.write(os.path.join(self.pyhuman_path, 'human.py'), 'human.py')
            win_zip.write(os.path.join(self.pyhuman_path, 'requirements.txt'), 'requirements.txt')

    async def _create_unix_archive(self, payload_path, behaviors, name):
        os.makedirs(payload_path, exist_ok=True)
        file_name = name + '.tar.gz'
        with tarfile.open(os.path.join(payload_path, file_name), 'w:gz') as unix_tar:
            unix_tar.add(os.path.join(self.pyhuman_path, 'data'), arcname='data/.')
            unix_tar.add(os.path.join(self.pyhuman_path, 'app', 'utility'), arcname='app/utility/.')
            for behavior in behaviors:
                unix_tar.add(
                    self.pyhuman_path + behavior,
                    arcname=os.path.join('app', 'workflows', os.path.basename(behavior)),
                )
            unix_tar.add(os.path.join(self.pyhuman_path, 'human.py'), arcname='human.py')
            unix_tar.add(os.path.join(self.pyhuman_path, 'requirements.txt'), arcname='requirements.txt')

    async def _select_modules_and_compress(
            self, modules, name, platform, task_interval,
            task_cluster_interval, tasks_per_cluster, extra):
        payload_path = os.path.abspath(os.path.join(self.human_dir, 'payloads'))
        behaviors, workflows = await self._append_module_paths(modules, [])
        self.log.debug('Compressing new legacy human: %s' % name)

        if platform == 'windows-psh':
            await self._create_windows_archive(payload_path, behaviors, name)
        else:
            await self._create_unix_archive(payload_path, behaviors, name)

        await self.data_svc.store(Human(
            name=name,
            task_interval=task_interval,
            task_cluster_interval=task_cluster_interval,
            tasks_per_cluster=tasks_per_cluster,
            platform=platform,
            extra=extra,
            workflows=workflows,
        ))

    async def _append_module_paths(self, modules, behaviors):
        workflows = []
        catalog = {w['name']: w for w in self._legacy_workflow_catalog()}
        catalog.update({
            os.path.splitext(w['file'])[0]: w
            for w in catalog.values()
        })
        for sm in modules:
            workflow = []
            try:
                workflow = await self.data_svc.locate('workflows', match=dict(name=sm))
            except Exception:
                workflow = []
            if workflow:
                item = workflow[0]
                disp = getattr(item, 'display', None) or {}
                file_name = disp.get('file') or getattr(item, 'file', None)
                if not file_name:
                    raise ValueError('workflow %s has no file' % sm)
                behaviors.append('/app/workflows/' + file_name)
                workflows.append(item)
                continue

            entry = catalog.get(sm)
            if not entry:
                raise ValueError('unknown workflow: %s' % sm)
            behaviors.append('/app/workflows/' + entry['file'])
            workflows.append(Workflow(
                name=entry['name'],
                description=entry.get('description') or '',
                file=entry['file'],
            ))
        return behaviors, workflows
