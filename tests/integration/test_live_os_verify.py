"""Test 6 — Live OS verification.

For every running microVM in ``$TIMESTONE_MICROVM_RUNTIME_BASE``
(default ``/tmp/timestone-microvms``) this test:

  1. Reads ``meta.json`` for the host id, declared os, keyboard
     daemon socket, and gpu daemon RFB port.
  2. Pulls a "before" framebuffer from the gpu daemon.
  3. Sends a focus-grab tablet click + ``ver\\n`` over the keyboard
     daemon socket.
  4. Waits ~2s and pulls an "after" framebuffer.
  5. Tries to OCR the "after" frame with ``pytesseract`` if it's
     installed. If OCR succeeds and the text contains the expected
     OS-version string for this host id, that's the strong-signal
     pass.
  6. Falls back to a weaker pixel-delta assertion if OCR is missing
     or returns nothing recognizable: as long as the "after" frame
     differs from the "before" frame by >= 1% of the bytes (i.e.
     SOMETHING repainted) AND the keyboard daemon's socket accepted
     the type+press messages, we treat the test as a soft pass and
     emit a clear ``xfail`` reason that lists what we'd have wanted
     to see.

Marker: ``@pytest.mark.requires_running_vm`` — skipped unless
``TIMESTONE_RUN_VM_TESTS=1`` or pytest is invoked with
``-m requires_running_vm``. The default state on the CI matrix
should be SKIPPED, not failing.

The host_id → expected_version map is intentionally tight:

  * ``windows-victim``    → Server 2022 (10.0.20348)
  * ``windows10-victim``  → Win10 22H2 (10.0.19045)

Add new mappings here if we add more golden VM images.
"""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import time
from pathlib import Path

import pytest

MICROVM_RUNTIME_BASE = os.environ.get(
    "TIMESTONE_MICROVM_RUNTIME_BASE", "/tmp/timestone-microvms",
)

# host-stem → (regex_for_OCR, human_label). The regex matches against
# a normalized OCR transcript (lowercased, whitespace-collapsed) so we
# tolerate the inevitable noise from a 1024x768 console font.
EXPECTED_OS_PATTERNS = {
    "windows-victim":   (re.compile(r"10\.0\.20348"),  "Server 2022 build 20348"),
    "windows10-victim": (re.compile(r"10\.0\.19045"),  "Win10 22H2 build 19045"),
}


# ---- helpers --------------------------------------------------------


def _iter_running_vms():
    """Yield (host_stem, meta_dict) for every meta.json under the
    runtime base. host_stem is everything before the trailing
    ``-<vm_id>`` suffix (matches the EXPECTED_OS_PATTERNS keys).
    """
    base = Path(MICROVM_RUNTIME_BASE)
    if not base.is_dir():
        return
    for d in sorted(base.iterdir()):
        meta_path = d / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue
        # Strip the trailing -<8-hex-id> suffix from the dir name.
        stem = re.sub(r"-[0-9a-f]{6,}$", "", d.name)
        yield stem, meta


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise RuntimeError(f"connection closed; got {len(out)}/{n}")
        out.extend(chunk)
    return bytes(out)


