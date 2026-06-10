<template>
  <!--
    LiveEndpointViewer (Human plugin)

    Two viewers behind one scaffold. Branch on `session_type`:

      session_type === 'gui'  → noVNC RFB client pointed at the per-VM
        `vhost-user-gpu-2d` daemon via the Range plugin's framebuffer
        WS proxy:
          wss://<caldera-host>/plugin/range/api/vnc/<vmName>/ws

      session_type === 'cli'  → xterm.js terminal pumped through the
        Range plugin's UDS console proxy:
          wss://<caldera-host>/plugin/range/onprem/console/<vmName>
        (Linux microvm → vsock.sock; Windows microvm → serial.sock.
        Selection happens server-side in range_console_proxy.py.)

    Same outer scaffold for both: collapse chevron, status pill,
    retry button, "Connecting / reconnecting (N/8)" overlay. Operator
    sees one familiar UI regardless of which transport is in play.

    GUI branch: 16:9 viewport (monitor-shaped). Framebuffer is
    1024x768 (4:3) today — letterboxed inside the 16:9 frame. View-
    only; input flows from the Human plugin's profile runner via the
    operator UDS, not the operator's keyboard/mouse.

    CLI branch: full-rect xterm.js (no aspect lock — terminals don't
    care). Operator keystrokes ARE forwarded (typing into the terminal
    is the supported input path for cli hosts; R4 still applies — no
    in-guest agent, just bytes over the serial/vsock UDS).

    A 404 from the WS proxy (host has no GUI/CLI session) is surfaced
    as a "stub mode" placeholder rather than an endless retry loop.
  -->
  <div class="live-endpoint" :class="{ 'is-collapsed': collapsed }">
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
      <div class="endpoint-header-actions">
        <span
          v-if="inputStatus"
          class="tag is-small mr-2"
          :class="inputStatusClass"
        >
          {{ inputStatus }}
        </span>
        <button
          v-if="vmName && (state === 'failed' || state === 'stub' || retriesExhausted)"
          class="button is-dark is-small mr-2"
          @click="manualReconnect"
        >
          <span class="icon is-small"><i class="fas fa-sync-alt"></i></span>
          <span>Retry</span>
        </button>
        <!-- Collapse / expand chevron. v-show (NOT v-if) on the body below
             keeps noVNC's RFB connection alive while the section is hidden,
             so the operator can collapse for screen real estate without
             tearing down the WebSocket. -->
        <button
          class="button is-dark is-small collapse-toggle"
          :title="collapsed ? 'Expand Live Endpoint' : 'Collapse Live Endpoint'"
          :aria-expanded="!collapsed"
          @click="collapsed = !collapsed"
        >
          <span class="icon is-small">
            <i
              class="fas"
              :class="collapsed ? 'fa-chevron-down' : 'fa-chevron-up'"
            ></i>
          </span>
        </button>
      </div>
    </div>

    <!-- Viewport. v-show (not v-if) so the noVNC RFB / xterm.js
         instance + WebSocket stay alive when the section is collapsed.
         The GUI branch wraps its child in a 16:9 monitor-shaped
         aspect frame; the CLI branch fills the whole rect (terminals
         resize cleanly). -->
    <div class="viewport-frame" v-show="!collapsed">
      <div :class="isCli ? 'viewport-fullrect' : 'viewport-aspect'">
        <!-- No host selected: pure CSS placeholder; component does NOT
             attempt to instantiate RFB/xterm until vmName is non-empty. -->
        <div v-if="!vmName" class="viewport-placeholder">
          <span class="icon is-large has-text-grey">
            <i class="fas fa-desktop fa-2x"></i>
          </span>
          <p class="mt-3 has-text-grey">
            Select a host above to view its live endpoint.
          </p>
        </div>

        <!-- Stub mode: server returned 404 / host has no live session. -->
        <div v-else-if="state === 'stub'" class="viewport-placeholder">
          <span class="icon is-large has-text-grey-light">
            <i class="fas fa-terminal fa-2x"></i>
          </span>
          <p class="mt-3 has-text-grey-light">
            <strong>{{ vmName }}</strong> has no live
            {{ isCli ? 'console' : 'framebuffer' }}.
          </p>
          <p class="is-size-7 has-text-grey mt-1">
            Using stub mode for this host.
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
            Connecting to VM {{ isCli ? 'console' : 'display' }}
            ({{ vmName }})
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

        <!-- GUI branch: microVM frame sockets render into our canvas.
             Legacy RFB/noVNC hosts still mount noVNC into the div. -->
        <canvas
          v-if="vmName && isFrameTransport && state !== 'stub'"
          ref="frameCanvas"
          class="frame-screen"
          tabindex="0"
          @pointermove="onFramePointerMove"
          @pointerdown="onFramePointerDown"
          @wheel="onFrameWheel"
          @keydown="onFrameKeyDown"
          @contextmenu.prevent
        ></canvas>
        <div
          v-if="vmName && !isCli && !isFrameTransport && state !== 'stub'"
          ref="screen"
          class="vnc-screen"
        ></div>

        <!-- CLI branch: xterm.js mounts its terminal DOM as a child
             of this div. Same lifecycle gate as the GUI branch. -->
        <div
          v-if="vmName && isCli && state !== 'stub'"
          ref="termHost"
          class="term-host"
        ></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  vmName: { type: String, default: '' },
  frameWs: { type: String, default: '' },
  // 'gui' → noVNC; 'cli' → xterm.js. Anything else falls through
  // to the GUI branch (back-compat: pre-cli hosts surface no value).
  sessionType: { type: String, default: 'gui' },
  // Modular, backend-authoritative endpoint descriptor (human_svc
  // resolve_viewer_endpoint). When `transport` is set, it drives the viewer
  // and `endpointUrl` (already profile-qualified) is used verbatim — the
  // component no longer string-builds URLs or infers transport from provider.
  // Empty transport falls back to the legacy sessionType/frameWs heuristic.
  transport: { type: String, default: '' },        // frame|vnc|console|none
  endpointUrl: { type: String, default: '' },       // profile-qualified WS path
  credentialsUrl: { type: String, default: '' },    // vnc one-time password URL
  interactive: { type: Boolean, default: true },    // false → viewer read-only
})

