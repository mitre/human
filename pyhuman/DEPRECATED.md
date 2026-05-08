# pyhuman/ — DEPRECATED on `timestone-human-rewrite`

This directory contains the original Python-based emulation runtime for the
Human plugin: `emulation_loop.py`, `control_server.py`, the `workflows/` tree,
and the supporting libraries that drove human behavior by running Python on
the target host.

## Why it's deprecated

The `timestone-human-rewrite` branch pivots Human to a **Caldera-ability-driven
runtime**. The new model:

- Human is a Caldera plugin that ships **ability YAMLs** under
  `data/abilities/benign-human-activity/`.
- Those abilities are picked up by Caldera's normal ability loader and run by
  the **sandcat** agent on each target, using whatever shell is already
  available there (PowerShell / cmd on Windows, sh on Linux, sh on macOS).
- An **adversary YAML** under `data/adversaries/office-worker.yml` bundles the
  abilities into a persona that operators can drop into an operation alongside
  a red-team adversary plan. The benign + malicious activity then show up
  side-by-side on the AE timeline.
- **No Python is required on the target host.** This is the whole point of
  the pivot: AE/range infrastructure rarely has a Python interpreter on every
  endpoint, and we don't want to make installing one a prerequisite for
  benign-activity emulation.

## Why it's still here (not deleted)

1. Reference - the workflow library (`pyhuman/library/workflows/...`) encodes
   a lot of the original MITRE Human plugin's behavior. The new ability YAMLs
   re-implement the user-facing pieces, but the library is a useful crib.
2. Legacy upstream-MITRE compatibility - if anyone runs this plugin against
   stock MITRE Caldera (not Timestone), the old emulation_loop is the path
   that works for them.
3. The Vue UI (`gui/views/human.vue`) and REST endpoints
   (`/plugin/human/api/...`) are still wired through `app/human_api.py` and
   `app/human_svc.py`. Those will be re-pointed at the ability runtime in a
   follow-up commit; until then, the old endpoints keep returning the
   `workflows` list so the UI doesn't break.

## What NOT to add here going forward

- New behaviors. Add a new ability YAML under
  `data/abilities/benign-human-activity/` instead.
- Bug fixes that only matter to the Python runtime - fix the equivalent
  ability YAML.

## Removal plan

Once `app/human_svc.py` is re-pointed at `data/abilities/...` and the Vue UI
shows abilities (not workflows), this directory will be removed in a single
commit. Tracked under the rewrite branch's TODO list.
