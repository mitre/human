<template>
  <div class="human-live">
    <!-- HEADER ============================================================ -->
    <section class="human-header mb-2">
      <h2 class="title is-4">
        Human (Live)
        <span class="subtitle is-6 ml-2" v-if="rangeProfileName">
          — {{ rangeProfileName }} ({{ hosts.length }} hosts)
        </span>
      </h2>
      <div class="is-flex is-align-items-center">
        <button class="button is-dark is-small mr-2" @click="refreshAll" :disabled="loading">
          <span class="icon is-small"><i class="fas fa-sync-alt" :class="{ 'fa-spin': loading }"></i></span>
          <span>Refresh</span>
        </button>
        <button class="button is-dark is-small" @click="commandLog = []">
          <span class="icon is-small"><i class="fas fa-trash"></i></span>
          <span>Clear log</span>
        </button>
      </div>
    </section>

    <div class="human-grid">
      <!-- HOSTS PANEL (left) ============================================== -->
      <aside class="hosts-panel">
        <h3 class="title is-6">Hosts</h3>
        <input
          class="input is-small mb-2"
          type="text"
          placeholder="Filter hosts..."
          v-model="hostFilter"
        />
        <ul class="hosts-list">
          <li
            v-for="host in filteredHosts"
            :key="host.id"
            :class="{ 'is-selected': selectedHostId === host.id }"
            @click="selectHost(host.id)"
          >
            <div class="host-row">
              <span class="host-name">{{ host.name || host.id }}</span>
              <span
                class="tag is-small"
                :class="statusTagClass(assignments[host.id]?.status)"
              >
                {{ assignments[host.id]?.status || 'idle' }}
              </span>
            </div>
            <div class="host-meta">
              <small>{{ host.ip || '—' }}</small>
            </div>
          </li>
          <li v-if="filteredHosts.length === 0" class="has-text-centered has-text-grey">
            <em>No hosts</em>
          </li>
        </ul>
      </aside>

      <!-- COMMAND STREAM (center) ========================================= -->
      <main class="command-stream">
        <div v-if="!selectedHost" class="notification is-dark has-text-centered">
          <p><em>Select a host on the left to assign human abilities or send commands.</em></p>
        </div>

        <div v-else>
          <h3 class="title is-5">{{ selectedHost.name || selectedHost.id }}</h3>

          <!-- Human-ability dropdown (mirrors range.vue:27-56 pattern) -->
          <div class="field">
            <label class="label is-small">Human Ability</label>
            <div
              class="dropdown searchable is-flex-grow-1"
              :class="{ 'is-active': isDropdownOpen }"
            >
              <div class="dropdown-trigger">
                <button
                  class="button is-fullwidth"
                  type="button"
                  aria-haspopup="true"
                  aria-controls="workflow-dropdown-menu"
                  @click="isDropdownOpen = !isDropdownOpen"
                >
                  <span>{{ selectedWorkflowName || 'Select Human Ability' }}</span>
                  <span class="icon is-small">
                    <i class="fas fa-angle-down"></i>
                  </span>
                </button>
              </div>
              <div class="dropdown-menu is-fullwidth" id="workflow-dropdown-menu" role="menu">
                <div class="dropdown-content">
                  <a
                    class="dropdown-item"
                    v-for="wf in workflows"
                    :key="wf.id"
                    :class="{ 'is-active': assignments[selectedHostId]?.workflow_id === wf.id }"
                    @click="assignWorkflow(wf); isDropdownOpen = false"
                  >
                    <strong>{{ wf.name }}</strong>
                    <p class="is-size-7">{{ wf.description }}</p>
                  </a>
                  <p
                    class="has-text-centered"
                    v-if="workflows.length === 0"
                  >
                    No human abilities available
                  </p>
                </div>
              </div>
            </div>
          </div>

          <!-- HID step preview ============================================ -->
          <!-- Shows the action sequence (move/click/type/dwell) that this
               ability will replay through the vhost-user-input daemon. The
               whole point of the rewrite is that abilities are NOT shell
               cradles — they're step-lists of HID events. The viewer below
               will show the actual cursor + keystrokes hit the guest. -->
          <div v-if="selectedAbilitySteps.length" class="step-preview mb-3">
            <div class="is-flex is-justify-content-space-between is-align-items-baseline">
              <h4 class="title is-6 mb-1">Step preview</h4>
              <small class="has-text-grey">
                {{ selectedAbilitySteps.length }} steps
                · ~{{ selectedAbilityDurationS }}s estimated
              </small>
            </div>
            <ol class="step-list">
              <li v-for="(s, i) in selectedAbilitySteps" :key="i" class="step-row">
                <span class="step-idx has-text-grey">{{ i + 1 }}</span>
                <span class="step-action tag is-dark is-small">{{ s.action }}</span>
                <span class="step-detail">{{ stepDetail(s) }}</span>
              </li>
            </ol>
          </div>
          <div v-else-if="selectedWorkflowName" class="notification is-dark py-2 px-3 mb-3">
            <p class="is-size-7">
              Legacy shell-cradle ability (no HID step-list). It will run as
              a single shell command via sandcat, not through the input
              daemon. Convert to the HID format
              (<code>data/abilities/HID_ABILITY_SCHEMA.md</code>) for real
              human emulation.
            </p>
          </div>

          <!-- Args + Run -->
          <div class="field">
            <label class="label is-small">Args (passed to human ability)</label>
            <div class="field has-addons">
              <div class="control is-expanded">
                <input
                  class="input is-small"
                  type="text"
                  placeholder="--flag value ..."
                  v-model="argsInput"
                  @keyup.enter="runAssignedWorkflow"
                />
              </div>
              <div class="control">
                <button
                  class="button is-dark is-small"
                  :disabled="!assignments[selectedHostId]?.workflow_id"
                  @click="runAssignedWorkflow"
                >
                  Run
                </button>
              </div>
            </div>
          </div>

          <!-- Free-form ad-hoc command -->
          <div class="field">
            <label class="label is-small">Ad-hoc command</label>
            <div class="field has-addons">
              <div class="control is-expanded">
                <input
                  class="input is-small"
                  type="text"
                  placeholder="raw command line..."
                  v-model="adhocInput"
                  @keyup.enter="runAdhoc"
                />
              </div>
              <div class="control">
                <button class="button is-dark is-small" @click="runAdhoc" :disabled="!adhocInput.trim()">
                  Send
                </button>
              </div>
            </div>
          </div>

          <!-- Live I/O log -->
          <div class="io-panel">
            <h4 class="title is-6 mb-2">Output</h4>
            <pre class="io-log">
