"""End-to-end test for pyhuman.recorder.RfbRecorder.

Spawns vhost-user-gpu-2d in `--synthetic-pattern gradient` mode on a
free port, points an RfbRecorder at it for 2 seconds at 5 fps, asserts
the resulting MP4 exists, is non-empty, and ffprobe agrees on the
resolution and (approximately) the duration.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pyhuman.recorder import RfbRecorder  # noqa: E402

DAEMON_CANDIDATES = [
    Path("/tmp/vhost-user-gpu-2d.g5-snapshot"),
    Path(
        "/home/caldera/Desktop/TimeStoneVENV/timestone/vhost-user-daemons/"
        "target/release/vhost-user-gpu-2d"
    ),
]


def _pick_daemon() -> Path:
    for p in DAEMON_CANDIDATES:
        if p.exists() and os.access(p, os.X_OK):
            return p
    pytest.skip(f"vhost-user-gpu-2d binary not found in any of: {DAEMON_CANDIDATES}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_rfb(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5) as s:
                # The server pushes its 12-byte version banner immediately.
                banner = s.recv(12)
                if banner.startswith(b"RFB 003."):
                    return
        except (ConnectionRefusedError, OSError) as e:
            last_err = e
        time.sleep(0.1)
    raise TimeoutError(f"RFB server at {host}:{port} never came up: {last_err!r}")


@pytest.fixture
def gpu2d_daemon(tmp_path):
    """Spin up vhost-user-gpu-2d in synthetic-gradient mode. Yields
    (host, port). Tears the daemon down on exit."""
    binary = _pick_daemon()
    port = _free_port()
    socket_path = tmp_path / "gpu.sock"

    proc = subprocess.Popen(
        [
            str(binary),
            "--socket", str(socket_path),
            "--vnc", f"127.0.0.1:{port}",
            "--synthetic-pattern", "gradient",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_rfb("127.0.0.1", port, timeout=10.0)
        yield ("127.0.0.1", port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _ffprobe(path: Path) -> dict:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_streams", "-show_format",
            "-of", "json",
            str(path),
        ],
        stderr=subprocess.STDOUT,
    )
    return json.loads(out)


def test_recorder_produces_valid_mp4(tmp_path, gpu2d_daemon):
    host, port = gpu2d_daemon
    out = tmp_path / "out.mp4"

    fps = 5
    duration = 2.0
    rec = RfbRecorder(host, port, out, fps=fps)
    rec.start()
    assert rec.resolution == (1024, 768)
    time.sleep(duration)
    final = rec.stop()

    assert final == out
    assert out.exists(), "output mp4 missing"
    assert out.stat().st_size > 0, "output mp4 is empty"
    assert rec.frames_written >= int(fps * duration * 0.5), (
        f"only {rec.frames_written} frames written in {duration}s @ {fps}fps"
    )

    info = _ffprobe(out)
    streams = [s for s in info["streams"] if s.get("codec_type") == "video"]
    assert streams, f"no video stream in {out}: {info}"
    vid = streams[0]
    assert vid["width"] == 1024
    assert vid["height"] == 768

    # Duration: ffprobe sometimes only fills in container duration, not
    # stream duration, so accept either. ±20% tolerance per spec.
    declared = vid.get("duration") or info.get("format", {}).get("duration")
    assert declared, f"no duration in ffprobe output: {info}"
    declared_s = float(declared)
    low, high = duration * 0.8, duration * 1.2
    assert low <= declared_s <= high, (
        f"duration {declared_s:.2f}s outside ±20% of {duration}s"
    )


def test_start_raises_on_bad_host(tmp_path):
    out = tmp_path / "nope.mp4"
    # Port 1 is reliably-closed on loopback.
    rec = RfbRecorder("127.0.0.1", 1, out, fps=5)
    with pytest.raises((ConnectionRefusedError, OSError)):
        rec.start()
    # No file should have been created.
    assert not out.exists()
