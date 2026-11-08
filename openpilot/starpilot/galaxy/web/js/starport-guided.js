// Guided setup shares the page’s pairing actions and slots.

export const FIRST_RUN_NOTICE = "The first connection can take a few minutes, and sometimes more than one drive, while we find what your car accepts. Keep the car on and its screen awake. Later connections are much faster because we remember what worked."
export const EXHAUSTED_NOTICE = "We tried every option we have this drive. We keep what works best and will try again the next time the car starts."
export const RESTART_TITLE = "Your dongle keeps hanging up"
export const RESTART_DETAIL = "Unplug it for 10 seconds, plug it back in and keep the car on. We carry on by ourselves when it comes back, or tap Continue."
export const HANGUP_NOTICE_AT = 6

// Adapter setup may require connecting the companion car first.
export const BLUETOOTH_FIRST = /connect your paired car in galaxy bluetooth first/i
export const BLUETOOTH_HINT = { text: "Connect your car in Bluetooth first, then come back and tap Connect.", href: "#/bluetooth", label: "Open Bluetooth" }

const CHOICES = [
  { key: "builtin", title: "Built-in wireless", detail: "Your car has wireless Android Auto.", icon: "car-front" },
  { key: "adapter", title: "Wireless adapter", detail: "AAWireless, Motorola MA1 or similar, plugged into the car.", icon: "usb-symbol" },
  { key: "usb", title: "USB cable", detail: "A data cable from the car to your comma.", icon: "usb-c" },
  { key: "app", title: "Tablet app", detail: "An iPad or Android tablet shows the dashboard.", icon: "tablet" },
]

const INSTRUCTIONS = {
  builtin: "Open your car’s phone pairing screen, then tap Find car below.",
  adapter: "Choose your adapter, put it in pairing mode, then tap Find adapter.",
  usb: "Plug the car’s Android Auto USB data port into the comma, then tap Connect.",
  app: "Choose your app, open it on your tablet on the same Wi-Fi, then tap Connect.",
}

const CAR_STEP_LABEL = { builtin: "Car", adapter: "Adapter", usb: "Cable", app: "App" }

const STAGES = {
  wireless: [
    { id: "bluetooth", label: "Reach the car over Bluetooth", states: ["connecting_bluetooth", "discovering", "rfcomm", "dongle_restart"] },
    { id: "wifi", label: "Join the car’s Wi-Fi", states: ["wifi_start", "wifi_info", "joining_wifi"] },
    { id: "secure", label: "Secure the connection", states: ["connecting_tcp", "authenticating"] },
    { id: "dashboard", label: "Bring up your dashboard", states: ["negotiating", "streaming", "suspended"] },
  ],
  wired: [
    { id: "cable", label: "Wait for the USB cable", states: ["waiting_for_usb", "usb_accessory"] },
    { id: "secure", label: "Secure the connection", states: ["connecting_tcp", "authenticating"] },
    { id: "dashboard", label: "Bring up your dashboard", states: ["negotiating", "streaming", "suspended"] },
  ],
  app: [
    { id: "app", label: "Wait for your app", states: ["waiting_for_ipad", "waiting_for_tablet"] },
    { id: "secure", label: "Secure the connection", states: ["connecting_tcp", "authenticating"] },
    { id: "dashboard", label: "Bring up your dashboard", states: ["negotiating", "streaming", "suspended"] },
  ],
}

const CONNECTING = new Set(["connecting_bluetooth", "discovering", "rfcomm", "wifi_start", "wifi_info", "joining_wifi", "connecting_tcp",
  "authenticating", "negotiating", "waiting_for_usb", "usb_accessory", "waiting_for_ipad", "waiting_for_tablet", "backoff", "dongle_restart"])

export function connectionStages(runtime) {
  const kind = runtime?.connection === "wired" ? "wired" : ["headunitpad", "openheadunit"].includes(runtime?.connection) ? "app" : "wireless"
  const stages = STAGES[kind]
  // While retrying the state is "backoff"; the stage that failed is the one to show as still working.
  const at = runtime?.state === "backoff" ? runtime.last_stage : runtime?.state
  const current = Math.max(0, stages.findIndex((stage) => stage.states.includes(at)))
  const live = runtime?.state === "streaming"
  return stages.map((stage, index) => ({
    id: stage.id, label: stage.label,
    state: live || index < current ? "done" : index === current ? (runtime?.state === "backoff" ? "retry" : "active") : "waiting",
  }))
}

