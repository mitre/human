<template>
  <div class="human-live">
    <!-- HEADER ============================================================ -->
    <section class="human-header mb-2">
      <h2 class="title is-4 is-flex is-align-items-center">
        <span>Human (Live)</span>
        <!-- Profile-switcher dropdown. Mirrors the Range plugin's profile
             picker (plugins/range/gui/views/range.vue) but fetches the
             cloud + on-prem profile lists itself rather than importing
             a Range component cross-plugin. The Range plugin does not
             expose a "set active profile" route — active profile is
             tracked client-side (localStorage), so this dropdown is
             effectively display + scope-refresh. See switchProfile(). -->
        <div class="dropdown profile-switcher ml-3" :class="{ 'is-active': profileDropdownOpen }">
          <div class="dropdown-trigger">
            <button
              class="button is-small is-dark"
              type="button"
              aria-haspopup="true"
              aria-controls="profile-switcher-menu"
              @click="profileDropdownOpen = !profileDropdownOpen"
            >
              <span>{{ rangeProfileName || 'Select profile' }}</span>
              <span class="icon is-small">
                <i class="fas fa-chevron-down"></i>
              </span>
            </button>
          </div>
          <div class="dropdown-menu" id="profile-switcher-menu" role="menu">
            <div class="dropdown-content">
              <a
                class="dropdown-item"
                v-for="p in availableProfiles"
                :key="p.id"
                :class="{ 'is-active': p.id === activeProfileId }"
                @click="switchProfile(p.id); profileDropdownOpen = false"
              >
                <strong>{{ p.name || p.id }}</strong>
                <small class="ml-2 has-text-grey">{{ p.range || '' }}</small>
              </a>
              <div v-if="!availableProfiles.length" class="dropdown-item">
                <em>No profiles available</em>
              </div>
            </div>
          </div>
        </div>
        <span class="subtitle is-6 ml-2 has-text-grey">({{ hosts.length }} hosts)</span>
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

    <!-- LAYOUT GRID =======================================================
         Row 1 (auto)              : hosts panel + selected-host detail
                                     (side-by-side narrow strip, ~200px tall).
         Row 2 (minmax(50vh, 1fr)) : live endpoint viewer
                                     (LiveEndpointViewer.vue) — at least
                                     half the viewport height, then grows
                                     into any remaining vertical space.
                                     The 16:9 (monitor-shaped) frame fits
                                     inside this row, centered.
         Row 3 (auto)              : command stream (ability picker /
                                     args / record / output) split into
                                     sub-columns.

         The viewer is the dominant visual surface; the command stream
         stays ergonomic but secondary. -->
    <div class="human-grid" :class="{ 'viewer-collapsed': viewerCollapsed }">

      <!-- ROW 1: HOSTS + SELECTED-HOST DETAIL ============================ -->
      <section class="row-hosts">
        <aside class="hosts-panel">
          <h3 class="title is-6 mb-1">Hosts</h3>
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

        <aside class="selected-host-info">
          <div v-if="!selectedHost" class="notification is-dark m-0 p-3 has-text-centered">
            <p><em>Select a host on the left to assign human abilities or send commands.</em></p>
          </div>
          <div v-else>
            <h3 class="title is-5 mb-1">{{ selectedHost.name || selectedHost.id }}</h3>
            <p class="is-size-7 mb-1">
              <strong>IP:</strong> <code>{{ selectedHost.ip || '—' }}</code>
              <span class="ml-3">
                <strong>Status:</strong>
                <span
                  class="tag is-small ml-1"
                  :class="statusTagClass(assignments[selectedHostId]?.status)"
                >
                  {{ assignments[selectedHostId]?.status || 'idle' }}
                </span>
              </span>
            </p>
            <p class="is-size-7 mb-1">
              <strong>Endpoint:</strong>
              <!-- Render from the backend transport descriptor (provider-
                   agnostic, profile-qualified) instead of a per-provider
                   frame_ws/vnc_ws/console_ws triple. -->
              <code v-if="selectedHost.endpoint_url">{{ selectedHost.transport }} · {{ selectedHost.endpoint_url }}</code>
              <span v-else class="has-text-grey">
                no live endpoint registered (stub / unknown session)
              </span>
            </p>
            <p class="is-size-7 has-text-grey">
              <strong>ID:</strong> <code>{{ selectedHost.id }}</code>
            </p>
          </div>
        </aside>
      </section>

      <!-- ROW 2: LIVE ENDPOINT VIEWER ====================================
           v-if (NOT v-show) on the inner component path so noVNC's RFB
           constructor never fires against an unmounted/empty target. The
           component itself also gates connect() on `vmName` being non-
           empty, but we belt-and-suspenders this by only mounting once
           we have a host. -->
      <section class="row-viewer" :class="{ 'is-collapsed': viewerCollapsed }">
        <LiveEndpointViewer
          :vm-name="liveEndpointVmName"
          :session-type="liveEndpointSessionType"
          :frame-ws="selectedHost?.frame_ws || ''"
          :transport="selectedHost?.transport || ''"
          :endpoint-url="selectedHost?.endpoint_url || ''"
          :credentials-url="selectedHost?.credentials_url || ''"
          :interactive="selectedHost?.interactive !== false"
          @update:collapsed="viewerCollapsed = $event"
        >
          <template #header-extra>
            <span
              v-if="currentStepIdx != null && selectedAbilitySteps.length"
              class="tag is-link is-small ml-2"
            >
              step {{ currentStepIdx + 1 }} / {{ selectedAbilitySteps.length }}
            </span>
          </template>
        </LiveEndpointViewer>
      </section>

      <!-- ROW 2.5: CHORD PALETTE =========================================
           Tier 1 (always visible): SAS + nav keys.
           Tier 2 (OS-aware):       Windows/Linux power-user shortcuts.
           Tier 3 (always visible): cancel / clear / refresh.
           Tier 4 (collapsible):    function keys, arrows, sticky modifiers.

           Each click POSTs to /plugin/human/api/chord with
             {host_id, keys: [...], hold_ms: 50}
           Sticky modifiers (Tier 4) layer their key names in front of
           the next chip's key. -->
      <!-- Hide keyboard chords for CLI-only hosts: there's no vhost-user-input
           daemon for session_type=cli microVMs, so the chord-send endpoint
           backs off with 409 "no GUI session". The xterm.js terminal in
           LiveEndpointViewer accepts those keystrokes directly (Ctrl+C,
           Ctrl+L, etc.) — modifiers route through the WebSocket. -->
      <section v-if="selectedHost && liveEndpointSessionType !== 'cli'" class="row-chord-palette">
        <div class="chord-header is-flex is-align-items-center">
          <h4 class="title is-6 mb-0 mr-3">Keyboard chords</h4>
          <span class="has-text-grey is-size-7 mr-3">
            target: <code>{{ selectedHost.name || selectedHost.id }}</code>
          </span>
          <button
            class="button is-dark is-small ml-auto"
            @click="chordTier4Open = !chordTier4Open"
            :title="chordTier4Open ? 'Hide F-keys / arrows / modifiers' : 'Show F-keys / arrows / modifiers'"
          >
            <span class="icon is-small"><i class="fas" :class="chordTier4Open ? 'fa-chevron-up' : 'fa-chevron-down'"></i></span>
            <span>{{ chordTier4Open ? 'Less' : 'More' }}</span>
          </button>
        </div>
        <div v-if="chordStatus" class="chord-status is-size-7 mb-1" :class="chordStatusClass">
          {{ chordStatus }}
        </div>

        <!-- Tier 1: always visible -->
        <div class="chord-row">
          <button class="button is-small is-warning chord-chip"
                  @click="sendChord(['LeftCtrl','LeftAlt','Delete'])"
                  title="Ctrl+Alt+Del — Secure Attention Sequence (login screen unlocks)">
            Ctrl+Alt+Del
          </button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['Enter'])" title="Enter">Enter</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['Escape'])" title="Escape">Esc</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['Tab'])" title="Tab">Tab</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['Backspace'])" title="Backspace">Backspace</button>
        </div>

        <!-- Tier 2: OS-aware. -->
        <div class="chord-row" v-if="chordOs === 'windows'">
          <span class="chord-row-label">Windows</span>
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftMeta','R'])"
                  title="Win+R — Run dialog">Win+R</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftMeta','L'])"
                  title="Win+L — Lock workstation">Win+L</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftMeta','D'])"
                  title="Win+D — Show desktop">Win+D</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftAlt','Tab'])"
                  title="Alt+Tab — Cycle windows">Alt+Tab</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftAlt','F4'])"
                  title="Alt+F4 — Close window">Alt+F4</button>
        </div>
        <div class="chord-row" v-else-if="chordOs === 'linux'">
          <span class="chord-row-label">Linux</span>
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftCtrl','LeftAlt','F1'])"
                  title="Ctrl+Alt+F1 — TTY1">Ctrl+Alt+F1</button>
          <button v-for="n in [2,3,4,5,6]" :key="n"
                  class="button is-small chord-chip"
                  @click="sendChord(['LeftCtrl','LeftAlt','F'+n])"
                  :title="'Ctrl+Alt+F'+n+' — TTY'+n">F{{n}}</button>
        </div>

        <!-- Tier 3: always visible. -->
        <div class="chord-row">
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftCtrl','C'])"
                  title="Ctrl+C — Interrupt / copy">Ctrl+C</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['LeftCtrl','L'])"
                  title="Ctrl+L — Clear screen / address bar">Ctrl+L</button>
          <button class="button is-small chord-chip"
                  @click="sendChord(['F5'])" title="F5 — Refresh">F5</button>
        </div>

        <!-- Tier 4: collapsible (F-keys, arrows, nav, sticky modifiers). -->
        <div v-if="chordTier4Open" class="chord-tier4">
          <div class="chord-row">
            <span class="chord-row-label">Function</span>
            <button v-for="n in 12" :key="'f'+n"
                    class="button is-small chord-chip"
                    @click="sendChord(['F'+n])"
                    :title="'F'+n">F{{n}}</button>
          </div>
          <div class="chord-row">
            <span class="chord-row-label">Arrows</span>
            <button class="button is-small chord-chip"
                    @click="sendChord(['Up'])" title="Up">↑</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['Down'])" title="Down">↓</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['Left'])" title="Left">←</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['Right'])" title="Right">→</button>
          </div>
          <div class="chord-row">
            <span class="chord-row-label">Nav</span>
            <button class="button is-small chord-chip"
                    @click="sendChord(['Home'])" title="Home">Home</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['End'])" title="End">End</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['PageUp'])" title="PageUp">PgUp</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['PageDown'])" title="PageDown">PgDn</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['Insert'])" title="Insert">Ins</button>
            <button class="button is-small chord-chip"
                    @click="sendChord(['Delete'])" title="Delete">Del</button>
          </div>
          <div class="chord-row">
            <span class="chord-row-label">Sticky</span>
            <button class="button is-small chord-chip"
                    :class="{ 'is-info': chordStickyMods.includes('LeftCtrl') }"
                    @click="toggleStickyMod('LeftCtrl')" title="Sticky Ctrl">Ctrl</button>
            <button class="button is-small chord-chip"
                    :class="{ 'is-info': chordStickyMods.includes('LeftAlt') }"
                    @click="toggleStickyMod('LeftAlt')" title="Sticky Alt">Alt</button>
            <button class="button is-small chord-chip"
                    :class="{ 'is-info': chordStickyMods.includes('LeftShift') }"
                    @click="toggleStickyMod('LeftShift')" title="Sticky Shift">Shift</button>
            <button class="button is-small chord-chip"
                    :class="{ 'is-info': chordStickyMods.includes('LeftMeta') }"
                    @click="toggleStickyMod('LeftMeta')" title="Sticky Win/Super">Win</button>
            <span class="has-text-grey is-size-7 ml-2" v-if="chordStickyMods.length">
              + next chip
            </span>
          </div>
        </div>
      </section>

      <!-- ROW 3: COMMAND STREAM (ability / args / record / output) ======
           Split into three sub-columns inside one row so the operator can
           see the picker, the args/run controls, and the live output log
           at a glance without scrolling. -->
      <section class="row-command">
        <div v-if="!selectedHost" class="notification is-dark m-0 p-3 has-text-centered">
          <p><em>Select a host above to assign human abilities or send commands.</em></p>
        </div>

        <div v-else class="command-grid">

          <!-- COL 1: Ability picker + step preview -->
          <div class="cmd-col cmd-ability">
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

            <div v-if="selectedAbilitySteps.length" class="step-preview">
              <div class="is-flex is-justify-content-space-between is-align-items-baseline">
                <h4 class="title is-6 mb-1">
                  Step preview
                  <span class="tag is-success is-small ml-2">HID</span>
                </h4>
                <small class="has-text-grey">
                  {{ selectedAbilitySteps.length }} steps
                  · ~{{ selectedAbilityDurationS }}s
                </small>
              </div>
              <ol class="step-list">
                <li v-for="(s, i) in selectedAbilitySteps" :key="i" class="step-row">
                  <span class="step-idx has-text-grey">{{ i + 1 }}</span>
                  <span class="step-action tag is-dark is-small">{{ stepLabel(s) }}</span>
                  <span class="step-detail">{{ stepDetail(s) }}</span>
                </li>
              </ol>
            </div>
            <div v-else-if="selectedWorkflowName && selectedIsLegacy"
                 class="notification is-dark py-2 px-3">
              <p class="is-size-7">
                Legacy shell-cradle ability (no HID step-list). It will run as
                a single shell command via sandcat, not through the input
                daemon.
              </p>
            </div>
          </div>

          <!-- COL 2: Args + Run + Record toggle + Ad-hoc -->
          <div class="cmd-col cmd-args">
            <div class="field">
              <label class="label is-small">Args (passed to human ability)</label>
              <div class="control mb-1">
                <input
                  class="input is-small"
                  type="text"
                  placeholder="--flag value ..."
                  v-model="argsInput"
                  @keyup.enter="runAssignedWorkflow"
                />
              </div>
              <label class="checkbox is-size-7">
                <input type="checkbox" v-model="record" />
                Record this run (MP4 of framebuffer)
              </label>
            </div>

            <!-- Typing tempo sliders. Default values mirror the daemon's
                 defaults (48 WPM ≈ 250 ms/char, 40% jitter). When either
                 slider moves off its default, runProfileSse appends the
                 query param and the backend overrides every materialized
                 `type` action's per_char_ms / jitter_pct accordingly. -->
            <div class="field tempo-controls">
              <label class="label is-small">Typing tempo</label>
              <div class="control tempo-row">
                <span class="tempo-label">WPM</span>
                <input
                  class="tempo-slider"
                  type="range"
                  min="20"
                  max="150"
                  step="1"
                  v-model.number="tempoWpm"
                  :title="`per-char ≈ ${Math.round(12000 / tempoWpm)}ms`"
                />
                <span class="tempo-value">{{ tempoWpm }}</span>
              </div>
              <div class="control tempo-row">
                <span class="tempo-label">Jitter %</span>
                <input
                  class="tempo-slider"
                  type="range"
                  min="0"
                  max="80"
                  step="1"
                  v-model.number="tempoJitterPct"
                  :title="`±${tempoJitterPct}% of per-char-ms`"
                />
                <span class="tempo-value">{{ tempoJitterPct }}</span>
              </div>
              <p
                v-if="tempoWpm !== 48 || tempoJitterPct !== 40"
                class="is-size-7 has-text-grey"
              >
                overrides daemon defaults (48 WPM, 40% jitter) for this run
              </p>
            </div>

            <div v-if="recordingUrl" class="recording-panel">
              <h4 class="title is-6 mb-1">Recording</h4>
              <video controls :src="recordingUrl" class="recording-video"></video>
              <p class="is-size-7 mt-1">
                <a :href="recordingUrl" download>Download MP4</a>
                <span class="has-text-grey ml-2" v-if="recordingPath">
                  ({{ recordingPath }})
                </span>
              </p>
            </div>

            <div class="field">
              <label class="label is-small">Manual command</label>
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
                  <button class="button is-primary is-small" @click="runAdhoc" :disabled="!adhocInput.trim()">
                    Send
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- COL 3: Output -->
          <div class="cmd-col cmd-output">
            <h4 class="title is-6 mb-2">Output</h4>
            <pre class="io-log">
