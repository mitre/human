"""Golden test for the profile materializer.

For every shipped HID profile in ``data/adversaries/*.yml``, this
test materializes the profile against the atomic-ability index and
compares the resulting OperatorMessage stream against a checked-in
golden JSON file under ``tests/golden/``. Drift in the materializer,
in any atomic ability YAML, or in the profile itself surfaces as a
failed diff instead of silently changing demo behavior.

Volatile fields (timestamps, random-route choices that the
materializer doesn't seed, anything ending in ``_idx`` or ``_route``)
are stripped before the diff. The materializer is seeded with
``seed=42`` so SplitMix64-derived dwell jitter is deterministic.

First-run flow: if a golden file is missing for a profile, this test
WRITES the golden and SKIPS that case with a clear "first-run, golden
written" message. Re-running locks it in. To regenerate after an
intentional change, delete the golden and re-run.

Pure unit test — no VM, no network, no I/O outside the human plugin
tree. Skips the canonical golden write inside CI by setting
``HUMAN_FORBID_GOLDEN_WRITE=1``.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

import pytest
import yaml

# Make `pyhuman` importable when pytest runs from the plugin root or
# from the Caldera root (both layouts are used in CI).
HUMAN_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(HUMAN_ROOT) not in sys.path:
    sys.path.insert(0, str(HUMAN_ROOT))

from pyhuman.profile_materializer import (  # noqa: E402
    load_atomic_index,
    load_yaml_list,
    materialize_profile,
)

ABILITIES_DIR = HUMAN_ROOT / "data" / "abilities" / "benign-human-activity" / "atomic"
ADVERSARIES_DIR = HUMAN_ROOT / "data" / "adversaries"
GOLDEN_DIR = pathlib.Path(__file__).resolve().parent / "golden"

# Volatile fields stripped from every materialized message before
# diffing. Keys ending in any of these suffixes are also stripped.
VOLATILE_FIELDS = {"timestamp", "ts", "ts_ms", "uuid", "trace_id"}
VOLATILE_SUFFIXES = ("_idx", "_route")

# Materializer seed — SplitMix64 dwell jitter is deterministic given
# this seed, so the golden file is a reproducible artifact.
DETERMINISTIC_SEED = 42

# Target OS used when generating goldens. Surf-the-web ships
# Windows / Linux / Darwin variants; for the golden we lock to
# windows since that's what the demo VMs run.
DETERMINISTIC_OS = "windows"


def _is_hid_profile(entry: dict) -> bool:
    """Mirror app/human_svc.py::_discover_profiles' HID classification.

    A profile counts as HID if it carries any of:
      * top-level ``steps:`` list,
      * ``platforms.<os>.steps`` list, or
      * legacy ``hid.steps`` list.
    """
    if isinstance(entry.get("steps"), list):
        return True
    platforms = entry.get("platforms") or {}
    if isinstance(platforms, dict):
        for branch in platforms.values():
            if isinstance(branch, dict) and isinstance(branch.get("steps"), list):
                return True
    hid_block = entry.get("hid") or {}
    if isinstance(hid_block, dict) and isinstance(hid_block.get("steps"), list):
        return True
    return False


def _discover_hid_profiles() -> list[tuple[str, pathlib.Path]]:
    """Walk ``data/adversaries/*.yml`` and return ``(filename, path)``
    tuples for HID profiles only.
    """
    out: list[tuple[str, pathlib.Path]] = []
    if not ADVERSARIES_DIR.is_dir():
        return out
    for path in sorted(ADVERSARIES_DIR.glob("*.yml")):
        try:
            entries = load_yaml_list(path)
        except Exception:
            continue
        if not entries:
            continue
        if _is_hid_profile(entries[0]):
            out.append((path.name, path))
    return out


_PROFILES = _discover_hid_profiles()


def _strip_volatile(obj: Any) -> Any:
    """Recursively strip volatile keys from a materialized message
    tree. Lists and dicts are walked; scalars pass through unchanged.
    """
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in VOLATILE_FIELDS:
                continue
            if any(k.endswith(s) for s in VOLATILE_SUFFIXES):
                continue
            out[k] = _strip_volatile(v)
        return out
    return obj


def _materialize(path: pathlib.Path) -> list[dict]:
    """Materialize ``path`` against the live atomic index, then
    strip volatile fields. Deterministic given ``DETERMINISTIC_SEED``.
    """
    abilities = load_atomic_index(ABILITIES_DIR)
    profile = load_yaml_list(path)[0]
    msgs = materialize_profile(
        profile, abilities,
        target_os=DETERMINISTIC_OS,
        seed=DETERMINISTIC_SEED,
    )
    return _strip_volatile(msgs)


@pytest.mark.parametrize(
    "filename,profile_path",
    _PROFILES,
    ids=[p[0] for p in _PROFILES] or ["no-profiles"],
)
def test_profile_materializer_matches_golden(
    filename: str, profile_path: pathlib.Path
) -> None:
    """Materialize the profile and diff against tests/golden/<file>.expected.json.

    On first run (golden missing): write it and skip with a clear
    message. CI-friendly env var ``HUMAN_FORBID_GOLDEN_WRITE=1``
    flips that to a hard failure (so an accidentally-deleted golden
    can't auto-revive in CI).
    """
    if not _PROFILES:
        pytest.skip(f"no HID profiles found under {ADVERSARIES_DIR}")

    actual = _materialize(profile_path)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    golden_path = GOLDEN_DIR / f"{filename}.expected.json"

    if not golden_path.is_file():
        if os.environ.get("HUMAN_FORBID_GOLDEN_WRITE"):
            pytest.fail(
                f"golden missing at {golden_path} and "
                f"HUMAN_FORBID_GOLDEN_WRITE=1 — refusing to auto-create"
            )
        golden_path.write_text(json.dumps(actual, indent=2, sort_keys=True))
        pytest.skip(
            f"first-run: wrote golden at {golden_path} "
            f"({len(actual)} messages). Re-run to lock it in."
        )

    with golden_path.open() as f:
        expected = json.load(f)

    if actual != expected:
        # Build a small, focused diff message — full diffs are huge
        # and pytest truncates them anyway.
        actual_count = len(actual)
        expected_count = len(expected)
        first_diff = None
        for i, (a, e) in enumerate(zip(actual, expected)):
            if a != e:
                first_diff = (i, a, e)
                break
        details = (
            f"profile {filename}: actual count={actual_count} "
            f"vs expected={expected_count}"
        )
        if first_diff is not None:
            i, a, e = first_diff
            details += (
                f"\nfirst differing message at index {i}:\n"
                f"  actual:   {json.dumps(a)}\n"
                f"  expected: {json.dumps(e)}"
            )
        elif actual_count != expected_count:
            extra_idx = min(actual_count, expected_count)
            extra = (actual if actual_count > expected_count else expected)[extra_idx]
            side = "actual" if actual_count > expected_count else "expected"
            details += (
                f"\nfirst extra message ({side}) at index {extra_idx}:\n"
                f"  {json.dumps(extra)}"
            )
        pytest.fail(
            f"materializer drift detected ({details}). "
            f"To accept the new output, delete {golden_path} and re-run."
        )
