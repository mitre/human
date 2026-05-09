<template>
  <!--
    LiveEndpointViewer (Human plugin)

    Embeds a noVNC RFB client pointed at a per-VM `vhost-user-gpu-2d`
    daemon via the same-origin WebSocket proxy mounted by the Range plugin:

      wss://<caldera-host>/plugin/range/api/vnc/<vmName>/ws

    Mirrors plugins/range/gui/components/WorkstationViewer.vue but renders
    inline (no modal), sized to fill the available area in its container,
    constrained to a 4:3 aspect ratio capped at 1024x768 for native
    framebuffer parity. View-only — input flows from the Human plugin's
    profile runner via the operator UDS, not the operator's keyboard/mouse.

    A 404 from the WS proxy (host has no GUI session — host-stub-1 etc)
    is surfaced as a "stub mode" placeholder rather than an endless retry
    loop.
  -->
  <div class="live-endpoint">
    <div class="endpoint-header">
      <h3 class="title is-6 mb-0">
        Live Endpoint
        <span v-if="vmName" class="has-text-grey is-size-7 ml-2">
          {{ vmName }}
        </span>
        <span
          v-if="vmName"
          class="tag is-small ml-2"
          :class="statusTagClass"
          :title="statusTitle"
        >
          {{ statusLabel }}
        </span>
        <slot name="header-extra" />
      </h3>
      <button
        v-if="vmName && (state === 'failed' || state === 'stub' || retriesExhausted)"
        class="button is-dark is-small"
        @click="manualReconnect"
      >
        <span class="icon is-small"><i class="fas fa-sync-alt"></i></span>
        <span>Retry</span>
      </button>
    </div>

    <!-- 4:3 aspect-ratio viewport. The canvas auto-fills via :deep(canvas). -->
    <div class="viewport-frame">
      <div class="viewport-aspect">
        <!-- No host selected: pure CSS placeholder; component does NOT
             attempt to instantiate RFB until vmName is non-empty. -->
        <div v-if="!vmName" class="viewport-placeholder">
          <span class="icon is-large has-text-grey">
            <i class="fas fa-desktop fa-2x"></i>
          </span>
          <p class="mt-3 has-text-grey">
            Select a host above to view its live endpoint.
          </p>
        </div>

        <!-- Stub mode: server returned 404 / host has no framebuffer. -->
        <div v-else-if="state === 'stub'" class="viewport-placeholder">
          <span class="icon is-large has-text-grey-light">
            <i class="fas fa-terminal fa-2x"></i>
          </span>
          <p class="mt-3 has-text-grey-light">
            <strong>{{ vmName }}</strong> has no live framebuffer.
          </p>
          <p class="is-size-7 has-text-grey mt-1">
            Using stub / command-line mode for this host.
          </p>
        </div>

        <!-- Connecting overlay -->
        <div
          v-else-if="state === 'connecting'"
          class="viewport-overlay"
        >
          <span class="icon is-large">
            <i class="fas fa-spinner fa-spin fa-2x"></i>
          </span>
          <p class="mt-3 has-text-white">
            Connecting to {{ vmName }}
            <span v-if="retryAttempt > 0">
              (retry {{ retryAttempt }} / {{ MAX_RETRIES }})
            </span>
            &hellip;
          </p>
        </div>

        <!-- Failure overlay (non-stub: real connection problem) -->
        <div
          v-else-if="state === 'failed' || state === 'disconnected'"
          class="viewport-overlay"
        >
          <span class="icon is-large has-text-danger">
            <i class="fas fa-times-circle fa-2x"></i>
          </span>
          <p class="mt-3 has-text-white">{{ errorMessage }}</p>
        </div>

        <!-- noVNC mounts its <canvas> as a child of this div on connect.
             We only mount this div when vmName is set so RFB never
             constructs against an empty target. -->
        <div
          v-if="vmName && state !== 'stub'"
          ref="screen"
          class="vnc-screen"
        ></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  vmName: { type: String, default: '' },
})

// state ∈ 'idle' | 'connecting' | 'connected' | 'disconnected' | 'failed' | 'stub'
const state = ref('idle')
const errorMessage = ref('')
const retryAttempt = ref(0)
const screen = ref(null)

const MAX_RETRIES = 5
const RETRY_DELAY_MS = 2000

let rfb = null
let retryTimer = null
let cancelled = false

const retriesExhausted = computed(() => retryAttempt.value >= MAX_RETRIES)

const statusLabel = computed(() => {
  switch (state.value) {
    case 'connecting':   return 'connecting'
    case 'connected':    return 'connected'
    case 'disconnected': return retriesExhausted.value ? 'disconnected' : 'reconnecting'
    case 'failed':       return 'failed'
    case 'stub':         return 'stub'
    default:             return 'idle'
  }
})

const statusTagClass = computed(() => ({
  'is-warning': state.value === 'connecting' ||
                (state.value === 'disconnected' && !retriesExhausted.value),
  'is-success': state.value === 'connected',
  'is-danger':  state.value === 'failed' ||
                (state.value === 'disconnected' && retriesExhausted.value),
  'is-dark':    state.value === 'stub',
  'is-light':   state.value === 'idle',
}))

const statusTitle = computed(() => errorMessage.value || statusLabel.value)

