#!/usr/bin/env bash
# Real end-to-end test for the Timestone-flavored Human plugin rewrite.
#
# What this proves
# -----------------
# Stage 1 (Provision):  spawn ONE Linux microVM via Cloud-Hypervisor +
#                       timestone-shim-on-vsock, exactly like
#                       caldera/plugins/range/scripts/smoke-ansible-vsock.sh.
#                       Run a `raw:` Ansible task (uname -a) over the vsock
#                       transport to prove provisioning + transport works.
#
# Stage 2 (Push tarball): tar the pyhuman/ tree to /tmp/<runtime>/pyhuman.tar.gz
#                       and *attempt* to put_file it into the guest. Whether
#                       it lands or not, the tarball itself is real artifact
#                       evidence on disk (bytes match the source tree).
#
# Stage 3 (Reality check):  the rootfs at
#                           timestone/scenarios/images/build-victim-ch.sh
#                           debootstraps with --variant=minbase and only
#                           includes systemd-sysv, linux-image-virtual, kmod,
#                           iproute2, init, locales, ca-certificates, curl,
#                           procps. **NO python3, NO tar, NO pip.** That
#                           means pyhuman cannot run inside the current rootfs
#                           — the BaseWorkflow imports the Python stdlib at
#                           minimum, the Selenium workflows need chromium +
#                           chromedriver + an X server, and even untarring
#                           the artifact is impossible without `tar`.
#
#                           Therefore we take **Path B**: run pyhuman's
#                           control server on the HOST, drive a real workflow
#                           over the real UDS, and verify evidence on disk.
#                           Path A (control server in-guest) is the morning
#                           task — see "FOLLOW-UP" at the bottom.
#
# Stage 4-7 (Drive control_server, evidence, teardown):
#   * Drop a tiny BaseWorkflow subclass `e2e_marker.py` that writes a unique
#     token to a marker file. This is real disk evidence: control_server ran
#     the workflow's action(), stdout came back over the UDS, AND the marker
#     file appeared with the right contents.
#   * Launch `python -m pyhuman.human --mode control --sock <path>` on the
#     host. Wait for the UDS to bind.
#   * Send `_list` and assert it contains both `ListFiles` (existing
#     spawn_shell) and `E2EMarker` (our injected one).
#   * Send `E2EMarker` with args carrying our token. Assert status=ok and
#     stdout contains the token (BaseWorkflow.action prints it).
#   * Read the marker file from disk; assert it contains the token. This is
#     the "observed evidence on disk" step.
#   * Send `_quit`; assert the server process exits 0.
#   * Tear down the microVM.
#
# Exit code: 0 ONLY if every assertion passes.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUMAN_ROOT="$(cd "$HERE/.." && pwd)"
PYHUMAN_DIR="$HUMAN_ROOT/pyhuman"
WORKFLOWS_DIR="$PYHUMAN_DIR/app/workflows"

CALDERA_RANGE="/home/caldera/Desktop/CalderaVENV/caldera/plugins/range"
TIMESTONE_ROOT="/home/caldera/Desktop/TimeStoneVENV/timestone"

CH_BIN="$TIMESTONE_ROOT/vendor/cloud-hypervisor/bin/cloud-hypervisor"
KERNEL="$CALDERA_RANGE/automation/_data/kernels/bzImage-x86_64"
ROOTFS="$TIMESTONE_ROOT/scenarios/images/var/victim-rootfs.ext4"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUNTIME="/tmp/timestone-microvms/human-e2e-$TS"
RESULTS="$RUNTIME/results"
mkdir -p "$RESULTS"

# Token must be unique and easy to grep — embed pid+ts.
TOKEN="HUMAN_E2E_$$_$TS"
MARKER_FILE="$RUNTIME/e2e-marker.txt"
SOCK_PATH="$RUNTIME/pyhuman.sock"
TARBALL="$RUNTIME/pyhuman.tar.gz"
INJECTED_WF="$WORKFLOWS_DIR/e2e_marker.py"