<template v-for="(entry, i) in scopedCommandLog" :key="i"><span :class="['io-line', 'io-' + entry.direction]">[{{ formatTs(entry.ts) }}] [{{ entry.direction }}] {{ entry.line }}
</span></template>
<span v-if="scopedCommandLog.length === 0" class="has-text-grey">(no output yet)</span>
            </pre>
          </div>
        </div>
      </main>

      <!-- GUI VIEWER (right) ============================================== -->
      <!-- Live framebuffer of the selected host. The vhost-user-gpu-2d
           daemon (timestone/vhost-user-daemons/) renders the guest's
           virtio-gpu surface and serves it over RFB; a host-side
           websockify proxy bridges that to a websocket the noVNC
           component below consumes. The host's `vnc_ws` field is set
           by the Range provider when `session_type: gui` is on the
           image and the GPU daemon is alive. -->
      <aside class="gui-viewer">
        <h3 class="title is-6">
          Live Endpoint
          <span v-if="currentStepIdx != null" class="tag is-link is-small ml-2">
            step {{ currentStepIdx + 1 }} / {{ selectedAbilitySteps.length }}
          </span>
        </h3>
        <div v-if="selectedHost && selectedHost.vnc_ws" class="vnc-wrapper">
          <iframe :src="selectedHost.vnc_ws" class="vnc-frame"></iframe>
        </div>
        <div v-else class="notification is-dark todo-panel">
          <p>
            <strong>Viewer not connected.</strong> The host has no
            <code>vnc_ws</code> yet — the vhost-user-gpu-2d daemon for
            this microVM either isn't running, or its websockify bridge
            hasn't been registered with the Range provider.
          </p>
          <p class="is-size-7 mt-2">
            Selected host: <code>{{ selectedHost?.id || '—' }}</code>
          </p>
          <p class="is-size-7 mt-2">
            Spawn the daemon manually for this host:<br/>
            <code class="is-size-7">vhost-user-gpu-2d --socket /tmp/ts-gpu-{{ selectedHost?.id || 'X' }}.sock --vnc 127.0.0.1:5900</code>
          </p>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, inject } from 'vue'

// Mirrors range.vue (line 357): the host app injects $api for HTTP calls.
const $api = inject('$api')

