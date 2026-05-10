"""Per-ability test harness for the HID plugin.

Drives a single atomic ability (or composite profile) against a live
microVM, automatically running prerequisite abilities first to put the
guest in the right UI state. Mirrors the pytest fixture pattern but
applied to *guest UI state* rather than Python objects.

Architecture:

    AbilityRunner(host_id, target_ability_id) ------> .run_with_setup()
        .plan()    walks state_requires -> state_providers
        .dispatch_chain()  feeds OperatorMessages over the UDS
        .verify()  pulls framebuffer, runs OCR, evaluates verify: list

State graph data lives in fixtures/state_providers.yml — a map from
canonical state-string to a list of ability IDs/stems that reach it.
For abilities whose YAMLs do not yet declare `state_requires:`, the
fixtures/state_dependencies.yml file overlays the same data
externally so we can develop the harness ahead of the YAML edits.

Reusable bits stolen / adapted from elsewhere in the repo:

* RFB framebuffer fetch — from /tmp/probe_fb.py.
* OperatorMessage materialization — from
  pyhuman.profile_materializer.
* Operator-socket resolution from meta.json — same pattern as
  app/human_api.py:_resolve_operator_socket.

The harness intentionally has zero asyncio: pytest tests block,
sockets are blocking, and the rest of the test suite is unittest /
synchronous. Keeps the failure modes obvious.
"""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import yaml


# ---------------------------------------------------------------------
# Paths.
# ---------------------------------------------------------------------

# /home/caldera/.../human/
HUMAN_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
ATOMIC_DIR = (
    HUMAN_PLUGIN_ROOT / "data" / "abilities"
    / "benign-human-activity" / "atomic"
)
ADVERSARIES_DIR = HUMAN_PLUGIN_ROOT / "data" / "adversaries"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

MICROVM_RUNTIME_BASE = os.environ.get(
    "TIMESTONE_MICROVM_RUNTIME_BASE", "/tmp/timestone-microvms"
)


# ---------------------------------------------------------------------
# Optional deps. The harness degrades gracefully when OCR / numpy are
# missing — verify: blocks that need them are skipped with a clear
# message instead of a hard error.
# ---------------------------------------------------------------------

try:
    import numpy as _np  # type: ignore
    _HAVE_NUMPY = True
except ImportError:
    _np = None
    _HAVE_NUMPY = False

try:
    import pytesseract as _pyt  # type: ignore
    _HAVE_OCR = True
except ImportError:
    _pyt = None
    _HAVE_OCR = False

try:
    from PIL import Image as _PILImage  # type: ignore
    _HAVE_PIL = True
except ImportError:
    _PILImage = None
    _HAVE_PIL = False


# ---------------------------------------------------------------------
# meta.json discovery.
# ---------------------------------------------------------------------


def _resolve_runtime_dir(host_id: str) -> Path:
    """Find the runtime dir for ``host_id`` under MICROVM_RUNTIME_BASE.

    Mirrors human_api._resolve_operator_socket but returns the dir so
    the harness can also hit gpu_daemon.vnc_port without re-walking.
    """
    base = Path(MICROVM_RUNTIME_BASE)
    matches = sorted(base.glob(f"{host_id}-*"))
    exact = base / host_id
    if exact.is_dir():
        matches.append(exact)
    if not matches:
        raise FileNotFoundError(
            f"no microVM runtime dir for host_id={host_id!r} under {base}"
        )
    return matches[0]


def _load_meta(host_id: str) -> dict:
    rd = _resolve_runtime_dir(host_id)
    meta_path = rd / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"meta.json missing at {meta_path}")
    with meta_path.open() as f:
        return json.load(f)


def _resolve_operator_socket(host_id: str) -> str:
    meta = _load_meta(host_id)
    idaemon = meta.get("input_daemon") or {}
    sock = idaemon.get("operator_socket")
    if not sock:
        raise KeyError(
            f"host {host_id!r} has no input_daemon.operator_socket "
            "(GUI session not running)"
        )
    return sock


def _resolve_keyboard_operator_socket(host_id: str) -> str | None:
    meta = _load_meta(host_id)
    kbd = meta.get("keyboard_daemon") or {}
    return kbd.get("operator_socket")


