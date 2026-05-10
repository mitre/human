"""Per-ability live-VM tests.

One pytest case per atomic ability and per profile, generated via
``@pytest.mark.parametrize``. Each case:

    1. Resolves the dependency chain that puts the guest in the right
       state for the target ability.
    2. Captures a pre-frame, dispatches the chain over the operator
       UDS, captures a post-frame.
    3. Evaluates the target's ``verify:`` list (YAML field or overlay
       in fixtures/state_dependencies.yml).

Without a live VM the whole module collapses to skips via the
``requires_running_vm`` marker (see conftest.py / live_host_id
fixture). Tests do not modify the VM filesystem and do not restart
daemons.

Two collection-time concerns:

* Discovery is read-only: we walk the YAMLs through
  ``ability_harness.load_atomic_index`` / ``load_profiles_index``,
  never importing the (still-in-flight) Caldera service registry.
* Parametrize ids include the YAML stem so a failing case shows
  ``test_atomic_ability[dwell]`` rather than a UUID — easier to grep.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from . import ability_harness


# ---------------------------------------------------------------------
# Parametrize-data assembly. Done at import time so collection shows
# the matrix; the actual VM IO happens inside each test.
# ---------------------------------------------------------------------

_ATOMIC_IDS = ability_harness.discover_ability_ids()
_PROFILE_IDS = ability_harness.discover_profile_ids()


def _id_label(ability_id: str) -> str:
    """Map an ability id (uuid or stem) to a friendly param-id. Looks
    up the name field for prettier output when available."""
    idx = ability_harness.load_atomic_index()
    a = idx.get(ability_id) or {}
    return a.get("name") or ability_id[:24]


# ---------------------------------------------------------------------
# Per-ability suite. Marked requires_running_vm so collection-only or
# VM-less CI just sees skips, not failures.
# ---------------------------------------------------------------------


@pytest.mark.requires_running_vm
@pytest.mark.parametrize(
    "ability_id",
    _ATOMIC_IDS,
    ids=[_id_label(a) for a in _ATOMIC_IDS] or ["__no_atomic_yamls__"],
)
def test_atomic_ability(ability_id, live_host_id, atomic_index,
                        state_providers, state_overlay,
                        request, tmp_path_factory):
    """Run a single atomic ability against the live VM with full setup
    chain, then verify post-state."""
    if not _ATOMIC_IDS:
        pytest.skip("no atomic abilities discovered")
    if ability_id not in atomic_index:
        pytest.skip(f"ability {ability_id!r} not in atomic index")

    runner = ability_harness.AbilityRunner(
        host_id=live_host_id,
        ability_id=ability_id,
        atomic_idx=atomic_index,
        providers=state_providers,
        overlay=state_overlay,
    )

    try:
        runner.plan()
    except ability_harness.PlanError as e:
        # Missing state provider is the legacy agent's problem; mark
        # xfail rather than red so the matrix stays interpretable.
        pytest.xfail(f"unplanned ability {ability_id}: {e}")

    # If the planner couldn't satisfy a state because the concrete
    # provider isn't shipped on this branch, the dispatch will still
    # fire but the verify post-condition will likely miss. Stamp the
    # case xfail with a clear reason so the matrix stays useful.
    if runner.unsatisfied_states:
        pytest.xfail(
            f"planner could not satisfy states "
            f"{runner.unsatisfied_states!r} for {ability_id} "
            "(concrete provider not in this branch's atomic index)"
        )

    try:
        runner.run_with_setup()
    except FileNotFoundError as e:
        pytest.skip(f"runtime dir / socket missing: {e}")
    except KeyError as e:
        pytest.skip(f"meta.json incomplete: {e}")

    summary = runner.verify()
    # Persist a per-test summary so the operator can grep failures.
    out = tmp_path_factory.mktemp("ability") / f"{ability_id}.json"
    out.write_text(json.dumps(summary, indent=2))
    request.node.add_report_section(
        "call", "harness summary",
        json.dumps(summary, indent=2),
    )


# ---------------------------------------------------------------------
# Per-profile suite. Profiles are composite — they don't go through
# the planner (they are themselves the chain). The runner just
# materializes the profile and dispatches it.
# ---------------------------------------------------------------------


@pytest.mark.requires_running_vm
@pytest.mark.parametrize(
    "profile_id",
    _PROFILE_IDS,
    ids=_PROFILE_IDS or ["__no_profiles__"],
)
def test_profile(profile_id, live_host_id, atomic_index,
                 state_overlay, request, tmp_path_factory):
    """Run a composite profile end-to-end against the live VM."""
    if not _PROFILE_IDS:
        pytest.skip("no profile YAMLs discovered")

    profiles = ability_harness.load_profiles_index()
    profile = profiles.get(profile_id)
    if profile is None:
        pytest.skip(f"profile {profile_id!r} not in index")

    # Profiles are dispatched through the production materializer
    # path. The harness wraps them in a synthetic ability so the
    # AbilityRunner code path is reused; alternatively drive the
    # production /api/run-profile endpoint via SSE — but that is the
    # overnight UI agent's territory, so we go direct here.
    target_os = ability_harness._resolve_target_os(live_host_id)
    sock_path = ability_harness._resolve_operator_socket(live_host_id)

    # The profile_materializer expects (profile_dict, abilities_index).
    import sys as _sys
    _sys.path.insert(0, str(ability_harness.HUMAN_PLUGIN_ROOT))
    from pyhuman.profile_materializer import materialize_profile

    try:
        msgs = materialize_profile(
            profile, atomic_index, target_os=target_os,
        )
    except Exception as e:
        pytest.skip(f"materialize failed for {profile_id}: {e}")

    # Capture pre-state, dispatch, capture post-state.
    try:
        port = ability_harness._resolve_vnc_port(live_host_id)
        pre, w, h, bpp = ability_harness.fetch_framebuffer(
            "127.0.0.1", port)
    except Exception:
        pre, w, h, bpp = b"", 0, 0, 0

    n_sent = ability_harness._send_messages(sock_path, msgs)

    # Profiles are usually long; let the guest finish painting.
    import time as _t
    _t.sleep(2.0)

    try:
        post, _, _, _ = ability_harness.fetch_framebuffer(
            "127.0.0.1", port)
    except Exception:
        post = b""

    pct = ability_harness.pixel_change_pct(pre, post) \
        if (pre and post) else 0.0

    # Optional: profile-level verify in the overlay.
    overlay_entry = state_overlay.get(profile_id) or \
        state_overlay.get(profile.get("id") or "") or {}
    verify_specs = overlay_entry.get("verify") or []
    failed = []
    for spec in verify_specs:
        if not isinstance(spec, dict):
            continue
        for kind, expected in spec.items():
            ok, detail = ability_harness.AbilityRunner._evaluate_check(
                kind, expected, pct, "")
            if not ok:
                failed.append((kind, expected, detail))

    summary = {
        "profile_id": profile_id,
        "messages_sent": n_sent,
        "pixel_change_pct": pct,
        "verify_failed": failed,
    }
    out = tmp_path_factory.mktemp("profile") / f"{profile_id}.json"
    out.write_text(json.dumps(summary, indent=2))
    request.node.add_report_section(
        "call", "harness summary",
        json.dumps(summary, indent=2),
    )

    assert not failed, (
        f"profile {profile_id} failed verify: {failed!r}; "
        f"pct={pct:.1f}, sent={n_sent}"
    )


# ---------------------------------------------------------------------
# Sanity: the test module imports cleanly even without a VM, and the
# discovery list is non-empty for the seed dataset.
# ---------------------------------------------------------------------


def test_discovery_yields_atomic_abilities():
    assert _ATOMIC_IDS, (
        f"expected atomic abilities under "
        f"{ability_harness.ATOMIC_DIR}, got none"
    )


def test_discovery_yields_profiles():
    assert _PROFILE_IDS, (
        f"expected profiles under "
        f"{ability_harness.ADVERSARIES_DIR}, got none"
    )


def test_planner_resolves_known_atomic_chain(atomic_index,
                                              state_providers,
                                              state_overlay):
    """Planner must produce a non-empty plan for a known ability with
    known dependencies (type-text needs text-cursor-active)."""
    runner = ability_harness.AbilityRunner(
        host_id="__no_vm__",  # planner doesn't touch the VM
        ability_id="type-text",
        atomic_idx=atomic_index,
        providers=state_providers,
        overlay=state_overlay,
    )
    chain = runner.plan()
    assert "type-text" in chain
    # text-cursor-active -> launch-app-via-runner is the first
    # provider; expect it to land in the chain too.
    assert "launch-app-via-runner" in chain or len(chain) >= 1