// Transport is backend-authoritative when provided; otherwise fall back to the
// legacy heuristic so pre-descriptor hosts still render. Adding a hypervisor is
// a backend registry row — no change here.
const resolvedTransport = computed(() => {
  if (props.transport) return props.transport
  if (props.sessionType === 'cli') return 'console'
  if (props.frameWs) return 'frame'
  return 'vnc'
})
const isCli = computed(() => resolvedTransport.value === 'console')
const isFrameTransport = computed(() => resolvedTransport.value === 'frame')
const isNone = computed(() => resolvedTransport.value === 'none')

// Notify the parent when the operator collapses/expands the section so
// the page-level grid can redistribute vertical space (give the freed
// real estate to the command stream below).
const emit = defineEmits(['update:collapsed'])

// state ∈ 'idle' | 'connecting' | 'connected' | 'disconnected' | 'failed' | 'stub'
const state = ref('idle')
const errorMessage = ref('')
const retryAttempt = ref(0)
const inputStatus = ref('')
const screen = ref(null)

// Section collapse state. Session-only (no localStorage). The viewer body
// uses v-show, so the noVNC RFB / WebSocket stays alive while collapsed.
const collapsed = ref(false)
watch(collapsed, (v) => emit('update:collapsed', v))

// Stage 3 (connection-recovery-hardening): widen the retry budget and
// switch to an exponential backoff that pairs with the WS proxy's
// backend-reconnect logic (range_vnc_proxy.py _RECONNECT_BACKOFFS_S)
// and the daemon supervisor (onprem_microvm_provider DaemonSupervisor).
//
// Backoff schedule (ms): 500, 1000, 2000, 4000, 8000, 8000, 8000, 8000.
// Total worst-case retry window ~39.5s — long enough to ride out a
// daemon respawn + CH UDS rebind + browser hand-off without giving up,
// but bounded so a truly-dead VM still surfaces a "failed" state.
const MAX_RETRIES = 8
const RETRY_BACKOFFS_MS = [500, 1000, 2000, 4000, 8000, 8000, 8000, 8000]

let rfb = null
let frameWs = null
let retryTimer = null
let cancelled = false