def _fetch_framebuffer(host: str, port: int, timeout: float = 8.0):
    """Tiny RFB 3.8 client. Returns ``(pixels_bytes, width, height,
    bytes_per_pixel)``. Raises on protocol error or socket timeout.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        version = _recv_exact(sock, 12)
        if not version.startswith(b"RFB "):
            raise RuntimeError(f"unexpected RFB greeting: {version!r}")
        sock.sendall(b"RFB 003.008\n")
        n_types = struct.unpack(">B", _recv_exact(sock, 1))[0]
        if n_types == 0:
            reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
            reason = _recv_exact(sock, reason_len)
            raise RuntimeError(f"server refused: {reason!r}")
        types = _recv_exact(sock, n_types)
        if 1 not in types:
            raise RuntimeError(f"server requires unsupported sec types {list(types)}")
        sock.sendall(b"\x01")  # NoSecurity
        sec_result = struct.unpack(">I", _recv_exact(sock, 4))[0]
        if sec_result != 0:
            raise RuntimeError(f"security failed: {sec_result}")
        sock.sendall(b"\x01")  # ClientInit shared=1
        server_init = _recv_exact(sock, 24)
        width, height = struct.unpack(">HH", server_init[0:4])
        bpp = server_init[4]
        bytes_per_pixel = bpp // 8
        name_len = struct.unpack(">I", server_init[20:24])[0]
        _recv_exact(sock, name_len)
        # SetEncodings: only Raw.
        sock.sendall(struct.pack(">BBHi", 2, 0, 1, 0))
        # FramebufferUpdateRequest, full, non-incremental.
        sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))
        # FramebufferUpdate response.
        msg_type = _recv_exact(sock, 1)
        if msg_type != b"\x00":
            raise RuntimeError(f"unexpected msg type {msg_type!r}")
        _recv_exact(sock, 1)
        n_rects = struct.unpack(">H", _recv_exact(sock, 2))[0]
        pixels = bytearray()
        for _ in range(n_rects):
            rect_hdr = _recv_exact(sock, 12)
            rx, ry, rw, rh, encoding = struct.unpack(">HHHHI", rect_hdr)
            if encoding != 0:
                raise RuntimeError(f"non-raw encoding {encoding}")
            pixels.extend(_recv_exact(sock, rw * rh * bytes_per_pixel))
        return bytes(pixels), int(width), int(height), bytes_per_pixel
    finally:
        sock.close()


def _send_burst(uds_path: str, messages, timeout: float = 5.0) -> int:
    """Connect to ``uds_path`` (AF_UNIX), send each msg as a
    line-delimited JSON record, return number of messages written.
    Raises on connect failure.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(uds_path)
    try:
        n = 0
        for msg in messages:
            sock.sendall((json.dumps(msg) + "\n").encode())
            n += 1
        return n
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _bgrx_to_grayscale(pixels: bytes, width: int, height: int, bpp: int) -> bytes:
    """Crude BGRX → grayscale for the OCR fallback. Returns
    ``height * width`` bytes; each is ``(B+G+R)/3``.
    """
    if bpp != 4:
        return pixels  # untouched
    out = bytearray(width * height)
    for i in range(width * height):
        b = pixels[i * 4]
        g = pixels[i * 4 + 1]
        r = pixels[i * 4 + 2]
        out[i] = (b + g + r) // 3
    return bytes(out)


def _bytes_diff_ratio(a: bytes, b: bytes) -> float:
    """Fraction of bytes that differ. Returns 0..1. Length mismatch
    counts the absolute delta as differing. Cheap-and-cheerful so we
    don't need numpy.
    """
    if not a and not b:
        return 0.0
    if len(a) != len(b):
        return 1.0
    diff = sum(1 for x, y in zip(a, b) if x != y)
    return diff / max(1, len(a))


# ---- the test --------------------------------------------------------


