# HID Ability + Profile Schema

This document describes the **platform-aware HID ability schema (v2)**
used by the Human plugin's atomic-ability YAMLs and the profile YAMLs
that compose them.

## Two-level model

```
Profile (= adversary YAML)         "Surf the Web"
   ├── ability                          open-default-browser
   ├── ability                          dwell-natural
   ├── ability                          focus-address-bar-v2
   ├── ability (parametrized)           navigate-to-url(url=...)
   ├── ability                          dwell-reading
   ├── ability                          scroll-page(direction=down, ticks=5)
   └── …
```

| Caldera term | Human term | File location |
|---|---|---|
| Adversary | **Profile** | `data/adversaries/<name>.yml` |
| Ability | **Atomic action** | `data/abilities/benign-human-activity/atomic/<name>.yml` |

A profile is just an ordered list of atomic-ability invocations with
optional per-call args. Profiles never contain raw HID — they always
delegate to atomics. This keeps abilities reusable and lets red-team
abilities intercut with human steps in the same operation.

## Atomic ability YAML — platform-aware shape

```yaml
- id: <stable-kebab-case-id>
  name: <Human Readable Name>
  description: <one-line description>
  tactic: benign-human-activity
  technique:
    attack_id: x_human_<verb>
    name: Benign User Activity - <Verb>
  plugin: human
  tags: [interaction, browser, launch]

  args:                                      # optional
    <arg-name>:
      description: <what it is>
      default:                               # platform-specific defaults supported
        windows: msedge
        linux: firefox
        darwin: Firefox
      # Or a single value:
      # default: "https://example.com"

  platforms:
    windows:
      steps:
        - { action: chord, keys: [LeftMeta, r], hold_ms: 50 }
        - { action: dwell, ms: 400 }
        - { action: type, text: "{{ args.browser_command }}" }
        - { action: press, key: Enter }
    linux:
      steps:
        - { action: press, key: LeftMeta }
        - { action: type, text: "{{ args.browser_command }}" }
        - { action: press, key: Enter }
    darwin:
      steps:
        - { action: chord, keys: [LeftMeta, space], hold_ms: 50 }
        - { action: type, text: "{{ args.browser_command }}" }
        - { action: press, key: Enter }
```

This mirrors stockpile's `platforms.{windows,linux,darwin}` pattern, but
uses the HID action vocabulary rather than `cmd:` / `sh:`.

### Action vocabulary

Each step's `action` MUST be one of the OperatorMessage variants from
`vhost-user-input/src/events.rs`:

| action | required fields | semantics |
|---|---|---|
| `move` | `target`, `duration_ms` | smooth interpolated motion. `target.kind=abs` (tablet) / `rel` (mouse) / `named` (taskbar.firefox, random_link, address_bar) |
| `click` | `button` | press+release |
| `press` | `key` | press+release of a single key |
| `keydown` / `keyup` | `key` | half of a press/release pair (chords) |
| `type` | `text`, `per_char_ms` | one-by-one keystrokes with timing |
| `dwell` | `ms` *or* `ms_range` *or* `{mean, jitter}` | sleep, no input |
| `wait_for` | `ms` | longer semantic wait |
| `scroll` | `wheel`, `ticks` | wheel events |
| `chord` | `keys: [...]`, `hold_ms` | press-all, hold, release-all |
| `raw` | `type_`, `code`, `value` | raw virtio-input event passthrough |
| `repeat` | `count`, `steps: [...]` | sub-sequence run N times (expanded inline) |

Tests in `tests/test_profile_materializer_v2.py` enforce that every step's
action is in this set.

### Args substitution

The materializer uses **double-curly-brace placeholders** (Mustache-style,
not full Jinja2). Two substitution forms:

- **Whole-string placeholder** — preserves type:
  `text: "{{ args.url }}"` becomes the literal arg value (str/int/list).
- **Embedded placeholder** — string replacement:
  `text: "Hello {{ args.name }}!"` turns into `"Hello alice!"`.

Substitution is applied recursively over dicts and lists. Only the
syntax `{{ args.<name> }}` is supported. No filters, no expressions, no
conditionals — keep it dumb.

### Platform fallback policy

The materializer picks `platforms.<target_os>.steps` where `target_os`
is read from:

1. `--os` CLI flag, if given;
2. otherwise `meta.json`'s `os` (or `platform`) key;
3. otherwise the `HUMAN_TARGET_OS` env var.

OS keys are normalized lowercase. Common synonyms map:
`mac/macos/osx → darwin`, `win → windows`.

If `platforms.<target_os>` is **absent** for an ability the profile
references, the materializer raises:

```
KeyError: ability 'foo' is not implemented for OS 'darwin';
available platforms: ['linux', 'windows']
```

Legacy abilities that pre-date the platforms shape (e.g. `dwell.yml`,
`type-text.yml`, `open-browser-firefox.yml`) keep their `hid.steps` list
and run the same on every OS. The materializer accepts both shapes.

### Random dwells

Dwell durations may be:

- **Fixed**: `ms: 1500`
- **Mean+jitter**: `ms: { mean: 800, jitter: 200 }` → uniform in
  `[mean-jitter, mean+jitter]`
- **Range**: `ms_range: [1500, 4000]` or `ms_range: { min: 1500, max: 4000 }`