<template v-for="(entry, i) in scopedCommandLog" :key="i"><span :class="['io-line', 'io-' + entry.direction]">[{{ formatTs(entry.ts) }}] [{{ entry.direction }}] {{ entry.line }}
</span></template>
<span v-if="scopedCommandLog.length === 0" class="has-text-grey">(no output yet)</span>
            </pre>
          </div>

        </div>

        <!-- Bottom action bar: primary Run + secondary Clear. Lives below
             the 3-col command grid so the Run button is the first thing
             the operator sees / clicks once they've picked an ability and
             filled in args. -->
        <div v-if="selectedHost" class="command-actions">
          <button
            class="button is-primary"
            :disabled="!assignments[selectedHostId]?.workflow_id"
            @click="runAssignedWorkflow"
          >
            Run
          </button>
          <button class="button is-light" @click="clearForm">
            Clear
          </button>
        </div>
      </section>
    </div>

    <!-- RECORDINGS SECTION =================================================
         Collapsible browser for past recorded runs. Lives BELOW the main
         3-row grid so it doesn't compete with the live framebuffer for
         vertical real estate. Default state is collapsed; expanding it
         fetches /plugin/human/api/recordings. The runProfileSse handler
         calls .autoRefreshAfterRecording() on the recordings_ready SSE
         event so a fresh MP4 pops to the top without a page reload. -->
    <RecordingsBrowser ref="recordingsBrowserRef" />
    <LegacyHumanBuilder />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, inject } from 'vue'