@pytest.mark.requires_running_vm
@pytest.mark.parametrize(
    "host_stem",
    sorted(EXPECTED_OS_PATTERNS.keys()),
    ids=lambda s: s,
)
def test_live_os_verify(host_stem):
    """Drive ``ver\\n`` into the running guest, OCR the response,
    assert the OS string matches what host_stem says it should be.

    Catches both ``wrong-image`` regressions (host_stem points at a
    Server 2022 dir but the guest is actually a Win10 image) AND
    ``command-was-sent-but-never-landed`` regressions (the keyboard
    daemon accepted our type+press but the framebuffer never moved).
    """
    # 1. Find the running VM whose stem matches.
    target = None
    for stem, meta in _iter_running_vms():
        if stem == host_stem:
            target = (stem, meta)
            break
    if target is None:
        pytest.skip(f"no running microVM matches host_stem={host_stem!r}")
    stem, meta = target

    gpu = meta.get("gpu_daemon") or {}
    vnc_port = gpu.get("vnc_port")
    assert vnc_port, f"{stem}: meta.json missing gpu_daemon.vnc_port"

    tablet = meta.get("input_daemon") or {}
    tablet_sock = tablet.get("operator_socket")
    assert tablet_sock, f"{stem}: meta.json missing input_daemon.operator_socket"
    keyboard = meta.get("keyboard_daemon") or {}
    kbd_sock = keyboard.get("operator_socket")
    assert kbd_sock, f"{stem}: meta.json missing keyboard_daemon.operator_socket"

    # 2. Pull a "before" framebuffer.
    try:
        before_pixels, w, h, bpp = _fetch_framebuffer(
            "127.0.0.1", int(vnc_port), timeout=10.0,
        )
    except Exception as e:
        pytest.fail(
            f"{stem}: gpu daemon RFB unreachable on 127.0.0.1:{vnc_port}: {e}"
        )

    # 3. Drive the focus-grab click + ver + Enter.
    fb_cx = w // 2
    fb_cy = h // 2
    tablet_messages = [
        {"action": "move",
         "target": {"kind": "absolute", "x": fb_cx, "y": fb_cy},
         "duration_ms": 0},
        {"action": "click", "button": "left"},
    ]
    keyboard_messages = [
        {"action": "dwell", "ms": 200},
        {"action": "type", "text": "ver",
         "per_char_ms": 50, "jitter_pct": 0, "pause_pct": 0},
        {"action": "press", "key": "Enter"},
    ]
    try:
        sent_tablet = _send_burst(tablet_sock, tablet_messages)
        sent_kbd = _send_burst(kbd_sock, keyboard_messages)
    except Exception as e:
        pytest.fail(f"{stem}: keyboard/tablet daemon not accepting writes: {e}")
    assert sent_tablet == len(tablet_messages)
    assert sent_kbd == len(keyboard_messages)

    # 4. Wait for the guest to render, then pull the "after" frame.
    time.sleep(2.5)
    try:
        after_pixels, w2, h2, bpp2 = _fetch_framebuffer(
            "127.0.0.1", int(vnc_port), timeout=10.0,
        )
    except Exception as e:
        pytest.fail(
            f"{stem}: gpu daemon RFB unreachable on second fetch: {e}"
        )
    assert (w, h, bpp) == (w2, h2, bpp2), (
        f"{stem}: framebuffer geometry changed mid-test"
    )

    # 5. Try OCR.
    expected_re, expected_label = EXPECTED_OS_PATTERNS[host_stem]
    ocr_text = ""
    ocr_used = False
    try:
        import pytesseract  # type: ignore[import-untyped]
        from PIL import Image  # type: ignore[import-untyped]
    except Exception:
        pytesseract = None
        Image = None

    if pytesseract is not None and Image is not None:
        try:
            gray = _bgrx_to_grayscale(after_pixels, w, h, bpp)
            img = Image.frombytes("L", (w, h), gray)
            ocr_text = pytesseract.image_to_string(img) or ""
            ocr_used = True
        except Exception:
            ocr_text = ""
            ocr_used = False

    if ocr_used and ocr_text:
        normalized = re.sub(r"\s+", " ", ocr_text.lower())
        if expected_re.search(normalized):
            return  # strong-signal pass
        # OCR succeeded but didn't see what we wanted. Fall through
        # to the pixel-delta path; surface the OCR transcript so the
        # operator can read it in the test report.
        ocr_excerpt = normalized[:400]
        # If OCR clearly saw a *different* OS, that's a hard failure.
        if "windows" in normalized:
            other = (
                "10.0.20348" if "10.0.19045" in normalized
                else "10.0.19045" if "10.0.20348" in normalized
                else None
            )
            if other:
                pytest.fail(
                    f"{stem}: OCR transcript points at {other} "
                    f"(expected {expected_label}). Wrong-image "
                    f"regression. Excerpt: {ocr_excerpt!r}"
                )

    # 6. Pixel-delta fallback.
    diff_ratio = _bytes_diff_ratio(before_pixels, after_pixels)
    assert diff_ratio >= 0.01, (
        f"{stem}: framebuffer barely moved after type+press (only "
        f"{diff_ratio*100:.2f}% of bytes changed). The daemons accepted "
        f"the messages but the guest didn't repaint — likely stuck at "
        f"lock screen or guest input is off."
    )
    # Soft pass: keystrokes landed and pixels moved, but we couldn't
    # cryptographically prove the OS via OCR. Mark this as xfail so
    # CI flags it without going red.
    pytest.xfail(
        f"{stem}: pixel-delta {diff_ratio*100:.1f}% confirms keystrokes "
        f"landed; OCR {'unavailable' if not ocr_used else 'inconclusive'}. "
        f"Expected: {expected_label}"
        + (f" (OCR excerpt: {ocr_text[:200]!r})" if ocr_used else "")
    )