// Worth showing once a handshake option has been tried this drive, has failed, or is remembered for this car.
export function handshakeSteps(ladder) {
  const steps = Array.isArray(ladder?.steps) ? ladder.steps.filter((step) => step && typeof step.label === "string") : []
  const interesting = ladder?.learned || steps.some((step) => step.state === "failed" || step.state === "worked" || step.tries > 0)
  return interesting ? steps.map((step) => ({ id: String(step.id), label: step.label, state: String(step.state),
    description: typeof step.description === "string" ? step.description : "", tries: Number.isInteger(step.tries) ? step.tries : 0,
    reason: typeof step.reason === "string" ? step.reason : "" })) : []
}

// "Round 2 of 3" while the options are still being worked through; nothing once one is learned or all are used up.
export function ladderRound(ladder) {
  if (!ladder || ladder.learned || ladder.exhausted || !Number.isInteger(ladder.max_passes)) return ""
  return `Round ${Math.min((Number(ladder.passes) || 0) + 1, ladder.max_passes)} of ${ladder.max_passes}`
}

// Reset learned or failed handshakes without re-pairing.
export function handshakeResettable(ladder) {
  if (!ladder || typeof ladder !== "object") return false
  const steps = Array.isArray(ladder.steps) ? ladder.steps : []
  return (typeof ladder.current === "string" && ladder.current !== "standard") ||
    steps.some((step) => step && (step.state === "failed" || step.state === "skipped"))
}

// The three steps always shown at the top: the package, the car (choice and pairing), and the connection.
export function setupSteps({ installed, carDone, live, carType }) {
  const state = (done, current) => (done ? "done" : current ? "current" : "todo")
  return [
    { id: "support", label: "Support", state: state(installed, !installed) },
    { id: "car", label: CAR_STEP_LABEL[carType] || "Car", state: state(installed && carDone, installed && !carDone) },
    { id: "connect", label: "Connect", state: state(live, installed && carDone && !live) },
  ]
}

function options(models, chosen) {
  return Object.entries(models || {}).map(([key, label]) => ({ key, label: String(label), selected: key === chosen }))
}

export function guidedState({ setup = null, runtime = null, selected = null, receivers = [], wired = false, app = "", carType = "builtin",
  setupDone = false, pairingActive = false, editing = false, adapterModels = {}, adapterKey = "", appModels = {} } = {}) {
  const ladder = runtime?.setup_ladder || null
  const state = runtime?.state || "idle"
  const connecting = !!runtime?.running && CONNECTING.has(state)
  const installed = !!setup?.identity?.installed
  const steps = setupSteps({ installed, carDone: setupDone, live: state === "streaming", carType })
  const base = { show: false, phase: "live", title: "", detail: "", choices: [], carType, candidates: [], progress: [], handshake: [], canResetHandshake: false,
    notice: "", exhausted: false, hint: null, steps, adapter: null, apps: null, editing: false, liveStep: "", round: "", restart: null }
  if (setup === null) return base
  const hint = BLUETOOTH_FIRST.test(String(runtime?.error || "")) ? BLUETOOTH_HINT : null
  if (connecting) {
    const wireless = (runtime?.connection || "wireless") === "wireless"
    const firstRun = !ladder?.learned && !(ladder?.successes > 0) && wireless
    const restarting = state === "dongle_restart"
    const hangups = Number(runtime?.hangups) || 0
    // Only an unofficial dongle waits for a restart; for every other receiver repeated hang-ups are said, not acted on.
    const hangupNotice = wireless && !restarting && hangups >= HANGUP_NOTICE_AT ?
      `${carType === "adapter" ? "The adapter" : "The car"} has hung up ${hangups} times before answering. If this keeps happening, ${carType === "adapter" ? "unplug the adapter for 10 seconds" : "turn the car off and on again"}.` : ""
    return { ...base, show: true, phase: restarting ? "restart" : "connecting", title: restarting ? RESTART_TITLE : runtime?.label || "Connecting to your car",
      detail: restarting ? RESTART_DETAIL : runtime?.detail || "Keep your car’s screen on. We’ll move to the next step automatically.",
      progress: connectionStages(runtime), handshake: wireless ? handshakeSteps(ladder) : [], exhausted: !!ladder?.exhausted,
      canResetHandshake: wireless && handshakeResettable(ladder), liveStep: wireless ? String(runtime?.handshake_step || "") : "",
      round: wireless ? ladderRound(ladder) : "", restart: restarting ? { hangups: Number(runtime?.dongle_restart?.hangups) || hangups } : null,
      notice: ladder?.exhausted ? EXHAUSTED_NOTICE : hangupNotice || (firstRun ? FIRST_RUN_NOTICE : ""), hint }
  }
  if (setupDone && !editing) {
    // A finished setup shows nothing, except a refusal that is the one thing to act on.
    return hint ? { ...base, show: true, phase: "bluetooth", title: "Your car needs to be connected in Bluetooth first", detail: hint.text, hint } : base
  }
  if (!installed) {
    return { ...base, show: true, phase: "package", title: "Add your Android Auto package",
      detail: "Your car only talks to devices that present Google’s Android Auto certificate. Follow the support step above, then come back here." }
  }
  const choices = CHOICES.map((choice) => ({ ...choice, selected: choice.key === carType }))
  const known = new Set([...(selected ? [selected.address] : []), ...(Array.isArray(receivers) ? receivers : []).map((car) => car?.address)])
  const candidates = []
  // Paired cars the page's own list does not show yet; the page lists the rest itself in the pairing step.
  for (const car of Array.isArray(runtime?.car_candidates) ? runtime.car_candidates : []) {
    if (car && typeof car.address === "string" && typeof car.name === "string" && !known.has(car.address)) {
      known.add(car.address)
      candidates.push({ address: car.address, name: car.name })
    }
  }
  const pairs = carType === "builtin" || carType === "adapter"
  return { ...base, show: true, phase: "setup", title: editing ? "Change how your car connects" : "Set up your car",
    detail: INSTRUCTIONS[carType] || INSTRUCTIONS.builtin, choices, candidates: pairs && !pairingActive ? candidates : [], hint, editing,
    adapter: carType === "adapter" ? { options: options(adapterModels, adapterKey), needsChoice: !adapterKey } : null,
    apps: carType === "app" ? { options: options(appModels, Object.keys(appModels).find((key) => appModels[key] === app) || ""), needsChoice: !app } : null }
}

