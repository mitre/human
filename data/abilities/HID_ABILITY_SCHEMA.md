# HID-style Human Ability Schema

The earlier 38 abilities are shell-cradle (PowerShell `Start-Process`, etc.).
That's fake human emulation — produces the OUTCOME (browser opens to a URL)
without any of the human-emission signal (mouse motion, focus changes,
keystroke timing, dwell). PCAP, EDR, and behavioral telemetry see the wrong
shape: "PowerShell launched edge.exe" instead of "user moved mouse to taskbar,
clicked, typed URL letter-by-letter."

The real model is HID-level: the operator dispatches a sequence of input
events (mouse moves, clicks, key presses) through our `vhost-user-input`
daemon, which forwards them as virtio-input events to the guest. The guest's
stock HID class drivers receive them indistinguishably from a real plugged-in
USB tablet/keyboard.

Abilities under `data/abilities/benign-human-activity/` should migrate to
this new step-list format. Existing shell-cradle YAMLs are deprecated but
kept for backward-compat reference.

## YAML shape

```yaml
- id: <uuid>
  name: <human-readable label>
  description: |
    What this simulates from a *user's* perspective. NOT
    what shell does. Think "user clicks browser icon and types URL"
    not "Start-Process msedge.exe".
  persona: office_worker | developer | executive | support_agent | sales_rep
  tactic: benign-human-activity
  technique:
    attack_id: x_human_<name>          # placeholder, not real ATT&CK
    name: Benign User Activity - <Name>
  hid:                                  # NEW SECTION — replaces `platforms`
    estimated_duration_s: 25            # for UI timeline display
    requires:
      - tablet                          # uses absolute-positioning input
      - keyboard
      - display                         # wants the GPU surface alive
    steps:
      - action: move
        target: { kind: abs, x: <0..32767>, y: <0..32767> }
        duration_ms: 450                # how long the move takes
        easing: ease-out                # human-like; default linear
      - action: dwell
        ms: { mean: 200, jitter: 80 }   # mean ± jitter, sampled per-run
      - action: click
        button: left | right | middle
      - action: type
        text: "letter-by-letter input"
        per_char_ms: { mean: 80, jitter: 30 }
      - action: press
        key: Enter | Tab | Escape | F1..F12 | <char>
      - action: scroll
        wheel: up | down
        ticks: 3
      - action: wait_for
        # No HID emission — daemon parks until condition. Deliberately
        # does NOT poll the guest (would require an in-guest agent).
        # Just sleeps. Operator validates separately if they need to.
        ms: 5000
```

## Action vocabulary

| action | required fields | semantics |
|---|---|---|
| `move` | `target`, `duration_ms` | smooth interpolated motion to target. `target.kind=abs` for tablets (x/y in 0..32767), `kind=rel` for mouse (dx/dy) |
| `click` | `button` | full press+release of the button |
| `press` | `key` | full press+release of a key |
| `keydown` / `keyup` | `key` | one half of the key-event pair (for chords) |
| `type` | `text`, `per_char_ms` | iterates `text`, presses each char with the inter-char delay |
| `dwell` | `ms` | sleep, no input emission. `ms` may be `int` or `{mean, jitter}` |
| `wait_for` | `ms` | sleep (longer). Same as dwell, semantic hint that we're "waiting on something the operator can't observe" |
| `scroll` | `wheel`, `ticks` | wheel events, equivalent to N notches |
| `chord` | `keys: [a, b, c]`, `hold_ms` | press-all, hold, release-all. For Ctrl+L, Alt+Tab, etc. |
| `repeat` | `count`, `steps: [...]` | sub-sequence run N times. Useful for typing characters one at a time |

## Easing functions (for `move`)

| name | shape | when to use |
|---|---|---|
| `linear` | constant velocity | default; least human-feeling |
| `ease-out` | starts fast, slows | human pointing-at-target motion |
| `ease-in-out` | accelerate, peak, decelerate | longer drags / drift |
| `human` | curve fit to real-mouse studies | most realistic; uses Bezier-fit |

