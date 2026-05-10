"""Integration-test fixtures for the Human plugin + per-ability HID
harness.

The tests in this directory are gated on a real running microVM
(``@pytest.mark.requires_running_vm``). They are skipped by default;
opt in via the env vars below.

Opt-in mechanisms:

* ``TIMESTONE_RUN_VM_TESTS=1`` — broad gate. Required for ANY live-VM
  test to run. Skipped tests get a clear "set TIMESTONE_RUN_VM_TESTS=1
  to run" reason.
* ``TIMESTONE_HOST_ID=<vm>`` — specific host the per-ability harness
  drives (see :mod:`test_each_ability`). Tests requesting the
  ``live_host_id`` fixture skip individually if this is unset OR the
  named host has no runtime dir under ``/tmp/timestone-microvms/``.
* ``-m requires_running_vm`` on the pytest CLI — alternative to the
  ``TIMESTONE_RUN_VM_TESTS`` env var for the broad gate.

CI without a hypervisor leaves both env vars unset and gets a clean
SKIPPED report.
"""

from __future__ import annotations

import os

import pytest

from . import ability_harness


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_running_vm: test requires a live microVM with input "
        "+ GPU daemons reachable. Skipped unless TIMESTONE_RUN_VM_TESTS "
        "is set (per-ability harness tests additionally require "
        "TIMESTONE_HOST_ID to name a deployed host).",
    )


def pytest_collection_modifyitems(config, items):
    """Skip live-VM tests unless explicitly opted in via the broad gate.

    The opt-in is ANY of:
      * ``-m requires_running_vm`` on the pytest CLI
      * env var ``TIMESTONE_RUN_VM_TESTS`` is truthy

    The per-ability harness layers an additional ``live_host_id``
    fixture gate on top of this (see below).
    """
    if config.getoption("-m") and "requires_running_vm" in config.getoption("-m"):
        return
    if os.environ.get("TIMESTONE_RUN_VM_TESTS", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return
    skip_marker = pytest.mark.skip(
        reason="live-VM test; set TIMESTONE_RUN_VM_TESTS=1 or pass "
               "-m requires_running_vm to run"
    )
    for item in items:
        if "requires_running_vm" in item.keywords:
            item.add_marker(skip_marker)


# ─── Per-ability harness fixtures ──────────────────────────────────
# Used by test_each_ability.py to drive abilities against a live VM.
# These layer ON TOP of the broad TIMESTONE_RUN_VM_TESTS gate above:
# if a test requests live_host_id, it ALSO needs TIMESTONE_HOST_ID to
# name a deployed host (otherwise the fixture skips with a helpful
# message instead of producing a noisy connection error).


def _live_host_or_none() -> str | None:
    host = os.environ.get("TIMESTONE_HOST_ID")
    if not host:
        return None
    try:
        ability_harness._resolve_runtime_dir(host)
    except FileNotFoundError:
        return None
    return host


@pytest.fixture(scope="session")
def live_host_id() -> str:
    """The host_id the per-ability harness drives.

    Skips the test cleanly when TIMESTONE_HOST_ID is unset or names a
    host with no runtime dir under /tmp/timestone-microvms/.
    """
    host = _live_host_or_none()
    if host is None:
        pytest.skip(
            "no live microVM detected; export TIMESTONE_HOST_ID=<vm>"
        )
    return host


@pytest.fixture(scope="session")
def atomic_index() -> dict:
    return ability_harness.load_atomic_index()


@pytest.fixture(scope="session")
def state_providers() -> dict:
    return ability_harness.load_state_providers()


@pytest.fixture(scope="session")
def state_overlay() -> dict:
    return ability_harness.load_state_dependencies()