CH_PID=""
SERVER_PID=""

log() { printf '[human-e2e] %s\n' "$*" >&2; }
fail() { printf '[human-e2e] FAIL: %s\n' "$*" >&2; exit 1; }

cleanup() {
    rc=$?
    log "cleanup (rc=$rc)"
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        log "killing leftover control_server pid=$SERVER_PID"
        kill "$SERVER_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ -n "$CH_PID" ]] && kill -0 "$CH_PID" 2>/dev/null; then
        log "killing CH pid=$CH_PID"
        kill "$CH_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$CH_PID" 2>/dev/null || true
    fi
    rm -f "$INJECTED_WF" 2>/dev/null || true
    # Leave $RUNTIME on failure for forensics, scrub on success.
    if [[ $rc -eq 0 ]]; then
        rm -rf "$RUNTIME" 2>/dev/null || true
    else
        log "preserving $RUNTIME for forensics"
    fi
    exit $rc
}
trap cleanup EXIT

# ----------------------------------------------------------------------------
# Stage 0: preflight — make sure all our pieces exist on the host.
# ----------------------------------------------------------------------------
[[ -x "$CH_BIN" ]] || fail "cloud-hypervisor binary missing at $CH_BIN"
[[ -f "$KERNEL" ]] || fail "kernel missing at $KERNEL"
[[ -f "$ROOTFS" ]] || fail "victim rootfs missing at $ROOTFS"
[[ -d "$PYHUMAN_DIR" ]] || fail "pyhuman dir missing at $PYHUMAN_DIR"
[[ -d "$WORKFLOWS_DIR" ]] || fail "workflows dir missing at $WORKFLOWS_DIR"
command -v python3 >/dev/null 2>&1 || fail "python3 not on host PATH"
command -v ansible-playbook >/dev/null 2>&1 || \
    log "warning: ansible-playbook not on PATH — Stage 1 ansible step will be skipped if missing"

log "PYHUMAN_DIR=$PYHUMAN_DIR"
log "RUNTIME=$RUNTIME"
log "TOKEN=$TOKEN"

# ----------------------------------------------------------------------------
# Stage 1: spawn ONE Linux microVM (provision proof).
# Mirrors smoke-ansible-vsock.sh exactly.
# ----------------------------------------------------------------------------
log "stage 1: spawning microVM"
cp --sparse=always "$ROOTFS" "$RUNTIME/rootfs.ext4" || fail "rootfs copy"
CID=4327
"$CH_BIN" \
    --api-socket "$RUNTIME/api.sock" \
    --kernel "$KERNEL" \
    --cmdline 'console=ttyS0 reboot=k panic=1 root=/dev/vda rw' \
    --disk path="$RUNTIME/rootfs.ext4" \
    --cpus boot=1 --memory size=512M \
    --vsock cid=$CID,socket="$RUNTIME/vsock.sock" \
    --serial file="$RUNTIME/serial.log" \
    --console off \
    > "$RUNTIME/ch.stdout" 2>&1 &
CH_PID=$!
log "CH spawned pid=$CH_PID cid=$CID vsock=$RUNTIME/vsock.sock"

# Wait for shim to bind on vsock port 5252.
SHIM_READY=0
for i in $(seq 1 90); do
    if grep -q "listening on vsock port 5252" "$RUNTIME/serial.log" 2>/dev/null; then
        SHIM_READY=1
        log "shim ready at ${i}s"
        break
    fi
    if ! kill -0 "$CH_PID" 2>/dev/null; then
        log "CH died early; serial.log tail:"
        tail -n 40 "$RUNTIME/serial.log" >&2 || true
        fail "cloud-hypervisor exited before shim came up"
    fi
    sleep 1
done
[[ "$SHIM_READY" == "1" ]] || fail "shim never bound vsock port 5252 (see $RUNTIME/serial.log)"