Easing is applied client-side in the daemon when interpolating between
waypoints. The daemon emits sub-pixel-accumulated `EV_REL`/`EV_ABS` packets
at ~125Hz for smooth motion.

## Coordinate system

`target.kind=abs` (recommended for tablets):
- x and y are uint16 in `[0, 32767]` range (the virtio-tablet's logical range).
- The daemon scales to actual screen geometry. Operator authoring an ability
  doesn't need to know the resolution.
- Use `coord_named` symbols for common targets:
  - `{ named: taskbar.firefox }` → daemon resolves at run-time via known taskbar layout
  - `{ named: address_bar }` → relative to the foreground window's bounds
  - `{ named: random_link }` → daemon picks a random `<a>` tag's screen position

`target.kind=rel`:
- dx, dy are signed deltas. Mouse motion only.

## Authoring tools

For now, abilities are hand-written YAML. A future authoring tool would:
1. Show a screen capture of a typical persona's desktop
2. Operator clicks the path they want, marks dwell points
3. Tool emits the `steps:` list

## Migration from shell-cradle abilities

The 38 abilities under `data/abilities/benign-human-activity/` will be:
1. Marked with `legacy: shell-cradle` in their YAML
2. Re-implemented as HID step-lists in the new format under
   `data/abilities/benign-human-activity/hid/`

Adversary YAMLs under `data/adversaries/` reference abilities by `id`. After
migration, the new HID-format abilities get NEW ids (UUIDs), and adversary
profiles get new versions that reference the new IDs. The old IDs stay valid
for legacy operations.

## Worked examples

### open-email-webmail (HID format)

```yaml
- id: a-new-uuid
  name: Open Email (Webmail) — HID
  description: Operator clicks the browser icon, types the webmail URL letter-by-letter, waits ~8s for inbox to render, scrolls.
  persona: office_worker
  tactic: benign-human-activity
  technique:
    attack_id: x_human_email_webmail
    name: Benign User Activity - Webmail Email
  hid:
    estimated_duration_s: 22
    requires: [tablet, keyboard, display]
    steps:
      - action: move
        target: { named: taskbar.firefox }
        duration_ms: 450
        easing: ease-out
      - action: dwell
        ms: { mean: 200, jitter: 80 }
      - action: click
        button: left
      - action: wait_for
        ms: 1500                         # window appearing
      - action: move
        target: { named: address_bar }
        duration_ms: 280
      - action: click
        button: left
      - action: type
        text: "https://outlook.live.com"
        per_char_ms: { mean: 80, jitter: 30 }
      - action: press
        key: Enter
      - action: dwell
        ms: { mean: 8000, jitter: 2000 }
      - action: scroll
        wheel: down
        ticks: 5
      - action: dwell
        ms: { mean: 4000, jitter: 1000 }
```

### click-random-links (HID format)

```yaml
- id: b-new-uuid
  name: Click Random Links — HID
  description: Operator scrolls a page, picks a random link, clicks, reads briefly, hits back. Repeats 3 times.
  persona: any
  tactic: benign-human-activity
  technique:
    attack_id: x_human_browse_links
    name: Benign User Activity - Random Link Browse
  hid:
    estimated_duration_s: 60
    requires: [tablet, display]
    steps:
      - action: repeat
        count: 3
        steps:
          - action: scroll
            wheel: down
            ticks: { mean: 4, jitter: 2 }
          - action: dwell
            ms: { mean: 3000, jitter: 1500 }
          - action: move
            target: { named: random_link }
            duration_ms: 350
          - action: click
            button: left
          - action: dwell
            ms: { mean: 8000, jitter: 3000 }
          - action: chord
            keys: [LeftAlt, Left]
            hold_ms: 100
          - action: dwell
            ms: 1500
```
