"""Tests for the legacy-workflow conversion profiles.

These profiles are HID translations of pyhuman/app/workflows/*.py and
live at data/adversaries/legacy-*.yml. Each must materialize cleanly
against the platform-aware materializer for at least target_os=windows
(the first microVM target) and emit a coherent OperatorMessage stream.

Contract checked here:
  - every legacy-*.yml YAML loads as a single profile entry
  - materializing with target_os=windows and seed=42 yields >= 3
    OperatorMessage dicts
  - every emitted message has an `action` field whose value is in the
    daemon's known action vocabulary
  - browser-flavored profiles also materialize cleanly for linux and
    darwin (they share the cross-OS atomic vocabulary)
  - the same seed yields the same output (RNG determinism)

Run from the human plugin root:

    pytest -q tests/test_legacy_workflow_profiles.py
"""

from __future__ import annotations

import pathlib
import sys
import unittest

HUMAN_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HUMAN_ROOT))

from pyhuman.profile_materializer import (  # noqa: E402
    load_atomic_index,
    load_yaml_list,
    materialize_profile,
)

ABILITIES_DIR = HUMAN_ROOT / "data" / "abilities" / "benign-human-activity" / "atomic"
ADVERSARIES_DIR = HUMAN_ROOT / "data" / "adversaries"

# Mirrors profile_materializer._ACTIONS, plus the wait_for normalization.
ALLOWED_ACTIONS = {
    "move", "click", "press", "keydown", "keyup",
    "type", "dwell", "wait_for", "scroll", "chord", "raw", "repeat",
}

# Legacy profiles produced by feature/legacy-workflow-conversion.
# (workflow_basename, expected_uuid)
LEGACY_PROFILES = [
    ("legacy-browse-web",         "c806a2b9-0d69-40dd-a025-8edce77dcd75"),
    ("legacy-browse-youtube",     "cdc7292a-911a-4603-91d6-85ab5a488a23"),
    ("legacy-google-search",      "b49acc35-e7b6-4888-aed0-f32439a86e9c"),
    ("legacy-open-email",         "7b68cf5d-06fd-4079-9f97-e144690f14d5"),
    ("legacy-click-links",        "74200f69-4e8a-4b15-853b-89dec15f52db"),
    ("legacy-create-document",    "13b80ba7-4d51-4a61-a7a3-fbe794dc3297"),
    ("legacy-open-office-writer", "d53b5ddd-dd60-47fa-8b99-0f5b879949ef"),
    ("legacy-open-office-calc",   "ae2972cd-120d-47ef-b89e-d1b26967d42e"),
    ("legacy-ms-paint",           "efd3531a-148b-41e5-b312-09de4b9e78bb"),
    ("legacy-spawn-shell",        "ced55d8f-40c0-4d44-9ea8-5c7cd36b9703"),
]

BROWSER_PROFILES = {
    "legacy-browse-web",
    "legacy-browse-youtube",
    "legacy-google-search",
    "legacy-open-email",
    "legacy-click-links",
}


def _load_profile(basename: str) -> dict:
    path = ADVERSARIES_DIR / f"{basename}.yml"
    entries = load_yaml_list(path)
    assert entries, f"{path} loaded as empty"
    return entries[0]


def _materialize(basename: str, target_os: str = "windows", seed: int = 42):
    abilities = load_atomic_index(ABILITIES_DIR)
    profile = _load_profile(basename)
    return materialize_profile(profile, abilities, target_os=target_os, seed=seed)


class LegacyProfileExistenceTests(unittest.TestCase):
    """Each declared legacy profile lives on disk with the right id."""

    def test_each_profile_yaml_exists(self):
        for basename, _uuid in LEGACY_PROFILES:
            with self.subTest(profile=basename):
                path = ADVERSARIES_DIR / f"{basename}.yml"
                self.assertTrue(path.is_file(),
                                f"{path} not found")

    def test_each_profile_has_expected_uuid(self):
        for basename, expected_uuid in LEGACY_PROFILES:
            with self.subTest(profile=basename):
                profile = _load_profile(basename)
                self.assertEqual(profile.get("id"), expected_uuid,
                                 f"{basename} id mismatch")

    def test_each_profile_has_required_fields(self):
        required = {"id", "name", "description", "steps"}
        for basename, _uuid in LEGACY_PROFILES:
            with self.subTest(profile=basename):
                profile = _load_profile(basename)
                missing = required - set(profile.keys())
                self.assertFalse(missing, f"{basename} missing {missing!r}")
                self.assertIsInstance(profile["steps"], list)
                self.assertGreater(len(profile["steps"]), 0,
                                   f"{basename} has empty steps")


