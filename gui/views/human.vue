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
          <p><em>Select a host on the left to assign workflows or send commands.</em></p>
        </div>

        <div v-else>
          <h3 class="title is-5">{{ selectedHost.name || selectedHost.id }}</h3>

          <!-- Workflow dropdown (mirrors range.vue:27-56 pattern) -->
          <div class="field">
            <label class="label is-small">Workflow</label>
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
                  <span>{{ selectedWorkflowName || 'Select Workflow' }}</span>
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
                    No workflows available
                  </p>
                </div>
              </div>
            </div>
          </div>

          <!-- Args + Run -->
          <div class="field">
            <label class="label is-small">Args (passed to workflow)</label>
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
      <aside class="gui-viewer">
        <h3 class="title is-6">Live GUI</h3>
        <!-- TODO: Wire up noVNC iframe once control_server.py exposes a
             per-host websocket URL. The contract is expected to look like:
                 vnc_ws = `ws://<server>/plugin/human/vnc/<host_id>`
             At that point replace this TODO panel with:
                 <iframe :src="vncUrl" class="vnc-frame" />
             where vncUrl is computed from selectedHost.vnc_ws. -->
        <div v-if="selectedHost && selectedHost.vnc_ws" class="vnc-wrapper">
          <iframe :src="selectedHost.vnc_ws" class="vnc-frame"></iframe>
        </div>
        <div v-else class="notification is-dark todo-panel">
          <p>
            <strong>TODO:</strong> noVNC iframe slot. Will embed a websocket
            stream once <code>control_server.py</code> exposes a per-host
            VNC bridge.
          </p>
          <p class="is-size-7 mt-2">
            Selected host: <code>{{ selectedHost?.id || '—' }}</code>
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
  appendLog(selectedHostId.value, 'stdin', `[workflow] ${a.workflow_id} ${a.args}`)
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
</style>