// ---- State ---------------------------------------------------------------
const hosts = ref([])                  // [{id, name, ip, status, vnc_ws}]
const workflows = ref([])              // [{id, name, description}]
const assignments = reactive({})       // assignments[host_id] = {workflow_id, args, status}
const commandLog = ref([])             // [{ts, host_id, direction, line}]
const selectedHostId = ref(null)
const isDropdownOpen = ref(false)
const argsInput = ref('')
const adhocInput = ref('')
const hostFilter = ref('')
const loading = ref(false)
const rangeProfileName = ref('')       // populated from /hosts response if available

const MAX_LOG_LINES = 200

// ---- Computed ------------------------------------------------------------
const selectedHost = computed(() =>
  hosts.value.find(h => h.id === selectedHostId.value) || null
)

const selectedWorkflowName = computed(() => {
  const a = assignments[selectedHostId.value]
  if (!a) return ''
  const wf = workflows.value.find(w => w.id === a.workflow_id)
  return wf ? wf.name : ''
})

const filteredHosts = computed(() => {
  const q = hostFilter.value.trim().toLowerCase()
  if (!q) return hosts.value
  return hosts.value.filter(h =>
    (h.name || '').toLowerCase().includes(q) ||
    (h.ip || '').toLowerCase().includes(q) ||
    (h.id || '').toLowerCase().includes(q)
  )
})

const scopedCommandLog = computed(() =>
  commandLog.value.filter(e => e.host_id === selectedHostId.value)
)

// ---- HID step preview ----------------------------------------------------
// `selectedAbilitySteps` is the list of HID steps for the currently-selected
// ability, IF the ability's YAML is the new HID format. Legacy shell-cradle
// abilities have no `hid.steps` key — those render the legacy notice block
// instead.
const selectedAbilitySteps = computed(() => {
  const a = assignments[selectedHostId.value]
  if (!a) return []
  const wf = workflows.value.find(w => w.id === a.workflow_id)
  return (wf && wf.hid && Array.isArray(wf.hid.steps)) ? wf.hid.steps : []
})

const selectedAbilityDurationS = computed(() => {
  const a = assignments[selectedHostId.value]
  if (!a) return 0
  const wf = workflows.value.find(w => w.id === a.workflow_id)
  return (wf && wf.hid && wf.hid.estimated_duration_s) || 0
})

// `currentStepIdx` is the step the in-flight human-actor is replaying right
// now (0-indexed). Set by the assignment-status SSE / poll once we wire it.
// Until then it stays null so the viewer header just shows the static label.
const currentStepIdx = ref(null)

// Pretty one-line description of a step row, for the preview list. We keep
// this short enough that 10-30 steps fit in the visible panel without scroll.
function stepDetail(step) {
  switch (step.action) {
    case 'move': {
      const t = step.target || {}
      const where = t.named ? t.named
        : t.kind === 'abs' ? `(${t.x}, ${t.y})`
        : t.kind === 'rel' ? `Δ(${t.dx ?? 0}, ${t.dy ?? 0})`
        : '?'
      return `→ ${where} (${step.duration_ms || 0}ms${step.easing ? ', ' + step.easing : ''})`
    }
    case 'click':    return step.button || 'left'
    case 'press':    return step.key || ''
    case 'keydown':  return `↓ ${step.key || ''}`
    case 'keyup':    return `↑ ${step.key || ''}`
    case 'type':     return JSON.stringify((step.text || '').slice(0, 40))
    case 'dwell':
    case 'wait_for': {
      const ms = step.ms
      if (ms == null) return ''
      if (typeof ms === 'object') return `${ms.mean}ms ± ${ms.jitter || 0}`
      return `${ms}ms`
    }
    case 'scroll':   return `${step.wheel || 'down'} × ${step.ticks || 1}`
    case 'chord':    return (step.keys || []).join(' + ')
    case 'repeat':   return `× ${step.count || 0}`
    default:         return ''
  }
}

// ---- Helpers -------------------------------------------------------------
function statusTagClass(status) {
  switch (status) {
    case 'running': return 'is-info'
    case 'success': return 'is-success'
    case 'error':   return 'is-danger'
    default:        return 'is-light'
  }
}

function formatTs(ts) {
  try { return new Date(ts).toLocaleTimeString() } catch (_) { return '' }
}

function appendLog(host_id, direction, line) {
  commandLog.value.push({ ts: Date.now(), host_id, direction, line })
  if (commandLog.value.length > MAX_LOG_LINES) {
    commandLog.value.splice(0, commandLog.value.length - MAX_LOG_LINES)
  }
}