A profile-scoped splitmix64 RNG (seeded by `--seed`) picks one ms value
per step. **Same seed + same profile = identical output** — guarantees
reproducibility for tests and forensic replay.

## Profile YAML

```yaml
- id: <uuid>
  name: <persona or task label>
  description: <one-line>
  duration_estimate_s: 90
  steps:
    # Plain ability ID (no args needed)
    - { ability: open-default-browser }

    # Ability with per-call arg overrides
    - { ability: navigate-to-url, args: { url: "https://news.ycombinator.com" } }

    - { ability: dwell-reading }
    - { ability: scroll-page, args: { direction: down, ticks: 5 } }
```

The legacy key `atomic_ordering:` is still accepted for backward compat
with old profiles that referenced abilities by UUID.

## OS-hotkey reference table

The new platform-aware abilities use these OS-native hotkeys:

| Action | Windows | Linux (GNOME/KDE) | macOS |
|---|---|---|---|
| Open launcher | Win+R | Super | Cmd+Space |
| Focus address bar | Ctrl+L | Ctrl+L | Cmd+L |
| New tab | Ctrl+T | Ctrl+T | Cmd+T |
| Close tab | Ctrl+W | Ctrl+W | Cmd+W |
| Switch tab forward | Ctrl+Tab | Ctrl+Tab | Ctrl+Tab |
| Switch tab back | Ctrl+Shift+Tab | Ctrl+Shift+Tab | Ctrl+Shift+Tab |
| Find on page | Ctrl+F | Ctrl+F | Cmd+F |
| Browser back | Alt+Left | Alt+Left | Cmd+Left |
| Browser forward | Alt+Right | Alt+Right | Cmd+Right |
| Reload | Ctrl+R / F5 | Ctrl+R / F5 | Cmd+R |
| Lock screen | Win+L | Super+L | Ctrl+Cmd+Q |
| Switch app | Alt+Tab | Alt+Tab | Cmd+Tab |
| Copy / Cut / Paste | Ctrl+C/X/V | Ctrl+C/X/V | Cmd+C/X/V |
| Select all | Ctrl+A | Ctrl+A | Cmd+A |
| Undo / Redo | Ctrl+Z / Ctrl+Y | Ctrl+Z / Ctrl+Y | Cmd+Z / Cmd+Shift+Z |

Every hotkey above is expressible with the existing key constants in
`vhost-user-input/src/events.rs::pub mod key` — no new scancodes needed.

## Worked example: open-default-browser composed into surf-the-web

`open-default-browser.yml` (excerpt):
```yaml
- id: open-default-browser
  args:
    browser_command:
      default:
        windows: msedge
        linux: firefox
        darwin: Firefox
  platforms:
    windows:
      steps:
        - { action: chord, keys: [LeftMeta, r], hold_ms: 50 }
        - { action: dwell, ms: 400 }
        - { action: type, text: "{{ args.browser_command }}", per_char_ms: 90 }
        - { action: dwell, ms: 200 }
        - { action: press, key: Enter }
        - { action: wait_for, ms: 2500 }
```

`surf-the-web.yml` (excerpt):
```yaml
- id: c8cb2ea6-1b8f-40eb-8b9b-b225f0368497
  name: Surf the Web
  steps:
    - { ability: open-default-browser }
    - { ability: dwell-natural }
    - { ability: focus-address-bar-v2 }
    - { ability: navigate-to-url, args: { url: "https://news.ycombinator.com" } }
    - { ability: dwell-reading }
    ...
```

Materialized for `os=windows`:
```json
{"action": "chord", "keys": ["LeftMeta", "r"], "hold_ms": 50}
{"action": "dwell", "ms": 400}
{"action": "type", "text": "msedge", "per_char_ms": 90}
{"action": "dwell", "ms": 200}
{"action": "press", "key": "Enter"}
{"action": "wait_for", "ms": 2500}
{"action": "dwell", "ms": 2326}            // dwell-natural, RNG-picked
{"action": "chord", "keys": ["LeftCtrl", "l"], "hold_ms": 50}
{"action": "dwell", "ms": 150}
{"action": "type", "text": "https://news.ycombinator.com", "per_char_ms": 110}
...
```

Materialized for `os=linux`:
```json
{"action": "press", "key": "LeftMeta"}      // Super, not chord
{"action": "dwell", "ms": 500}
{"action": "type", "text": "firefox", "per_char_ms": 90}
...
```

The same profile YAML produces OS-correct streams for all three desktop
platforms by virtue of each atomic ability's per-platform branch.

## Composing in your own profile

1. Start a new file under `data/adversaries/<persona>.yml`.
2. List the ability IDs you want, in order, under `steps:`.
3. Override defaults inline with `{ability: X, args: {...}}`.
4. Materialize:

```bash
python3 -m pyhuman.profile_materializer \
    --profile data/adversaries/my-profile.yml \
    --abilities data/abilities/benign-human-activity/atomic/ \
    --os windows --seed 42
```

5. Pipe to a microvm's operator socket:

```bash
... | socat - UNIX-CONNECT:/var/run/microvms/<vm>/op.sock
```

## Migration from v1

The 8 original abilities (`open-browser-firefox`, `focus-address-bar`,
`type-text`, `press-enter`, `dwell`, `scroll-down`, `click-link-random`,
`chord-alt-left`) used `hid.steps:` directly with no platform awareness.
They are still loaded for backward compat but new profiles should
reference the v2 platform-aware abilities instead.