class LegacyProfileMaterializationTests(unittest.TestCase):
    """Materialization contract: >=3 messages, every msg has `action`."""

    def test_each_profile_materializes_for_windows(self):
        for basename, _uuid in LEGACY_PROFILES:
            with self.subTest(profile=basename):
                msgs = _materialize(basename, target_os="windows", seed=42)
                self.assertGreaterEqual(
                    len(msgs), 3,
                    f"{basename} materialized to <3 messages: {len(msgs)}"
                )
                for i, m in enumerate(msgs):
                    self.assertIn("action", m,
                                  f"{basename}[{i}] missing action: {m!r}")
                    self.assertIn(
                        m["action"], ALLOWED_ACTIONS,
                        f"{basename}[{i}] unknown action {m['action']!r}",
                    )

    def test_each_profile_materializes_deterministically(self):
        for basename, _uuid in LEGACY_PROFILES:
            with self.subTest(profile=basename):
                a = _materialize(basename, target_os="windows", seed=42)
                b = _materialize(basename, target_os="windows", seed=42)
                self.assertEqual(a, b, f"{basename} non-deterministic at seed=42")

    def test_browser_profiles_materialize_for_linux_and_darwin(self):
        for basename, _uuid in LEGACY_PROFILES:
            if basename not in BROWSER_PROFILES:
                continue
            for target_os in ("linux", "darwin"):
                with self.subTest(profile=basename, os=target_os):
                    msgs = _materialize(basename, target_os=target_os, seed=42)
                    self.assertGreaterEqual(len(msgs), 3)
                    for m in msgs:
                        self.assertIn("action", m)
                        self.assertIn(m["action"], ALLOWED_ACTIONS)


class NewAtomicAbilitiesTests(unittest.TestCase):
    """The legacy ports introduced 3 new atomic abilities. Verify they
    round-trip through the materializer with reasonable defaults."""

    NEW_ATOMIC_IDS = ["press-key", "chord-keys", "launch-app-via-runner"]

    def test_each_new_atomic_indexed(self):
        idx = load_atomic_index(ABILITIES_DIR)
        for aid in self.NEW_ATOMIC_IDS:
            with self.subTest(ability=aid):
                self.assertIn(aid, idx, f"{aid} missing from atomic index")

    def test_each_new_atomic_has_three_platforms(self):
        idx = load_atomic_index(ABILITIES_DIR)
        for aid in self.NEW_ATOMIC_IDS:
            ability = idx[aid]
            platforms = ability.get("platforms") or {}
            for required_os in ("windows", "linux", "darwin"):
                with self.subTest(ability=aid, os=required_os):
                    self.assertIn(required_os, platforms,
                                  f"{aid} missing platforms.{required_os}")

    def test_press_key_with_default_arg(self):
        abilities = load_atomic_index(ABILITIES_DIR)
        prof = {"steps": [{"ability": "press-key"}]}
        msgs = materialize_profile(prof, abilities,
                                   target_os="windows", seed=0)
        self.assertEqual(msgs[0], {"action": "press", "key": "Enter"})

    def test_press_key_with_override(self):
        abilities = load_atomic_index(ABILITIES_DIR)
        prof = {"steps": [{"ability": "press-key", "args": {"key": "F4"}}]}
        msgs = materialize_profile(prof, abilities,
                                   target_os="windows", seed=0)
        self.assertEqual(msgs[0], {"action": "press", "key": "F4"})

    def test_chord_keys_with_list_arg(self):
        abilities = load_atomic_index(ABILITIES_DIR)
        prof = {"steps": [{
            "ability": "chord-keys",
            "args": {"keys": ["LeftAlt", "F4"]},
        }]}
        msgs = materialize_profile(prof, abilities,
                                   target_os="windows", seed=0)
        self.assertEqual(msgs[0]["action"], "chord")
        self.assertEqual(msgs[0]["keys"], ["LeftAlt", "F4"])

    def test_launch_app_via_runner_windows(self):
        abilities = load_atomic_index(ABILITIES_DIR)
        prof = {"steps": [{
            "ability": "launch-app-via-runner",
            "args": {"app_command": "notepad", "settle_ms": 2000},
        }]}
        msgs = materialize_profile(prof, abilities,
                                   target_os="windows", seed=0)
        # Win+R, dwell, type "notepad", dwell, Enter, wait_for(2000).
        self.assertEqual(msgs[0], {
            "action": "chord", "keys": ["LeftMeta", "r"], "hold_ms": 50,
        })
        types = [m for m in msgs if m["action"] == "type"]
        self.assertTrue(types)
        self.assertEqual(types[0]["text"], "notepad")
        wait_fors = [m for m in msgs if m["action"] == "wait_for"]
        self.assertTrue(wait_fors)
        self.assertEqual(wait_fors[0]["ms"], 2000)


if __name__ == "__main__":
    unittest.main()