def _resolve_vnc_port(host_id: str) -> int:
    meta = _load_meta(host_id)
    gpu = meta.get("gpu_daemon") or {}
    port = gpu.get("vnc_port")
    if not port:
        raise KeyError(f"host {host_id!r} has no gpu_daemon.vnc_port")
    return int(port)


def _resolve_target_os(host_id: str) -> str:
    try:
        meta = _load_meta(host_id)
    except FileNotFoundError:
        return ""
    raw = meta.get("os") or meta.get("platform") or ""
    s = str(raw).strip().lower()
    if s in ("mac", "macos", "osx", "os-x"):
        return "darwin"
    if s == "win":
        return "windows"
    return s


# ---------------------------------------------------------------------
# Ability index / state graph.
# ---------------------------------------------------------------------


def load_atomic_index() -> dict[str, dict]:
    """ability_id -> ability dict. Keyed by id AND filename stem so the
    state-providers map can use either."""
    idx: dict[str, dict] = {}
    if not ATOMIC_DIR.is_dir():
        return idx
    for path in sorted(ATOMIC_DIR.glob("*.yml")):
        try:
            with path.open() as f:
                entries = yaml.safe_load(f) or []
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("id")
            if aid:
                idx[aid] = entry
            # Also key by filename stem (legacy + new abilities mix).
            idx[path.stem] = entry
    return idx


def load_profiles_index() -> dict[str, dict]:
    """profile_id (uuid OR stem) -> profile dict."""
    idx: dict[str, dict] = {}
    if not ADVERSARIES_DIR.is_dir():
        return idx
    for path in sorted(ADVERSARIES_DIR.glob("*.yml")):
        try:
            with path.open() as f:
                entries = yaml.safe_load(f) or []
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("id")
            if pid:
                idx[pid] = entry
            idx[path.stem] = entry
    return idx


def _load_yaml(path: Path) -> Any:
    with path.open() as f:
        return yaml.safe_load(f)


def load_state_providers() -> dict[str, list[str]]:
    """state-string -> [ability_id, ...] mapping."""
    fp = FIXTURES_DIR / "state_providers.yml"
    if not fp.is_file():
        return {}
    data = _load_yaml(fp) or {}
    out: dict[str, list[str]] = {}
    for k, v in data.items():
        if isinstance(v, list):
            out[k] = [str(x) for x in v]
        elif isinstance(v, str):
            out[k] = [v]
    return out


def load_state_dependencies() -> dict[str, dict]:
    """Overlay file: ability_id -> {state_requires: [...], state_provides: [...], verify: [...]}.

    Lets the harness work even if the source YAMLs don't yet carry the
    new schema fields. The overlay is read-only; the YAMLs remain
    canonical once the legacy translation agent ships its updates.
    """
    fp = FIXTURES_DIR / "state_dependencies.yml"
    if not fp.is_file():
        return {}
    data = _load_yaml(fp) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _overlay_entry(ability_id: str, ability: dict | None,
                   overlay: dict[str, dict]) -> dict:
    """Return the overlay entry for ``ability_id``.

    The atomic-ability index is double-keyed (uuid AND filename stem)
    by ``load_atomic_index``, but the overlay file keys by stem only.
    When a caller passes a uuid, look for an overlay entry that
    matches either the uuid OR the ability's `name` lowercased+slugified
    OR — most useful — any other key in the ambient atomic index that
    points to the same dict.
    """
    o = overlay.get(ability_id)
    if isinstance(o, dict):
        return o
    if ability is not None:
        # Try filename-stem lookups: walk the loaded index and find
        # all keys that resolve to this same ability dict.
        idx = load_atomic_index()
        for k, v in idx.items():
            if v is ability or (isinstance(v, dict) and v.get("id")
                                == ability.get("id")):
                if k != ability_id:
                    o = overlay.get(k)
                    if isinstance(o, dict):
                        return o
    return {}


def ability_state_requires(ability_id: str, ability: dict | None,
                           overlay: dict[str, dict]) -> list[str]:
    """Return state_requires for ``ability_id`` — YAML field first,
    overlay fallback. Empty list = no setup needed."""
    if ability and isinstance(ability.get("state_requires"), list):
        return [str(s) for s in ability["state_requires"]]
    o = _overlay_entry(ability_id, ability, overlay)
    if isinstance(o.get("state_requires"), list):
        return [str(s) for s in o["state_requires"]]
    return []