import LiveEndpointViewer from '../components/LiveEndpointViewer.vue'
import RecordingsBrowser from '../components/RecordingsBrowser.vue'
import LegacyHumanBuilder from '../components/LegacyHumanBuilder.vue'

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
// Profile-switcher state. availableProfiles is fetched from the Range
// plugin's GET /plugin/range/profiles + /plugin/range/onprem/profiles
// endpoints (read-only cross-plugin call). activeProfileId tracks the
// currently-selected profile; persisted to localStorage so reloads
// remember the choice (mirrors how Range tracks selection client-side
// — Range exposes no "set active profile" server route).
const availableProfiles = ref([])      // [{id, name, range}]
const activeProfileId = ref('')
const profileDropdownOpen = ref(false)
// Mirrors LiveEndpointViewer's collapsed state so the page-grid can
// reflow when the section is collapsed (giving the freed vertical
// space to the command stream below). Session-only.
const viewerCollapsed = ref(false)

// ---- Chord palette state (overnight-stabilization 2026-05-10) -----------
// Tier 4 (function/arrows/nav/sticky-mods) defaults closed so the
// initial Tier1+2+3 view fits in one row-strip. chordStickyMods holds
// modifier names (LeftCtrl, LeftAlt, LeftShift, LeftMeta) that will
// be prepended to the next chord chip's keys, then auto-cleared.
// chordStatus is a one-line "sent" / "failed" message reset by the
// next click.
const chordTier4Open = ref(false)
const chordStickyMods = ref([])     // ['LeftCtrl', 'LeftAlt', ...]
const chordStatus = ref('')
const chordStatusOk = ref(true)

