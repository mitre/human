<template>
  <section class="legacy-builder-section">
    <div class="legacy-builder-header" @click="collapsed = !collapsed">
      <button
        class="button is-dark is-small collapse-toggle"
        :title="collapsed ? 'Expand Legacy Python Builder' : 'Collapse Legacy Python Builder'"
        :aria-expanded="!collapsed"
        @click.stop="collapsed = !collapsed"
      >
        <span class="icon is-small">
          <i
            class="fas"
            :class="collapsed ? 'fa-chevron-right' : 'fa-chevron-down'"
          ></i>
        </span>
      </button>
      <h3 class="title is-6 mb-0 ml-2">
        Legacy Python Builder
        <span v-if="humans.length" class="has-text-grey is-size-7 ml-2">
          ({{ humans.length }})
        </span>
      </h3>
      <button
        v-if="!collapsed"
        class="button is-dark is-small ml-2"
        :disabled="loading"
        title="Refresh builder data"
        @click.stop="refresh"
      >
        <span class="icon is-small">
          <i class="fas fa-sync-alt" :class="{ 'fa-spin': loading }"></i>
        </span>
      </button>
    </div>

    <div v-show="!collapsed" class="legacy-builder-body">
      <div v-if="errorText" class="notification is-danger m-0 mb-2 p-2 is-size-7">
        {{ errorText }}
      </div>
      <div v-if="statusText" class="notification is-success m-0 mb-2 p-2 is-size-7">
        {{ statusText }}
      </div>

      <div class="builder-grid">
        <div class="builder-column">
          <div class="field">
            <label class="label is-small">Name</label>
            <input
              class="input is-small"
              type="text"
              v-model.trim="humanName"
              placeholder="human name"
            />
          </div>

          <div class="field">
            <label class="label is-small">Platform</label>
            <div class="select is-small is-fullwidth">
              <select v-model="selectedPlatform">
                <option disabled value="">Select target OS</option>
                <option value="linux">Linux</option>
                <option value="darwin">macOS</option>
                <option value="windows-psh">Windows PowerShell</option>
              </select>
            </div>
          </div>

          <div class="field">
            <label class="label is-small">Behaviors</label>
            <div class="workflow-list">
              <label
                v-for="wf in workflows"
                :key="wf.name"
                class="workflow-row"
              >
                <input
                  type="checkbox"
                  :value="wf.name"
                  v-model="selectedWorkflows"
                />
                <span class="workflow-text">
                  <strong>{{ wf.name }}</strong>
                  <small>{{ wf.description }}</small>
                </span>
              </label>
              <p v-if="!workflows.length && !loading" class="has-text-grey is-size-7">
                No legacy workflows found.
              </p>
            </div>
          </div>
        </div>

        <div class="builder-column">
          <div class="field">
            <label class="label is-small">Task Sleep Interval</label>
            <div class="range-row">
              <input
                type="range"
                min="5"
                max="50"
                v-model.number="sleepInterval"
              />
              <input
                class="input is-small number-input"
                type="number"
                min="1"
                v-model.number="sleepInterval"
              />
            </div>
          </div>

          <div class="field">
            <label class="label is-small">Task Cluster Sleep Interval</label>
            <div class="range-row">
              <input
                type="range"
                min="5"
                max="1000"
                v-model.number="clusterSleepInterval"
              />
              <input
                class="input is-small number-input"
                type="number"
                min="1"
                v-model.number="clusterSleepInterval"
              />
            </div>
          </div>

          <div class="field">
            <label class="label is-small">Tasks per Cluster</label>
            <div class="range-row">
              <input
                type="range"
                min="1"
                max="20"
                v-model.number="tasksPerCluster"
              />
              <input
                class="input is-small number-input"
                type="number"
                min="1"
                v-model.number="tasksPerCluster"
              />
            </div>
          </div>

          <div class="field">
            <label class="label is-small">Custom Commands</label>
            <textarea
              class="textarea is-small command-textarea"
              rows="5"
              v-model="extraCommands"
              placeholder="one command per line"
            ></textarea>
          </div>

          <button
            class="button is-primary is-small"
            :disabled="!canBuild || building"
            @click="buildHuman"
          >
            <span class="icon is-small">
              <i class="fas" :class="building ? 'fa-spinner fa-spin' : 'fa-box'"></i>
            </span>
            <span>Build</span>
          </button>
        </div>

        <div class="builder-column">
          <div class="field">
            <label class="label is-small">Caldera Server</label>
            <input
              class="input is-small"
              type="text"
              v-model.trim="serverIp"
              placeholder="http://localhost:8888"
            />
          </div>

          <div class="field">
            <label class="label is-small">Built Humans</label>
            <div class="select is-small is-fullwidth">
              <select v-model="selectedHumanName">
                <option disabled value="">Select existing human</option>
                <option
                  v-for="h in humans"
                  :key="h.name"
                  :value="h.name"
                >
                  {{ h.name }} - {{ platformLabel(h.platform) }}
                </option>
              </select>
            </div>
          </div>

          <div class="command-panel">
            <div class="command-header">
              <label class="label is-small mb-0">Payload Command</label>
              <button
                class="button is-dark is-small"
                :disabled="!commandBlock"
                title="Copy payload command"
                @click="copyCommand"
              >
                <span class="icon is-small"><i class="fas fa-copy"></i></span>
              </button>
            </div>
            <pre class="payload-command">{{ commandBlock || '(select or build a human)' }}</pre>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, inject, ref, watch } from 'vue'