def ability_state_provides(ability_id: str, ability: dict | None,
                           overlay: dict[str, dict]) -> list[str]:
    if ability and isinstance(ability.get("state_provides"), list):
        return [str(s) for s in ability["state_provides"]]
    o = _overlay_entry(ability_id, ability, overlay)
    if isinstance(o.get("state_provides"), list):
        return [str(s) for s in o["state_provides"]]
    return []


def ability_verify(ability_id: str, ability: dict | None,
                   overlay: dict[str, dict]) -> list[dict]:
    if ability and isinstance(ability.get("verify"), list):
        return list(ability["verify"])
    o = _overlay_entry(ability_id, ability, overlay)
    if isinstance(o.get("verify"), list):
        return list(o["verify"])
    return []


# ---------------------------------------------------------------------
# Planner.
# ---------------------------------------------------------------------


# States the harness assumes are reached after VM boot — no chain
# needed. Lets the planner short-circuit.
_BOOTSTRAP_STATES = {"guest-at-desktop"}


class PlanError(RuntimeError):
    pass


class Planner:
    """Resolves a target ability into an ordered list of ability IDs to
    dispatch. Depth-first; first-provider-wins. Cycle-detecting."""

    def __init__(self, atomic_idx: dict[str, dict],
                 providers: dict[str, list[str]],
                 overlay: dict[str, dict]):
        self.atomic_idx = atomic_idx
        self.providers = providers
        self.overlay = overlay
        # States we *wanted* to provide but couldn't because the
        # concrete ability isn't shipped on this branch yet (the
        # legacy-translation agent's territory). Surfaced in the
        # AbilityRunner.verify() summary so the test report can flag
        # holes without blowing up.
        self.unsatisfied_states: list[str] = []

    def plan(self, target_ability_id: str) -> list[str]:
        chain: list[str] = []
        visited_states: set[str] = set()
        visited_abils: set[str] = set()
        self._resolve_ability(
            target_ability_id, chain, visited_states, visited_abils,
            stack=()
        )
        return chain

    def _resolve_state(self, state: str, chain: list[str],
                       visited_states: set[str],
                       visited_abils: set[str],
                       stack: tuple[str, ...]) -> None:
        if state in _BOOTSTRAP_STATES:
            # Assumed reached after VM boot.
            return
        if state in visited_states:
            return
        if state in stack:
            raise PlanError(
                f"cycle detected resolving state {state!r}; "
                f"stack={stack!r}"
            )
        visited_states.add(state)

        provs = self.providers.get(state) or []
        if not provs:
            raise PlanError(
                f"no provider declared for state {state!r}; "
                "add an entry to fixtures/state_providers.yml"
            )

        # Two flavors of provider entry:
        #   - state name  -> transitive prereq; recurse, do NOT count
        #     it as the concrete provider.
        #   - ability id  -> concrete provider; pick the FIRST one and
        #     dispatch it (after all transitive states above it are
        #     satisfied).
        # This means an entry like:
        #   text-cursor-active:
        #     - guest-at-desktop          # transitive
        #     - launch-app-via-runner     # concrete
        # resolves to: ensure desktop, then run launch-app-via-runner.
        chosen_concrete: str | None = None
        for entry in provs:
            if entry in _BOOTSTRAP_STATES or entry in self.providers:
                # Transitive — recurse but keep looking for the
                # concrete provider in the same list.
                self._resolve_state(
                    entry, chain, visited_states, visited_abils,
                    stack + (state,)
                )
                continue
            # First non-state entry wins as the concrete provider.
            chosen_concrete = entry
            break

        if chosen_concrete is None:
            # All entries were transitive states — the state is
            # implicitly satisfied once they all hold. Common for
            # 'guest-at-desktop' itself.
            return
        if chosen_concrete not in self.atomic_idx:
            # Unknown ability — concrete provider isn't shipped on
            # this branch. Record so the harness can surface the
            # gap in its summary; treat as implicit (no chain entry).
            self.unsatisfied_states.append(state)
            return
        self._resolve_ability(
            chosen_concrete, chain, visited_states, visited_abils,
            stack + (state,)
        )

    def _resolve_ability(self, ability_id: str, chain: list[str],
                         visited_states: set[str],
                         visited_abils: set[str],
                         stack: tuple[str, ...]) -> None:
        if ability_id in visited_abils:
            return
        visited_abils.add(ability_id)
        ability = self.atomic_idx.get(ability_id)
        requires = ability_state_requires(
            ability_id, ability, self.overlay)
        for state in requires:
            self._resolve_state(
                state, chain, visited_states, visited_abils, stack)
        chain.append(ability_id)


