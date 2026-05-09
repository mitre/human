<template>
  <!--
    RecordingsBrowser (Human plugin)

    Collapsible "Recordings" section that lives below the row-3 command
    grid in human.vue. Hits ``/plugin/human/api/recordings`` for the
    catalog and renders:

      [chevron] Recordings (N)
        ┌──────────────────────────────┐
        │ [Dropdown ▼] [Refresh] [×]   │
        │   <host> · <ability> · <ts>  │
        └──────────────────────────────┘
        ┌──────────────────────────────┐
        │      <video controls>        │
        └──────────────────────────────┘

    Default state is collapsed (chevron-down). On expand we fetch the
    index. The parent (human.vue) calls ``refresh()`` via ref when the
    SSE pipe emits ``recording_ready`` so a fresh MP4 appears at the
    top of the dropdown without a page reload.

    Built as a standalone component so it lands in a separate file
    from a parallel-agent edit on human.vue (Run-button reposition +
    Clear button) — no merge conflicts on the row-3 region.
  -->
  <section class="recordings-section">
    <div class="recordings-header" @click="collapsed = !collapsed">
      <button
        class="button is-dark is-small collapse-toggle"
        :title="collapsed ? 'Expand Recordings' : 'Collapse Recordings'"
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
        Recordings
        <span v-if="recordings.length" class="has-text-grey is-size-7 ml-2">
          ({{ recordings.length }})
        </span>
      </h3>
      <button
        v-if="!collapsed"
        class="button is-dark is-small ml-2"
        :disabled="loading"
        :title="'Refresh recordings list'"
        @click.stop="refresh"
      >
        <span class="icon is-small">
          <i class="fas fa-sync-alt" :class="{ 'fa-spin': loading }"></i>
        </span>
      </button>
    </div>

    <div v-show="!collapsed" class="recordings-body">
      <div v-if="loadError" class="notification is-danger m-0 p-2 is-size-7">
        Failed to load recordings: {{ loadError }}
      </div>

      <div v-else-if="recordings.length === 0 && !loading"
           class="notification is-dark m-0 p-2 has-text-centered is-size-7">
        <em>No recordings yet. Tick "Record this run" before clicking Run.</em>
      </div>

      <template v-else>
        <div class="recordings-controls">
          <div class="select is-small is-fullwidth">
            <select v-model="selectedKey">
              <option disabled value="">
                Select a recording…
              </option>
              <option
                v-for="rec in recordings"
                :key="recKey(rec)"
                :value="recKey(rec)"
              >
                {{ recLabel(rec) }}
              </option>
            </select>
          </div>
          <button
            v-if="selected"
            class="button is-danger is-small ml-2"
            :disabled="deleting"
            :title="'Delete this recording'"
            @click="deleteSelected"
          >
            <span class="icon is-small"><i class="fas fa-trash"></i></span>
          </button>
        </div>

        <div v-if="selected" class="recordings-player">
          <video
            controls
            preload="metadata"
            :src="selected.url"
            class="recordings-video"
            :key="selected.url"
          ></video>
          <p class="is-size-7 has-text-grey mt-1">
            <a :href="selected.url" download>Download MP4</a>
            <span class="ml-2">
              {{ selected.vm_name }} · {{ selected.ability || '?' }}
              · {{ formatTs(selected.timestamp) }}
              <span v-if="selected.size_bytes != null">
                · {{ formatBytes(selected.size_bytes) }}
              </span>
            </span>
          </p>
        </div>
      </template>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, inject } from 'vue'

// Same $api injection as human.vue.
const $api = inject('$api')

// ---- State ---------------------------------------------------------------
const collapsed = ref(true)        // default collapsed per spec
const recordings = ref([])         // [{vm_name, filename, ability, timestamp, size_bytes, url}]
const selectedKey = ref('')        // <vm_name>/<filename>
const loading = ref(false)
const loadError = ref(null)
const deleting = ref(false)

// Auto-fetch on first expand (or refresh from parent via ref). We
// intentionally don't fetch on mount when collapsed — the section is
// hidden so the network call would just be wasted.
watch(collapsed, (now) => {
  if (!now && recordings.value.length === 0 && !loadError.value) {
    refresh()
  }
})

// ---- Computed ------------------------------------------------------------
const selected = computed(() => {
  if (!selectedKey.value) return null
  return recordings.value.find(r => recKey(r) === selectedKey.value) || null
})

// ---- Helpers -------------------------------------------------------------
function recKey(r) {
  return `${r.vm_name}/${r.filename}`
}

function recLabel(r) {
  const ts = formatTs(r.timestamp)
  const ability = r.ability || r.filename
  return `${r.vm_name} · ${ability} · ${ts}`
}

// Render `2026-05-09T12:21:00` -> `2026-05-09 12:21`. Falls back to
// the raw filename's timestamp segment if parsing fails.
function formatTs(iso) {
  if (!iso) return '?'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`
  } catch (_) {
    return iso
  }
}

function formatBytes(b) {
  if (b == null) return ''
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`
}

// ---- Actions -------------------------------------------------------------
async function refresh() {
  loading.value = true
  loadError.value = null
  try {
    const res = await $api.get('/plugin/human/api/recordings')
    recordings.value = res.data?.recordings || []
    // If the previously-selected recording is gone (deleted /
    // renamed), drop the selection so the player doesn't 404.
    if (selectedKey.value
        && !recordings.value.find(r => recKey(r) === selectedKey.value)) {
      selectedKey.value = ''
    }
  } catch (err) {
    loadError.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

async function deleteSelected() {
  if (!selected.value) return
  const r = selected.value
  // Confirm via window.confirm — keeps the component dependency-free.
  // eslint-disable-next-line no-alert
  if (!window.confirm(`Delete recording ${r.filename}?`)) return
  deleting.value = true
  try {
    await $api.delete(
      `/plugin/human/api/recording/${encodeURIComponent(r.vm_name)}/`
      + `${encodeURIComponent(r.filename)}`)
    selectedKey.value = ''
    await refresh()
  } catch (err) {
    loadError.value = err.message || String(err)
  } finally {
    deleting.value = false
  }
}

// Public API: parent can call `refresh()` after a `recording_ready`
// SSE event so the new file pops to the top. Also auto-expands so the
// operator visually sees the new entry without hunting for the chevron.
function autoRefreshAfterRecording() {
  collapsed.value = false
  refresh()
}

defineExpose({ refresh, autoRefreshAfterRecording })
</script>

<style scoped>
/* Mirrors the row-viewer / row-command palette in human.vue. */
.recordings-section {
  background-color: #272727;
  border: 1px solid #939393;
  border-radius: 4px;
  padding: 0.5rem 0.75rem;
  margin-top: 0.75rem;
  flex: 0 0 auto;
}

.recordings-header {
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.recordings-header h3 {
  flex: 0 0 auto;
}

.collapse-toggle {
  flex: 0 0 auto;
}

.recordings-body {
  margin-top: 0.5rem;
}

.recordings-controls {
  display: flex;
  align-items: center;
}

.recordings-controls .select {
  flex: 1 1 auto;
}

.recordings-player {
  margin-top: 0.5rem;
  background: #1b1b1b;
  border: 1px solid #272727;
  border-radius: 3px;
  padding: 0.5rem 0.75rem;
}

.recordings-video {
  width: 100%;
  max-height: 360px;
  background: black;
  border-radius: 3px;
}
</style>