function ensureAssignment(host_id) {
  if (!assignments[host_id]) {
    assignments[host_id] = { workflow_id: null, args: '', status: 'idle' }
  }
  return assignments[host_id]
}

// ---- Actions -------------------------------------------------------------
function selectHost(host_id) {
  selectedHostId.value = host_id
  ensureAssignment(host_id)
  argsInput.value = assignments[host_id].args || ''
}

function assignWorkflow(wf) {
  if (!selectedHostId.value) return
  const a = ensureAssignment(selectedHostId.value)
  a.workflow_id = wf.id
}

async function fetchHosts() {
  try {
    const res = await $api.get('/plugin/human/api/hosts')
    hosts.value = res.data?.hosts || []
    rangeProfileName.value = res.data?.profile || ''
  } catch (err) {
    console.error('fetchHosts failed', err)
    appendLog('_system', 'stderr', `Failed to fetch hosts: ${err.message || err}`)
  }
}

async function fetchWorkflows() {
  try {
    const res = await $api.get('/plugin/human/api/workflows')
    workflows.value = res.data?.workflows || []
  } catch (err) {
    console.error('fetchWorkflows failed', err)
    appendLog('_system', 'stderr', `Failed to fetch workflows: ${err.message || err}`)
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([fetchHosts(), fetchWorkflows()])
  } finally {
    loading.value = false
  }
}

async function runAssignedWorkflow() {
  if (!selectedHostId.value) return
  const a = ensureAssignment(selectedHostId.value)
  if (!a.workflow_id) return
  a.args = argsInput.value
  a.status = 'running'

  // HID profiles carry a `profile_id` (set by human_svc._discover_profiles).
  // Those go through the SSE pipe so the operator socket receives one
  // OperatorMessage per step, with the UI's step-counter following along.
  // Legacy shell-cradle abilities (no profile_id) keep using /api/run.
  const wf = workflows.value.find(w => w.id === a.workflow_id)
  if (wf && wf.profile_id) {
    return runProfileSse(selectedHostId.value, wf.profile_id, a)
  }

  appendLog(selectedHostId.value, 'stdin', `[human-ability] ${a.workflow_id} ${a.args}`)
  try {
    const res = await $api.post('/plugin/human/api/run', {
      host_id: selectedHostId.value,
      workflow: a.workflow_id,
      args: a.args,
    })
    handleRunResponse(selectedHostId.value, res.data)
    a.status = res.data?.status === 'error' ? 'error' : 'success'
  } catch (err) {
    a.status = 'error'
    appendLog(selectedHostId.value, 'stderr', `run failed: ${err.message || err}`)
  }
}

// SSE-based dispatch for HID profiles (Phase C2). EventSource is GET-
// only, which is why the backend route accepts both POST and GET. We
// take the GET path here because EventSource is the simplest reliable
// SSE consumer in browsers; the streaming endpoint mirrors the messages
// one-for-one into the operator UDS server-side.
function runProfileSse(host_id, profile_id, assignment) {
  const url = `/plugin/human/api/run-profile`
    + `?host_id=${encodeURIComponent(host_id)}`
    + `&profile_id=${encodeURIComponent(profile_id)}`
  appendLog(host_id, 'stdin',
            `[profile] ${profile_id} -> input-daemon (host=${host_id})`)
  currentStepIdx.value = null

  let es
  try {
    es = new EventSource(url)
  } catch (err) {
    assignment.status = 'error'
    appendLog(host_id, 'stderr', `EventSource open failed: ${err.message || err}`)
    return
  }
  es.onmessage = (ev) => {
    let payload
    try { payload = JSON.parse(ev.data) } catch (_) { payload = { raw: ev.data } }

    if (payload && payload.event === 'done') {
      appendLog(host_id, 'stdin', `[profile] done (${payload.count} messages)`)
      assignment.status = 'success'
      currentStepIdx.value = null
      es.close()
      return
    }
    if (payload && payload.event === 'error') {
      appendLog(host_id, 'stderr', `[profile] ${payload.error}`)
      assignment.status = 'error'
      es.close()
      return
    }
    // Normal OperatorMessage. Show as stdin (it's input we are pushing
    // into the guest) and bump currentStepIdx so the step-preview header
    // highlights live.
    if (typeof payload._idx === 'number') {
      currentStepIdx.value = payload._idx
    }
    const action = payload.action || '(?)'
    appendLog(host_id, 'stdin',
              `${(payload._idx ?? 0) + 1}. ${action}  ${stepDetail(payload)}`)
  }
  es.onerror = () => {
    // EventSource fires onerror both for transient reconnects and for
    // hard failures; in our case the server closes the stream after
    // `done`, which the browser surfaces as an error. If we already
    // saw the done event we've nulled currentStepIdx and set status.
    if (assignment.status === 'running') {
      assignment.status = 'error'
      appendLog(host_id, 'stderr', `[profile] stream closed unexpectedly`)
    }
    es.close()
  }
}

