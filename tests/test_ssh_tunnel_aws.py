"""AWS-side coverage for SSHTunnelManager + the cloud-carrier branch
of ``human_api._resolve_operator_socket``.

Companion to ``test_ssh_tunnel.py`` (which covers the Azure-shape
transport block). The Azure-side suite already asserts the manager's
core argv contract; this file adds:

  * AWS-specific transport blocks (``user="ubuntu"``, AWS-style hostname).
  * The full `_resolve_operator_socket` round-trip when meta.json carries
    the ssh-tunnel:// sentinel — proves AWS provider's meta.json shape
    drives the right resolver branch.
  * Backwards-compat: on-prem meta.json (no transport block) still
    returns the local UDS path unchanged.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import via Caldera's plugin namespace. ORDER MATTERS: we must load
# `human_api` (which does `from app.service.auth_svc import ...`) BEFORE
# anything else loads `app.ssh_tunnel` from Human's bare `app/`. Pytest
# sometimes collects `test_ssh_tunnel.py` (which uses the bare-`app`
# import pattern) first; if so, `app` gets bound to Human's package and
# `app.service` resolution fails. Workaround: try-import; if it fails
# because of that pollution, force-reload `app` from Caldera first.
CALDERA_ROOT = '/home/caldera/Desktop/CalderaVENV/caldera'
if CALDERA_ROOT not in sys.path:
    sys.path.insert(0, CALDERA_ROOT)


def _import_human_api_module():
    """Resilient import of plugins.human.app.human_api — handles the
    sys.modules pollution caused by test_ssh_tunnel.py's bare-`app`
    import when it runs first in the same pytest session.

    Strategy: try a fresh import; if it fails because `app.service`
    isn't found (Human's namespace `app/` package shadowed Caldera's),
    evict every `app.*` from sys.modules and retry once."""
    import importlib
    try:
        return importlib.import_module('plugins.human.app.human_api')
    except ModuleNotFoundError as e:
        if 'app.service' not in str(e) and "'app'" not in str(e):
            raise
        # Evict only the top-level `app` binding; leave already-loaded
        # `app.ssh_tunnel` alone (sibling Azure tests' patches reference
        # it by that name and would crash if we tear it down here).
        sys.modules.pop('app', None)
        sys.modules.pop('plugins.human.app.human_api', None)
        return importlib.import_module('plugins.human.app.human_api')


from plugins.human.app.ssh_tunnel import (  # noqa: E402
    SSHTunnelManager, TunnelHandle,
)
human_api_module = _import_human_api_module()


def _fake_proc():
    p = MagicMock()
    p.pid = 4242
    p.poll.return_value = None
    return p


# ---------------------------------------------------------------------------
# AWS transport-block shape
# ---------------------------------------------------------------------------

class TestAwsTransportShape:
    """The AWS provider's `to_meta_json` emits exactly this shape. If
    SSHTunnelManager ever rejects it the cloud spawn path silently
    breaks — assert it explicitly."""

    @pytest.fixture
    def manager(self, tmp_path):
        return SSHTunnelManager(
            local_base=str(tmp_path),
            ssh_bin="/usr/bin/ssh",
            strict_host_key_checking=False,
        )

    def test_aws_uds_block_opens_correctly(self, manager):
        """Mirrors AwsMetalCarrier.to_meta_json output for input UDS."""
        transport = {
            "kind": "ssh-tunnel",
            "host": "ec2-1-2-3-4.compute.amazonaws.com",
            "user": "ubuntu",
            "key_path": "/var/lib/timestone/keys/win-victim-01-abc123.key",
            "remote_input_op_socket":
                "/run/timestone/win-victim-01/input-op.sock",
            "remote_vnc_port": 5961,
        }
        with patch("plugins.human.app.ssh_tunnel.subprocess.Popen",
                   return_value=_fake_proc()) as popen, \
             patch("plugins.human.app.ssh_tunnel._await_path", return_value=True):
            handle = manager.open_tunnel(transport, vm_id="win-victim-01")

        assert handle.kind == "uds"
        argv = popen.call_args[0][0]
        assert "ubuntu@ec2-1-2-3-4.compute.amazonaws.com" in argv
        # The forward spec must end with the AWS-style remote path.
        l_idx = argv.index("-L")
        spec = argv[l_idx + 1]
        assert spec.endswith("/run/timestone/win-victim-01/input-op.sock")

    def test_aws_vnc_tcp_block(self, manager):
        transport = {
            "kind": "ssh-tunnel",
            "host": "ec2-1-2-3-4.compute.amazonaws.com",
            "user": "ubuntu",
            "key_path": "/k.pem",
            "remote_vnc_port": 5961,
        }
        with patch("plugins.human.app.ssh_tunnel.subprocess.Popen",
                   return_value=_fake_proc()), \
             patch("plugins.human.app.ssh_tunnel._await_tcp_listener", return_value=True):
            handle = manager.open_tunnel(transport, vm_id="win-vnc-01")
        assert handle.kind == "tcp"
        assert handle.remote_target == "127.0.0.1:5961"


# ---------------------------------------------------------------------------
# _resolve_operator_socket: cloud branch + back-compat
# ---------------------------------------------------------------------------

class TestResolveOperatorSocket:
    """End-to-end: meta.json on disk → _resolve_operator_socket → either
    a local UDS path (on-prem) or a tunnel-rewritten local path (cloud).
    Touches both providers' meta.json shapes."""

    @pytest.fixture
    def runtime_base(self, tmp_path):
        # Tests pivot the module-global MICROVM_RUNTIME_BASE here.
        d = tmp_path / "runtime"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_meta(self, runtime_base: Path, vm_name: str, meta: dict) -> Path:
        run_dir = runtime_base / f"{vm_name}-deadbeef"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "meta.json").write_text(json.dumps(meta))
        return run_dir

    def test_cloud_branch_calls_open_tunnel(self, runtime_base):
        human_api = human_api_module

        vm = "aws-victim-01"
        transport = {
            "kind": "ssh-tunnel",
            "host": "ec2-1-2-3-4.compute.amazonaws.com",
            "user": "ubuntu",
            "key_path": "/var/lib/timestone/keys/aws-victim-01-abc.key",
            "remote_input_op_socket":
                "/run/timestone/aws-victim-01/input-op.sock",
            "remote_vnc_port": 5961,
        }
        meta = {
            "vm_name": vm,
            "transport": transport,
            "input_daemon": {
                "operator_socket": f"ssh-tunnel://{vm}/input-op.sock",
            },
            "gpu_daemon": {"vnc_port": 0},
        }
        self._write_meta(runtime_base, vm, meta)

        stub_handle = TunnelHandle(
            vm_id=vm, kind="uds",
            local_path="/tmp/timestone-tunnels/aws-victim-01/input-op.sock",
            remote_target=transport["remote_input_op_socket"],
            pid=1234, proc=None, started_at=time.time(),
        )
        fake_mgr = MagicMock()
        fake_mgr.get.return_value = None
        fake_mgr.open_tunnel.return_value = stub_handle

        with patch.object(human_api, "MICROVM_RUNTIME_BASE", str(runtime_base)), \
             patch.object(human_api, "_SSH_TUNNEL_MGR", fake_mgr):
            local = human_api.HumanApi._resolve_operator_socket(vm)

        assert local == stub_handle.local_path
        fake_mgr.open_tunnel.assert_called_once_with(transport, vm)

    def test_cloud_branch_reuses_existing_handle(self, runtime_base):
        human_api = human_api_module

        vm = "aws-victim-02"
        transport = {
            "kind": "ssh-tunnel", "host": "h", "user": "ubuntu",
            "key_path": "/k", "remote_input_op_socket": "/r/x",
        }
        meta = {
            "transport": transport,
            "input_daemon": {
                "operator_socket": f"ssh-tunnel://{vm}/input-op.sock",
            },
        }
        self._write_meta(runtime_base, vm, meta)

        existing = TunnelHandle(
            vm_id=vm, kind="uds",
            local_path="/tmp/existing/input-op.sock",
            remote_target="/r/x",
            pid=42, proc=None, started_at=time.time(),
        )
        fake_mgr = MagicMock()
        fake_mgr.get.return_value = existing
        fake_mgr.open_tunnel.side_effect = AssertionError(
            "should not re-open when handle exists"
        )

        with patch.object(human_api, "MICROVM_RUNTIME_BASE", str(runtime_base)), \
             patch.object(human_api, "_SSH_TUNNEL_MGR", fake_mgr):
            local = human_api.HumanApi._resolve_operator_socket(vm)

        assert local == existing.local_path
        fake_mgr.get.assert_called_once_with(vm)

    def test_onprem_meta_passes_through_unchanged(self, runtime_base):
        """Backwards compat: meta.json with no transport block returns
        the on-prem UDS path verbatim."""
        human_api = human_api_module

        vm = "onprem-vm"
        meta = {
            "vm_name": vm,
            "input_daemon": {
                "operator_socket": "/run/timestone/onprem-vm/input-op.sock",
            },
            "gpu_daemon": {"vnc_port": 5961},
        }
        self._write_meta(runtime_base, vm, meta)

        with patch.object(human_api, "MICROVM_RUNTIME_BASE", str(runtime_base)):
            sock = human_api.HumanApi._resolve_operator_socket(vm)
        assert sock == "/run/timestone/onprem-vm/input-op.sock"

    def test_cloud_sentinel_without_transport_block_raises(self, runtime_base):
        """Defensive: if a provider emits the ssh-tunnel:// sentinel but
        forgets the transport block, fail loud — silently returning the
        sentinel as a UDS path would crash inside socket.connect()."""
        human_api = human_api_module

        vm = "broken"
        meta = {
            "input_daemon": {
                "operator_socket": f"ssh-tunnel://{vm}/input-op.sock",
            },
            # transport block intentionally missing
        }
        self._write_meta(runtime_base, vm, meta)

        with patch.object(human_api, "MICROVM_RUNTIME_BASE", str(runtime_base)):
            with pytest.raises(KeyError):
                human_api.HumanApi._resolve_operator_socket(vm)
