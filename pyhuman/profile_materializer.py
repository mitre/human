"""Profile materializer.

Reads a Caldera-style profile YAML (data/adversaries/<name>.yml) plus
the atomic ability YAMLs it references (data/abilities/benign-human-
activity/atomic/*.yml), substitutes args, and emits a flat stream of
OperatorMessage JSON lines suitable for piping into the
vhost-user-input daemon's operator socket.

Usage:
    python3 -m pyhuman.profile_materializer \\
        --profile data/adversaries/surf-the-web.yml \\
        --abilities data/abilities/benign-human-activity/atomic/ \\
        --os windows

Output: one OperatorMessage JSON object per line, in the order the
profile's ordering dictates, with all `{{ args.* }}` placeholders
substituted.

The output is the literal wire format the daemon expects, so:

    python3 -m pyhuman.profile_materializer --profile X.yml --abilities Y/ \\
        | socat - UNIX-CONNECT:/tmp/op.sock

drives the daemon end-to-end without any other tooling.

Two ability shapes are supported:

  Legacy ("v1"):   hid.steps: [...]    -- single platform, used by the
                   first 8 atomic abilities ported from the prototype.
  Platform-aware:  platforms.{windows,linux,darwin}.steps: [...] -- the
                   new shape that mirrors stockpile's `platforms:` key.
                   The materializer picks one branch by the host's OS
                   read from meta.json (or `--os <name>` on the CLI).

Two profile shapes are supported:

  Legacy:    atomic_ordering: [<id>, {ability: <id>, args: {...}}, ...]
  New:       steps: [{ability: <id>}, {ability: <id>, args: {...}}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml


_PLACEHOLDER = re.compile(r"\{\{\s*args\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# Action vocabulary the materializer understands. Each maps onto an
# OperatorMessage variant in vhost-user-input/src/events.rs.
_ACTIONS = {
    "move", "click", "press", "keydown", "keyup",
    "type", "dwell", "wait_for", "waitfor",
    "scroll", "chord", "raw", "repeat",
}


# ---------------------------------------------------------------------
# Deterministic RNG for randomized dwells.
# ---------------------------------------------------------------------

class SplitMix64:
    """64-bit splitmix64 — same algorithm used elsewhere in the daemon
    chain. Deterministic given a seed, so the same profile materialized
    twice with the same seed yields identical OperatorMessage streams.
    Keeps tests reproducible without globally seeding random."""

    __slots__ = ("state",)

    _MASK = (1 << 64) - 1

    def __init__(self, seed: int = 0) -> None:
        self.state = seed & self._MASK

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & self._MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E7B5) & self._MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self._MASK
        return z ^ (z >> 31)

    def randint(self, lo: int, hi: int) -> int:
        if hi <= lo:
            return lo
        span = hi - lo + 1
        return lo + int(self.next_u64() % span)


# ---------------------------------------------------------------------
# YAML I/O.
# ---------------------------------------------------------------------

def load_yaml_list(path: Path) -> list[dict]:
    """Caldera ability/adversary YAML is a top-level list."""
    with path.open() as f:
        data = yaml.safe_load(f) or []
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: expected top-level YAML list, got {type(data)}")


def load_atomic_index(abilities_dir: Path) -> dict[str, dict]:
    """Map ability_id -> ability dict for every YAML in abilities_dir."""
    index: dict[str, dict] = {}
    for path in sorted(abilities_dir.glob("*.yml")):
        for entry in load_yaml_list(path):
            aid = entry.get("id")
            if not aid:
                continue
            index[aid] = entry
    return index


# ---------------------------------------------------------------------
# OS normalization.
# ---------------------------------------------------------------------

def normalize_os(name: str | None) -> str:
    """Lowercase + strip; map common synonyms (mac, macos, osx -> darwin;
    win -> windows). Returns '' if input is None/empty."""
    if not name:
        return ""
    s = name.strip().lower()
    if s in ("mac", "macos", "osx", "os-x"):
        return "darwin"
    if s == "win":
        return "windows"
    return s


# ---------------------------------------------------------------------
# Args.
# ---------------------------------------------------------------------

def _resolve_default(spec: Any, target_os: str) -> Any:
    """An arg `default:` may be a scalar, list, or a dict keyed by OS.
    If it's an OS-keyed dict (any of windows/linux/darwin present),
    return spec[target_os]. Otherwise, return spec verbatim."""
    if isinstance(spec, dict):
        os_keys = {"windows", "linux", "darwin"}
        if os_keys & set(spec.keys()):
            if target_os in spec:
                return spec[target_os]
            # No match: nothing to substitute.
            return None
    return spec


def resolve_args(
    ability: dict,
    call_args: dict[str, Any],
    target_os: str,
) -> dict[str, Any]:
    """Merge per-ability defaults (legacy hid.args list AND new top-level
    args dict) with the per-call args from the profile. Per-call wins."""
    out: dict[str, Any] = {}

    # Legacy: hid.args is a list of {name, default, ...}.
    for spec in ability.get("hid", {}).get("args", []) or []:
        out[spec["name"]] = _resolve_default(spec.get("default"), target_os)

    # New: top-level `args:` dict, value is {description, default}.
    for name, spec in (ability.get("args") or {}).items():
        if isinstance(spec, dict):
            out[name] = _resolve_default(spec.get("default"), target_os)
        else:
            # Allow shorthand: args: {url: "https://..."}.
            out[name] = spec

    out.update(call_args or {})
    return out


def substitute(value: Any, args: dict[str, Any]) -> Any:
    """Recursively substitute `{{ args.NAME }}` placeholders.

    Strings get full-string replacement when the placeholder is the
    whole string (so int args stay int), otherwise text replacement.
    Dicts and lists recurse. Other types pass through.
    """
    if isinstance(value, str):
        m = _PLACEHOLDER.fullmatch(value.strip())
        if m:
            return args.get(m.group(1))
        return _PLACEHOLDER.sub(
            lambda mm: str(args.get(mm.group(1), "")), value
        )
    if isinstance(value, dict):
        return {k: substitute(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, args) for v in value]
    return value


# ---------------------------------------------------------------------
# Step materialization.
# ---------------------------------------------------------------------

def _normalize_move_target(t: dict) -> dict:
    """Ensure the target dict has the `kind` discriminator the Rust
    side expects."""
    if "kind" in t:
        return t
    out = dict(t)
    if "named" in out:
        out["kind"] = "named"
    elif "x" in out and "y" in out:
        out["kind"] = "abs"
    elif "dx" in out or "dy" in out:
        out["kind"] = "rel"
        out.setdefault("dx", 0)
        out.setdefault("dy", 0)
    else:
        raise ValueError(f"can't infer move-target kind: {t!r}")
    return out


def _resolve_dwell_ms(step: dict, rng: SplitMix64) -> int:
    """Pick a single ms value from whatever shape the YAML used:
    plain int, {mean, jitter}, or [min, max] / {min, max} via ms_range."""
    if "ms_range" in step:
        r = step["ms_range"]
        if isinstance(r, dict):
            lo = int(r.get("min", 0))
            hi = int(r.get("max", 0))
        else:
            lo = int(r[0])
            hi = int(r[1])
        return rng.randint(lo, hi)
    ms = step.get("ms", 0)
    if isinstance(ms, dict):
        mean = int(ms.get("mean", 0))
        jitter = int(ms.get("jitter", 0))
        if jitter <= 0:
            return mean
        return rng.randint(max(0, mean - jitter), mean + jitter)
    return int(ms)


def materialize_step(step: dict, rng: SplitMix64 | None = None) -> dict:
    """Translate an HID-schema step into an OperatorMessage dict.

    `rng` is required for steps that use ms_range / {mean,jitter}; the
    caller should pass a profile-scoped SplitMix64 so the same input
    yields the same output on every run."""
    if rng is None:
        rng = SplitMix64(0)
    a = step.get("action")
    if a == "move":
        return {
            "action": "move",
            "target": _normalize_move_target(step["target"]),
            "duration_ms": int(step.get("duration_ms", 200)),
            "easing": step.get("easing", "linear"),
        }
    if a == "click":
        return {"action": "click", "button": step.get("button", "left")}
    if a in ("press", "keydown", "keyup"):
        return {"action": a, "key": step["key"]}
    if a == "type":
        per = step.get("per_char_ms", 80)
        if isinstance(per, dict):
            per = int(per.get("mean", 80))
        out = {"action": "type", "text": step["text"], "per_char_ms": int(per)}
        if "jitter_pct" in step:
            out["jitter_pct"] = int(step["jitter_pct"])
        if "pause_pct" in step:
            out["pause_pct"] = int(step["pause_pct"])
        if "pause_ms_range" in step:
            r = step["pause_ms_range"]
            if isinstance(r, dict):
                lo = int(r.get("min", 0))
                hi = int(r.get("max", 0))
            else:
                lo = int(r[0])
                hi = int(r[1])
            out["pause_ms_range"] = [lo, hi]
        return out
    if a in ("dwell", "wait_for", "waitfor"):
        ms = _resolve_dwell_ms(step, rng)
        action = "wait_for" if a in ("wait_for", "waitfor") else "dwell"
        return {"action": action, "ms": int(ms)}
    if a == "scroll":
        ticks = step.get("ticks", 1)
        if isinstance(ticks, dict):
            ticks = int(ticks.get("mean", 1))
        return {
            "action": "scroll",
            "wheel": step.get("wheel", "down"),
            "ticks": int(ticks),
        }
    if a == "chord":
        return {
            "action": "chord",
            "keys": step["keys"],
            "hold_ms": int(step.get("hold_ms", 50)),
        }
    if a == "raw":
        return {
            "action": "raw",
            "type_": int(step["type_"]),
            "code": int(step["code"]),
            "value": int(step["value"]),
        }
    if a == "repeat":
        out = []
        for _ in range(int(step.get("count", 1))):
            for sub in step.get("steps", []):
                out.append(materialize_step(sub, rng))
        return {"_inline": out}
    raise ValueError(f"unknown action: {a!r}")


# ---------------------------------------------------------------------
# Ability + profile materialization.
# ---------------------------------------------------------------------

def _select_steps(ability: dict, target_os: str) -> list[dict]:
    """Choose the step list to execute for this OS.

    Order of precedence:
      1. New: ability['platforms'][target_os]['steps']
      2. Legacy: ability['hid']['steps'] (no platform branching)

    Raises KeyError with a clear message if neither path matches."""
    platforms = ability.get("platforms")
    if platforms:
        if not target_os:
            raise KeyError(
                f"ability {ability.get('id')!r} requires an OS to pick a "
                f"platform branch from {sorted(platforms.keys())!r}, but "
                f"no os was provided"
            )
        branch = platforms.get(target_os)
        if branch is None:
            raise KeyError(
                f"ability {ability.get('id')!r} is not implemented for "
                f"OS {target_os!r}; available platforms: "
                f"{sorted(platforms.keys())!r}"
            )
        steps = branch.get("steps")
        if steps is None:
            raise KeyError(
                f"ability {ability.get('id')!r} platforms.{target_os} "
                f"has no `steps:` list"
            )
        return steps

    legacy = ability.get("hid", {}).get("steps")
    if legacy is not None:
        return legacy

    raise KeyError(
        f"ability {ability.get('id')!r} has neither `platforms.<os>.steps` "
        f"nor `hid.steps` — nothing to materialize"
    )


def materialize_ability(
    ability: dict,
    call_args: dict[str, Any],
    target_os: str = "",
    rng: SplitMix64 | None = None,
) -> list[dict]:
    """Expand one atomic ability invocation into OperatorMessage dicts."""
    if rng is None:
        rng = SplitMix64(0)
    args = resolve_args(ability, call_args, target_os)
    steps = _select_steps(ability, target_os)
    out: list[dict] = []
    for step in steps:
        sub = substitute(step, args)
        msg = materialize_step(sub, rng)
        if "_inline" in msg:
            out.extend(msg["_inline"])
        else:
            out.append(msg)
    return out


def _profile_ordering(profile: dict) -> list[Any]:
    """Accept either the new `steps:` key or the legacy `atomic_ordering:`."""
    if "steps" in profile:
        return profile["steps"] or []
    return profile.get("atomic_ordering", []) or []


def materialize_profile(
    profile: dict,
    abilities: dict[str, dict],
    target_os: str = "",
    seed: int = 0,
) -> list[dict]:
    """Walk profile ordering, expand each entry to OperatorMessage dicts."""
    rng = SplitMix64(seed)
    out: list[dict] = []
    for entry in _profile_ordering(profile):
        if isinstance(entry, str):
            ability_id, call_args = entry, {}
        elif isinstance(entry, dict):
            ability_id = entry.get("ability")
            if ability_id is None:
                raise ValueError(f"profile step missing `ability:`: {entry!r}")
            call_args = entry.get("args", {}) or {}
        else:
            raise ValueError(f"bad profile step: {entry!r}")
        ability = abilities.get(ability_id)
        if ability is None:
            raise KeyError(f"ability id {ability_id!r} not in atomic dir")
        out.extend(materialize_ability(ability, call_args, target_os, rng))
    return out


# ---------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------

def _read_meta_os(meta_path: Path | None) -> str:
    """Pull `os` from a Range-provider meta.json. Returns '' if absent."""
    if not meta_path:
        return ""
    try:
        with meta_path.open() as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return ""
    return normalize_os(meta.get("os") or meta.get("platform") or "")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True, type=Path,
                   help="Path to a profile YAML")
    p.add_argument("--abilities", required=True, type=Path,
                   help="Directory of atomic ability YAMLs")
    p.add_argument("--os", default=os.environ.get("HUMAN_TARGET_OS", ""),
                   help="Target OS: windows / linux / darwin")
    p.add_argument("--meta", type=Path, default=None,
                   help="Optional meta.json — read `os` from it if --os not set")
    p.add_argument("--seed", type=int, default=0,
                   help="Seed for the deterministic RNG (random dwells)")
    p.add_argument("--pretty", action="store_true")
    args = p.parse_args()

    target_os = normalize_os(args.os) or _read_meta_os(args.meta)

    profile_entries = load_yaml_list(args.profile)
    if not profile_entries:
        print(f"{args.profile}: empty profile YAML", file=sys.stderr)
        return 2
    profile = profile_entries[0]
    abilities = load_atomic_index(args.abilities)

    messages = materialize_profile(profile, abilities, target_os, seed=args.seed)

    if args.pretty:
        print(json.dumps(messages, indent=2))
    else:
        for m in messages:
            print(json.dumps(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
