"""Profile materializer.

Reads a Caldera-style profile YAML (data/adversaries/<name>.yml) plus
the atomic ability YAMLs it references (data/abilities/benign-human-
activity/atomic/*.yml), substitutes args, and emits a flat stream of
OperatorMessage JSON lines suitable for piping into the
vhost-user-input daemon's operator socket.

Usage:
    python3 -m pyhuman.profile_materializer \\
        --profile data/adversaries/surf-the-web.yml \\
        --abilities data/abilities/benign-human-activity/atomic/

Output: one OperatorMessage JSON object per line, in the order the
profile's atomic_ordering dictates, with all `{{ args.* }}`
placeholders substituted.

The output is the literal wire format the daemon expects, so:

    python3 -m pyhuman.profile_materializer --profile X.yml --abilities Y/ \\
        | socat - UNIX-CONNECT:/tmp/op.sock

drives the daemon end-to-end without any other tooling.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


_PLACEHOLDER = re.compile(r"\{\{\s*args\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


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


def resolve_args(ability: dict, call_args: dict[str, Any]) -> dict[str, Any]:
    """Merge the ability's arg defaults with the per-call args."""
    out: dict[str, Any] = {}
    for spec in ability.get("hid", {}).get("args", []) or []:
        out[spec["name"]] = spec.get("default")
    out.update(call_args or {})
    return out


def _normalize_move_target(t: dict) -> dict:
    """Ensure the target dict has the `kind` discriminator the Rust
    side expects. YAML authors leave it implicit
    (`{named: foo}`, `{x: 1, y: 2}`); we infer it here."""
    if "kind" in t:
        return t
    out = dict(t)
    if "named" in out:
        out["kind"] = "named"
    elif "x" in out and "y" in out:
        out["kind"] = "abs"
    elif "dx" in out or "dy" in out:
        out["kind"] = "rel"
        # Fill missing axis with 0 so serde gets both fields.
        out.setdefault("dx", 0)
        out.setdefault("dy", 0)
    else:
        raise ValueError(f"can't infer move-target kind: {t!r}")
    return out


def materialize_step(step: dict) -> dict:
    """Translate an HID-schema step into an OperatorMessage dict
    (matches the Rust `OperatorMessage` enum's serde shape)."""
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
        # Schema allows {mean, jitter}; Rust side currently expects an int —
        # collapse to mean when given a dict.
        if isinstance(per, dict):
            per = int(per.get("mean", 80))
        return {"action": "type", "text": step["text"], "per_char_ms": int(per)}
    if a in ("dwell", "wait_for"):
        ms = step.get("ms", 0)
        if isinstance(ms, dict):
            ms = int(ms.get("mean", 0))
        return {"action": a, "ms": int(ms)}
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
    if a == "repeat":
        # repeat is expanded inline by the materializer
        out = []
        for _ in range(int(step.get("count", 1))):
            for sub in step.get("steps", []):
                out.append(materialize_step(sub))
        # caller will flatten — return a list marker
        return {"_inline": out}
    raise ValueError(f"unknown action: {a!r}")


def materialize_ability(ability: dict, call_args: dict[str, Any]) -> list[dict]:
    """Expand one atomic ability invocation into OperatorMessage dicts."""
    args = resolve_args(ability, call_args)
    out: list[dict] = []
    for step in ability.get("hid", {}).get("steps", []):
        # First substitute placeholders in the step against resolved args.
        sub = substitute(step, args)
        msg = materialize_step(sub)
        if "_inline" in msg:
            out.extend(msg["_inline"])
        else:
            out.append(msg)
    return out


def materialize_profile(
    profile: dict, abilities: dict[str, dict]
) -> list[dict]:
    """Walk profile.atomic_ordering, expand each entry to messages."""
    out: list[dict] = []
    for entry in profile.get("atomic_ordering", []):
        if isinstance(entry, str):
            ability_id, call_args = entry, {}
        elif isinstance(entry, dict):
            ability_id = entry["ability"]
            call_args = entry.get("args", {}) or {}
        else:
            raise ValueError(f"bad atomic_ordering entry: {entry!r}")
        ability = abilities.get(ability_id)
        if ability is None:
            raise KeyError(f"ability id {ability_id!r} not in atomic dir")
        out.extend(materialize_ability(ability, call_args))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True, type=Path,
                   help="Path to a profile YAML (data/adversaries/<name>.yml)")
    p.add_argument("--abilities", required=True, type=Path,
                   help="Directory of atomic ability YAMLs")
    p.add_argument("--pretty", action="store_true",
                   help="Pretty-print messages (debugging only — not the wire format)")
    args = p.parse_args()

    profile_entries = load_yaml_list(args.profile)
    if not profile_entries:
        print(f"{args.profile}: empty profile YAML", file=sys.stderr)
        return 2
    profile = profile_entries[0]
    abilities = load_atomic_index(args.abilities)

    messages = materialize_profile(profile, abilities)

    if args.pretty:
        print(json.dumps(messages, indent=2))
    else:
        for m in messages:
            print(json.dumps(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