export const StarPortGuidedSetup = {
  props: { state: { type: Object, required: true }, busy: { type: Boolean, default: false },
    choicesDisabled: { type: Boolean, default: false } },
  emits: ["choose-type", "choose-adapter", "choose-app", "use-car", "classic", "reset-handshake", "continue-ladder", "done"],
  template: `
    <section class="gx-card gx-guided" :data-phase="state.phase" aria-label="Guided StarPort setup">
      <ol class="gx-guided__steps" aria-label="Setup progress">
        <li v-for="(step, index) in state.steps" :key="step.id" class="gx-guided__step" :class="'is-' + step.state"
            :aria-current="step.state === 'current' ? 'step' : undefined">
          <span class="gx-guided__node" aria-hidden="true"><i v-if="step.state === 'done'" class="bi bi-check-lg"></i><template v-else>{{ index + 1 }}</template></span>
          <span class="gx-guided__step-label">{{ step.label }}<span class="gx-sr-only">{{ step.state === 'done' ? ', done' : step.state === 'current' ? ', current step' : '' }}</span></span>
        </li>
      </ol>

      <header class="gx-guided__header">
        <h2 class="gx-guided__title">{{ state.title }}</h2>
        <p class="gx-guided__lede">{{ state.detail }}</p>
      </header>

      <div v-if="state.choices.length" class="gx-guided__section">
        <ul class="gx-starport-list gx-guided__options" role="radiogroup" aria-label="How your car connects">
          <li v-for="choice in state.choices" :key="choice.key" role="none">
            <label class="gx-guided__option">
              <input type="radio" name="starport-connection" :checked="choice.selected" :disabled="busy || choicesDisabled" @change="$emit('choose-type', choice.key)">
              <span class="gx-guided__tile" aria-hidden="true"><i :class="'bi bi-' + choice.icon"></i></span>
              <span class="gx-starport-row__text"><strong>{{ choice.title }}</strong><small>{{ choice.detail }}</small></span>
            </label>
          </li>
        </ul>
      </div>

      <div v-if="state.adapter" class="gx-guided__section">
        <h3 class="gx-guided__subhead">Which adapter?</h3>
        <div class="gx-guided__segmented" role="radiogroup" aria-label="Adapter model">
          <label v-for="option in state.adapter.options" :key="option.key" class="gx-btn gx-btn--tonal gx-guided__choice">
            <input class="gx-sr-only" type="radio" name="starport-adapter" :checked="option.selected" :disabled="busy || choicesDisabled" @change="$emit('choose-adapter', option.key)">{{ option.label }}
          </label>
        </div>
        <p v-if="state.adapter.needsChoice" class="gx-guided__hint">Choose your adapter to continue.</p>
      </div>

      <div v-if="state.apps" class="gx-guided__section">
        <h3 class="gx-guided__subhead">Which app?</h3>
        <div class="gx-guided__segmented" role="radiogroup" aria-label="Tablet app">
          <label v-for="option in state.apps.options" :key="option.key" class="gx-btn gx-btn--tonal gx-guided__choice">
            <input class="gx-sr-only" type="radio" name="starport-apps" :checked="option.selected" :disabled="busy || choicesDisabled" @change="$emit('choose-app', option.key)">{{ option.label }}
          </label>
        </div>
        <p v-if="state.apps.needsChoice" class="gx-guided__hint">Choose your app to continue.</p>
      </div>

      <div v-if="state.candidates.length" class="gx-guided__section">
        <h3 class="gx-guided__subhead">Paired cars</h3>
        <ul class="gx-starport-list gx-guided__cars" aria-label="Paired cars that offer Android Auto">
          <li v-for="car in state.candidates" :key="car.address" class="gx-starport-row">
            <span class="gx-guided__tile" aria-hidden="true"><i class="bi bi-car-front"></i></span>
            <span class="gx-starport-row__text"><strong>{{ car.name }}</strong><small>Paired and offers Android Auto</small></span>
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy || choicesDisabled" @click="$emit('use-car', car.address)">Use</button>
          </li>
        </ul>
      </div>

      <div v-if="state.hint" class="gx-guided__callout" role="status">
        <span>{{ state.hint.text }}</span>
        <a class="gx-btn gx-btn--tonal" :href="state.hint.href">{{ state.hint.label }}</a>
      </div>

      <div v-if="state.progress.length" class="gx-guided__section">
        <ol class="gx-starport-list gx-guided__checklist" aria-label="Connection progress">
          <li v-for="step in state.progress" :key="step.id" class="gx-starport-row" :class="'is-' + step.state" :aria-current="step.state === 'active' || step.state === 'retry' ? 'step' : undefined">
            <span class="gx-guided__mark" aria-hidden="true">
              <i v-if="step.state === 'done'" class="bi bi-check-circle-fill"></i>
              <span v-else-if="step.state === 'active'" class="gx-spinner"></span>
              <i v-else-if="step.state === 'retry'" class="bi bi-arrow-repeat"></i>
              <i v-else class="bi bi-circle"></i>
            </span>
            <span class="gx-starport-row__text"><strong>{{ step.label }}</strong><small v-if="step.state === 'retry'">Trying again</small><small v-else-if="step.state === 'active' && state.liveStep" aria-live="polite">{{ state.liveStep }}</small></span>
          </li>
        </ol>
      </div>

      <div v-if="state.restart" class="gx-guided__callout" role="alert">
        <span>It hung up {{ state.restart.hangups }} times before sending anything, so we stopped calling it.</span>
        <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="$emit('continue-ladder')">Continue</button>
      </div>

      <details v-if="state.handshake.length" class="gx-starport-wiki" open>
        <summary>What we are trying<template v-if="state.round"> · {{ state.round }}</template></summary>
        <ol class="gx-starport-list gx-guided__checklist" aria-label="Handshake options">
          <li v-for="step in state.handshake" :key="step.id" class="gx-starport-row" :class="'is-' + step.state">
            <span class="gx-guided__mark" aria-hidden="true">
              <i v-if="step.state === 'worked'" class="bi bi-check-circle-fill"></i>
              <i v-else-if="step.state === 'failed'" class="bi bi-x-circle"></i>
              <i v-else-if="step.state === 'skipped'" class="bi bi-dash-circle"></i>
              <span v-else-if="step.state === 'current'" class="gx-spinner"></span>
              <i v-else class="bi bi-circle"></i>
            </span>
            <span class="gx-starport-row__text"><strong>{{ step.label }}</strong>
              <small v-if="step.description">{{ step.description }}</small>
              <small v-if="step.state === 'current' && state.liveStep">Now: {{ state.liveStep }}</small>
              <small v-if="step.tries || step.reason">{{ step.tries === 1 ? "1 try" : step.tries + " tries" }}<template v-if="step.reason"> · last: {{ step.reason }}</template></small></span>
          </li>
        </ol>
        <button v-if="state.canResetHandshake" type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="$emit('reset-handshake')">
          <i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i> Start over with the standard handshake</button>
        <p v-if="state.canResetHandshake" class="gx-guided__hint">Your Bluetooth pairing is kept.</p>
      </details>

      <p v-if="state.notice" class="gx-guided__callout gx-guided__callout--quiet" role="status">{{ state.notice }}</p>

      <div v-if="state.phase === 'setup'" class="gx-guided__body"><slot name="car" /></div>

      <div class="gx-guided__footer">
        <slot name="connect" />
        <button v-if="state.editing" type="button" class="gx-guided__link" :disabled="busy" @click="$emit('done')">Done</button>
        <button type="button" class="gx-guided__link" :disabled="busy" @click="$emit('classic')">Use classic setup</button>
      </div>
    </section>`,
}