// Record-this-run toggle: wired into runProfileSse so the SSE handler
// can spawn an RfbRecorder against the GPU daemon. Set per-host via
// the checkbox next to the Run button.
const record = ref(false)

// Per-run typing-tempo overrides. Sliders in the command row let the
// operator tune WPM + jitter without editing the profile YAML. Sent as
// query params on /api/run-profile only when they differ from the
// defaults (matches the daemon defaults — 48 WPM ≈ 250 ms/char, 40%
// jitter). The backend reads tempo_wpm + tempo_jitter_pct and rewrites
// per_char_ms / jitter_pct on every materialized `type` action before
// dispatch. Profile YAMLs with per-step overrides aren't double-
// overridden — the materialize output's existing values reflect what
// the YAML asked for AND get re-stamped here. (If you need to keep a
// YAML's per-step tempo immune to the slider, document it in the
// profile.)
const TEMPO_WPM_DEFAULT = 48
const TEMPO_JITTER_DEFAULT = 40
const tempoWpm = ref(TEMPO_WPM_DEFAULT)
const tempoJitterPct = ref(TEMPO_JITTER_DEFAULT)
// Filled in by the SSE `recording_ready` event after a recorded run.
const recordingUrl = ref(null)
const recordingPath = ref(null)
// RecordingsBrowser exposes a refresh() / autoRefreshAfterRecording()
// pair via defineExpose; we hold a template ref so runProfileSse can
// poke it once the recorder finalizes a fresh MP4.
const recordingsBrowserRef = ref(null)

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

