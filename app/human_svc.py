import glob
import json
import logging
import os
import sys
import zipfile
import tarfile

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


class HumanService(BaseService):

    def __init__(self, services):
        self.file_svc = services.get('file_svc')
        self.data_svc = services.get('data_svc')
        self.log = self.add_service('human_svc', self)
        self.human_dir = os.path.relpath(os.path.join('plugins', 'human'))
        self.pyhuman_path = os.path.join(self.human_dir, 'pyhuman')
        sys.path.insert(0, self.pyhuman_path)  # needed to load relative module paths in pyhuman for workflows

    async def build_human(self, data):
        try:
            _, name = os.path.split(data.pop('name'))
            await self._select_modules_and_compress(modules=data.pop('tasks'), name=name, platform=data.pop('platform'),
                                                    task_interval=data.pop('task_interval'), tasks_per_cluster=data.pop('task_count'),
                                                    task_cluster_interval=data.pop('task_cluster_interval'), extra=data.pop('extra', []))
            return (await self.data_svc.locate('humans', match=dict(name=name)))[0].display
        except Exception as e:
            self.log.error('Error building human. %s' % e)

    async def load_humans(self, data):
        return [h.display for h in await self.data_svc.locate('humans', match=dict(name=data.get('name')))]

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

    # ------------------------------------------------------------------ #
    # Timestone live-UI helpers                                           #
    # ------------------------------------------------------------------ #

    async def list_range_hosts(self):
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
        """
        # 1. Forward-compat: prefer the Range plugin's in-process data
        # store if it ever publishes there.
        try:
            range_hosts = await self.data_svc.locate('range_instances')
            if range_hosts:
                return {
                    'profile': '(active range)',
                    'hosts': [self._normalize_range_host(h) for h in range_hosts],
                }
        except Exception:
            # data_svc.locate raises for unknown collections; that's fine,
            # fall through to the meta.json scan.
            pass

        # 2. Scan meta.json files written by the Range provider.
        hosts = self._scan_microvm_meta()
        if hosts:
            return {'profile': '(active range)', 'hosts': hosts}

        # 3. No microVMs running — return stubs so the dropdown is
        # populated on a fresh dev box (preserves pre-G6a behavior).
        return {
            'profile': '(stub)',
            'hosts': [
                {'id': 'host-stub-1', 'name': 'microvm-1', 'ip': '10.0.0.11',
                 'status': 'unknown', 'vnc_ws': None},
                {'id': 'host-stub-2', 'name': 'microvm-2', 'ip': '10.0.0.12',
                 'status': 'unknown', 'vnc_ws': None},
            ],
        }

    @staticmethod
    def _scan_microvm_meta():
        """Glob ``<BASE>/*/meta.json`` and return UI-shaped host dicts.

        Re-reads ``human_api.MICROVM_RUNTIME_BASE`` on every call so
        tests that patch the module global pick up the new value, and
        so an env-var change at runtime is honored.

        Malformed / unreadable meta.json files are skipped + logged
        (one bad file should not blank the dropdown).
        """
        base = getattr(_human_api, 'MICROVM_RUNTIME_BASE',
                       '/tmp/timestone-microvms')
        pattern = os.path.join(base, '*', 'meta.json')
        out = []
        for meta_path in sorted(glob.glob(pattern)):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except Exception as e:
                _log.warning('skipping unreadable meta.json at %s: %s',
                             meta_path, e)
                continue
            vm_name = meta.get('vm_name')
            ip = meta.get('ip')
            if not vm_name or not ip:
                _log.warning('skipping meta.json at %s: missing required '
                             'fields (vm_name=%r, ip=%r)',
                             meta_path, vm_name, ip)
                continue
            out.append({
                'id':       vm_name,
                'name':     vm_name,
                'ip':       ip,
                'os':       meta.get('os', 'unknown'),
                'status':   'running',
                'provider': 'microvm',
                'vnc_ws':   None,
            })
        return out

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
        """
        import yaml
        out = []
        adv_dir = os.path.join(self.human_dir, 'data', 'adversaries')
        if not os.path.isdir(adv_dir):
            return out
        for name in sorted(os.listdir(adv_dir)):
            if not name.endswith('.yml'):
                continue
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

    async def _create_windows_archive(self, payload_path, behaviors, name):
        file_name = name + '.zip'
        win_zip = zipfile.ZipFile(os.path.join(payload_path, file_name), 'w')
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
        win_zip.close()

    async def _create_unix_archive(self, payload_path, behaviors, name):
        file_name = name + '.tar.gz'
        unix_tar = tarfile.open(os.path.join(payload_path, file_name), 'w:gz')
        unix_tar.add(os.path.join(self.pyhuman_path, 'data'), arcname='data/.')
        unix_tar.add(os.path.join(self.pyhuman_path, 'app', 'utility'), arcname='app/utility/.')
        for behavior in behaviors:
            unix_tar.add(self.pyhuman_path + behavior, arcname=os.path.join('app', 'workflows',
                                                                           os.path.basename(behavior)))
        unix_tar.add(os.path.join(self.pyhuman_path, 'human.py'), arcname='human.py')
        unix_tar.add(os.path.join(self.pyhuman_path, 'requirements.txt'), arcname='requirements.txt')
        unix_tar.close()

    async def _select_modules_and_compress(self, modules, name, platform, task_interval, task_cluster_interval, tasks_per_cluster, extra):
        payload_path = os.path.abspath(os.path.join(self.human_dir, 'payloads'))
        behaviors = []
        behaviors, workflows = await self._append_module_paths(modules, behaviors)
        self.log.debug('Compressing new human: %s' % name)

        if platform == 'windows-psh':
            await self._create_windows_archive(payload_path, behaviors, name)
        else:
            await self._create_unix_archive(payload_path, behaviors, name)

        await self.data_svc.store(Human(name=name, task_interval=task_interval, task_cluster_interval=task_cluster_interval,
                                        tasks_per_cluster=tasks_per_cluster, platform=platform, extra=extra, workflows=workflows))

    async def _append_module_paths(self, modules, behaviors):
        workflows = []
        for sm in modules:
            workflow = await self.data_svc.locate('workflows', match=dict(name=sm))
            behaviors += ['/app/workflows/' + workflow[0].file]
            workflows.append(workflow[0])
        return behaviors, workflows
