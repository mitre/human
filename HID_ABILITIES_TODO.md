# Human HID Abilities & Personas — Status and TODO

**Status: NOT production-ready. Not validated end-to-end.** This document captures
what exists, what does *not* work yet, and how to finish it. It is the source of
truth for the "human abilities" workstream.

> **Scope guard — what this does NOT touch.** The human plugin's core capability —
> driving a guest's **keyboard/mouse over virtio-input and viewing its framebuffer**
> — is a separate, working layer and is unaffected by everything below. That is:
> `POST /plugin/human/api/input`, `/api/run`, `/api/chord`, and the
> `LiveEndpointViewer` (framebuffer / noVNC) viewer. Use of human for interacting
> with VMs visually + via HID continues to work; the "abilities/personas" layer
> described here sits *on top* of it and is the unfinished part.

## What the "abilities/personas" layer is

A higher-level automation layer that lets an operator pick a *persona* (e.g.
"benign web browsing", "Turla Snake user") and have it drive a guest through a
scripted sequence of HID actions:

- **HID step-abilities** — YAML files under `data/abilities/` using a human-owned
  DSL: `platforms.<os>.steps:` is a list of `{action, ...}` items (chord, dwell,
  press, navigate, etc.), *not* the standard Caldera `platforms.<os>.<executor>.command`
  shape.
- **Persona adversaries** — YAML under `data/adversaries/` that compose those
  abilities into an ordered scenario (e.g. `surf-the-web.yml`, the `legacy-*`
  personas, `turla-snake-user-persona.yml`).
- **The materializer** (`app/human_svc.py`, indexed from
  `ATOMIC_ABILITIES_DIR = data/abilities/benign-human-activity/atomic`) — resolves a
  persona into a flat step list the HID engine can execute.

## What works vs. what does not

| Piece | State |
|-------|-------|
| HID interaction API + framebuffer viewer | **Works** (separate layer — do not disturb) |
| Persona → step-list **materialization** | Passes unit/golden tests (`test_profile_materializer_golden`, `test_legacy_workflow_profiles`) — materialization only |
| Persona **end-to-end execution** (materialize → drive a live VM → verify on screen) | **Never built / never tested** — there is no e2e validation that a persona actually runs against a real microVM |
| Core ability-store integration | **Broken** — see known issue below |

### Known issue — 19 boot-time "Failed to load ability file" errors
Caldera **core's** generic ability loader (`app/service/data_svc.py`) globs
`plugins/*/data/abilities/**/*.yml` and expects the standard executor-dict schema.
It cannot parse the HID `steps:` DSL, so it logs `'list' object has no attribute
'get'` for **19** files (16 active HID atoms in
`benign-human-activity/atomic/` + 3 stale duplicates in `turla-user/`). These are
**non-fatal** — the server boots, all plugins enable, and the materializer (which
reads the files itself) is unaffected — but they are noise that misrepresents the
files as broken.

## What needs to be done (in order)

1. **Decouple HID ability files from core's loader.** Move the HID step-abilities
   out of `data/abilities/` into a human-owned directory (e.g. `data/hid-atomic/`)
   and repoint `ATOMIC_ABILITIES_DIR` (and any persona-resolution paths) at it, so
   core stops globbing them. This clears all 19 boot errors without changing
   behaviour. *(Verify `test_profile_materializer_golden` stays green after the
   repoint.)*
2. **Settle the schema decision.** Either (a) keep the HID `steps:` DSL as a
   human-owned format (preferred — it is purpose-built for HID), kept outside
   `data/abilities/` per step 1; or (b) reformat to standard Caldera abilities so
   they register in the core ability store. Pick one and document it.
3. **Remove the stale duplicates.** `data/abilities/turla-user/{lockscreen-logon,
   lockscreen-logoff,browser-hard-refresh}.yml` carry the *same IDs*
   (`7ae00001-…-0001/0004/0005`) as their `benign-human-activity/atomic/` copies.
   Delete the `turla-user/` copies once step 1 lands.
4. **Build the end-to-end validation harness (the actual missing work).** Prove a
   persona runs for real:
   - deploy a microVM (range `microvm` provider),
   - materialize a persona to a step list,
   - drive it through the HID API (`/api/run` / `/api/chord` / `/api/input`),
   - **verify on the framebuffer** that the expected screen state occurred
     (see the `human-hid` approach: read frames over `frame.sock`).
   Add e2e tests that assert *execution* outcomes, not just materialization shape.
5. **AE-clean compliance.** Nothing is installed or run inside the guest — the
   persona is keystrokes/mouse at the hypervisor only. Keep it that way; no
   in-guest agent for the persona layer.

## How to approach it

- Treat the HID DSL as a first-class human-owned format with its own loader/validator
  (a small `ma.Schema` for a step-ability), rather than leaning on core's ability store.
- Use the existing materializer as the resolve stage; add an **execute** stage that
  maps each DSL `action` to the HID API call it already supports.
- Lean on `human-hid` (framebuffer read + HID inject) for ground-truth verification —
  a persona "passes" only when the screen reaches the expected state, not when the
  YAML parses.
- Gate everything behind the microVM `session_type: gui` path (HID + framebuffer
  require a GUI guest; CLI guests have neither).
