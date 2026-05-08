# Human ability + profile model

## Two-level model

The Human plugin uses Caldera's standard adversary→ability hierarchy,
sized for human emulation:

```
Profile (= Caldera adversary YAML)         "Surf the Web"
   ├── Atomic ability                          open-browser-firefox
   ├── Atomic ability                          focus-address-bar
   ├── Atomic ability (parametrized)           type-text(text="https://news.ycombinator.com")
   ├── Atomic ability                          press-enter
   ├── Atomic ability (parametrized)           dwell(ms=8000)
   ├── Atomic ability                          scroll-down
   ├── Atomic ability                          click-link-random
   ├── Atomic ability (parametrized)           dwell(ms=12000)
   └── …
```

| Caldera term | Human term | File location | Examples |
|---|---|---|---|
| Adversary | **Profile** | `data/adversaries/` | `surf-the-web.yml`, `office-worker.yml`, `developer.yml` |
| Ability | **Atomic action** | `data/abilities/benign-human-activity/atomic/` | `open-browser-firefox.yml`, `type-text.yml`, `press-enter.yml`, `dwell.yml` |

This is the same shape as red-team operations in stockpile: an
adversary references atomic abilities; a profile references atomic
human actions. Operators can mix: an adversary can include both red
abilities AND human-profile abilities in the same operation, which is
exactly the AE goal — emulate a real user doing benign work while a
red-team adversary moves laterally.

## Why atomic, not chunky

The earlier 38 abilities were chunky: one ability did the whole "open
email + read + click around" sequence. That's wrong because:

1. **No reuse.** Every ability re-implemented "type a URL" inline.
2. **Profiles can't compose.** A "browse-then-email" profile would
   need a new chunky ability or copy-paste the URL-typing logic.
3. **Caldera operations can't intercut.** If a red ability takes 30s
   in the middle of a human's session, the chunky ability has to
   yield — atomic abilities just take their natural turn in the
   ordered queue.

Atomic = one HID act, parametrized. Profile = ordered list of those.

## Atomic ability YAML

```yaml
- id: <uuid>
  name: <short verb-phrase>
  description: <one-line, what the user perceives>
  tactic: benign-human-activity
  technique:
    attack_id: x_human_<verb>
    name: Benign User Activity - <Verb>
  hid:
    estimated_duration_s: <int>      # for UI timeline
    requires:
      - tablet | mouse | keyboard | display
    args:                            # optional — parametrize per-call
      - name: text
        type: string
        default: ""
      - name: per_char_ms
        type: int
        default: 80
    steps:
      # step uses {{ args.text }} etc. for substitution; daemon does
      # the substitution at run time.
      - action: type
        text: "{{ args.text }}"
        per_char_ms: { mean: "{{ args.per_char_ms }}", jitter: 30 }
```

## Profile YAML (Caldera adversary)

```yaml
- id: <uuid>
  name: <persona or task label>
  description: <what the human is trying to accomplish>
  atomic_ordering:
    # Plain ability ID (no args needed)
    - <ability_uuid>
    # Ability with args
    - {ability: <ability_uuid>, args: {text: "https://news.ycombinator.com"}}
    - {ability: <dwell_uuid>, args: {ms: 8000}}
    # … etc.
```

The arg-bound entry is the same shape Caldera's stockpile uses for
parametrized abilities. The Human plugin's `human_svc` materializes
the ordering into a sequence of HID steps the human-actor daemon
replays.

## Action vocabulary (used inside atomic abilities)

| action | required fields | semantics |
|---|---|---|
| `move` | `target`, `duration_ms` | smooth interpolated motion. `target.kind=abs` for tablets, `kind=rel` for mouse, `target.named=…` for known coords |
| `click` | `button` | press+release |
| `press` | `key` | press+release |
| `keydown` / `keyup` | `key` | half of the press/release pair (chords) |
| `type` | `text`, `per_char_ms` | one-by-one keystrokes with timing |
| `dwell` | `ms` | sleep, no input emission. `ms` may be int or `{mean, jitter}` |
| `wait_for` | `ms` | sleep (longer); semantic hint |
| `scroll` | `wheel`, `ticks` | wheel events |
| `chord` | `keys: [...]`, `hold_ms` | press-all, hold, release-all |
| `repeat` | `count`, `steps: [...]` | sub-sequence run N times |

## Easing (for `move`)

| name | shape | when to use |
|---|---|---|
| `linear` | constant velocity | least human |
| `ease-out` | starts fast, slows | pointing at target |
| `ease-in-out` | accelerate, peak, decelerate | longer drags |
| `human` | Bezier-fit to real-mouse studies | most realistic |

## Coordinate system (for `move`)

`target.kind=abs` (tablet, recommended): x, y in [0, 32767].
`target.kind=rel` (mouse): dx, dy as signed deltas.
`target.named=...`: daemon resolves at run time:

| named | resolved to |
|---|---|
| `taskbar.firefox` | (configured taskbar layout) |
| `taskbar.chrome` | (configured taskbar layout) |
| `address_bar` | foreground window's URL bar |
| `random_link` | screen position of a random `<a>` in the foreground browser |
| `app.<name>.window_close` | close button of a named app window |

## Migration plan from the 38 chunky abilities

1. Catalog the 38 — list which atomic actions each one was made of.
2. Write the ~20 atomic ability YAMLs (one per primitive verb).
3. Convert the 6 persona adversaries to reference atomic abilities.
4. Mark the original 38 with `legacy: shell-cradle` and stop loading
   them in the human-actor pipeline (they remain queryable for
   backward-compatibility with any operator who pinned them by ID).
5. Add a compatibility shim in `human_svc` so `surf-the-web`-style
   profiles materialize correctly into HID step sequences.

## Worked example: "Surf the Web"

### Atomic abilities (each gets its own YAML)

`open-browser-firefox`:
```yaml
- id: <uuid>
  name: Open Browser (Firefox)
  hid:
    estimated_duration_s: 3
    requires: [tablet, display]
    steps:
      - { action: move, target: { named: taskbar.firefox }, duration_ms: 450, easing: ease-out }
      - { action: dwell, ms: { mean: 200, jitter: 80 } }
      - { action: click, button: left }
      - { action: wait_for, ms: 1500 }
```

`focus-address-bar`:
```yaml
- id: <uuid>
  name: Focus Address Bar
  hid:
    estimated_duration_s: 1
    requires: [keyboard]
    steps:
      - { action: chord, keys: [LeftControl, l], hold_ms: 50 }
```

`type-text` (parametrized):
```yaml
- id: <uuid>
  name: Type Text
  hid:
    estimated_duration_s: 4   # estimate; varies with text length
    requires: [keyboard]
    args:
      - name: text
        type: string
        default: ""
      - name: per_char_ms
        type: int
        default: 80
    steps:
      - { action: type, text: "{{ args.text }}",
          per_char_ms: { mean: "{{ args.per_char_ms }}", jitter: 30 } }
```

`press-enter`:
```yaml
- id: <uuid>
  name: Press Enter
  hid:
    estimated_duration_s: 0
    requires: [keyboard]
    steps:
      - { action: press, key: Enter }
```

`dwell` (parametrized):
```yaml
- id: <uuid>
  name: Dwell (read / pause)
  hid:
    estimated_duration_s: 8     # placeholder; real value comes from args
    requires: []
    args:
      - { name: ms, type: int, default: 5000 }
    steps:
      - { action: dwell, ms: "{{ args.ms }}" }
```

`scroll-down`:
```yaml
- id: <uuid>
  name: Scroll Down
  hid:
    estimated_duration_s: 1
    requires: [tablet]
    args:
      - { name: ticks, type: int, default: 4 }
    steps:
      - { action: scroll, wheel: down, ticks: "{{ args.ticks }}" }
```

`click-link-random`:
```yaml
- id: <uuid>
  name: Click Random Link
  hid:
    estimated_duration_s: 1
    requires: [tablet, display]
    steps:
      - { action: move, target: { named: random_link }, duration_ms: 350 }
      - { action: click, button: left }
```

### Profile (adversary YAML)

`surf-the-web.yml`:
```yaml
- id: <uuid>
  name: Surf the Web
  description: |
    Operator opens Firefox, navigates to a news aggregator, idles to
    "read", scrolls, clicks a random link, idles, scrolls more, alt-back,
    repeats 2 more times.
  atomic_ordering:
    - <open-browser-firefox>
    - <focus-address-bar>
    - {ability: <type-text>, args: {text: "https://news.ycombinator.com"}}
    - <press-enter>
    - {ability: <dwell>, args: {ms: 8000}}
    - {ability: <scroll-down>, args: {ticks: 5}}
    - <click-link-random>
    - {ability: <dwell>, args: {ms: 12000}}
    - {ability: <scroll-down>, args: {ticks: 3}}
    - {ability: <chord-alt-left>, args: {}}     # back to news listing
    - {ability: <dwell>, args: {ms: 3000}}
    - {ability: <scroll-down>, args: {ticks: 4}}
    - <click-link-random>
    - {ability: <dwell>, args: {ms: 10000}}
```

The same atomic abilities compose into other profiles (Office Worker,
Developer, Sales Rep) by changing the ordering and the arg values.

## Where Vue renders this

`gui/views/human.vue`:

- **Profile selector** (per host): operator picks "Surf the Web",
  "Office Worker", etc. — these come from `data/adversaries/` filtered
  to `tactic includes benign-human-activity` or by tag.
- **Step preview panel**: when a profile is selected, the panel
  expands the profile's atomic ordering into a flat list of steps.
  Per-ability args are rendered inline (e.g. `type-text("https://...")`).
- **Live viewer**: VNC iframe of the selected host's framebuffer.
- **Now-running indicator**: highlights the currently-executing
  step in the preview list (driven by an SSE from the human-actor
  daemon).