function vncWsUrl(vm) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/plugin/range/api/vnc/${encodeURIComponent(vm)}/ws`
}

// Probe the proxy with a HEAD/GET on the ws upgrade URL re-cast to https
// to detect "host has no framebuffer" (404) cheaply, before opening RFB.
// The Range proxy returns 404 on the GET path for unknown VMs; the WS
// upgrade path itself returns the same 404 status to a non-WS request.
async function probeStubMode(vm) {
  try {
    const probeUrl = `/plugin/range/api/vnc/${encodeURIComponent(vm)}/ws`
    const res = await fetch(probeUrl, { method: 'GET', credentials: 'same-origin' })
    // 404 -> stub. 400/426 ("upgrade required") means the proxy IS willing
    // to talk to us about this VM, just not over plain HTTP — that's the
    // happy path; it'll accept the WS upgrade.
    if (res.status === 404) return true
    return false
  } catch (_err) {
    // Network errors fall through to the RFB connect, which has its own
    // retry / failure handling.
    return false
  }
}

async function connect() {
  if (!props.vmName) return
  state.value = 'connecting'
  errorMessage.value = ''
  cancelled = false

  // Cheap server-side probe first: skip noVNC entirely for stub hosts.
  const isStub = await probeStubMode(props.vmName)
  if (cancelled) return
  if (isStub) {
    state.value = 'stub'
    return
  }

  let RFB
  try {
    const mod = await import(
      /* @vite-ignore */
      '/plugin/range/static/novnc/core/rfb.js'
    )
    RFB = mod.default
  } catch (err) {
    console.error('[live-endpoint] failed to load noVNC core', err)
    state.value = 'failed'
    errorMessage.value = 'Failed to load noVNC client library.'
    return
  }

  await nextTick()
  if (cancelled) return
  if (!screen.value) {
    state.value = 'failed'
    errorMessage.value = 'Internal error: screen container missing.'
    return
  }

  const url = vncWsUrl(props.vmName)
  try {
    rfb = new RFB(screen.value, url, {
      credentials: { password: '' },
    })
  } catch (err) {
    console.error('[live-endpoint] RFB constructor threw', err)
    state.value = 'failed'
    errorMessage.value = 'Failed to construct VNC client.'
    return
  }

  rfb.viewOnly = true        // input flows from the Human plugin only
  rfb.scaleViewport = true   // fit canvas to container, preserve aspect
  rfb.resizeSession = false
  rfb.background = '#000'

  rfb.addEventListener('connect', onRfbConnect)
  rfb.addEventListener('disconnect', onRfbDisconnect)
  rfb.addEventListener('securityfailure', onRfbSecurityFailure)
}

function onRfbConnect() {
  state.value = 'connected'
  retryAttempt.value = 0
  errorMessage.value = ''
}

function onRfbDisconnect(ev) {
  const clean = ev?.detail?.clean
  if (cancelled) {
    state.value = 'idle'
    return
  }
  state.value = 'disconnected'
  errorMessage.value = clean
    ? 'VNC disconnected.'
    : 'VNC disconnected unexpectedly.'
  scheduleRetry()
}

function onRfbSecurityFailure(ev) {
  const reason = ev?.detail?.reason || 'unknown'
  state.value = 'failed'
  errorMessage.value = `VNC security failed: ${reason}`
}

function scheduleRetry() {
  if (cancelled) return
  if (retryAttempt.value >= MAX_RETRIES) {
    errorMessage.value = `VNC disconnected — gave up after ${MAX_RETRIES} retries.`
    return
  }
  retryAttempt.value += 1
  retryTimer = setTimeout(() => {
    retryTimer = null
    if (!cancelled) connect()
  }, RETRY_DELAY_MS)
}

function manualReconnect() {
  retryAttempt.value = 0
  errorMessage.value = ''
  connect()
}

function teardown() {
  cancelled = true
  if (retryTimer) {
    clearTimeout(retryTimer)
    retryTimer = null
  }
  if (rfb) {
    try {
      rfb.removeEventListener('connect', onRfbConnect)
      rfb.removeEventListener('disconnect', onRfbDisconnect)
      rfb.removeEventListener('securityfailure', onRfbSecurityFailure)
      rfb.disconnect()
    } catch (err) {
      console.warn('[live-endpoint] teardown: rfb.disconnect threw', err)
    }
    rfb = null
  }
  state.value = 'idle'
  retryAttempt.value = 0
  errorMessage.value = ''
}

// Reconnect to a different host whenever vmName changes. teardown() flips
// `cancelled = true`, so we must reset it before kicking off `connect()`.
watch(
  () => props.vmName,
  (next, prev) => {
    if (prev) teardown()
    if (next) connect()
  },
  { immediate: true }
)

onBeforeUnmount(teardown)

defineExpose({ vncWsUrl, MAX_RETRIES, RETRY_DELAY_MS })
</script>

<style scoped>
.live-endpoint {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.endpoint-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

/* The frame fills its parent. The aspect-ratio child caps the actual
   canvas at 4:3 (matching the 1024x768 native framebuffer) and centers
   it horizontally so wide containers don't stretch the picture. */
.viewport-frame {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #000;
  border: 1px solid #939393;
  border-radius: 4px;
  overflow: hidden;
  position: relative;
}

.viewport-aspect {
  position: relative;
  aspect-ratio: 4 / 3;
  /* Take the larger of: width that fits parent, OR height that fits
     parent. CSS picks the smaller dimension automatically because of the
     aspect-ratio constraint plus max-width/max-height. */
  max-width: min(100%, 1024px);
  max-height: min(100%, 768px);
  width: 100%;
  height: 100%;
  background: #000;
}

.vnc-screen {
  width: 100%;
  height: 100%;
}

.vnc-screen :deep(canvas) {
  width: 100% !important;
  height: 100% !important;
  display: block;
}

.viewport-placeholder,
.viewport-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 1rem;
}

.viewport-placeholder {
  background: #1b1b1b;
}

.viewport-overlay {
  background: rgba(0, 0, 0, 0.7);
  z-index: 10;
}
</style>