// LiveEndpointViewer takes a vmName. Range's WS proxy keys on the VM/host
// name, which on our side maps to the host record's `name` (falling back
// to `id`). When no host is selected we pass an empty string and the
// component renders its placeholder without instantiating RFB.
const liveEndpointVmName = computed(() => {
  const h = selectedHost.value
  if (!h) return ''
  return h.name || h.id || ''
})

// session_type comes from /plugin/human/api/hosts (human_svc._scan_microvm_meta);
// 'gui' → noVNC, 'cli' → xterm.js, anything else → stub placeholder.
// Default to 'gui' for back-compat with pre-cli hosts that may surface
// no session_type at all (the viewer still has its own stub-probe path).
const liveEndpointSessionType = computed(() => {
  const h = selectedHost.value
  return (h && h.session_type) || 'gui'
})

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

// True only when the picked ability is unambiguously legacy (cradle-
// builder shell command, no step list). human_svc.py sets is_hid:false
// on stub workflows + any data_svc legacy workflows; HID profiles set
// is_hid:true. We default to FALSE so the warning doesn't flash for
// abilities that arrived from older API responses without the flag —
// the warning is only worth showing when we KNOW it's legacy.
const selectedIsLegacy = computed(() => {
  const a = assignments[selectedHostId.value]
  if (!a) return false
  const wf = workflows.value.find(w => w.id === a.workflow_id)
  return !!wf && wf.is_hid === false
})

// `currentStepIdx` is the step the in-flight human-actor is replaying right
// now (0-indexed). Set by the assignment-status SSE / poll once we wire it.
// Until then it stays null so the viewer header just shows the static label.
const currentStepIdx = ref(null)

// Short label for the action pill on the left of each step row. Materialized
// OperatorMessages have `action`; YAML-reference shape (composite profiles
// like Surf the Web) has `ability` (a UUID) instead. For the latter we show
// `ability` so the operator at least sees the row isn't empty.
function stepLabel(step) {
  if (step.action) return step.action
  if (step.ability) return 'ability'
  return '?'
}

