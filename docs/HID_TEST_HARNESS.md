# HID Per-Ability Test Harness

A pytest harness that exercises each HID ability against a live microVM,
auto-composing prerequisite abilities to put the guest in the right state
before the target ability runs. Mirrors the `requires` / fixture pattern
that pytest uses for setup, applied to *guest UI state* rather than
Python objects.

## Why

Atomic abilities don't all start from the same UI state. `type-text`
needs a focused text-input cursor; `scroll-page` needs something
scrollable on screen; `click-link-random` needs a page with links. A
test that just fires `type-text` against an idle desktop produces a
green check that proves nothing.

The harness solves this by treating each ability's **pre-state** as a
declared dependency. The test for `click-link-random` first runs
whatever chain of abilities lands the VM at *browser-shows-page-with-
links*, **then** runs the target, **then** verifies post-state.

## Schema additions

Three new optional top-level fields per atomic-ability YAML and per
adversary/profile YAML.

### `state_requires:`

Free-text identifiers, list. The harness must reach **all** of these
states before running the ability. Empty / missing means "no setup".

### `state_provides:`

Free-text identifiers, list. After the ability runs successfully it
guarantees the guest is in these states. The harness's planner uses
this to chain abilities.

### `verify:`

List of post-condition assertions evaluated against the framebuffer
captured **after** the ability finishes. Three assertion kinds:

| key | value | meaning |
|---|---|---|
| `ocr_contains` | string | OCR of the post-frame must contain this substring (case-insensitive). |
| `ocr_contains_any` | list[string] | OCR must contain at least one of the listed substrings. |
| `pixel_change_pct_min` | int | At least N% of pixel bytes differ from the pre-frame. |
| `pixel_change_pct_max` | int | At most N% of pixel bytes differ (use to detect that *nothing should have moved*, e.g. a successful dwell). |

Multiple assertions are AND-ed. If `verify:` is omitted the test
passes whenever the ability dispatched without raising.

## Worked example: `click-first-search-result.yml` (mockup)

```yaml
---
- id: 7c9b2e5d-1f4a-4d8e-bb1a-25e9f2c0a8b1
  name: Click first search result
  description: |
    Move the cursor to the first <a> in the rendered search-results
    page and click it. Assumes the browser is showing a results page.
  tactic: benign-human-activity
  technique:
    attack_id: x_human_click_first_result
    name: Benign User Activity - Click First Search Result
  plugin: human
  tags: [interaction, browser, click]

  # NEW: pre-conditions the harness must satisfy before running.
  state_requires:
    - browser-shows-search-results

  # NEW: states the harness can chain to once this ability succeeds.
  state_provides:
    - browser-shows-target-page

  # NEW: post-condition assertions evaluated on the post-run frame.
  verify:
    - pixel_change_pct_min: 5    # navigation always repaints heavily
    - ocr_contains_any:
        - "http"
        - "https"

  platforms:
    windows:
      steps:
        - { action: move, target: { named: first_search_result },
            duration_ms: 350, easing: ease-out }
        - { action: dwell, ms: 150 }
        - { action: click, button: left }
        - { action: wait_for, ms: 2500 }
    linux: { steps: [...] }
    darwin: { steps: [...] }
```

## State vocabulary (initial)

These are the canonical state strings the bootstrap fixture seeds.
Ability authors should reuse them when possible; new ones are fine
when nothing existing fits.

| state | meaning |
|---|---|
| `guest-at-desktop` | Logged in, no modal dialog, taskbar visible. |
| `guest-at-cmd-prompt` | A focused `cmd.exe` / shell window has the keyboard. |
| `text-cursor-active` | Some app has a focused text-input control (run dialog, address bar, notepad, etc.). |
| `mouse-cursor-positioned` | Cursor is over a clickable element. |
| `viewport-scrollable` | Foreground window has scrollable content. |
| `browser-open` | Default browser is open and painted. |
| `browser-address-bar-focused` | Browser is open and Ctrl/Cmd+L was pressed. |
| `browser-shows-search-results` | Browser is showing a results page (Google etc.). |
| `browser-shows-target-page` | Browser navigated past a results page to a chosen link. |

## Planner

The harness reads `tests/integration/fixtures/state_providers.yml` —
a map from each state string to the list of abilities (by id or stem)
that achieve it. Planning is recursive: target_ability →
state_requires → providers → THEIR state_requires → ... terminating
at abilities with empty `state_requires`.

The planner is intentionally simple:
- depth-first, take first provider for each state;
- detect cycles by tracking visited states and bailing with a clear
  error;
- short-circuit on `guest-at-desktop` (assumed reached after VM boot).

## Verification execution

After dispatching the ability's OperatorMessages over the operator
socket, the harness:

1. Captures `pre_frame` via the RFB probe (lifted from `/tmp/probe_fb.py`).
2. Sleeps `wait_for_settle_ms` (default 750 ms) so the guest can repaint.
3. Captures `post_frame`.
4. Computes `pixel_change_pct = byte_diff_count / total_bytes * 100`.
5. If any `ocr_*` assertion is present, runs `pytesseract` on `post_frame`.
6. Evaluates each assertion. First failure raises `AssertionError`.

## Test discovery

`tests/integration/test_each_ability.py` enumerates every YAML in
`data/abilities/benign-human-activity/atomic/` and every adversary
profile, generating one parametrized pytest case per ability with
`@pytest.mark.requires_running_vm`. CI without a live VM skips them
all; local runs against a known good `host_id` run the full matrix.

## What the harness does NOT do

- Does **not** modify ability YAMLs.
- Does **not** boot or restart VMs.
- Does **not** modify daemon binaries.
- Does **not** push to remote / merge.

Schema fields are read if present, ignored if absent. Abilities
without `state_requires:` are tested standalone (assumed to work from
`guest-at-desktop`). Abilities without `verify:` pass on dispatch
success alone.