# ---------------------------------------------------------------------
# Materialization (delegates to the existing profile_materializer).
# ---------------------------------------------------------------------


def materialize_ability(ability: dict, target_os: str,
                        call_args: dict[str, Any] | None = None
                        ) -> list[dict]:
    """Materialize one atomic ability into OperatorMessages. Reuses
    the production materializer path (single-step profile)."""
    # Lazy import — keeps `pytest --collect-only` working even when
    # the plugin isn't on sys.path yet.
    sys.path.insert(0, str(HUMAN_PLUGIN_ROOT))
    try:
        from pyhuman.profile_materializer import (
            materialize_profile,
        )
    finally:
        # Don't pollute sys.path for the test process beyond what's
        # needed — but leave it; subsequent calls reuse it.
        pass

    aid = ability.get("id") or ability.get("name") or "unnamed"
    pseudo_profile = {
        "id": f"harness-{aid}",
        "name": f"harness wrapper for {aid}",
        "steps": [{"ability": aid, "args": call_args or {}}],
    }
    abilities_index = {aid: ability}
    return materialize_profile(
        pseudo_profile, abilities_index, target_os=target_os,
    )


# ---------------------------------------------------------------------
# Operator-socket dispatcher.
# ---------------------------------------------------------------------


def _send_messages(sock_path: str, messages: Iterable[dict],
                   per_msg_settle_s: float = 0.05) -> int:
    """Open the UDS, write one JSON line per message, close. Returns
    the number of messages sent. Tiny inter-message sleep keeps the
    daemon's pacing happy on slow hosts."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(sock_path)
    try:
        n = 0
        for msg in messages:
            line = json.dumps(msg) + "\n"
            s.sendall(line.encode())
            n += 1
            if per_msg_settle_s > 0:
                time.sleep(per_msg_settle_s)
        return n
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# Framebuffer probe (RFB 3.8). Adapted from /tmp/probe_fb.py.
# ---------------------------------------------------------------------


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise RuntimeError(
                f"connection closed; got {len(out)}/{n} bytes")
        out.extend(chunk)
    return bytes(out)


def fetch_framebuffer(host: str, port: int,
                      timeout: float = 10.0
                      ) -> tuple[bytes, int, int, int]:
    """Tiny synchronous RFB 3.8 client: handshake, request whole
    frame in Raw encoding. Returns (pixels, width, height, bpp_bytes).
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        server_version = _recv_exact(sock, 12)
        if not server_version.startswith(b"RFB "):
            raise RuntimeError(
                f"unexpected server version: {server_version!r}")
        sock.sendall(b"RFB 003.008\n")

        n_types = struct.unpack(">B", _recv_exact(sock, 1))[0]
        if n_types == 0:
            reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
            reason = _recv_exact(sock, reason_len)
            raise RuntimeError(f"security failure: {reason!r}")
        types = _recv_exact(sock, n_types)
        if 1 not in types:
            raise RuntimeError(
                f"server requires unsupported types {list(types)}")
        sock.sendall(b"\x01")
        sec_result = struct.unpack(">I", _recv_exact(sock, 4))[0]
        if sec_result != 0:
            raise RuntimeError(f"security result failed: {sec_result}")

        sock.sendall(b"\x01")  # ClientInit shared=1

        server_init = _recv_exact(sock, 24)
        width, height, bpp, depth, big_endian, true_color = struct.unpack(
            ">HHBBBB", server_init[:8])
        name_len = struct.unpack(">I", server_init[20:24])[0]
        _recv_exact(sock, name_len)

        bpp_bytes = bpp // 8

        # SetEncodings: only Raw (0)
        sock.sendall(struct.pack(">BBHi", 2, 0, 1, 0))

        # FramebufferUpdateRequest (full, incremental=0)
        sock.sendall(
            struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))

        msg_type = _recv_exact(sock, 1)
        if msg_type != b"\x00":
            raise RuntimeError(
                f"expected FramebufferUpdate, got {msg_type!r}")
        _recv_exact(sock, 1)  # padding
        n_rects = struct.unpack(">H", _recv_exact(sock, 2))[0]

        pixels = bytearray()
        for _ in range(n_rects):
            rect_hdr = _recv_exact(sock, 12)
            rx, ry, rw, rh, encoding = struct.unpack(">HHHHI", rect_hdr)
            if encoding != 0:
                raise RuntimeError(f"non-raw encoding {encoding}")
            pixels.extend(_recv_exact(sock, rw * rh * bpp_bytes))

        return bytes(pixels), width, height, bpp_bytes
    finally:
        sock.close()


