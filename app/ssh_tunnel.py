"""SSHTunnelManager — shared SSH local-forward harness for cloud-hosted
AE-clean carriers (AWS bare-metal, Azure nested, etc.).

Cloud-spawned microVM carriers terminate their AF_UNIX operator socket
(and VNC TCP port) inside the cloud VM. The Caldera Human plugin runs
on the operator's machine. To bridge them we open an `ssh -L` tunnel
on demand, surface a *local* path/port, and let `_resolve_operator_socket`
return that local handle in place of the on-prem
`/tmp/timestone-microvms/<id>/input-op.sock`.

Single implementation; both the AWS and Azure providers' `meta.json`
emit `transport.kind = "ssh-tunnel"` and reuse this class.

Two transport flavors are supported:

  * Unix-domain  (`-L /local-path:/remote/path`)
      OpenSSH 6.7+ supports forwarding a local UDS to a remote UDS,
      which lets us preserve the SOCK_STREAM contract end-to-end.
  * TCP         (`-L localport:127.0.0.1:remoteport`)
      Used for the VNC display channel (5901+).

Lifecycle:
  * `open_tunnel(meta_transport, vm_id)` — spawns ssh, returns a
    LocalEndpoint dict the caller passes to whatever code expects an
    operator-socket path / VNC TCP port.
  * `close_tunnel(vm_id)` — SIGTERM/SIGKILL the ssh child, unlink local
    socket file, drop registry entry.
  * `close_all()` — call from plugin shutdown hooks.

Process model: one ssh process per VM. We do NOT use ControlMaster; each
tunnel is independent and dies cleanly on `kill(pid, SIGTERM)`. Auto-add
unknown host keys is OFF by default — the caller (Range provider) is
expected to seed `~/.ssh/known_hosts` after the cloud VM publishes its
host key. For test/dev, `strict_host_key_checking=False` is supported
but logged as a warning.

Threading: the manager is plain-Python sync; the Human plugin's request
handlers run inside aiohttp's loop and call `open_tunnel` via
`run_in_executor` if blocking-on-ssh-handshake is a concern. Tunnel
setup is fast enough (<1s on a warm path) that current callers invoke
it directly.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


log = logging.getLogger(__name__)


def _ssh_tunnel_conf(prop: str):
    """Read ``prop`` from the human plugin conf, or ``None`` if unavailable.

    ``human_api`` imports this module at top, so we can't import back from it
    without a cycle; instead read ``conf/local.yml`` then ``conf/default.yml``
    straight off disk. Used as the middle layer of the ENV -> conf -> default
    chain for ``DEFAULT_LOCAL_TUNNEL_BASE`` below. Mirrors the same disk-read
    fallback ``human_api._human_conf`` uses.

    :param prop: config key under the ``human`` scope.
    :return: the configured value, or ``None`` when absent/unreadable.
    :raises: never — any parse/IO error degrades to ``None``.
    """
    conf_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'conf')
    for fname in ('local.yml', 'default.yml'):
        try:
            import yaml
            with open(os.path.join(conf_dir, fname), encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, dict) and data.get(prop) is not None:
                return data[prop]
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001 — malformed conf must not break import
            continue
    return None


# Where local forwarding sockets live on the operator's machine. Same
# /tmp prefix as MICROVM_RUNTIME_BASE so cleanup tooling already knows
# where to look. One subdir per VM keeps file-name collisions out of
# the picture even for parallel deploys. Resolution: TIMESTONE_SSH_TUNNEL_BASE
# env -> human conf (ssh_tunnel_base) -> the historical default. Env wins so
# existing deploys are unchanged.
DEFAULT_LOCAL_TUNNEL_BASE = (
    os.environ.get('TIMESTONE_SSH_TUNNEL_BASE')
    or _ssh_tunnel_conf('ssh_tunnel_base')
    or '/tmp/timestone-tunnels'
)


@dataclass
class TunnelHandle:
    """In-memory record of one open `ssh -L` child process."""
    vm_id: str
    kind: str            # "uds" or "tcp"
    local_path: str = ""    # for kind="uds"
    local_port: int = 0     # for kind="tcp"
    remote_target: str = ""  # display only
    pid: int = 0
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    started_at: float = 0.0


class SSHTunnelManager:
    """Per-process registry of ssh -L tunnels.

    Construct one at plugin load time; pass it into every code path
    that resolves a cloud-hosted operator socket. `_resolve_operator_socket`
    in `human_api.py` calls `open_tunnel` when meta.json declares
    `transport.kind == 'ssh-tunnel'` and returns the local handle.
    """

    def __init__(self,
                 local_base: str = DEFAULT_LOCAL_TUNNEL_BASE,
                 ssh_bin: str = 'ssh',
                 strict_host_key_checking: bool = True):
        self.local_base = local_base
        self.ssh_bin = shutil.which(ssh_bin) or ssh_bin
        self.strict_host_key_checking = strict_host_key_checking
        self._tunnels: Dict[str, TunnelHandle] = {}
        self._lock = threading.Lock()
        os.makedirs(self.local_base, exist_ok=True)

    # ---- public API -------------------------------------------------------

    def open_tunnel(self, transport: dict, vm_id: str) -> TunnelHandle:
        """Open a tunnel for a meta.json `transport` block.

        `transport` shape (Unix-domain):
            {"kind": "ssh-tunnel",
             "host": "1.2.3.4", "user": "azureuser",
             "key_path": "/var/lib/timestone/keys/<vm_id>",
             "remote_input_op_socket": "/run/timestone/<vm_id>/input-op.sock"}

        TCP:
            {"kind": "ssh-tunnel",
             "host": "...", "user": "...", "key_path": "...",
             "remote_vnc_port": 5961}

        If both UDS and VNC are present in the same transport block,
        the caller invokes `open_tunnel` twice (once per dimension)
        with `vm_id`-suffixed keys. We don't try to multiplex one ssh
        child across two -L's because it complicates teardown.
        """
        if transport.get('kind') != 'ssh-tunnel':
            raise ValueError(
                f'unexpected transport.kind={transport.get("kind")!r} '
                f'(expected "ssh-tunnel")')

        host = transport['host']
        user = transport.get('user', 'azureuser')
        key_path = transport['key_path']

        # Decide UDS vs TCP. UDS preferred (preserves SOCK_STREAM).
        remote_uds = transport.get('remote_input_op_socket')
        remote_port = transport.get('remote_vnc_port')

        if remote_uds and not remote_port:
            return self._open_uds(vm_id, host, user, key_path, remote_uds)
        if remote_port and not remote_uds:
            return self._open_tcp(vm_id, host, user, key_path, int(remote_port))
        if remote_uds and remote_port:
            # Caller mixed both — open the UDS one (input is the
            # latency-sensitive channel). Display tunnel is opened by
            # the noVNC proxy via a separate call.
            return self._open_uds(vm_id, host, user, key_path, remote_uds)
        raise KeyError(
            'transport block has neither remote_input_op_socket nor '
            'remote_vnc_port; nothing to forward')

    def close_tunnel(self, vm_id: str) -> None:
        """Tear down the tunnel for `vm_id` (idempotent)."""
        with self._lock:
            handle = self._tunnels.pop(vm_id, None)
        if handle is None:
            return
        try:
            if handle.proc is not None and handle.proc.poll() is None:
                handle.proc.send_signal(signal.SIGTERM)
                try:
                    handle.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    handle.proc.kill()
                    handle.proc.wait(timeout=2)
        except Exception as e:
            log.warning(f'close_tunnel({vm_id}): kill failed: {e}')
        if handle.kind == 'uds' and handle.local_path:
            try:
                os.unlink(handle.local_path)
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning(
                    f'close_tunnel({vm_id}): unlink {handle.local_path} '
                    f'failed: {e}')

    def close_all(self) -> None:
        """Plugin-shutdown hook: reap every tunnel."""
        with self._lock:
            ids = list(self._tunnels.keys())
        for vm_id in ids:
            self.close_tunnel(vm_id)

    def get(self, vm_id: str) -> Optional[TunnelHandle]:
        with self._lock:
            return self._tunnels.get(vm_id)

    # ---- internals --------------------------------------------------------

    def _open_uds(self, vm_id: str, host: str, user: str,
                  key_path: str, remote_uds: str) -> TunnelHandle:
        local_dir = os.path.join(self.local_base, vm_id)
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, 'input-op.sock')
        # Stale socket file from prior run blocks ssh -L. Pre-unlink.
        try:
            os.unlink(local_path)
        except FileNotFoundError:
            pass

        argv = self._base_ssh_argv(key_path, user, host)
        argv += ['-L', f'{local_path}:{remote_uds}']
        # Hold the channel open with no remote command (`-N`). Send
        # keepalives so a half-dead path is detected within ~30s.
        argv += ['-N', '-o', 'ServerAliveInterval=10',
                 '-o', 'ServerAliveCountMax=3']

        proc = self._spawn(argv)
        # Block briefly until the local socket appears (ssh creates it
        # after auth completes). Cap at 5s.
        if not _await_path(local_path, timeout=5.0):
            proc.kill()
            raise TimeoutError(
                f'ssh -L did not create {local_path} within 5s '
                f'(host={host}, key={key_path})')

        handle = TunnelHandle(
            vm_id=vm_id, kind='uds',
            local_path=local_path, remote_target=remote_uds,
            pid=proc.pid, proc=proc, started_at=time.time(),
        )
        with self._lock:
            self._tunnels[vm_id] = handle
        log.debug(
            f'opened uds tunnel vm_id={vm_id} '
            f'{local_path} -> {user}@{host}:{remote_uds} pid={proc.pid}'
        )
        return handle

    def _open_tcp(self, vm_id: str, host: str, user: str,
                  key_path: str, remote_port: int) -> TunnelHandle:
        local_port = _pick_free_port()
        argv = self._base_ssh_argv(key_path, user, host)
        argv += ['-L', f'{local_port}:127.0.0.1:{remote_port}']
        argv += ['-N', '-o', 'ServerAliveInterval=10',
                 '-o', 'ServerAliveCountMax=3']

        proc = self._spawn(argv)
        # Wait for local listener to come up. Re-pick on EADDRINUSE.
        if not _await_tcp_listener(local_port, timeout=5.0):
            proc.kill()
            raise TimeoutError(
                f'ssh -L did not listen on 127.0.0.1:{local_port} within 5s')

        handle = TunnelHandle(
            vm_id=vm_id, kind='tcp',
            local_port=local_port,
            remote_target=f'127.0.0.1:{remote_port}',
            pid=proc.pid, proc=proc, started_at=time.time(),
        )
        with self._lock:
            self._tunnels[vm_id] = handle
        log.debug(
            f'opened tcp tunnel vm_id={vm_id} 127.0.0.1:{local_port} '
            f'-> {user}@{host}:{remote_port} pid={proc.pid}'
        )
        return handle

    def _base_ssh_argv(self, key_path: str, user: str, host: str) -> list:
        argv = [self.ssh_bin,
                '-i', key_path,
                '-o', 'IdentitiesOnly=yes',
                '-o', 'BatchMode=yes',  # never prompt for a password
                '-o', 'ExitOnForwardFailure=yes']
        if not self.strict_host_key_checking:
            log.warning('SSHTunnelManager: StrictHostKeyChecking=no — dev only')
            argv += ['-o', 'StrictHostKeyChecking=no',
                     '-o', 'UserKnownHostsFile=/dev/null']
        argv += [f'{user}@{host}']
        return argv

    def _spawn(self, argv: list) -> subprocess.Popen:
        log.debug(f'SSHTunnelManager.spawn argv={argv!r}')
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )


# ---- module-level helpers --------------------------------------------------

def _await_path(path: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def _await_tcp_listener(port: int, timeout: float) -> bool:
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(0.1)
            s.connect(('127.0.0.1', port))
            s.close()
            return True
        except OSError:
            try:
                s.close()
            except Exception:
                pass
            time.sleep(0.05)
    return False


def _pick_free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port