# Run a tiny ansible playbook to prove the transport works end-to-end. This
# is the "provision" evidence — vsock plumbing is real.
if command -v ansible-playbook >/dev/null 2>&1; then
    cat > "$RESULTS/inventory.yml" <<EOF
microvms_linux:
  hosts:
    human-e2e-vm:
      ansible_connection: timestone_microvm
      timestone_os: linux
      timestone_vsock_uds: $RUNTIME/vsock.sock
      timestone_vsock_port: 5252
EOF
    cat > "$RESULTS/play.yml" <<'EOF'
---
- hosts: human-e2e-vm
  gather_facts: false
  tasks:
    - name: prove transport — uname
      raw: uname -a
      register: uname_out
    - debug: msg="uname={{ uname_out.stdout | trim }}"
    - name: prove rootfs gap — which python3 (expected to be empty)
      raw: command -v python3 || echo NO_PYTHON3
      register: py_out
    - debug: msg="python3={{ py_out.stdout | trim }}"
    - name: prove rootfs gap — which tar (expected to be empty)
      raw: command -v tar || echo NO_TAR
      register: tar_out
    - debug: msg="tar={{ tar_out.stdout | trim }}"
EOF
    cat > "$RESULTS/ansible.cfg" <<EOF
[defaults]
connection_plugins = $CALDERA_RANGE/automation/connection_plugins
inventory = $RESULTS/inventory.yml
host_key_checking = False
gathering = explicit
stdout_callback = yaml
EOF
    log "stage 1: running ansible-playbook (transport proof + rootfs gap probe)"
    (cd "$RESULTS" && ANSIBLE_CONFIG="$RESULTS/ansible.cfg" \
        ansible-playbook play.yml 2>&1 | tee "$RESULTS/play.log") \
        || fail "ansible-playbook failed (see $RESULTS/play.log)"
    grep -q "NO_PYTHON3\|NO_TAR" "$RESULTS/play.log" \
        && log "confirmed: rootfs is missing python3 and/or tar — Path A blocked"
else
    log "stage 1: skipping ansible step (ansible-playbook not installed); transport proven by shim bind only"
fi

# ----------------------------------------------------------------------------
# Stage 2: build the pyhuman tarball — real artifact, even though we can't
# untar it inside the guest tonight.
# ----------------------------------------------------------------------------
log "stage 2: tarring pyhuman -> $TARBALL"
tar -czf "$TARBALL" -C "$HUMAN_ROOT" pyhuman \
    || fail "could not build pyhuman tarball"
[[ -s "$TARBALL" ]] || fail "tarball is empty"
log "tarball size: $(stat -c%s "$TARBALL") bytes"
# (Path A would put_file this into the guest. Skipped — see header note.)

# ----------------------------------------------------------------------------
# Stage 3: rootfs reality check is logged above; we are committed to Path B.
# ----------------------------------------------------------------------------
log "stage 3: PATH B chosen (host-side pyhuman) — rootfs gap documented"

# ----------------------------------------------------------------------------
# Stage 4: inject a marker workflow + start control server on host.
# The marker workflow writes $TOKEN to $MARKER_FILE — that is our "evidence
# on disk" after the workflow runs.
# ----------------------------------------------------------------------------
log "stage 4: injecting e2e_marker workflow"
cat > "$INJECTED_WF" <<EOF
"""Auto-generated by test_e2e_real.sh — do not edit, do not commit.

Writes a token to a known path so the e2e harness can prove the control
server actually invoked action() (not just answered the wire protocol).
"""
import os

from app.utility.base_workflow import BaseWorkflow

WORKFLOW_NAME = 'E2EMarker'
WORKFLOW_DESCRIPTION = 'Write an e2e marker file with a known token'

MARKER_PATH = '$MARKER_FILE'
DEFAULT_TOKEN = '$TOKEN'


def load():
    return E2EMarker()