// Pretty one-line description of a step row, for the preview list. We keep
// this short enough that 10-30 steps fit in the visible panel without scroll.
function stepDetail(step) {
  // YAML reference shape: {ability: <uuid>, args: {...}} — render args summary.
  if (step.ability && !step.action) {
    const args = step.args || {}
    const keys = Object.keys(args)
    if (!keys.length) return step.ability.slice(0, 8) + '…'
    return keys
      .map(k => {
        const v = args[k]
        const sv = typeof v === 'string' ? v.slice(0, 24) : JSON.stringify(v)
        return `${k}=${sv}`
      })
      .join(' ')
  }
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

// Resets the row-3 form selections for the currently-selected host:
// clears the chosen Human Ability, Args input, Record toggle, and the
// Ad-hoc command line. Does NOT touch the output log — that has its own
// "Clear log" button in the page header.
function clearForm() {
  if (selectedHostId.value) {
    const a = ensureAssignment(selectedHostId.value)
    a.workflow_id = null
    a.args = ''
  }
  argsInput.value = ''
  record.value = false
  adhocInput.value = ''
}

async function fetchHosts(profileName) {
  // Scope the host inventory to a Range profile when one is selected.
  // Falls back to the legacy union behavior (no query param) so the
  // initial mount — before activeProfileId has been restored from
  // localStorage — still renders something useful.
  const effective = (profileName !== undefined && profileName !== null)
    ? profileName
    : activeProfileId.value
  let url = '/plugin/human/api/hosts'
  if (effective) {
    url += `?profile=${encodeURIComponent(effective)}`
  }
  try {
    const res = await $api.get(url)
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

// Fetch the union of cloud + on-prem profiles registered with the Range
// plugin. Both endpoints are read-only and don't require any state from
// the Range plugin (they parse the on-disk yaml). On failure we fall
// back to a single synthetic "(active range)" entry so the dropdown
// trigger still reflects whatever rangeProfileName we got from /hosts.
async function loadProfiles() {
  const collected = []
  const fetchOne = async (url, range) => {
    try {
      const res = await $api.get(url)
      const list = res.data?.profiles || []
      for (const p of list) {
        const name = p.profile || p.name || p.id
        if (!name) continue
        collected.push({ id: name, name, range: range })
      }
    } catch (err) {
      // Range plugin may be absent or endpoint unreachable; not fatal.
      console.warn(`[human] loadProfiles: ${url} failed`, err)
    }
  }
  await Promise.all([
    fetchOne('/plugin/range/profiles', 'cloud'),
    fetchOne('/plugin/range/onprem/profiles', 'onprem'),
  ])
  availableProfiles.value = collected
  // Restore prior selection from localStorage (Range does the same
  // client-side, so picking a profile here mirrors the Range view).
  try {
    const saved = localStorage.getItem('human_active_profile')
    if (saved && collected.some(p => p.id === saved)) {
      activeProfileId.value = saved
      rangeProfileName.value = saved
    } else if (rangeProfileName.value) {
      // /hosts already gave us a profile name — adopt it.
      activeProfileId.value = rangeProfileName.value
    }
  } catch (_) { /* ignore localStorage failures */ }
}

// Switching profile: the Range plugin has no server-side "set active
// profile" route, so we update local state and refetch hosts. If a
// future Range release adds POST /plugin/range/active-profile, drop
// it in below — until then this is display-only (still useful to see
// which profile you're operating on, and to scope the host list
// refetch off of localStorage).
async function switchProfile(profileId) {
  if (!profileId) return
  activeProfileId.value = profileId
  rangeProfileName.value = profileId
  try { localStorage.setItem('human_active_profile', profileId) } catch (_) {}
  // Pass the new id explicitly so fetchHosts doesn't race against
  // Vue's reactivity batching for activeProfileId.
  await refreshAll(profileId)
}

async function refreshAll(profileName) {
  loading.value = true
  try {
    await Promise.all([fetchHosts(profileName), fetchWorkflows()])
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
  // Typing-tempo overrides: when the sliders are touched, send them.
  // Defaults match the daemon's defaults so the URL stays compact for
  // vanilla runs (omitted params → daemon defaults apply).
  const tempoQuery =
    (tempoWpm.value !== TEMPO_WPM_DEFAULT ? `&tempo_wpm=${tempoWpm.value}` : '')
    + (tempoJitterPct.value !== TEMPO_JITTER_DEFAULT ? `&tempo_jitter_pct=${tempoJitterPct.value}` : '')
  const url = `/plugin/human/api/run-profile`
    + `?host_id=${encodeURIComponent(host_id)}`
    + `&profile_id=${encodeURIComponent(profile_id)}`
    + `&record=${record.value ? 'true' : 'false'}`
    + tempoQuery
  appendLog(host_id, 'stdin',
            `[profile] ${profile_id} -> input-daemon (host=${host_id}, `
            + `record=${record.value})`)
  currentStepIdx.value = null
  // Reset any previous run's playback state so the panel doesn't show
  // stale video from a prior host/profile until the new one finalizes.
  recordingUrl.value = null
  recordingPath.value = null

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
      // Don't close yet — the recorder finalize emits `recording_ready`
      // after `done`, on the `log` event channel. We close on the
      // recording_ready handler (or onerror, whichever comes first).
      if (!record.value) {
        es.close()
      }
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
  // The SSE handler emits `event: log` lines for recording lifecycle
  // events (recording_started / finalizing_recording / recording_ready
  // / recording_error). `onmessage` only sees default-event lines, so
  // we need an explicit listener for the `log` channel.
  es.addEventListener('log', (ev) => {
    let payload
    try { payload = JSON.parse(ev.data) } catch (_) { return }
    if (!payload || !payload.event) return
    if (payload.event === 'recording_started') {
      appendLog(host_id, 'stdout', `[recording] started -> ${payload.path}`)
    } else if (payload.event === 'finalizing_recording') {
      appendLog(host_id, 'stdout', `[recording] finalizing...`)
    } else if (payload.event === 'recording_ready') {
      recordingUrl.value = payload.url
      recordingPath.value = payload.path
      appendLog(host_id, 'stdout', `[recording] ready -> ${payload.url}`)
      // Pop the new MP4 into the Recordings dropdown without a page
      // reload. autoRefreshAfterRecording() also expands the section
      // so the operator sees the new entry immediately.
      try {
        recordingsBrowserRef.value?.autoRefreshAfterRecording()
      } catch (_) { /* component not mounted yet — fine */ }
      es.close()
    } else if (payload.event === 'recording_error') {
      appendLog(host_id, 'stderr', `[recording] ${payload.error}`)
    }
  })
  es.onerror = () => {
    // EventSource fires onerror both for transient reconnects and for
    // hard failures; in our case the server closes the stream after
    // `done` (or after `recording_ready` for recorded runs), which the
    // browser surfaces as an error. If we already saw the done event
    // we've nulled currentStepIdx and set status.
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

// ---- Chord palette --------------------------------------------------------
// `chordOs` drives the Tier 2 row's OS-specific chip set. Defaults to
// 'windows' if the host record carries no `os` field — both VMs in the
// current Range deploy are Windows; Linux support exists for future
// fixtures. Computed (not a stored ref) so it stays in sync with host
// switches.
const chordOs = computed(() => {
  const h = selectedHost.value
  const raw = String(h?.os || '').toLowerCase().trim()
  if (raw.startsWith('linux')) return 'linux'
  if (raw.startsWith('darwin') || raw.startsWith('mac')) return 'darwin'
  // 'windows' / '' / anything else falls through to the Windows row;
  // those VMs are the dominant case for the live UI.
  return 'windows'
})

const chordStatusClass = computed(() => ({
  'has-text-success': chordStatus.value && chordStatusOk.value,
  'has-text-danger':  chordStatus.value && !chordStatusOk.value,
}))

function toggleStickyMod(mod) {
  const i = chordStickyMods.value.indexOf(mod)
  if (i >= 0) chordStickyMods.value.splice(i, 1)
  else chordStickyMods.value.push(mod)
}

async function sendChord(keys) {
  if (!selectedHostId.value) return
  // Sticky modifiers prepend to the chip's own key list. We auto-clear
  // sticky state after the chord fires so the next chip is "clean"
  // unless the operator deliberately re-arms.
  const sticky = chordStickyMods.value.slice()
  const fullKeys = sticky.length ? [...sticky, ...keys] : keys
  chordStatus.value = ''
  appendLog(selectedHostId.value, 'stdin', `[chord] ${fullKeys.join('+')}`)
  try {
    const res = await $api.post('/plugin/human/api/chord', {
      host_id: selectedHostId.value,
      keys: fullKeys,
      hold_ms: 50,
    })
    chordStatus.value = `sent ${fullKeys.join('+')}`
    chordStatusOk.value = true
    if (res?.data?.kbd_socket === 'tablet-fallback') {
      chordStatus.value += ' (tablet-fallback — KEY_* may be dropped)'
      chordStatusOk.value = false
    }
  } catch (err) {
    chordStatus.value = `chord failed: ${err.response?.data?.stderr || err.message || err}`
    chordStatusOk.value = false
    appendLog(selectedHostId.value, 'stderr', chordStatus.value)
  }
  // Auto-clear sticky modifiers after the chord fires.
  if (sticky.length) chordStickyMods.value = []
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
let _profilesPollHandle = null
onMounted(async () => {
  // Load profiles FIRST so the localStorage-restored selection drives
  // the very first /api/hosts call. Otherwise the initial refresh runs
  // unscoped and the operator briefly sees the union of every VENV's
  // VMs before the dropdown kicks in.
  await loadProfiles()
  await refreshAll(activeProfileId.value || undefined)
  // Live-refresh the profile dropdown every 10 s so profiles created
  // / torn down by other workflows (e.g. cti_pipeline_deploy_range,
  // the operator deleting a profile, the e2e script reaping a stale
  // mcp-* entry) appear / disappear without a page reload.
  _profilesPollHandle = setInterval(() => {
    if (document.hidden) return
    loadProfiles().catch(() => {})
  }, 10000)
})
onUnmounted(() => {
  if (_profilesPollHandle) {
    clearInterval(_profilesPollHandle)
    _profilesPollHandle = null
  }
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
  display: flex;
  flex-direction: column;
  /* Use min-height instead of a fixed height so the page can extend
     when content (live viewer + 3-row grid + Recordings section) is
     intrinsically taller than the viewport. With a fixed height, the
     grid's `minmax(75vh, 1fr)` viewer track plus row-1 (200px) plus
     row-3 (~280-420px) can push past the container — and the
     RecordingsBrowser sibling (flex 0 0 auto, no overflow rules)
     ends up painting at a y-offset that overlaps the bottom of the
     viewer ("No recordings yet…" text appearing inside the Live
     Endpoint frame). Switching to min-height lets the surrounding
     page scroll naturally; nothing overlaps.
     overflow-y: auto on .human-live (rather than the document body)
     keeps Caldera-core's top navigation pinned in place. */
  min-height: calc(100vh - 60px);
  overflow-y: auto;
  overflow-x: hidden;
}

.human-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #555;
  padding-bottom: 0.5rem;
  flex: 0 0 auto;
}

/* Profile-switcher dropdown in the page header. The chevron lives inside
   a Bulma .icon, which Caldera-core's dark theme sometimes leaves at the
   default body color (invisible against #1b1b1b). Force the muted-text
   color used elsewhere in this view so the indicator is visible. */
.dropdown.profile-switcher .button .icon i {
  color: #939393;
}
.dropdown.profile-switcher .dropdown-menu {
  min-width: 14rem;
}
.dropdown.profile-switcher .dropdown-item.is-active {
  background-color: #191970;
  color: #ffffff;
}

/* Vertical 3-row layout:
     auto       hosts row  (fixed-height strip, ~200px)
     minmax(75vh, 1fr)
                viewer row (at least 75% of viewport height — guaranteed
                real estate so the 16:9 frame isn't crushed when its
                height: 100% would otherwise resolve through a chain of
                auto-height ancestors). Bumped from 50vh to 75vh on
                operator feedback ("make the height of Live Endpoint
                50% larger keeping aspect of width") — height grows by
                50%, the 16:9 frame's height: 100% then derives a
                proportionally wider frame too.
     auto       command stream (sub-grid of 3 columns)
   The viewer row dominates the visible area — that's the whole point
   of this restructure. When the viewer is collapsed, .row-viewer
   carries the .is-collapsed class and the grid template flips so the
   viewer track shrinks to header-height and the command stream gets
   the freed space (see .human-grid.viewer-collapsed below). */
.human-grid {
  display: grid;
  grid-template-rows: auto minmax(75vh, 1fr) auto;
  gap: 0.75rem;
  flex: 1 1 auto;
  min-height: 0;
}

/* Collapsed-viewer mode: shrink the viewer's grid track (override the
   75vh minmax from the default template) and let the command stream
   use the freed vertical space. */
.human-grid.viewer-collapsed {
  grid-template-rows: auto auto minmax(0, 1fr);
}
.human-grid.viewer-collapsed .row-command {
  max-height: none;
}

/* ---------------- Row 1: hosts + selected-host ---------------- */
.row-hosts {
  display: grid;
  grid-template-columns: 1fr 3fr;
  gap: 0.75rem;
  height: 200px;
  min-height: 0;
}

.hosts-panel,
.selected-host-info {
  background-color: #272727;
  border: 1px solid #939393;
  border-radius: 4px;
  padding: 0.75rem;
  overflow: auto;
  min-width: 0;
  min-height: 0;
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

/* ---------------- Row 2: live endpoint viewer ----------------- */
.row-viewer {
  background-color: #272727;
  border: 1px solid #939393;
  border-radius: 4px;
  padding: 0.75rem;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* Collapsed: pad less and let the row shrink to its header bar. */
.row-viewer.is-collapsed {
  padding: 0.5rem 0.75rem;
}

/* The LiveEndpointViewer fills .row-viewer; its CSS internally caps the
   canvas to 16:9 (monitor-shaped) and centers it. */
.row-viewer > :deep(.live-endpoint) {
  height: 100%;
  min-height: 0;
}

/* ---------------- Row 2.5: chord palette ---------------------- */
/* Lives between the Live Endpoint viewer (row 2) and the Command
   stream (row 3). Tier rows are flex-wrap'd so a narrow page lays the
   chips out across multiple lines instead of forcing a horizontal
   scrollbar. The Tier 4 collapsible holds F-keys, arrows, nav, and
   sticky modifiers; default-closed so the strip stays compact. */
.row-chord-palette {
  background-color: #272727;
  border: 1px solid #939393;
  border-radius: 4px;
  padding: 0.5rem 0.75rem;
  margin-top: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.row-chord-palette .chord-header {
  margin-bottom: 0.1rem;
}
.row-chord-palette .chord-status {
  margin: 0;
  min-height: 1.1rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.row-chord-palette .chord-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
}
.row-chord-palette .chord-row-label {
  color: #b8b8b8;
  font-size: 0.72rem;
  width: 4.5rem;
  flex: 0 0 auto;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.row-chord-palette .chord-chip {
  padding-left: 0.55rem;
  padding-right: 0.55rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.78rem;
  border-radius: 4px;
}
.row-chord-palette .chord-tier4 {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding-top: 0.25rem;
  border-top: 1px dashed #555;
}

/* ---------------- Row 3: command stream ----------------------- */
.row-command {
  background-color: #272727;
  border: 1px solid #939393;
  border-radius: 4px;
  padding: 0.75rem;
  min-height: 280px;
  max-height: 420px;        /* bumped to accommodate bottom action bar */
  overflow: visible;        /* let dropdowns escape */
  position: relative;
  display: flex;
  flex-direction: column;
}
.row-command > .command-grid {
  flex: 1 1 auto;
  min-height: 0;
}

.command-grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1.6fr;
  gap: 0.75rem;
  height: 100%;
  min-height: 0;
}

/* Bottom action bar inside .row-command: [Run] [Clear] aligned to the
   left, with a small gap. Padding-top separates it from the 3-col grid
   above. The Run button is the page's primary action so it gets Bulma's
   is-primary (Caldera purple); Clear is is-light to read as secondary. */
.command-actions {
  display: flex;
  gap: 0.5rem;
  padding-top: 0.75rem;
  justify-content: flex-start;
}

/* Typing-tempo sliders. Compact two-row layout that fits inside the
   col-2 command pane without crowding the Manual command field. */
.tempo-controls {
  margin-top: 0.25rem;
}
.tempo-row {
  display: grid;
  grid-template-columns: 4rem 1fr 2.5rem;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.2rem;
}
.tempo-label {
  font-size: 0.78em;
  color: #c8c8c8;
}
.tempo-slider {
  width: 100%;
  cursor: pointer;
}
.tempo-value {
  font-size: 0.78em;
  font-variant-numeric: tabular-nums;
  text-align: right;
  color: #fff;
}

.cmd-col {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* Output column needs its log to fill remaining height */
.cmd-output {
  height: 100%;
}

.io-log {
  background-color: #1b1b1b;
  color: #939393;
  padding: 10px;
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  border-radius: 7px;
  font-family: monospace;
  font-size: 0.8em;
  margin: 0;
}

.io-stdin  { color: #ffffff; }
.io-stdout { color: #939393; }
.io-stderr { color: #8B0000; }

.dropdown.is-fullwidth,
.dropdown-menu.is-fullwidth {
  width: 100%;
}

/* Ability-picker dropdown: the panel needs to be wide enough to show the
   ability description without truncation, and stack above neighbouring
   chrome. min-width + auto width lets it grow past its column. */
.dropdown.searchable .dropdown-menu {
  min-width: 360px;
  width: max-content;
  max-width: min(560px, 90vw);
  z-index: 60;
}

.dropdown.searchable .dropdown-content {
  max-height: 50vh;
  overflow-y: auto;
}

.dropdown.searchable .dropdown-item {
  white-space: normal;
  line-height: 1.25;
}
.dropdown.searchable .dropdown-item p {
  white-space: normal;
  color: #939393;
  margin-top: 0.15rem;
}

/* Step-preview list: dense, monospace step lines for HID abilities. */
.step-preview {
  background: #1b1b1b;
  border: 1px solid #272727;
  border-radius: 3px;
  padding: 0.5rem 0.75rem;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.step-list {
  list-style: none;
  margin: 0.25rem 0 0 0;
  padding: 0;
  flex: 1 1 auto;
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
  color: #c8c8c8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Action pill text. Same root cause as the collapse chevron: Caldera's
   --caldera-fg variable resolves to a dark colour in this theme, so
   Bulma's .tag.is-dark default white-on-dark loses to a global rule
   and the pill renders as dark-on-dark (looks blank). Force #fff. */
.step-action.tag.is-dark {
  color: #fff !important;
}

/* Inline post-run MP4 player. Compact since the live framebuffer is the
   primary visual surface now. */
.recording-panel {
  background: #1b1b1b;
  border: 1px solid #272727;
  border-radius: 3px;
  padding: 0.5rem 0.75rem;
  margin-top: 0.5rem;
}
.recording-video {
  width: 100%;
  max-height: 180px;
  background: black;
  border-radius: 3px;
}
</style>
