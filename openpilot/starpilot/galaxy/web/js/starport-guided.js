// The guided StarPort setup. Three steps (support, car, connect), one choice about how the car connects, and a live checklist of
// what is being tried while connecting. It reads the daemon status the page already has and drives the page's existing actions,
// so it adds no state of its own. The pairing, cable and app instructions are the page's own and arrive through the `car` slot;
// the Connect button arrives through the `connect` slot. Turned off, the classic screens come back ("Use classic setup").

export const FIRST_RUN_NOTICE = "The first connection can take a few minutes, and sometimes more than one drive, while we find what your car accepts. Keep the car on and its screen awake. Later connections are much faster because we remember what worked."
export const EXHAUSTED_NOTICE = "We tried every option we have this drive. We keep what works best and will try again the next time the car starts."

// The adapter flow refuses to continue until the car itself is connected in Galaxy Bluetooth (27 Bugsink reports, 7 devices).
export const BLUETOOTH_FIRST = /connect your paired car in galaxy bluetooth first/i
export const BLUETOOTH_HINT = { text: "Connect your car in Bluetooth first, then come back and tap Connect.", href: "#/bluetooth", label: "Open Bluetooth" }

const CHOICES = [
  { key: "builtin", title: "Built-in wireless", detail: "Your car has wireless Android Auto.", icon: "car-front-fill", tone: "blue" },
  { key: "adapter", title: "Wireless adapter", detail: "AAWireless, Motorola MA1 or similar, plugged into the car.", icon: "usb-plug-fill", tone: "orange" },
  { key: "usb", title: "USB cable", detail: "A data cable from the car to your comma.", icon: "usb-c-fill", tone: "gray" },
  { key: "app", title: "Tablet app", detail: "An iPad or Android tablet shows the dashboard.", icon: "tablet-fill", tone: "purple" },
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
    { id: "bluetooth", label: "Reach the car over Bluetooth", states: ["connecting_bluetooth", "discovering", "rfcomm"] },
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
  "authenticating", "negotiating", "waiting_for_usb", "usb_accessory", "waiting_for_ipad", "waiting_for_tablet", "backoff"])

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