async function runAdhoc() {
  if (!selectedHostId.value || !adhocInput.value.trim()) return
  const cmd = adhocInput.value
  appendLog(selectedHostId.value, 'stdin', cmd)
  adhocInput.value = ''
  try {
    const res = await $api.post('/plugin/human/api/run', {
      host_id: selectedHostId.value,
      workflow: null,
      args: cmd,
    })
    handleRunResponse(selectedHostId.value, res.data)
  } catch (err) {
    appendLog(selectedHostId.value, 'stderr', `adhoc failed: ${err.message || err}`)
  }
}

function handleRunResponse(host_id, data) {
  if (!data) {
    appendLog(host_id, 'stdout', '(no response body)')
    return
  }
  // Backend currently echoes; later it will return {stdout, stderr, status}.
  if (data.stdout) {
    String(data.stdout).split('\n').forEach(l => appendLog(host_id, 'stdout', l))
  }
  if (data.stderr) {
    String(data.stderr).split('\n').forEach(l => appendLog(host_id, 'stderr', l))
  }
  if (!data.stdout && !data.stderr) {
    appendLog(host_id, 'stdout', JSON.stringify(data))
  }
}

// ---- Lifecycle -----------------------------------------------------------
onMounted(() => {
  refreshAll()
})
</script>

<style scoped>
/* Palette mirrors plugins/range/gui/range.css and Caldera-core:
     panel bg     #272727
     panel hover  #1b1b1b
     muted text   #939393
     accent blue  #191970
     accent red   #8B0000
*/
.human-live {
  padding: 1rem;
}

.human-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #555;
  padding-bottom: 0.5rem;
}

.human-grid {
  display: grid;
  grid-template-columns: 1fr 2fr 1.2fr;
  gap: 1rem;
  min-height: 70vh;
}

.hosts-panel,
.command-stream,
.gui-viewer {
  background-color: #272727;
  border: 1px solid #939393;
  border-radius: 4px;
  padding: 0.75rem;
  overflow: auto;
}

.hosts-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.hosts-list li {
  padding: 0.5rem;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid transparent;
}

.hosts-list li:hover {
  background-color: #1b1b1b;
}

.hosts-list li.is-selected {
  background-color: #1b1b1b;
  border-color: #191970;
}

.host-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.host-meta {
  color: #939393;
}

.io-panel {
  margin-top: 1rem;
}

.io-log {
  background-color: #1b1b1b;
  color: #939393;
  padding: 10px;
  height: 30vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  border-radius: 7px;
  font-family: monospace;
  font-size: 0.8em;
}

.io-stdin  { color: #ffffff; }
.io-stdout { color: #939393; }
.io-stderr { color: #8B0000; }

.todo-panel {
  text-align: center;
  margin-top: 1rem;
}

.vnc-wrapper {
  width: 100%;
  height: 60vh;
}

.vnc-frame {
  width: 100%;
  height: 100%;
  border: none;
}

.dropdown.is-fullwidth,
.dropdown-menu.is-fullwidth {
  width: 100%;
}

/* Step-preview list: dense, monospace step lines for HID abilities. */
.step-preview {
  background: #1b1b1b;
  border: 1px solid #272727;
  border-radius: 3px;
  padding: 0.5rem 0.75rem;
}
.step-list {
  list-style: none;
  margin: 0.25rem 0 0 0;
  padding: 0;
  max-height: 240px;
  overflow-y: auto;
  font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
  font-size: 0.78em;
}
.step-row {
  display: grid;
  grid-template-columns: 1.5rem 4.5rem 1fr;
  gap: 0.5rem;
  align-items: center;
  padding: 0.15rem 0;
  border-bottom: 1px dotted #272727;
}
.step-row:last-child { border-bottom: none; }
.step-idx {
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.step-detail {
  color: #939393;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