class E2EMarker(BaseWorkflow):

    def __init__(self):
        super().__init__(name=WORKFLOW_NAME, description=WORKFLOW_DESCRIPTION)

    def action(self, extra=None):
        token = DEFAULT_TOKEN
        if isinstance(extra, dict) and extra.get('token'):
            token = str(extra['token'])
        elif isinstance(extra, list) and extra:
            token = str(extra[0])
        os.makedirs(os.path.dirname(MARKER_PATH), exist_ok=True)
        with open(MARKER_PATH, 'w') as fh:
            fh.write(token + '\n')
        # Print so the operator sees it on the wire too.
        print('e2e-marker wrote token={} to {}'.format(token, MARKER_PATH))
EOF

log "stage 4: starting pyhuman control_server on host"
PYTHONPATH="$HUMAN_ROOT" python3 "$PYHUMAN_DIR/human.py" \
    --mode control --sock "$SOCK_PATH" \
    > "$RUNTIME/server.stdout" 2> "$RUNTIME/server.stderr" &
SERVER_PID=$!
log "server pid=$SERVER_PID sock=$SOCK_PATH"

# Wait for UDS to appear and accept connections.
SOCK_READY=0
for i in $(seq 1 100); do
    if [[ -S "$SOCK_PATH" ]]; then
        if python3 -c "import socket,sys
s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(1.0)
try:
    s.connect('$SOCK_PATH'); s.close(); sys.exit(0)
except Exception:
    sys.exit(1)" 2>/dev/null; then
            SOCK_READY=1
            log "control socket ready at ${i}*0.1s"
            break
        fi
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        log "server died; stderr tail:"
        tail -n 40 "$RUNTIME/server.stderr" >&2 || true
        fail "control_server exited before binding socket"
    fi
    sleep 0.1
done
[[ "$SOCK_READY" == "1" ]] || fail "control socket never appeared"

# ----------------------------------------------------------------------------
# Stage 5: drive the control server over the real UDS.
# Uses an inline python3 client — real socket bytes, real JSON-line frames.
# ----------------------------------------------------------------------------
log "stage 5: sending _list"
python3 - "$SOCK_PATH" "$TOKEN" "$MARKER_FILE" <<'PY' \
    > "$RUNTIME/client.stdout" 2> "$RUNTIME/client.stderr"
import json, os, socket, sys

sock_path, token, marker_file = sys.argv[1], sys.argv[2], sys.argv[3]


