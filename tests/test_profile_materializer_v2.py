"""Tests for the platform-aware profile materializer (v2 schema).

Covers:
  - every new atomic ability YAML loads cleanly
  - each new ability defines at least the windows + linux platforms
  - surf-the-web materializes for os=windows / os=linux with the
    expected first ~10 OperatorMessages
  - profile-level args overrides reach the materialized output
  - random dwells are deterministic given a seed
  - missing-platform errors surface clearly

Run from the human plugin root:

    pytest -q tests/test_profile_materializer_v2.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import textwrap
import unittest

import yaml

# Make `pyhuman` importable when pytest is run from the plugin root.
HUMAN_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HUMAN_ROOT))

from pyhuman.profile_materializer import (  # noqa: E402
    SplitMix64,
    load_atomic_index,
    load_yaml_list,
    materialize_ability,
    materialize_profile,
    normalize_os,
)

ABILITIES_DIR = HUMAN_ROOT / "data" / "abilities" / "benign-human-activity" / "atomic"
PROFILE_PATH = HUMAN_ROOT / "data" / "adversaries" / "surf-the-web.yml"

# IDs of the 10 new platform-aware abilities introduced by this change.
NEW_ABILITIES = [
    "open-default-browser",
    "focus-address-bar-v2",
    "navigate-to-url",
    "open-new-tab",
    "close-current-tab",
    "switch-tab",
    "focus-page-search",
    "scroll-page",
    "dwell-reading",
    "dwell-natural",
]

ALLOWED_ACTIONS = {
    "move", "click", "press", "keydown", "keyup",
    "type", "dwell", "wait_for", "scroll", "chord", "raw", "repeat",
}


def _load_index():
    return load_atomic_index(ABILITIES_DIR)


def _load_profile():
    entries = load_yaml_list(PROFILE_PATH)
    assert entries, "surf-the-web profile is empty"
    return entries[0]


class AbilityLoadTests(unittest.TestCase):

    def test_each_new_ability_yaml_loads(self):
        idx = _load_index()
        for aid in NEW_ABILITIES:
            with self.subTest(ability=aid):
                self.assertIn(aid, idx, f"{aid} missing from atomic index")

    def test_each_new_ability_has_windows_and_linux_platforms(self):
        idx = _load_index()
        for aid in NEW_ABILITIES:
            with self.subTest(ability=aid):
                a = idx[aid]
                platforms = a.get("platforms") or {}
                self.assertIn("windows", platforms,
                              f"{aid} missing windows platform")
                self.assertIn("linux", platforms,
                              f"{aid} missing linux platform")

    def test_each_step_action_is_in_action_vocabulary(self):
        idx = _load_index()
        for aid in NEW_ABILITIES:
            a = idx[aid]
            for plat, branch in (a.get("platforms") or {}).items():
                for step in branch.get("steps", []):
                    with self.subTest(ability=aid, platform=plat, step=step):
                        self.assertIn(step.get("action"), ALLOWED_ACTIONS)


class SurfTheWebWindowsTests(unittest.TestCase):

    def setUp(self):
        self.idx = _load_index()
        self.profile = _load_profile()

    def test_first_messages_are_windows_specific(self):
        msgs = materialize_profile(self.profile, self.idx,
                                   target_os="windows", seed=42)
        # Win+R, dwell, type "msedge", dwell, Enter, wait_for, dwell-natural.
        self.assertEqual(msgs[0], {
            "action": "chord", "keys": ["LeftMeta", "r"], "hold_ms": 50,
        })
        self.assertEqual(msgs[1], {"action": "dwell", "ms": 400})
        self.assertEqual(msgs[2]["action"], "type")
        self.assertEqual(msgs[2]["text"], "msedge")
        self.assertEqual(msgs[3], {"action": "dwell", "ms": 200})
        self.assertEqual(msgs[4], {"action": "press", "key": "Enter"})
        self.assertEqual(msgs[5], {"action": "wait_for", "ms": 2500})
        # 7th: dwell-natural -> a single dwell in [1500, 4000].
        self.assertEqual(msgs[6]["action"], "dwell")
        self.assertGreaterEqual(msgs[6]["ms"], 1500)
        self.assertLessEqual(msgs[6]["ms"], 4000)
        # 8th: focus-address-bar-v2 -> Ctrl+L
        self.assertEqual(msgs[7], {
            "action": "chord", "keys": ["LeftControl", "l"], "hold_ms": 50,
        })

    def test_args_override_reaches_type_messages(self):
        msgs = materialize_profile(self.profile, self.idx,
                                   target_os="windows", seed=42)
        types = [m for m in msgs if m["action"] == "type"]
        # First Type is the launcher command "msedge"; subsequent ones are URLs.
        urls = [m["text"] for m in types if m["text"].startswith("http")]
        self.assertIn("https://news.ycombinator.com", urls)
        self.assertIn("https://github.com", urls)
        self.assertIn("https://www.google.com", urls)

    def test_total_message_count_is_reasonable(self):
        msgs = materialize_profile(self.profile, self.idx,
                                   target_os="windows", seed=42)
        # 23 profile steps; each expands to 1-6 OperatorMessages.
        self.assertGreater(len(msgs), 30)
        self.assertLess(len(msgs), 200)


class SurfTheWebLinuxTests(unittest.TestCase):

    def setUp(self):
        self.idx = _load_index()
        self.profile = _load_profile()

    def test_first_messages_are_linux_specific(self):
        msgs = materialize_profile(self.profile, self.idx,
                                   target_os="linux", seed=42)
        # Linux uses bare LeftMeta press (Super) — NOT a chord.
        self.assertEqual(msgs[0], {"action": "press", "key": "LeftMeta"})
        self.assertEqual(msgs[1], {"action": "dwell", "ms": 500})
        self.assertEqual(msgs[2]["action"], "type")
        self.assertEqual(msgs[2]["text"], "firefox")


class DeterministicRngTests(unittest.TestCase):

    def test_same_seed_same_output(self):
        idx = _load_index()
        prof = _load_profile()
        a = materialize_profile(prof, idx, target_os="windows", seed=42)
        b = materialize_profile(prof, idx, target_os="windows", seed=42)
        self.assertEqual(a, b)

    def test_different_seed_changes_random_dwells(self):
        idx = _load_index()
        prof = _load_profile()
        a = materialize_profile(prof, idx, target_os="windows", seed=1)
        b = materialize_profile(prof, idx, target_os="windows", seed=999)
        # Some dwells must differ — but the structure must be identical.
        self.assertEqual(len(a), len(b))
        self.assertNotEqual(a, b)


class MissingPlatformTests(unittest.TestCase):

    def test_clear_error_when_platform_absent(self):
        # Build a synthetic ability that only defines windows; ask for darwin.
        ability = {
            "id": "x-test-only-windows",
            "platforms": {
                "windows": {"steps": [{"action": "dwell", "ms": 100}]},
            },
        }
        with self.assertRaises(KeyError) as ctx:
            materialize_ability(ability, {}, target_os="darwin")
        self.assertIn("darwin", str(ctx.exception))
        self.assertIn("not implemented", str(ctx.exception))

    def test_clear_error_when_neither_platforms_nor_hid(self):
        ability = {"id": "x-empty"}
        with self.assertRaises(KeyError):
            materialize_ability(ability, {}, target_os="windows")


class OsNormalizationTests(unittest.TestCase):

    def test_synonyms(self):
        self.assertEqual(normalize_os("Windows"), "windows")
        self.assertEqual(normalize_os(" WIN "), "windows")
        self.assertEqual(normalize_os("macOS"), "darwin")
        self.assertEqual(normalize_os("osx"), "darwin")
        self.assertEqual(normalize_os("LINUX"), "linux")
        self.assertEqual(normalize_os(""), "")
        self.assertEqual(normalize_os(None), "")


class PlatformDefaultArgTests(unittest.TestCase):

    def test_browser_command_is_platform_specific(self):
        idx = _load_index()
        prof = {"steps": [{"ability": "open-default-browser"}]}
        win = materialize_profile(prof, idx, target_os="windows", seed=0)
        lin = materialize_profile(prof, idx, target_os="linux", seed=0)
        mac = materialize_profile(prof, idx, target_os="darwin", seed=0)
        # Pull the Type message from each.
        win_type = next(m for m in win if m["action"] == "type")
        lin_type = next(m for m in lin if m["action"] == "type")
        mac_type = next(m for m in mac if m["action"] == "type")
        self.assertEqual(win_type["text"], "msedge")
        self.assertEqual(lin_type["text"], "firefox")
        self.assertEqual(mac_type["text"], "Firefox")


class SplitMix64Tests(unittest.TestCase):

    def test_deterministic(self):
        a = SplitMix64(42)
        b = SplitMix64(42)
        for _ in range(50):
            self.assertEqual(a.next_u64(), b.next_u64())

    def test_randint_in_bounds(self):
        rng = SplitMix64(7)
        for _ in range(200):
            v = rng.randint(100, 200)
            self.assertGreaterEqual(v, 100)
            self.assertLessEqual(v, 200)


if __name__ == "__main__":
    unittest.main()
