# Human Plugin Rewrite — Timestone Live UI

This document captures the layout and data flow for the rewrite of the MITRE
Human plugin under the Timestone effort. The legacy view (a "build a download
cradle" generator at `gui/views/human.vue`) is being replaced with a live,
range-aware control surface that drives the new `control_server.py`
JSON-RPC backend (work in progress on a sibling branch).

## High-level layout

```
+---------------------------------------------------------------------------+
| HEADER                                                                    |
|   "Human (Live) — <range-profile-name> (<n hosts>)"   [Refresh] [Logs]    |
+---------------------------------------------------------------------------+
| HOSTS PANEL (left, ~25%) | COMMAND STREAM (center, ~45%) | GUI VIEWER     |
|                          |                               | (right, ~30%) |
|  - active range hosts    |  - Selected host header       |  - iframe slot |
|  - search/filter         |  - Workflow dropdown          |    for noVNC   |
|  - per-host status badge |    (mirrors Range's profile   |  - clearly     |
|    (idle/running/error)  |     dropdown pattern)         |    marked TODO |
|  - click selects host    |  - Args text input            |    until VNC   |
|                          |  - "Run" / "Stop" buttons     |    ws URL is   |
|                          |  - Free-form command box      |    wired       |
|                          |  - stdout/stderr rolling tail |                |
+---------------------------------------------------------------------------+
```

## Component breakdown

* **HostsPanel (left).** Renders `hosts[]` returned from
  `GET /plugin/human/api/hosts`. The Caldera Range plugin already exposes
  per-profile inventory via its onprem/cloud routes (see
  `plugins/range/hook.py:124` `POST /plugin/range/onprem/hosts` and the
  microvm substrate provider). For this first pass we proxy through a Human
  endpoint so the Vue layer never has to know which Range backend (cloud,
  onprem, microvm) actually supplied the host. If the active range cannot be
  read directly, the endpoint returns a stub list with a TODO log line.
* **CommandStream (center).** Two sub-areas:
  - **Workflow assignment.** Mirrors the dropdown pattern from
    `plugins/range/gui/views/range.vue:27-56` (the "Select Profile"
    dropdown). The Human plugin's dropdown will list workflows returned
    from `GET /plugin/human/api/workflows` (eventually backed by
    `control_server.py`'s `_list` JSON-RPC method; stubbed for now).
  - **Live I/O.** A text input fires `POST /plugin/human/api/run` with
    `{host_id, workflow, args}`. Backend (a separate agent's task) will
    relay over the control_server transport to the microVM. Responses are
    appended to `command_log[]` and rendered as a rolling tail (oldest
    pruned past N=200). Each entry tags `host_id`, direction
    (`stdin`/`stdout`/`stderr`), and timestamp.
* **GuiViewer (right).** Placeholder `<div class="todo-panel">` containing
  a clearly-marked TODO. Once the noVNC bridge is wired, replace with
  `<iframe :src="vncUrl">`. The host selection drives `vncUrl` via a
  computed property (currently always falsy).

## Data model (Composition API refs)

```js
const hosts = ref([])              // [{id, name, ip, status, vnc_ws}]
const workflows = ref([])          // [{id, name, description}]
const assignments = reactive({})   // assignments[host_id] = {workflow_id, args, status}
const commandLog = ref([])         // [{ts, host_id, direction, line}]
const selectedHost = ref(null)
const isDropdownOpen = ref(false)  // mirrors range.vue dropdown state
```

## Range plugin patterns we mirror

* **Dropdown shell.** `range.vue:27-56` — `dropdown searchable is-active`
  toggle pattern with a Bulma `dropdown-trigger` button and a
  `dropdown-menu`. We reuse the exact class structure, swapping
  `currentProfiles` for `workflows`.
* **Composition API + `inject('$api')`.** `range.vue:357` —
  `const $api = inject('$api')` — same wiring; calls go to the Human
  routes registered in `hook.py`.
* **Composable extraction.** Range moves view logic into
  `composables/useRangeView.js`. For this first pass we keep everything in
  `human.vue` for clarity; if the file grows past ~400 lines we split into
  `composables/useHumanView.js`.
* **Section toggling.** Range uses Bulma `tabs` for cloud/onprem; Human
  may eventually grow tabs (live vs. cradle-builder), but for now the
  cradle-builder is removed entirely.

## Backend route additions (this branch)

Registered in `hook.py`, implemented in `app/human_api.py`:

* `GET /plugin/human/api/hosts`     — host inventory proxy (stub w/ TODO).
* `GET /plugin/human/api/workflows` — workflow list (stub list for now;
  later: forwards to `control_server.py` `_list`).
* `POST /plugin/human/api/run`      — body `{host_id, workflow, args}`.
  Currently echoes the request back. The transport-to-VM side is the
  responsibility of a separate agent.

## Open questions

1. Where exactly does the active range's host inventory live in the
   running Caldera process? `useRangeView.js:109-114` reads
   `inv.all.hosts` — that's a frontend-cached inventory. For a server-side
   proxy we likely need to talk to the Range plugin's data store directly
   or hit one of its existing HTTP endpoints (e.g.,
   `POST /plugin/range/onprem/hosts`). Decided: stub for now, leave a
   TODO referencing the Range routes in `plugins/range/hook.py`.
2. VNC websocket URL shape — depends on how `control_server.py` exposes
   the per-VM noVNC bridge. TODO panel until that contract lands.
3. Persisting `assignments` across page reloads — punted for v1; keep
   in component state only.