const $api = inject('$api')

const collapsed = ref(true)
const loaded = ref(false)
const loading = ref(false)
const building = ref(false)
const workflows = ref([])
const humans = ref([])
const selectedWorkflows = ref([])
const selectedHumanName = ref('')
const humanName = ref('')
const selectedPlatform = ref('')
const sleepInterval = ref(10)
const clusterSleepInterval = ref(500)
const tasksPerCluster = ref(5)
const extraCommands = ref('')
const statusText = ref('')
const errorText = ref('')
const serverIp = ref(
  typeof window === 'undefined'
    ? ''
    : `${window.location.protocol}//${window.location.hostname}:${window.location.port}`
)

watch(collapsed, (now) => {
  if (!now && !loaded.value) {
    refresh()
  }
})

const canBuild = computed(() =>
  !!humanName.value
  && !!selectedPlatform.value
  && selectedWorkflows.value.length > 0
)

const selectedHuman = computed(() =>
  humans.value.find(h => h.name === selectedHumanName.value) || null
)

const commandBlock = computed(() => {
  const h = selectedHuman.value
  if (!h || !serverIp.value) return ''
  const extra = formatExtra(h.extra || [], h.platform)
  const extraPart = extra ? ` --extra ${extra}` : ''
  if (h.platform === 'windows-psh') {
    const name = psQuote(h.name)
    return [
      `$server=${psQuote(serverIp.value)}`,
      `$name=${name}`,
      '$zip="$name.zip"',
      '$url="$server/file/download"',
      '$wc=New-Object System.Net.WebClient',
      '$wc.Headers.add("file", $zip)',
      '$wc.DownloadFile($url, (Join-Path $pwd $zip))',
      'Expand-Archive $zip -DestinationPath $name -Force',
      'python.exe -m venv $name',
      '& ".\\$name\\Scripts\\pip.exe" install -r ".\\$name\\requirements.txt"',
      `& ".\\$name\\Scripts\\python.exe" ".\\$name\\human.py" --clustersize ${h.tasks_per_cluster} --taskinterval ${h.task_interval} --taskgroupinterval ${h.task_cluster_interval}${extraPart}`,
    ].join('; ')
  }

  const archive = `${h.name}.tar.gz`
  const runCmd = `${shellQuote(`${h.name}/bin/python`)} ${shellQuote(`${h.name}/human.py`)}`
    + ` --clustersize ${h.tasks_per_cluster}`
    + ` --taskinterval ${h.task_interval}`
    + ` --taskgroupinterval ${h.task_cluster_interval}`
    + extraPart
  return [
    `curl -sk -o ${shellQuote(archive)} -X POST -H ${shellQuote(`file:${archive}`)} ${serverIp.value}/file/download`,
    `mkdir -p ${shellQuote(h.name)}`,
    `tar -C ${shellQuote(h.name)} -zxvf ${shellQuote(archive)}`,
    `python3 -m venv ${shellQuote(h.name)}`,
    `${shellQuote(`${h.name}/bin/pip`)} install -r ${shellQuote(`${h.name}/requirements.txt`)}`,
    runCmd,
  ].join(' && ')
})

function platformLabel(platform) {
  if (platform === 'windows-psh') return 'Windows'
  if (platform === 'darwin') return 'macOS'
  return platform || '?'
}

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'"
}