// CLI branch state. `term` is the xterm.js Terminal instance; `fit`
// is the FitAddon (auto-size terminal cols/rows to its container);
// `termWs` is the WebSocket to the console proxy; `termHost` is the
// ref to the <div> xterm mounts inside; `resizeObs` is the
// ResizeObserver that calls fit.fit() when the container changes
// size. All four nulled out in teardown().
let term = null
let fit = null
let termWs = null
const termHost = ref(null)
const frameCanvas = ref(null)
let resizeObs = null
let lastFramePointerMoveAt = 0
let framePointerMoveInFlight = false
let pendingFramePointerMove = null
let framePointerMoveTimer = null
const FRAME_POINTER_MOVE_INTERVAL_MS = 20
const TABLET_ABS_MAX = 32767

const retriesExhausted = computed(() => retryAttempt.value >= MAX_RETRIES)

const statusLabel = computed(() => {
  switch (state.value) {
    case 'connecting':
      // Distinguish first connect ("connecting") from the post-disconnect
      // retry path ("reconnecting (N/8)") so the operator can see we're
      // actively trying to recover instead of stuck.
      return retryAttempt.value > 0
        ? `reconnecting (${retryAttempt.value}/${MAX_RETRIES})`
        : 'connecting'
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
const inputStatusClass = computed(() => ({
  'is-info': inputStatus.value === 'input sent',
  'is-warning': inputStatus.value === 'input pending',
  'is-danger': inputStatus.value === 'input failed',
}))

function vncWsUrl(vm) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/plugin/range/api/vnc/${encodeURIComponent(vm)}/ws`
}

// Console (cli) WS URL — symmetric with vncWsUrl. Served by
// plugins/range/app/range_console_proxy.py; routes to the per-VM
// vsock.sock (linux) or serial.sock (windows) on the host side.
function consoleWsUrl(vm) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/plugin/range/onprem/console/${encodeURIComponent(vm)}`
}

function frameWsUrl(vm) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const path = props.frameWs || `/plugin/range/api/frame/${encodeURIComponent(vm)}/ws`
  if (path.startsWith('ws://') || path.startsWith('wss://')) return path
  return `${proto}//${window.location.host}${path}`
}

// Wrap a server-supplied path into a ws(s):// URL for the current host.
function wsFromPath(path) {
  if (!path) return ''
  if (path.startsWith('ws://') || path.startsWith('wss://')) return path
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${path}`
}

// The live WS URL for the active transport. Prefers the backend descriptor's
// profile-qualified endpointUrl (so proxmox/vbox resolve correctly); falls
// back to the legacy per-transport builders for pre-descriptor hosts.
function liveWsUrl(vm) {
  if (props.endpointUrl) return wsFromPath(props.endpointUrl)
  if (isCli.value) return consoleWsUrl(vm)
  if (isFrameTransport.value) return frameWsUrl(vm)
  return vncWsUrl(vm)
}

// Probe the proxy with a HEAD/GET on the ws upgrade URL re-cast to https
// to detect "host has no live session" (404) cheaply, before opening
// RFB/xterm. Both proxies return 404 for unknown VMs and 426 ("upgrade
// required") on plain-HTTP GET against a known VM — 426 is the happy
// path; it means the proxy IS willing to upgrade to WS.
async function probeStubMode(vm) {
  try {
    const probeUrl = props.endpointUrl
      ? props.endpointUrl
      : (isCli.value
          ? `/plugin/range/onprem/console/${encodeURIComponent(vm)}`
          : (isFrameTransport.value
              ? (props.frameWs || `/plugin/range/api/frame/${encodeURIComponent(vm)}/ws`)
              : `/plugin/range/api/vnc/${encodeURIComponent(vm)}/ws`))
    const res = await fetch(probeUrl, { method: 'GET', credentials: 'same-origin' })
    if (res.status === 404) return true
    return false
  } catch (_err) {
    // Network errors fall through to the connect path, which has its
    // own retry / failure handling.
    return false
  }
}

async function connect() {
  if (!props.vmName) return
  // The backend resolved no viewable transport for this host (unknown
  // provider/session) — render the honest empty state, never probe/connect.
  if (isNone.value) { state.value = 'stub'; return }
  state.value = 'connecting'
  errorMessage.value = ''
  cancelled = false

  // Cheap server-side probe first: skip noVNC / xterm entirely for
  // stub hosts. Same shape either way; the probe URL flips on isCli.
  const isStub = await probeStubMode(props.vmName)
  if (cancelled) return
  if (isStub) {
    state.value = 'stub'
    return
  }

  // Dispatch to the right transport. The outer scaffold (status pill,
  // retry button, connecting overlay) is shared; only the inner
  // rendering + WS wiring differ.
  if (isCli.value) {
    await connectCli()
  } else if (isFrameTransport.value) {
    await connectFrame()
  } else {
    await connectGui()
  }
}

async function connectFrame() {
  await nextTick()
  if (cancelled) return
  if (!frameCanvas.value) {
    state.value = 'failed'
    errorMessage.value = 'Internal error: frame canvas missing.'
    return
  }

  const url = liveWsUrl(props.vmName)
  try {
    frameWs = new WebSocket(url)
    frameWs.binaryType = 'arraybuffer'
  } catch (err) {
    console.error('[live-endpoint] frame WS construct failed', err)
    state.value = 'failed'
    errorMessage.value = 'Failed to open frame WebSocket.'
    return
  }

  frameWs.addEventListener('open', onFrameWsOpen)
  frameWs.addEventListener('message', onFrameWsMessage)
  frameWs.addEventListener('close', onFrameWsClose)
  frameWs.addEventListener('error', onFrameWsError)
}

async function connectGui() {
  let RFB
  try {
    // noVNC core is served at runtime by the range plugin. Build the URL as a
    // variable so the bundler treats this as a true runtime import: a literal
    // dynamic import of an absolute /plugin/... path is a HARD build error in
    // vite 7 / rollup ("failed to resolve import"), which @vite-ignore alone
    // does not suppress. A non-literal specifier is left un-analyzed.
    const rfbUrl = '/plugin/range/static/novnc/core/rfb.js'
    const mod = await import(/* @vite-ignore */ rfbUrl)
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

  const url = liveWsUrl(props.vmName)
  // Some host-side VNC transports (proxmox QEMU console) require a one-time
  // password the backend mints; the descriptor hands us the (profile-qualified)
  // URL to fetch it. microVM/TigerVNC has no password and supplies no URL.
  let password = ''
  if (props.credentialsUrl) {
    try {
      const res = await fetch(props.credentialsUrl, { credentials: 'same-origin' })
      if (res.ok) { const j = await res.json(); password = j.password || '' }
    } catch (_err) { /* fall through with empty password */ }
  }
  if (cancelled) return
  try {
    rfb = new RFB(screen.value, url, {
      credentials: { password },
    })
  } catch (err) {
    console.error('[live-endpoint] RFB constructor threw', err)
    state.value = 'failed'
    errorMessage.value = 'Failed to construct VNC client.'
    return
  }

  // Read-only unless the descriptor says this transport is interactive
  // host-side (proxmox QEMU input). microVM frames take input via the input
  // daemon, not RFB, so they pass interactive=true but never reach this path.
  rfb.viewOnly = !props.interactive
  rfb.scaleViewport = true   // fit canvas to container, preserve aspect
  rfb.resizeSession = false
  rfb.background = '#000'

  rfb.addEventListener('connect', onRfbConnect)
  rfb.addEventListener('disconnect', onRfbDisconnect)
  rfb.addEventListener('securityfailure', onRfbSecurityFailure)
}

// Lazy-load xterm.css the first time the CLI branch runs. The
// stylesheet is vendored under /plugin/range/gui/static/xterm/ along
// with the JS. We append it to <head> once; subsequent calls no-op.
function ensureXtermCss() {
  const href = '/plugin/range/gui/static/xterm/xterm.css'
  if (document.querySelector(`link[data-xterm="1"]`)) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = href
  link.dataset.xterm = '1'
  document.head.appendChild(link)
}

async function connectCli() {
  // The vendored xterm.js + xterm-addon-fit are plain UMD bundles
  // (not ES modules). Inject them via <script> tags so they register
  // their globals (window.Terminal, window.FitAddon) without the
  // dynamic-import "Failed to parse module specifier" trap.
  ensureXtermCss()
  try {
    await loadGlobalScriptOnce(
      '/plugin/range/gui/static/xterm/xterm.js',
      'Terminal'
    )
    await loadGlobalScriptOnce(
      '/plugin/range/gui/static/xterm/xterm-addon-fit.min.js',
      'FitAddon'
    )
  } catch (err) {
    console.error('[live-endpoint] failed to load xterm.js', err)
    state.value = 'failed'
    errorMessage.value = 'Failed to load xterm.js client library.'
    return
  }

  await nextTick()
  if (cancelled) return
  if (!termHost.value) {
    state.value = 'failed'
    errorMessage.value = 'Internal error: terminal container missing.'
    return
  }

  // Wipe any prior xterm DOM (a reconnect after disconnect re-enters
  // this function and we don't want stacked terminal divs).
  termHost.value.innerHTML = ''

  try {
    const Terminal = window.Terminal
    const FitAddon = window.FitAddon && window.FitAddon.FitAddon
    term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      theme: { background: '#000000', foreground: '#e6e6e6' },
      convertEol: false,
    })
    if (FitAddon) {
      fit = new FitAddon()
      term.loadAddon(fit)
    }
    term.open(termHost.value)
    if (fit) {
      try { fit.fit() } catch (_e) { /* container may still be 0×0 */ }
    }
  } catch (err) {
    console.error('[live-endpoint] xterm constructor threw', err)
    state.value = 'failed'
    errorMessage.value = 'Failed to construct xterm.js terminal.'
    return
  }

  // Auto-refit on container size changes (e.g. operator collapses
  // another section and ours grows). xterm.js needs an explicit
  // fit() call to recompute cols/rows; without it the terminal sits
  // at its initial dimensions even though the DOM grew.
  if (typeof ResizeObserver !== 'undefined') {
    resizeObs = new ResizeObserver(() => {
      try { if (fit) fit.fit() } catch (_e) { /* ignore transient 0×0 */ }
    })
    resizeObs.observe(termHost.value)
  }

  // Open the WS and wire input/output.
  const url = liveWsUrl(props.vmName)
  try {
    termWs = new WebSocket(url)
    termWs.binaryType = 'arraybuffer'
  } catch (err) {
    console.error('[live-endpoint] console WS construct failed', err)
    state.value = 'failed'
    errorMessage.value = 'Failed to open console WebSocket.'
    return
  }

  termWs.addEventListener('open', onTermWsOpen)
  termWs.addEventListener('message', onTermWsMessage)
  termWs.addEventListener('close', onTermWsClose)
  termWs.addEventListener('error', onTermWsError)

  // Every keystroke from xterm.js → WS. xterm gives us the raw
  // sequence (e.g. "\x1b[A" for arrow-up); we just forward bytes.
  term.onData((data) => {
    if (termWs && termWs.readyState === WebSocket.OPEN) {
      termWs.send(data)
    }
  })
}

// ---- xterm.js script-loader -------------------------------------------------
// Tiny, idempotent <script src="…"> loader keyed on a window global.
// Multiple LiveEndpointViewer mounts (operator flips between hosts)
// must not re-inject the script tag each time, and must wait for the
// global to appear before the second caller continues.
const _xtermScriptPromises = new Map()
function loadGlobalScriptOnce(src, globalName) {
  if (window[globalName]) return Promise.resolve()
  if (_xtermScriptPromises.has(src)) return _xtermScriptPromises.get(src)
  const p = new Promise((resolve, reject) => {
    const tag = document.createElement('script')
    tag.src = src
    tag.async = true
    tag.onload = () => {
      if (window[globalName]) resolve()
      else reject(new Error(`script ${src} loaded but ${globalName} undefined`))
    }
    tag.onerror = () => reject(new Error(`script load failed: ${src}`))
    document.head.appendChild(tag)
  })
  _xtermScriptPromises.set(src, p)
  return p
}

function onTermWsOpen() {
  state.value = 'connected'
  retryAttempt.value = 0
  errorMessage.value = ''
  // Nudge the guest with a newline so an idle agetty re-prints its
  // login prompt; otherwise the operator sees a blank black square
  // until they type something.
  try {
    if (termWs && termWs.readyState === WebSocket.OPEN) termWs.send('\r')
  } catch (_e) { /* best-effort */ }
}

function onTermWsMessage(ev) {
  if (!term) return
  const data = ev.data
  if (data instanceof ArrayBuffer) {
    // Binary frames from the proxy → write bytes verbatim.
    term.write(new Uint8Array(data))
  } else if (typeof data === 'string') {
    term.write(data)
  } else if (data && typeof data.text === 'function') {
    // Blob (some browsers); decode async.
    data.text().then((s) => { if (term) term.write(s) })
  }
}

function onTermWsClose(ev) {
  if (cancelled) {
    state.value = 'idle'
    return
  }
  if (retryAttempt.value < MAX_RETRIES) {
    state.value = 'connecting'
  } else {
    state.value = 'disconnected'
  }
  errorMessage.value = ev && ev.wasClean
    ? 'Console disconnected.'
    : 'Console disconnected unexpectedly.'
  scheduleRetry()
}

function onTermWsError(ev) {
  console.warn('[live-endpoint] console WS error', ev)
  // Don't flip to 'failed' here — onTermWsClose runs right after and
  // owns the retry decision. Just record a friendlier message.
  errorMessage.value = 'Console WebSocket error.'
}

function onFrameWsOpen() {
  state.value = 'connected'
  retryAttempt.value = 0
  errorMessage.value = ''
}

function onFrameWsMessage(ev) {
  const canvas = frameCanvas.value
  if (!canvas || !(ev.data instanceof ArrayBuffer)) return
  const buf = ev.data
  if (buf.byteLength < 24) return
  const magic = String.fromCharCode(...new Uint8Array(buf, 0, 4))
  if (magic !== 'TSFB') return
  const view = new DataView(buf)
  const w = view.getUint32(4, true)
  const h = view.getUint32(8, true)
  const len = view.getUint32(20, true)
  if (!w || !h || len !== w * h * 4 || buf.byteLength < 24 + len) return
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w
    canvas.height = h
  }
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const pixels = new Uint8ClampedArray(buf, 24, len)
  ctx.putImageData(new ImageData(pixels, w, h), 0, 0)
}

function frameCanvasPoint(ev) {
  const canvas = frameCanvas.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  if (!rect.width || !rect.height) return null
  return {
    x: Math.max(0, Math.min(TABLET_ABS_MAX, Math.round(((ev.clientX - rect.left) / rect.width) * TABLET_ABS_MAX))),
    y: Math.max(0, Math.min(TABLET_ABS_MAX, Math.round(((ev.clientY - rect.top) / rect.height) * TABLET_ABS_MAX))),
  }
}

async function sendFrameInput(messages, options = {}) {
  if (!props.vmName || !messages.length) return
  if (!options.quiet) inputStatus.value = 'input pending'
  try {
    const res = await fetch('/plugin/human/api/input', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host_id: props.vmName,
        messages,
      }),
    })
    if (!options.quiet || !res.ok) {
      inputStatus.value = res.ok ? 'input sent' : 'input failed'
    }
  } catch (err) {
    console.warn('[live-endpoint] frame input failed', err)
    inputStatus.value = 'input failed'
  }
}

function flushFramePointerMove() {
  if (framePointerMoveInFlight || !pendingFramePointerMove || state.value !== 'connected') return
  const now = performance.now()
  const waitMs = Math.max(0, FRAME_POINTER_MOVE_INTERVAL_MS - (now - lastFramePointerMoveAt))
  if (waitMs > 0) {
    if (!framePointerMoveTimer) {
      framePointerMoveTimer = setTimeout(() => {
        framePointerMoveTimer = null
        flushFramePointerMove()
      }, waitMs)
    }
    return
  }

  const message = pendingFramePointerMove
  pendingFramePointerMove = null
  framePointerMoveInFlight = true
  lastFramePointerMoveAt = now
  sendFrameInput([message], { quiet: true }).finally(() => {
    framePointerMoveInFlight = false
    flushFramePointerMove()
  })
}

function onFramePointerMove(ev) {
  if (state.value !== 'connected') return
  const point = frameCanvasPoint(ev)
  if (!point) return
  pendingFramePointerMove = {
    action: 'move',
    target: { kind: 'abs', x: point.x, y: point.y },
    duration_ms: 0,
  }
  flushFramePointerMove()
}

function onFramePointerDown(ev) {
  if (state.value !== 'connected') return
  frameCanvas.value?.focus()
  const point = frameCanvasPoint(ev)
  if (!point) return
  pendingFramePointerMove = null
  const button = ev.button === 2 ? 'right' : ev.button === 1 ? 'middle' : 'left'
  sendFrameInput([
    { action: 'move', target: { kind: 'abs', x: point.x, y: point.y }, duration_ms: 0 },
    { action: 'click', button, count: 1 },
  ])
}

function onFrameWheel(ev) {
  if (state.value !== 'connected') return
  ev.preventDefault()
  const direction = ev.deltaY < 0 ? 'up' : 'down'
  const ticks = Math.max(1, Math.min(8, Math.round(Math.abs(ev.deltaY) / 100) || 1))
  sendFrameInput([{ action: 'scroll', direction, ticks }])
}

function frameKeyName(ev) {
  const special = {
    ArrowUp: 'up',
    ArrowDown: 'down',
    ArrowLeft: 'left',
    ArrowRight: 'right',
    Enter: 'enter',
    Escape: 'esc',
    Backspace: 'backspace',
    Tab: 'tab',
    Delete: 'delete',
    Home: 'home',
    End: 'end',
    PageUp: 'pageup',
    PageDown: 'pagedown',
    ' ': 'space',
  }
  return special[ev.key] || ev.key
}

function onFrameKeyDown(ev) {
  if (state.value !== 'connected') return
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return
  if (ev.key.length === 1) {
    ev.preventDefault()
    sendFrameInput([{ action: 'type', text: ev.key }])
    return
  }
  const key = frameKeyName(ev)
  if (key) {
    ev.preventDefault()
    sendFrameInput([{ action: 'press', key }])
  }
}

function onFrameWsClose(ev) {
  if (cancelled) {
    state.value = 'idle'
    return
  }
  if (retryAttempt.value < MAX_RETRIES) {
    state.value = 'connecting'
  } else {
    state.value = 'disconnected'
  }
  errorMessage.value = ev && ev.wasClean
    ? 'Frame stream disconnected.'
    : 'Frame stream disconnected unexpectedly.'
  scheduleRetry()
}

function onFrameWsError(ev) {
  console.warn('[live-endpoint] frame WS error', ev)
  errorMessage.value = 'Frame WebSocket error.'
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
  // Stage 3: stay in 'connecting' (warning tag, spinner) instead of
  // flipping to 'disconnected' (red error overlay) while we have
  // retries left. The operator should see "we're working on it",
  // not "everything's broken", until we actually exhaust the budget.
  if (retryAttempt.value < MAX_RETRIES) {
    state.value = 'connecting'
  } else {
    state.value = 'disconnected'
  }
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
    state.value = 'disconnected'  // committed-give-up
    errorMessage.value = `VNC disconnected — gave up after ${MAX_RETRIES} retries.`
    return
  }
  // Index into the backoff table BEFORE we increment retryAttempt so
  // the very first retry uses backoffs[0] (= 500ms), not backoffs[1].
  const delay = RETRY_BACKOFFS_MS[
    Math.min(retryAttempt.value, RETRY_BACKOFFS_MS.length - 1)
  ]
  retryAttempt.value += 1
  retryTimer = setTimeout(() => {
    retryTimer = null
    if (!cancelled) connect()
  }, delay)
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
  if (framePointerMoveTimer) {
    clearTimeout(framePointerMoveTimer)
    framePointerMoveTimer = null
  }
  pendingFramePointerMove = null
  framePointerMoveInFlight = false
  // GUI branch teardown
  if (frameWs) {
    try {
      frameWs.removeEventListener('open', onFrameWsOpen)
      frameWs.removeEventListener('message', onFrameWsMessage)
      frameWs.removeEventListener('close', onFrameWsClose)
      frameWs.removeEventListener('error', onFrameWsError)
      frameWs.close()
    } catch (err) {
      console.warn('[live-endpoint] teardown: frameWs.close threw', err)
    }
    frameWs = null
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
  // CLI branch teardown
  if (termWs) {
    try {
      termWs.removeEventListener('open', onTermWsOpen)
      termWs.removeEventListener('message', onTermWsMessage)
      termWs.removeEventListener('close', onTermWsClose)
      termWs.removeEventListener('error', onTermWsError)
      // close() is a no-op if already closed; safe to call.
      termWs.close()
    } catch (err) {
      console.warn('[live-endpoint] teardown: termWs.close threw', err)
    }
    termWs = null
  }
  if (resizeObs) {
    try { resizeObs.disconnect() } catch (_e) { /* ignore */ }
    resizeObs = null
  }
  if (term) {
    try { term.dispose() } catch (_e) { /* ignore */ }
    term = null
    fit = null
  }
  state.value = 'idle'
  retryAttempt.value = 0
  errorMessage.value = ''
  inputStatus.value = ''
}

// Reconnect to a different host whenever vmName OR sessionType changes
// (the latter so e.g. switching from a gui host to a cli host on the
// same component instance flips transports cleanly). teardown() sets
// `cancelled = true`, so we don't reset it explicitly — connect()
// does that on entry.
watch(
  () => [props.vmName, props.sessionType, props.frameWs,
         props.transport, props.endpointUrl],
  ([nextVm], [prevVm] = [null]) => {
    if (prevVm) teardown()
    if (nextVm) connect()
  },
  { immediate: true }
)

onBeforeUnmount(teardown)

defineExpose({ vncWsUrl, consoleWsUrl, frameWsUrl, MAX_RETRIES, RETRY_BACKOFFS_MS })
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

.endpoint-header-actions {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
}

.collapse-toggle {
  /* Square-ish chevron-only button; lets the title stretch naturally. */
  padding-left: 0.5rem;
  padding-right: 0.5rem;
}

/* Force the chevron glyph white on the dark button background. Bulma's
   .button.is-dark sets color:#fff on the button, but Vue's scoped-style
   isolation + Caldera's global CSS load order can cause the inherited
   color to lose to a more-specific rule on `i.fas` elsewhere, leaving
   the icon rendered in the same dark grey as the button background
   (operator sees an apparently-blank/black square). Setting an explicit
   color here matches the convention used elsewhere in Caldera Vue
   (e.g. ShowDeployedInstances.vue's `style="color: white"` on its
   collapse icon). */
.collapse-toggle .icon,
.collapse-toggle .icon i {
  color: #fff !important;
}

/* When collapsed, drop bottom-margin from the header so the section
   shrinks to a single bar and the page redistributes the freed space. */
.live-endpoint.is-collapsed .endpoint-header {
  margin-bottom: 0;
}
.live-endpoint.is-collapsed {
  flex: 0 0 auto;
  height: auto;
  min-height: 0;
}

/* The frame fills its parent. The aspect-ratio child fills as much of the
   parent as possible while keeping 16:9 (monitor-shaped), picking
   whichever of width or height is the constraining dimension. No hard
   pixel cap so wider browser windows produce wider canvases. */
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
  aspect-ratio: 16 / 9;
  /* Use ALL the vertical space first (height: 100%), then derive width
     from the 16:9 aspect ratio. max-width: 100% caps us at the parent's
     width when the row is narrower-than-tall (then aspect-ratio derives
     height from width instead). margin: 0 auto centers horizontally
     when the parent is wider than the resulting canvas. The parent row
     in human.vue is given a min-height of 50vh so the height: 100% here
     resolves to a real pixel value and we get a properly monitor-shaped
     frame instead of being clamped by an auto-height grandparent. */
  height: 100%;
  width: auto;
  max-width: 100%;
  max-height: 100%;
  margin: 0 auto;
  background: #000;
}

.vnc-screen {
  width: 100%;
  height: 100%;
}

.frame-screen {
  width: 100%;
  height: 100%;
  display: block;
  image-rendering: auto;
  background: #000;
}

.vnc-screen :deep(canvas) {
  width: 100% !important;
  height: 100% !important;
  display: block;
}

/* CLI branch: terminals don't have a meaningful aspect ratio — fill
   the whole viewport rect, no monitor-shaped letterbox. */
.viewport-fullrect {
  position: relative;
  width: 100%;
  height: 100%;
  background: #000;
}

.term-host {
  width: 100%;
  height: 100%;
}

/* xterm.js injects a .xterm wrapper inside our host div. Force it
   to fill so cols/rows track the container, and recolor the
   default background to true black (matches the surrounding
   .viewport-frame). */
.term-host :deep(.xterm),
.term-host :deep(.xterm-viewport),
.term-host :deep(.xterm-screen) {
  width: 100% !important;
  height: 100% !important;
  background-color: #000 !important;
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
