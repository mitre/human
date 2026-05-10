"""Integration-test fixtures for the Human plugin.

The tests in this directory are gated on a real running microVM
(``@pytest.mark.requires_running_vm``). They are skipped by default;
opt in with ``pytest -m requires_running_vm`` or by setting the env
var ``TIMESTONE_RUN_VM_TESTS=1``. The CI matrix doesn't have a hyper-
visor, so leaving them silent is the right default.
"""

import os
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_running_vm: test requires a live microVM "
        "(skip unless TIMESTONE_RUN_VM_TESTS=1 or "
        "-m requires_running_vm is passed)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip live-VM tests unless explicitly opted in.

    The opt-in is ANY of:
      * ``-m requires_running_vm`` on the pytest CLI
      * env var ``TIMESTONE_RUN_VM_TESTS`` is set to a truthy value
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