// Only worth showing once a handshake option has been tried and failed, or one is remembered for this car.
export function handshakeSteps(ladder) {
  const steps = Array.isArray(ladder?.steps) ? ladder.steps.filter((step) => step && typeof step.label === "string") : []
  const interesting = ladder?.learned || steps.some((step) => step.state === "failed" || step.state === "worked")
  return interesting ? steps.map((step) => ({ id: String(step.id), label: step.label, state: String(step.state) })) : []
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
  const base = { show: false, phase: "live", title: "", detail: "", choices: [], carType, candidates: [], progress: [], handshake: [],
    notice: "", exhausted: false, hint: null, steps, adapter: null, apps: null, editing: false }
  if (setup === null) return base
  const hint = BLUETOOTH_FIRST.test(String(runtime?.error || "")) ? BLUETOOTH_HINT : null
  if (connecting) {
    const firstRun = !ladder?.learned && !(ladder?.successes > 0) && (runtime?.connection || "wireless") === "wireless"
    return { ...base, show: true, phase: "connecting", title: runtime?.label || "Connecting to your car",
      detail: runtime?.detail || "Keep your car’s screen on. We’ll move to the next step automatically.",
      progress: connectionStages(runtime), handshake: handshakeSteps(ladder), exhausted: !!ladder?.exhausted,
      notice: ladder?.exhausted ? EXHAUSTED_NOTICE : firstRun ? FIRST_RUN_NOTICE : "", hint }
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
  props: { state: { type: Object, required: true }, busy: { type: Boolean, default: false } },
  emits: ["choose-type", "choose-adapter", "choose-app", "use-car", "classic", "done"],
  template: `
    <section class="gx-card gx-guided" :data-phase="state.phase" aria-label="Guided StarPort setup">
      <ol class="gx-guided__steps" aria-label="Setup progress">
        <li v-for="(step, index) in state.steps" :key="step.id" class="gx-guided__step" :class="'is-' + step.state"
            :aria-current="step.state === 'current' ? 'step' : undefined">
          <span class="gx-guided__node" aria-hidden="true"><i v-if="step.state === 'done'" class="bi bi-check-lg"></i><template v-else>{{ index + 1 }}</template></span>
          <span class="gx-guided__step-label">{{ step.label }}<span class="gx-guided__sr">{{ step.state === 'done' ? ', done' : step.state === 'current' ? ', current step' : '' }}</span></span>
        </li>
      </ol>

      <header class="gx-guided__header">
        <h2 class="gx-guided__title">{{ state.title }}</h2>
        <p class="gx-guided__lede">{{ state.detail }}</p>
      </header>

      <div v-if="state.choices.length" class="gx-guided__section">
        <ul class="gx-guided__group" role="radiogroup" aria-label="How your car connects">
          <li v-for="choice in state.choices" :key="choice.key">
            <button type="button" class="gx-guided__option" role="radio" :aria-checked="choice.selected ? 'true' : 'false'"
                :disabled="busy" @click="$emit('choose-type', choice.key)">
              <span class="gx-guided__tile" :class="'is-' + choice.tone" aria-hidden="true"><i :class="'bi bi-' + choice.icon"></i></span>
              <span class="gx-guided__text"><strong>{{ choice.title }}</strong><small>{{ choice.detail }}</small></span>
              <span class="gx-guided__radio" aria-hidden="true"><i v-if="choice.selected" class="bi bi-check-lg"></i></span>
            </button>
          </li>
        </ul>
      </div>

      <div v-if="state.adapter" class="gx-guided__section">
        <h3 class="gx-guided__subhead">Which adapter?</h3>
        <div class="gx-guided__segmented" role="radiogroup" aria-label="Adapter model">
          <button v-for="option in state.adapter.options" :key="option.key" type="button" role="radio" :aria-checked="option.selected ? 'true' : 'false'"
              :class="{ 'is-selected': option.selected }" :disabled="busy" @click="$emit('choose-adapter', option.key)">{{ option.label }}</button>
        </div>
        <p v-if="state.adapter.needsChoice" class="gx-guided__hint">Choose your adapter to continue.</p>
      </div>

      <div v-if="state.apps" class="gx-guided__section">
        <h3 class="gx-guided__subhead">Which app?</h3>
        <div class="gx-guided__segmented" role="radiogroup" aria-label="Tablet app">
          <button v-for="option in state.apps.options" :key="option.key" type="button" role="radio" :aria-checked="option.selected ? 'true' : 'false'"
              :class="{ 'is-selected': option.selected }" :disabled="busy" @click="$emit('choose-app', option.key)">{{ option.label }}</button>
        </div>
        <p v-if="state.apps.needsChoice" class="gx-guided__hint">Choose your app to continue.</p>
      </div>

      <div v-if="state.candidates.length" class="gx-guided__section">
        <h3 class="gx-guided__subhead">Paired cars</h3>
        <ul class="gx-guided__group" aria-label="Paired cars that offer Android Auto">
          <li v-for="car in state.candidates" :key="car.address" class="gx-guided__car">
            <span class="gx-guided__tile is-blue" aria-hidden="true"><i class="bi bi-car-front-fill"></i></span>
            <span class="gx-guided__text"><strong>{{ car.name }}</strong><small>Paired and offers Android Auto</small></span>
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="$emit('use-car', car.address)">Use</button>
          </li>
        </ul>
      </div>

      <div v-if="state.hint" class="gx-guided__callout" role="status">
        <span>{{ state.hint.text }}</span>
        <a class="gx-btn gx-btn--tonal" :href="state.hint.href">{{ state.hint.label }}</a>
      </div>

      <div v-if="state.progress.length" class="gx-guided__section">
        <ol class="gx-guided__group gx-guided__checklist" aria-label="Connection progress">
          <li v-for="step in state.progress" :key="step.id" :class="'is-' + step.state" :aria-current="step.state === 'active' || step.state === 'retry' ? 'step' : undefined">
            <span class="gx-guided__mark" aria-hidden="true">
              <i v-if="step.state === 'done'" class="bi bi-check-circle-fill"></i>
              <span v-else-if="step.state === 'active'" class="gx-spinner gx-guided__spinner"></span>
              <i v-else-if="step.state === 'retry'" class="bi bi-arrow-repeat"></i>
              <i v-else class="bi bi-circle"></i>
            </span>
            <span class="gx-guided__text"><strong>{{ step.label }}</strong><small v-if="step.state === 'retry'">Trying again</small></span>
          </li>
        </ol>
      </div>

      <details v-if="state.handshake.length" class="gx-guided__disclosure" open>
        <summary>What we are trying</summary>
        <ol class="gx-guided__group gx-guided__checklist" aria-label="Handshake options">
          <li v-for="step in state.handshake" :key="step.id" :class="'is-' + step.state">
            <span class="gx-guided__mark" aria-hidden="true">
              <i v-if="step.state === 'worked'" class="bi bi-check-circle-fill"></i>
              <i v-else-if="step.state === 'failed'" class="bi bi-x-circle"></i>
              <i v-else-if="step.state === 'skipped'" class="bi bi-dash-circle"></i>
              <span v-else-if="step.state === 'current'" class="gx-spinner gx-guided__spinner"></span>
              <i v-else class="bi bi-circle"></i>
            </span>
            <span class="gx-guided__text"><strong>{{ step.label }}</strong></span>
          </li>
        </ol>
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
