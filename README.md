# MITRE Caldera Plugin: Human

Plugin supplying Caldera with human-emulation capabilities. Runs in two
modes — **standalone** (legacy, in-guest agent) or **paired with the
Range plugin** (HID stack, host-side drive over virtio-input).

## Two operating modes

### A. Standalone (in-guest agent)

The original Human flow. Caldera deploys an agent inside the target
guest; the agent drives the desktop directly using selenium (browser
automation) and pyautogui (mouse/keyboard via the OS WinAPI / X11 /
AppKit), running as the logged-in user.

**Requires (inside the guest):**

* Linux, macOS, or Windows (with PowerShell)
* Python 3 + `virtualenv`
* Google Chrome
* Python packages from `pyhuman/requirements.txt` (selenium,
  pyautogui, plus per-OS deps)

**Delivers:**

* Idle-browse / Google-search / open-email / YouTube-browse workflows
* Office-document open + edit (Writer / Calc / Notepad / Paint)
* Click-links / spawn-shell / execute-command
* Each workflow is selenium- or pyautogui-driven; **the agent must
  be installed in the guest** and have access to the desktop session

**When to choose this mode:** target hosts where you already have a
Caldera agent foothold and an active desktop session, and you want to
generate benign user activity to obfuscate other Caldera operations.

### B. Paired with Range (HID stack — no in-guest agent)

The new flow. Range spawns microVMs (Cloud Hypervisor) wired to host-
side daemons that emulate USB-like input + a virtual GPU. Human plugin
streams keyboard / mouse events directly through the guest's
`vioinput.sys` and `viogpudo.sys` drivers — **no agent inside the
guest, no software installed there beyond what shipped with the
prepared image.**

**Requires (host-side):**

* Caldera with **Range plugin** on `feature/cloud-cdktf-providers @
  91d3c2a` (or later) — bundles the recovery-hardened binaries
* The Range plugin owns spawning + tearing down microVMs and exposes
  a WebSocket→VNC proxy for live framebuffer viewing
* See `RECOVERY_STACK.md` in the Range plugin for the full
  dependency picture

**Delivers:**

* **HID profiles** under `data/adversaries/*.yml` — composable action
  scripts (move/click/type/chord/dwell/scroll/wait) materialized into
  per-OS step sequences (Windows / Linux / macOS branches each)
* **10+ shipping profiles**: `surf-the-web`, `server-core-demo`, plus
  the legacy pyhuman workflows translated to HID
  (`legacy-google-search`, `legacy-browse-youtube`,
  `legacy-open-email`, `legacy-open-office-writer`,
  `legacy-open-office-calc`, `legacy-create-document`,
  `legacy-ms-paint`, `legacy-spawn-shell`, `legacy-click-links`)
* **Live Endpoint viewer** in the Human UI — noVNC framebuffer
  streamed through the WS proxy; chord-button palette
  (`Ctrl+Alt+Del` / Win shortcuts / F-keys / arrows / sticky
  modifiers) for live-driving the guest
* **Recording** — RfbRecorder captures the framebuffer to MP4 during
  a profile run; recordings browseable + playable inline in the UI
* **Daemon hot-restart recovery** — if the gpu daemon crashes
  mid-plan, supervisor respawns + reattaches state + CH reconnects
  vhost-user, recovering the framebuffer in ~2s without VM redeploy

**When to choose this mode:** AE-clean scenarios where the guest must
have NO Caldera artifacts (no in-guest agent, no installed Python,
no selenium). Demos that need a reproducible browser/Office desktop
session driven by external input. Anywhere you want to record the
operator's exact action stream as an MP4.

## Quick decision matrix

| You need... | Mode |
|---|---|
| To drive a real Windows desktop with no in-guest software | B (HID stack) |
| To generate benign noise on a host where you already have an agent | A (standalone) |
| To record a profile run as a video MP4 | B |
| To target a host you don't control the hypervisor for | A |
| Recovery from a daemon/VM hiccup without redeploying | B |
| Cross-OS (Linux / macOS) benign-activity workflows | A (today) — B is windows-centric; Linux/macOS profiles in progress |

## Mode B architecture (one-paragraph)

Operator → Caldera (Human plugin) → operator UDS sockets per VM →
`vhost-user-input` (tablet + keyboard daemons) → vhost-user protocol →
Cloud Hypervisor → guest `vioinput.sys` → Windows desktop receives
keystrokes as if from real USB. Framebuffer flows the other way:
guest `viogpudo.sys` → `vhost-user-gpu-2d` daemon → RFB/VNC →
WebSocket-to-TCP proxy in Range plugin → browser noVNC. See
`data/abilities/HID_ABILITY_SCHEMA.md` for the action vocabulary and
`docs/HID_TEST_HARNESS.md` for the per-ability test infrastructure.

## Requirements (server-side, both modes)

The Caldera server runs additional Python packages for the Human
plugin. Install via:

```
cd plugins/human
pip3 install -r requirements.txt
```

## Further Reading

* [Step-by-step setup for the standalone mode (wiki)](https://github.com/mitre/human/wiki)
* [Caldera plugin-library docs](https://caldera.readthedocs.io/en/latest/Plugin-library.html?#human)
* `data/abilities/HID_ABILITY_SCHEMA.md` — action vocabulary + atomic
  ability YAML format (Mode B)
* `docs/HID_TEST_HARNESS.md` — per-ability test framework with
  state-graph planner (Mode B)
* Range plugin's `RECOVERY_STACK.md` — the dependency picture for
  Mode B (which TimeStone / CH patch / daemon binaries you need)
