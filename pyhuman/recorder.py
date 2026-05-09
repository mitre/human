"""Fast RFB framebuffer recorder for vhost-user-gpu-2d.

Connects to a host:port RFB 3.8 server (NoSecurity, Raw encoding), pulls
full-screen FramebufferUpdate frames in a background thread, and pipes
the BGRX pixel data straight into a long-running ffmpeg subprocess that
encodes a real-time MP4 (libx264, ultrafast). No intermediate PNG/PPM
files; no per-pixel Python loops.

Public API:

    rec = RfbRecorder("127.0.0.1", 5900, Path("/tmp/out.mp4"), fps=10)
    rec.start()
    ... do other things ...
    out_path = rec.stop()

The capture thread targets a steady fps. If a network read is slow, the
next sleep shrinks (or is skipped) so the average rate stays on target;
if reads are fast, we sleep to avoid hammering the server.

Pixel conversion is a single vectorized numpy op:

    rgb = np.frombuffer(data, np.uint8).reshape(h, w, 4)[:, :, [2, 1, 0]]

…but we actually skip that and feed BGRX straight to ffmpeg via the
`bgr0` pixel-format flag, which is even cheaper. The numpy reshape is
still done so partial-rect updates can be composited into a persistent
framebuffer (the daemon currently sends the whole screen each time, but
the spec allows partial rects).
"""

from __future__ import annotations

import logging
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

RFB_VERSION = b"RFB 003.008\n"
_HANDSHAKE_TIMEOUT_S = 10.0
_RECV_TIMEOUT_S = 30.0


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes or raise."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(f"RFB peer closed after {n - remaining}/{n} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _rfb_handshake(sock: socket.socket) -> tuple[int, int]:
    """Run RFB 3.8 handshake (NoSecurity, shared=1). Return (width, height)."""
    server_v = _recv_exact(sock, 12)
    if not server_v.startswith(b"RFB 003."):
        raise RuntimeError(f"Bad RFB server version banner: {server_v!r}")
    sock.sendall(RFB_VERSION)

    n_sec = _recv_exact(sock, 1)[0]
    if n_sec == 0:
        rl = struct.unpack("!I", _recv_exact(sock, 4))[0]
        reason = _recv_exact(sock, rl)
        raise RuntimeError(f"RFB server refused connection: {reason!r}")
    types = _recv_exact(sock, n_sec)
    if 1 not in types:  # 1 = NoSecurity
        raise RuntimeError(f"RFB server does not offer NoSecurity: types={list(types)}")
    sock.sendall(b"\x01")

    sec_result = struct.unpack("!I", _recv_exact(sock, 4))[0]
    if sec_result != 0:
        raise RuntimeError(f"RFB security handshake failed: result={sec_result}")

    sock.sendall(b"\x01")  # ClientInit, shared=1

    si = _recv_exact(sock, 24)
    width, height = struct.unpack("!HH", si[:4])
    name_len = struct.unpack("!I", si[20:24])[0]
    if name_len:
        _recv_exact(sock, name_len)
    return width, height


def _request_update(sock: socket.socket, w: int, h: int, incremental: int = 0) -> None:
    sock.sendall(struct.pack("!BBHHHH", 3, incremental, 0, 0, w, h))


def _read_one_update(sock: socket.socket, fb: np.ndarray) -> bool:
    """Read exactly one server message and, if it's a FramebufferUpdate,
    composite all rects into ``fb`` (BGRX uint8, shape=(h, w, 4)).

    Returns True if the framebuffer changed (i.e. it was an update with
    >0 rects), False if it was a non-update message we silently drop.
    """
    hdr = _recv_exact(sock, 4)
    msg_type = hdr[0]
    if msg_type == 0:
        n_rects = struct.unpack("!H", hdr[2:4])[0]
        if n_rects == 0:
            return False
        for _ in range(n_rects):
            rh = _recv_exact(sock, 12)
            rx, ry, rw, rrh, encoding = struct.unpack("!HHHHi", rh)
            if encoding != 0:
                raise RuntimeError(f"unsupported RFB encoding {encoding}; need Raw (0)")
            n_bytes = rw * rrh * 4
            data = _recv_exact(sock, n_bytes)
            # Vectorized splat into the persistent framebuffer.
            patch = np.frombuffer(data, dtype=np.uint8).reshape(rrh, rw, 4)
            fb[ry:ry + rrh, rx:rx + rw, :] = patch
        return True
    elif msg_type == 2:  # Bell
        return False
    elif msg_type == 3:  # ServerCutText
        ct = _recv_exact(sock, 7)
        n = struct.unpack("!I", ct[3:7])[0]
        if n:
            _recv_exact(sock, n)
        return False
    else:
        raise RuntimeError(f"unknown RFB server message type {msg_type}")


