"""Unit tests for SSHTunnelManager.

Both AWS-bare-metal and Azure-nested cloud carriers route their
operator UDS through this manager. Tests focus on the contract the
Range providers depend on:

  * `open_tunnel({"kind":"ssh-tunnel", ...})` invokes ssh with a
    correct `-L` flag (UDS or TCP), records a TunnelHandle, and
    returns a local path/port the AF_UNIX/TCP client code can use
    unchanged.
  * `close_tunnel(vm_id)` SIGTERMs the ssh child and unlinks the
    local UDS file.
  * `close_all()` reaps every registered tunnel.
  * Bad transport blocks raise (kind mismatch, neither remote_*).

We mock subprocess.Popen so no actual ssh runs.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import via the canonical Caldera plugin namespace so the full pytest
# suite (which also collects test_ssh_tunnel_aws.py and other tests
# that load `plugins.human.app.*`) doesn't get a sys.modules collision
# on the top-level `app` binding. The companion test file
# test_ssh_tunnel_aws.py used to fight this collision with a recovery
# shim; pinning both tests to the same namespace avoids the dance.
CALDERA_ROOT = '/home/caldera/Desktop/CalderaVENV/caldera'
if CALDERA_ROOT not in sys.path:
    sys.path.insert(0, CALDERA_ROOT)
# Keep Human plugin root on path too for any sibling utilities that
# import from `plugins.human.app.*` relative to the plugin tree.
HUMAN_ROOT = Path(__file__).resolve().parents[1]
if str(HUMAN_ROOT.parent.parent) not in sys.path:
    sys.path.insert(0, str(HUMAN_ROOT.parent.parent))

from plugins.human.app.ssh_tunnel import (  # noqa: E402
    SSHTunnelManager, TunnelHandle,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tunnel_dir(tmp_path):
    return tmp_path / "tunnels"


@pytest.fixture
def manager(tunnel_dir):
    return SSHTunnelManager(
        local_base=str(tunnel_dir),
        ssh_bin="/usr/bin/ssh",
        strict_host_key_checking=False,
    )


def _fake_proc(pid: int = 12345):
    p = MagicMock()
    p.pid = pid
    p.poll.return_value = None
    return p


# ---------------------------------------------------------------------------
# UDS tunnel
# ---------------------------------------------------------------------------

def test_open_uds_tunnel_invokes_ssh_with_local_forward(manager, tunnel_dir):
    transport = {
        "kind": "ssh-tunnel",
        "host": "1.2.3.4",
        "user": "azureuser",
        "key_path": "/var/lib/timestone/keys/k",
        "remote_input_op_socket": "/run/timestone/az-1/input-op.sock",
    }
    fake = _fake_proc()
    with patch("plugins.human.app.ssh_tunnel.subprocess.Popen", return_value=fake) as popen, \
         patch("plugins.human.app.ssh_tunnel._await_path", return_value=True):
        handle = manager.open_tunnel(transport, vm_id="az-1")

    assert isinstance(handle, TunnelHandle)
    assert handle.kind == "uds"
    assert handle.local_path.endswith("input-op.sock")
    assert handle.remote_target == "/run/timestone/az-1/input-op.sock"
    assert handle.pid == 12345

    argv = popen.call_args[0][0]
    # ssh ... -L /local/path:/remote/path ... user@host
    assert argv[0] == "/usr/bin/ssh"
    assert "-L" in argv
    forward_idx = argv.index("-L")
    forward_spec = argv[forward_idx + 1]
    assert forward_spec.startswith(handle.local_path + ":")
    assert forward_spec.endswith(":/run/timestone/az-1/input-op.sock") or \
        forward_spec.endswith("/run/timestone/az-1/input-op.sock")
    assert "azureuser@1.2.3.4" in argv
    assert "-N" in argv  # no remote command
    assert "-i" in argv
    assert "/var/lib/timestone/keys/k" in argv


def test_open_uds_returns_existing_handle_via_get(manager):
    transport = {
        "kind": "ssh-tunnel",
        "host": "h", "user": "u", "key_path": "/k",
        "remote_input_op_socket": "/r.sock",
    }
    with patch("plugins.human.app.ssh_tunnel.subprocess.Popen", return_value=_fake_proc()), \
         patch("plugins.human.app.ssh_tunnel._await_path", return_value=True):
        opened = manager.open_tunnel(transport, vm_id="x")
    assert manager.get("x") is opened
    assert manager.get("nope") is None


# ---------------------------------------------------------------------------
# TCP tunnel
# ---------------------------------------------------------------------------

def test_open_tcp_tunnel_picks_local_port(manager):
    transport = {
        "kind": "ssh-tunnel",
        "host": "h", "user": "u", "key_path": "/k",
        "remote_vnc_port": 5961,
    }
    with patch("plugins.human.app.ssh_tunnel.subprocess.Popen", return_value=_fake_proc()) as popen, \
         patch("plugins.human.app.ssh_tunnel._await_tcp_listener", return_value=True):
        handle = manager.open_tunnel(transport, vm_id="vnc-x")

    assert handle.kind == "tcp"
    assert handle.local_port > 0
    assert handle.remote_target == "127.0.0.1:5961"

    argv = popen.call_args[0][0]
    forward_idx = argv.index("-L")
    forward_spec = argv[forward_idx + 1]
    assert forward_spec == f"{handle.local_port}:127.0.0.1:5961"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_wrong_transport_kind_raises(manager):
    with pytest.raises(ValueError):
        manager.open_tunnel({"kind": "bogus"}, vm_id="x")


def test_neither_uds_nor_port_raises(manager):
    with pytest.raises(KeyError):
        manager.open_tunnel(
            {"kind": "ssh-tunnel", "host": "h", "user": "u", "key_path": "/k"},
            vm_id="x",
        )


def test_uds_and_port_prefers_uds(manager):
    """A meta block carrying BOTH remote_input_op_socket and
    remote_vnc_port (rare) opens the UDS — the input channel is
    latency-sensitive. The display tunnel is opened separately by
    the noVNC proxy."""
    transport = {
        "kind": "ssh-tunnel",
        "host": "h", "user": "u", "key_path": "/k",
        "remote_input_op_socket": "/r.sock",
        "remote_vnc_port": 5961,
    }
    with patch("plugins.human.app.ssh_tunnel.subprocess.Popen", return_value=_fake_proc()), \
         patch("plugins.human.app.ssh_tunnel._await_path", return_value=True):
        handle = manager.open_tunnel(transport, vm_id="x")
    assert handle.kind == "uds"


def test_open_uds_timeout_kills_proc_and_raises(manager):
    transport = {
        "kind": "ssh-tunnel",
        "host": "h", "user": "u", "key_path": "/k",
        "remote_input_op_socket": "/r.sock",
    }
    fake = _fake_proc()
    with patch("plugins.human.app.ssh_tunnel.subprocess.Popen", return_value=fake), \
         patch("plugins.human.app.ssh_tunnel._await_path", return_value=False):
        with pytest.raises(TimeoutError):
            manager.open_tunnel(transport, vm_id="x")
    fake.kill.assert_called_once()


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def test_close_tunnel_terminates_proc_and_unlinks(manager, tunnel_dir):
    transport = {
        "kind": "ssh-tunnel",
        "host": "h", "user": "u", "key_path": "/k",
        "remote_input_op_socket": "/r.sock",
    }
    fake = _fake_proc()
    fake.poll.return_value = None
    with patch("plugins.human.app.ssh_tunnel.subprocess.Popen", return_value=fake), \
         patch("plugins.human.app.ssh_tunnel._await_path", return_value=True):
        handle = manager.open_tunnel(transport, vm_id="x")
    # Touch the local sock file so we can verify unlink.
    open(handle.local_path, 'w').close()
    assert os.path.exists(handle.local_path)

    manager.close_tunnel("x")
    fake.send_signal.assert_called_with(signal.SIGTERM)
    assert manager.get("x") is None
    assert not os.path.exists(handle.local_path)


def test_close_tunnel_idempotent(manager):
    manager.close_tunnel("never-opened")  # must not raise


def test_close_all(manager):
    transport = {
        "kind": "ssh-tunnel",
        "host": "h", "user": "u", "key_path": "/k",
        "remote_input_op_socket": "/r.sock",
    }
    with patch("plugins.human.app.ssh_tunnel.subprocess.Popen", return_value=_fake_proc()), \
         patch("plugins.human.app.ssh_tunnel._await_path", return_value=True):
        manager.open_tunnel(transport, vm_id="a")
        manager.open_tunnel(transport, vm_id="b")
    assert manager.get("a") and manager.get("b")
    manager.close_all()
    assert manager.get("a") is None
    assert manager.get("b") is None