function psQuote(value) {
  return "'" + String(value).replace(/'/g, "''") + "'"
}

function formatExtra(extra, platform) {
  if (!Array.isArray(extra) || !extra.length) return ''
  return extra.map((raw) => {
    let value = String(raw)
    if (platform === 'windows-psh') {
      value = value.replace(/"/g, '`"')
    } else {
      value = value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
    }
    return `"${value}"`
  }).join(' ')
}

function extraList() {
  return extraCommands.value
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
}

function upsertHuman(human) {
  if (!human || !human.name) return
  const idx = humans.value.findIndex(h => h.name === human.name)
  if (idx >= 0) humans.value.splice(idx, 1, human)
  else humans.value.push(human)
  selectedHumanName.value = human.name
}

async function refresh() {
  loading.value = true
  errorText.value = ''
  statusText.value = ''
  try {
    const [wfRes, humansRes] = await Promise.all([
      $api.get('/plugin/human/api/legacy/workflows'),
      $api.get('/plugin/human/api/legacy/humans'),
    ])
    workflows.value = wfRes.data?.workflows || []
    humans.value = humansRes.data?.humans || []
    if (selectedHumanName.value
        && !humans.value.find(h => h.name === selectedHumanName.value)) {
      selectedHumanName.value = ''
    }
    loaded.value = true
  } catch (err) {
    errorText.value = err.response?.data?.error || err.message || String(err)
  } finally {
    loading.value = false
  }
}

async function buildHuman() {
  if (!canBuild.value) return
  building.value = true
  errorText.value = ''
  statusText.value = ''
  try {
    const res = await $api.post('/plugin/human/api/legacy/build', {
      name: humanName.value,
      platform: selectedPlatform.value,
      task_cluster_interval: clusterSleepInterval.value,
      task_interval: sleepInterval.value,
      task_count: tasksPerCluster.value,
      tasks: selectedWorkflows.value,
      extra: extraList(),
    })
    const built = res.data?.human
    upsertHuman(built)
    humanName.value = ''
    selectedWorkflows.value = []
    extraCommands.value = ''
    statusText.value = built?.name ? `Built ${built.name}` : 'Built human'
  } catch (err) {
    errorText.value = err.response?.data?.error || err.message || String(err)
  } finally {
    building.value = false
  }
}

async function copyCommand() {
  if (!commandBlock.value) return
  try {
    await navigator.clipboard.writeText(commandBlock.value)
    statusText.value = 'Copied command'
    errorText.value = ''
  } catch (err) {
    errorText.value = err.message || String(err)
  }
}
</script>

<style scoped>
.legacy-builder-section {
  background-color: #272727;
  border: 1px solid #939393;
  border-radius: 4px;
  padding: 0.5rem 0.75rem;
  margin-top: 0.75rem;
  flex: 0 0 auto;
}

.legacy-builder-header {
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.legacy-builder-header h3 {
  flex: 0 0 auto;
}

.collapse-toggle {
  flex: 0 0 auto;
}

.collapse-toggle .icon,
.collapse-toggle .icon i,
.legacy-builder-header .button .icon i {
  color: #fff !important;
}

.legacy-builder-body {
  margin-top: 0.75rem;
}

.builder-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(220px, 0.9fr) minmax(280px, 1.2fr);
  gap: 0.75rem;
}

.builder-column {
  min-width: 0;
}

.workflow-list {
  background: #1b1b1b;
  border: 1px solid #272727;
  border-radius: 3px;
  max-height: 240px;
  overflow-y: auto;
  padding: 0.5rem;
}

.workflow-row {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.35rem 0;
}

.workflow-row input {
  flex: 0 0 auto;
  margin-top: 0.2rem;
}

.workflow-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.workflow-text small {
  color: #b5b5b5;
  line-height: 1.25;
}

.range-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px;
  gap: 0.5rem;
  align-items: center;
}

.range-row input[type="range"] {
  min-width: 0;
}

.number-input {
  width: 72px;
}

.command-textarea {
  min-height: 96px;
  resize: vertical;
}

.command-panel {
  background: #1b1b1b;
  border: 1px solid #272727;
  border-radius: 3px;
  padding: 0.5rem;
}

.command-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}

.payload-command {
  min-height: 180px;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  color: #f1f1f1;
  background: #111;
  border-radius: 3px;
  padding: 0.5rem;
}

@media (max-width: 1100px) {
  .builder-grid {
    grid-template-columns: 1fr;
  }
}
</style>
