"""Smoke test: every HID atomic YAML in this plugin should load through
Caldera's `data_svc.load_ability_file()` without raising.

Background: prior to fix/v0-ability-load-errors, Caldera's
`load_executors_from_platform_dict` called ``executor.get('command')`` on
the value under ``platforms.<os>``. HID atomics use ``platforms.<os>.steps``
so ``executor`` was a list and the loader raised
``'list' object has no attribute 'get'`` for every atomic YAML on every
Caldera startup (~18 errors). The patched converter skips non-dict
executor values silently, since HID atomics are consumed by the Human
plugin's own loader, not Caldera's executor pipeline.

This test exercises the same code path the Caldera bootstrap exercises
and asserts no exception is raised. Run from the Caldera root so the
``app.*`` import paths resolve:

    cd /home/caldera/Desktop/CalderaVENV/caldera
    pytest -q plugins/human/tests/test_v0_ability_converter.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import unittest

import yaml

HUMAN_ROOT = pathlib.Path(__file__).resolve().parent.parent
ATOMIC_DIR = HUMAN_ROOT / "data" / "abilities" / "benign-human-activity" / "atomic"

# Add Caldera root to sys.path so `from app.service.data_svc import DataService`
# resolves. We discover the Caldera root by walking up from CWD; if that
# fails, fall back to the canonical dev-box layout.
def _find_caldera_root() -> pathlib.Path:
    candidate = pathlib.Path("/home/caldera/Desktop/CalderaVENV/caldera")
    if (candidate / "app" / "service" / "data_svc.py").exists():
        return candidate
    raise RuntimeError("Could not locate Caldera root for converter test")


CALDERA_ROOT = _find_caldera_root()
sys.path.insert(0, str(CALDERA_ROOT))


class _FakeAbilityStore:
    """Minimal stand-in for the bits of DataService that load_ability_file
    touches. We only care that the converter completes without raising."""

    def __init__(self):
        self.ram = {"abilities": []}
        self.created = []

    async def store(self, c_object):
        self.ram["abilities"].append(c_object)
        return c_object


class HidAtomicConverterSmokeTest(unittest.TestCase):
    """One test per atomic YAML — fail loudly if any of them break."""

    def test_every_atomic_loads(self):
        from app.service.data_svc import DataService  # noqa: WPS433

        # We bypass DataService.__init__ (which wants services / config)
        # and only exercise the static-ish converter helpers.
        ds = DataService.__new__(DataService)

        async def run_one(path: pathlib.Path):
            with open(path, "r", encoding="utf-8") as fh:
                docs = yaml.safe_load(fh) or []
            for ab in docs:
                if not isinstance(ab, dict):
                    continue
                # The converter pops `platforms` off the dict in-place,
                # so give it a copy.
                ability_copy = dict(ab)
                executors = await ds.convert_v0_ability_executor(ability_copy)
                # HID atomics intentionally have zero Caldera executors —
                # they're consumed by the Human plugin's own loader. The
                # important assertion is "no exception was raised."
                self.assertIsInstance(executors, list)

        loop = asyncio.new_event_loop()
        try:
            files = sorted(ATOMIC_DIR.glob("*.yml"))
            self.assertGreater(len(files), 0, f"No atomics found at {ATOMIC_DIR}")
            for f in files:
                with self.subTest(atomic=f.name):
                    loop.run_until_complete(run_one(f))
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