class RfbRecorder:
    """Record an RFB framebuffer to MP4 in a background thread."""

    def __init__(
        self,
        host: str,
        port: int,
        output_mp4: Path,
        fps: int = 10,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.output_mp4 = Path(output_mp4)
        self.fps = int(fps)
        if self.fps <= 0:
            raise ValueError(f"fps must be > 0, got {fps}")

        self._sock: Optional[socket.socket] = None
        self._ffmpeg: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._fb: Optional[np.ndarray] = None
        self._width: int = 0
        self._height: int = 0
        self._frames_written: int = 0
        self._capture_error: Optional[BaseException] = None
        self._started = False

    # ---- public API ------------------------------------------------------

    def start(self) -> None:
        """Connect, handshake, spawn ffmpeg + capture thread. Blocks only
        for the handshake (sub-second). Raises on connection or ffmpeg
        startup failure; the recorder is left in a clean state."""
        if self._started:
            raise RuntimeError("RfbRecorder.start() called twice")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_HANDSHAKE_TIMEOUT_S)
        try:
            sock.connect((self.host, self.port))
            w, h = _rfb_handshake(sock)
        except Exception:
            sock.close()
            raise
        sock.settimeout(_RECV_TIMEOUT_S)
        self._sock = sock
        self._width, self._height = w, h
        self._fb = np.zeros((h, w, 4), dtype=np.uint8)

        self.output_mp4.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-y",
            "-f", "rawvideo",
            "-pixel_format", "bgr0",
            "-video_size", f"{w}x{h}",
            "-framerate", str(self.fps),
            "-i", "-",
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            str(self.output_mp4),
        ]
        try:
            self._ffmpeg = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception:
            sock.close()
            self._sock = None
            raise

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"RfbRecorder({self.host}:{self.port})",
            daemon=True,
        )
        self._started = True
        self._thread.start()
        log.info(
            "RfbRecorder started: %s:%d -> %s, %dx%d @ %d fps",
            self.host, self.port, self.output_mp4, w, h, self.fps,
        )

    def stop(self, join_timeout: float = 5.0) -> Path:
        """Signal the capture thread to stop, finalize the MP4, return
        its path. Always returns the (possibly partial) output path; only
        raises if ffmpeg's exit code is non-zero AND no frames were
        written, so the caller can salvage partial captures."""
        if not self._started:
            raise RuntimeError("RfbRecorder.stop() called before start()")

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            if self._thread.is_alive():
                log.warning("RfbRecorder capture thread did not exit within %.1fs", join_timeout)

        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        if self._ffmpeg is not None:
            try:
                if self._ffmpeg.stdin and not self._ffmpeg.stdin.closed:
                    self._ffmpeg.stdin.close()
            except BrokenPipeError:
                pass
            try:
                rc = self._ffmpeg.wait(timeout=15.0)
            except subprocess.TimeoutExpired:
                log.warning("ffmpeg did not finalize within 15s; killing")
                self._ffmpeg.kill()
                rc = self._ffmpeg.wait()
            stderr = b""
            if self._ffmpeg.stderr is not None:
                try:
                    stderr = self._ffmpeg.stderr.read() or b""
                except Exception:
                    pass
            if rc != 0 and self._frames_written == 0:
                raise RuntimeError(
                    f"ffmpeg exited {rc} with no frames written: "
                    f"{stderr.decode('utf-8', 'replace').strip()}"
                )
            elif rc != 0:
                log.warning(
                    "ffmpeg exited %d after %d frames: %s",
                    rc, self._frames_written,
                    stderr.decode("utf-8", "replace").strip(),
                )
            self._ffmpeg = None

        if self._capture_error is not None:
            log.warning("RfbRecorder capture thread raised: %r", self._capture_error)

        log.info(
            "RfbRecorder stopped: %d frames written to %s",
            self._frames_written, self.output_mp4,
        )
        return self.output_mp4

    @property
    def frames_written(self) -> int:
        return self._frames_written

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._width, self._height)

    # ---- internals -------------------------------------------------------

    def _capture_loop(self) -> None:
        sock = self._sock
        ffmpeg = self._ffmpeg
        fb = self._fb
        assert sock is not None and ffmpeg is not None and fb is not None
        assert ffmpeg.stdin is not None

        w, h = self._width, self._height
        period = 1.0 / self.fps
        next_deadline = time.monotonic()

        try:
            while not self._stop.is_set():
                # Pull as many pending updates as we can; the daemon
                # answers each request with one update, but ServerCutText
                # / Bell may show up too. We send exactly one request per
                # tick and accept exactly one FramebufferUpdate (looping
                # past non-update messages).
                _request_update(sock, w, h, incremental=0)
                got_update = False
                # Bound the inner loop so a chatty server can't starve us.
                for _ in range(8):
                    if self._stop.is_set():
                        break
                    if _read_one_update(sock, fb):
                        got_update = True
                        break

                if got_update:
                    try:
                        ffmpeg.stdin.write(fb.tobytes())
                    except BrokenPipeError as e:
                        raise RuntimeError("ffmpeg stdin broken; encoder died") from e
                    self._frames_written += 1

                # Steady-fps pacing: schedule the next tick relative to
                # an absolute deadline, not to "now", so jitter averages
                # out. If we're already late, skip the sleep entirely.
                next_deadline += period
                slack = next_deadline - time.monotonic()
                if slack > 0:
                    # Use Event.wait so stop() returns promptly.
                    if self._stop.wait(timeout=slack):
                        break
                elif slack < -2 * period:
                    # We're more than two frames behind — resync to now
                    # so we don't burn CPU spinning to "catch up".
                    log.warning(
                        "RfbRecorder behind by %.2fs; resyncing pacing",
                        -slack,
                    )
                    next_deadline = time.monotonic()
        except BaseException as e:  # noqa: BLE001 — surface in stop()
            self._capture_error = e
            log.warning("RfbRecorder capture loop aborted: %r", e)