# ---------------------------------------------------------------------
# Verification helpers.
# ---------------------------------------------------------------------


def pixel_change_pct(before: bytes, after: bytes) -> float:
    """Percentage of bytes that differ between two equal-length frames.

    Falls back to a manual byte-by-byte counter when numpy is missing.
    Length mismatch (e.g. resolution change between captures) is
    treated as 100% changed — the conservative answer.
    """
    if not before or not after:
        return 0.0
    if len(before) != len(after):
        return 100.0
    if _HAVE_NUMPY:
        a = _np.frombuffer(before, dtype=_np.uint8)
        b = _np.frombuffer(after, dtype=_np.uint8)
        diff = int(_np.count_nonzero(a != b))
        return diff / len(before) * 100.0
    diff = sum(1 for x, y in zip(before, after) if x != y)
    return diff / len(before) * 100.0


def _frame_to_pil(pixels: bytes, w: int, h: int, bpp: int):
    """Convert the raw RFB pixel buffer to a PIL.Image.

    The vhost-user-gpu daemon reports BGRX (32 bpp, little-endian) by
    default. We slice the alpha byte and swap BGR->RGB so OCR sees
    legible text.
    """
    if not _HAVE_PIL:
        return None
    if bpp == 4:
        # BGRX -> RGB
        ba = bytearray(pixels)
        # Build raw RGB without numpy if needed.
        if _HAVE_NUMPY:
            arr = _np.frombuffer(pixels, dtype=_np.uint8).reshape(
                (h, w, 4))
            rgb = arr[:, :, [2, 1, 0]].tobytes()
        else:
            rgb = bytearray(w * h * 3)
            for i in range(w * h):
                # BGRA in RFB is little-endian -> bytes are [B,G,R,X].
                rgb[3*i] = ba[4*i + 2]
                rgb[3*i + 1] = ba[4*i + 1]
                rgb[3*i + 2] = ba[4*i]
            rgb = bytes(rgb)
        return _PILImage.frombytes("RGB", (w, h), rgb)
    if bpp == 3:
        # Already 24-bit BGR.
        ba = pixels
        if _HAVE_NUMPY:
            arr = _np.frombuffer(ba, dtype=_np.uint8).reshape(
                (h, w, 3))
            rgb = arr[:, :, [2, 1, 0]].tobytes()
        else:
            rgb = bytearray(w * h * 3)
            for i in range(w * h):
                rgb[3*i] = ba[3*i + 2]
                rgb[3*i + 1] = ba[3*i + 1]
                rgb[3*i + 2] = ba[3*i]
            rgb = bytes(rgb)
        return _PILImage.frombytes("RGB", (w, h), rgb)
    return None


def ocr_text(pixels: bytes, w: int, h: int, bpp: int) -> str:
    """Run pytesseract over a captured framebuffer. Returns "" if any
    optional dep is missing."""
    if not (_HAVE_OCR and _HAVE_PIL):
        return ""
    img = _frame_to_pil(pixels, w, h, bpp)
    if img is None:
        return ""
    try:
        return _pyt.image_to_string(img) or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------
# AbilityRunner — the per-ability harness.
# ---------------------------------------------------------------------