def call(req, timeout=30.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    try:
        s.sendall((json.dumps(req) + '\n').encode('utf-8'))
        f = s.makefile('rb')
        line = f.readline()
        if not line:
            raise SystemExit('server closed without responding to {!r}'.format(req))
        return json.loads(line.decode('utf-8'))
    finally:
        s.close()


# 5a) _list
resp = call({'id': '1', 'workflow': '_list'})
print('LIST_RESP:', json.dumps(resp))
assert resp.get('id') == '1', 'bad id echo'
assert resp.get('status') == 'ok', 'list not ok: {}'.format(resp)
wfs = resp.get('workflows') or []
assert 'E2EMarker' in wfs, 'E2EMarker not registered (got {})'.format(wfs)
assert 'ListFiles' in wfs, 'ListFiles regression — not registered (got {})'.format(wfs)
print('LIST_OK count={} expected_seen=2'.format(len(wfs)))

# 5b) Run the marker workflow with our token.
resp = call({'id': '2', 'workflow': 'E2EMarker', 'args': {'token': token}},
            timeout=30.0)
print('RUN_RESP:', json.dumps(resp))
assert resp.get('id') == '2', 'bad id echo'
assert resp.get('status') == 'ok', 'run not ok: {}'.format(resp)
assert token in (resp.get('stdout') or ''), \
    'token {!r} missing from stdout {!r}'.format(token, resp.get('stdout'))
assert isinstance(resp.get('duration_ms'), int) and resp['duration_ms'] >= 0, \
    'duration_ms missing/bad'

# 5c) Evidence on disk — marker file should now exist with the token.
assert os.path.exists(marker_file), 'marker file not on disk: {}'.format(marker_file)
with open(marker_file) as fh:
    contents = fh.read().strip()
assert contents == token, \
    'marker file has wrong contents: {!r} vs expected {!r}'.format(contents, token)
print('MARKER_OK file={} bytes={}'.format(marker_file, len(contents)))

# 5d) Also exercise the existing spawn_shell (ListFiles) — proves the wire
# protocol doesn't bind to our injected workflow only.
resp = call({'id': '3', 'workflow': 'ListFiles', 'args': {}}, timeout=30.0)
print('SHELL_RESP:', json.dumps(resp))
assert resp.get('status') == 'ok', 'spawn_shell not ok: {}'.format(resp)

# 5e) _quit
resp = call({'id': '4', 'workflow': '_quit'})
print('QUIT_RESP:', json.dumps(resp))
assert resp.get('status') == 'ok', 'quit not ok: {}'.format(resp)
print('ALL_CLIENT_ASSERTIONS_PASSED')
PY
CLIENT_RC=$?

# Surface client output regardless of pass/fail for diagnosis.
log "stage 5 client stdout:"
sed 's/^/    /' "$RUNTIME/client.stdout" >&2 || true
if [[ -s "$RUNTIME/client.stderr" ]]; then
    log "stage 5 client stderr:"
    sed 's/^/    /' "$RUNTIME/client.stderr" >&2 || true
fi
[[ "$CLIENT_RC" == "0" ]] || fail "client driver failed (rc=$CLIENT_RC)"

# ----------------------------------------------------------------------------
# Stage 7: confirm the server cleanly exited after _quit.
# ----------------------------------------------------------------------------
log "stage 7: waiting for control_server to exit"
for i in $(seq 1 50); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    sleep 0.1
done
if kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "control_server did not exit after _quit"
fi
wait "$SERVER_PID"
SERVER_RC=$?
SERVER_PID=""
log "control_server exit rc=$SERVER_RC"
[[ "$SERVER_RC" == "0" ]] || fail "control_server exited with rc=$SERVER_RC"

log "PASSED — control server drove real UDS, real workflow ran, marker on disk"
log "summary:"
log "  path:           B (pyhuman on HOST; rootfs lacks python3/tar)"
log "  workflow run:   E2EMarker (injected) + ListFiles (existing spawn_shell)"
log "  evidence:       $MARKER_FILE held token=$TOKEN"
log "  microVM:        spawned (cid=$CID), shim handshake on vsock 5252"
log "  tarball:        $TARBALL ($(stat -c%s "$TARBALL" 2>/dev/null || echo '?') bytes)"
log ""
log "FOLLOW-UP for tomorrow (Path A — control server INSIDE the guest):"
log "  build-victim-ch.sh debootstraps with --variant=minbase and only adds:"
log "    systemd-sysv linux-image-virtual kmod iproute2 init locales"
log "    ca-certificates curl procps"
log "  To run pyhuman in-guest we need at minimum: python3 (the workflows"
log "  use the stdlib; that alone enables --mode control + spawn_shell). For"
log "  the desktop workflows (open_email, click_links, create_document) we"
log "  also need: chromium-browser, chromium-chromedriver, an X server (xvfb"
log "  is enough for headed Selenium), python3-selenium, python3-pyautogui,"
log "  and the pyhuman/requirements.txt deps. Two reasonable next steps:"
log "    1. Add 'python3,tar' to the debootstrap include list and rebuild"
log "       the rootfs; re-run this script with a Path A branch that"
log "       put_file's the tarball into /tmp via the timestone-shim, untars"
log "       it, then runs human.py --mode control over a vsock<->UDS bridge."
log "    2. For the Selenium workflows, layer chromium + xvfb on top — that"
log "       is a separate (~250MB) image variant and probably its own PR."
exit 0