class AbilityRunner:
    """Drive a single ability against the live VM with full setup chain.

    Usage:
        runner = AbilityRunner('windows10-victim', 'type-text',
                               call_args={'text': 'hello'})
        result = runner.run_with_setup()
        runner.verify()
        # -> raises AssertionError on verify failure.
    """

    def __init__(self,
                 host_id: str,
                 ability_id: str,
                 call_args: dict[str, Any] | None = None,
                 settle_ms: int = 750,
                 atomic_idx: dict[str, dict] | None = None,
                 providers: dict[str, list[str]] | None = None,
                 overlay: dict[str, dict] | None = None):
        self.host_id = host_id
        self.ability_id = ability_id
        self.call_args = call_args or {}
        self.settle_ms = settle_ms
        self.atomic_idx = atomic_idx if atomic_idx is not None \
            else load_atomic_index()
        self.providers = providers if providers is not None \
            else load_state_providers()
        self.overlay = overlay if overlay is not None \
            else load_state_dependencies()

        # Filled in as the harness progresses.
        self.target_os: str = ""
        self.plan_chain: list[str] = []
        self.pre_pixels: bytes = b""
        self.post_pixels: bytes = b""
        self.frame_geometry: tuple[int, int, int] = (0, 0, 0)
        self.dispatched_count: int = 0
        self.unsatisfied_states: list[str] = []

    # ------------------------------------------------------------------
    # Planning.
    # ------------------------------------------------------------------

    def plan(self) -> list[str]:
        """Resolve target -> [ability_id, ...] in execution order. The
        target is appended last."""
        planner = Planner(self.atomic_idx, self.providers, self.overlay)
        self.plan_chain = planner.plan(self.ability_id)
        # Capture any states the planner couldn't satisfy (concrete
        # provider missing on this branch) so the test can report.
        self.unsatisfied_states = list(planner.unsatisfied_states)
        return self.plan_chain

    # ------------------------------------------------------------------
    # State capture.
    # ------------------------------------------------------------------

    def pre_state(self) -> bytes:
        """Capture framebuffer before dispatch. Sets self.pre_pixels."""
        port = _resolve_vnc_port(self.host_id)
        pixels, w, h, bpp = fetch_framebuffer("127.0.0.1", port)
        self.pre_pixels = pixels
        self.frame_geometry = (w, h, bpp)
        return pixels

    def post_state(self) -> bytes:
        """Capture framebuffer after dispatch + settle. Sets
        self.post_pixels."""
        # Daemon paces actions via the queue but the *guest* still has
        # to repaint. Sleep for the configured settle window so OCR /
        # pixel-diff has something stable to look at.
        time.sleep(self.settle_ms / 1000.0)
        port = _resolve_vnc_port(self.host_id)
        pixels, w, h, bpp = fetch_framebuffer("127.0.0.1", port)
        self.post_pixels = pixels
        self.frame_geometry = (w, h, bpp)
        return pixels

    # ------------------------------------------------------------------
    # Dispatch.
    # ------------------------------------------------------------------

    def _materialize_chain(self) -> list[dict]:
        """Build the full OperatorMessage stream: setup chain +
        target. Each ability is materialized independently and
        concatenated."""
        if not self.plan_chain:
            self.plan()
        os_name = self.target_os or _resolve_target_os(self.host_id)
        self.target_os = os_name

        full: list[dict] = []
        for aid in self.plan_chain:
            ability = self.atomic_idx.get(aid)
            if ability is None:
                # Tolerate "implicit" abilities that the planner pulled
                # in via providers but that have no YAML — e.g. the
                # autologon stub. Just skip.
                continue
            args = self.call_args if aid == self.ability_id else {}
            try:
                msgs = materialize_ability(
                    ability, target_os=os_name, call_args=args)
            except Exception as e:
                raise RuntimeError(
                    f"materialize failed for {aid}: {e}") from e
            full.extend(msgs)
        return full

    def dispatch_chain(self) -> int:
        """Send the materialized stream over the operator UDS. Returns
        the count of messages sent. Sets self.dispatched_count."""
        sock_path = _resolve_operator_socket(self.host_id)
        msgs = self._materialize_chain()
        n = _send_messages(sock_path, msgs)
        self.dispatched_count = n
        return n

    # ------------------------------------------------------------------
    # Top-level driver.
    # ------------------------------------------------------------------

    def run_with_setup(self) -> "AbilityRunner":
        """Plan -> capture pre -> dispatch -> capture post.

        Does NOT call verify(); the test calls that explicitly so it
        can choose to inspect the runner's state on failure (e.g. dump
        post_pixels for debugging).
        """
        self.plan()
        try:
            self.pre_state()
        except Exception:
            # If the GPU daemon isn't reachable we still try the
            # dispatch — the test will fail on verify. Some abilities
            # only declare dispatch-success as their bar.
            self.pre_pixels = b""
        self.dispatch_chain()
        try:
            self.post_state()
        except Exception:
            self.post_pixels = b""
        return self

    # ------------------------------------------------------------------
    # Verification.
    # ------------------------------------------------------------------

    def verify(self) -> dict:
        """Evaluate the target ability's verify: list against captured
        post-state. Returns a dict summary; raises AssertionError on
        first failed assertion.

        Summary keys:
            ok: bool
            checks: [{kind, ok, detail}, ...]
            pixel_change_pct: float
            ocr_excerpt: str  (first 160 chars of OCR for debug)
        """
        ability = self.atomic_idx.get(self.ability_id)
        verify_specs = ability_verify(
            self.ability_id, ability, self.overlay)

        pct = pixel_change_pct(self.pre_pixels, self.post_pixels) \
            if (self.pre_pixels and self.post_pixels) else 0.0

        ocr = ""
        # Only run OCR if any check needs it — saves several seconds.
        needs_ocr = any(
            isinstance(c, dict) and (
                "ocr_contains" in c or "ocr_contains_any" in c)
            for c in verify_specs
        )
        if needs_ocr and self.post_pixels:
            w, h, bpp = self.frame_geometry
            ocr = ocr_text(self.post_pixels, w, h, bpp).lower()

        checks: list[dict] = []
        for spec in verify_specs:
            if not isinstance(spec, dict):
                continue
            for kind, expected in spec.items():
                ok, detail = self._evaluate_check(
                    kind, expected, pct, ocr)
                checks.append({
                    "kind": kind, "expected": expected,
                    "ok": ok, "detail": detail,
                })

        summary = {
            "ok": all(c["ok"] for c in checks) if checks else True,
            "checks": checks,
            "pixel_change_pct": pct,
            "ocr_excerpt": ocr[:160],
            "dispatched_count": self.dispatched_count,
            "plan_chain": list(self.plan_chain),
            "unsatisfied_states": list(self.unsatisfied_states),
        }
        if not summary["ok"]:
            failed = [c for c in checks if not c["ok"]]
            raise AssertionError(
                f"verify failed for {self.ability_id}: "
                f"{failed!r}; pct={pct:.1f}; "
                f"ocr_excerpt={ocr[:80]!r}"
            )
        return summary

    @staticmethod
    def _evaluate_check(kind: str, expected: Any,
                        pct: float, ocr: str
                        ) -> tuple[bool, str]:
        if kind == "pixel_change_pct_min":
            ok = pct >= float(expected)
            return ok, f"observed={pct:.2f}%"
        if kind == "pixel_change_pct_max":
            ok = pct <= float(expected)
            return ok, f"observed={pct:.2f}%"
        if kind == "ocr_contains":
            needle = str(expected).lower()
            if not ocr:
                return False, "OCR unavailable or empty"
            return needle in ocr, f"needle={needle!r}"
        if kind == "ocr_contains_any":
            if not isinstance(expected, list):
                return False, "ocr_contains_any expects a list"
            if not ocr:
                return False, "OCR unavailable or empty"
            for needle in expected:
                if str(needle).lower() in ocr:
                    return True, f"matched={needle!r}"
            return False, f"no candidate matched: {expected!r}"
        # Unknown verify kind — tolerate but mark not-ok so authors
        # notice typos.
        return False, f"unknown verify kind {kind!r}"


# ---------------------------------------------------------------------
# Discovery helpers (used by test_each_ability.py).
# ---------------------------------------------------------------------


def discover_ability_ids() -> list[str]:
    """Stable-sorted list of atomic-ability IDs. Filename-stem keys
    are filtered out so each ability appears once per its `id:`."""
    idx = load_atomic_index()
    seen: set[str] = set()
    out: list[str] = []
    for aid, ability in idx.items():
        canonical = ability.get("id") or aid
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    return sorted(out)


def discover_profile_ids() -> list[str]:
    idx = load_profiles_index()
    seen: set[str] = set()
    out: list[str] = []
    for pid, profile in idx.items():
        canonical = profile.get("id") or pid
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    return sorted(out)
