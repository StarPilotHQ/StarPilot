// The guided StarPort setup: one question (how does the car connect), only the steps for that answer, and a live checklist of
// what is being tried while connecting. It reads the daemon status the page already has and drives the page's existing actions,
// so it adds no new state of its own. Turned off, the classic screens come back ("Use classic setup").

export const FIRST_RUN_NOTICE = "The first connection can take a few minutes, and sometimes more than one drive, while we find what your car accepts. Keep the car on and its screen awake. Later connections are much faster because we remember what worked."
export const EXHAUSTED_NOTICE = "We tried every option we have this drive. We keep what works best and will try again the next time the car starts."

const CHOICES = [
  { key: "builtin", detail: "The car connects to Android Auto wirelessly by itself." },
  { key: "adapter", detail: "A USB adapter such as AAWireless or Motorola MA1 plugged into the car." },
  { key: "usb", detail: "A USB data cable from the car to the comma." },
  { key: "app", detail: "An app on an iPad or Android tablet shows the dashboard." },
]

const INSTRUCTIONS = {
  builtin: "Open your car’s phone pairing screen, then tap Find car in the pairing step below.",
  adapter: "Put the adapter in pairing mode, choose its model below, then tap Find adapter.",
  usb: "Connect the car’s Android Auto USB data port to the comma with a data cable, then tap Connect.",
  app: "Choose your app below, open it on your tablet on the same Wi-Fi, then tap Connect.",
}

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

export function guidedState({ setup = null, runtime = null, selected = null, receivers = [], wired = false, app = "", carType = "builtin",
  setupDone = false, pairingActive = false, labels = {} } = {}) {
  const ladder = runtime?.setup_ladder || null
  const state = runtime?.state || "idle"
  const connecting = !!runtime?.running && CONNECTING.has(state)
  const installed = !!setup?.identity?.installed
  const base = { show: false, phase: "live", title: "", detail: "", choices: [], carType, candidates: [], progress: [], handshake: [],
    notice: "", exhausted: false }
  if (setup === null) return base
  if (connecting) {
    const firstRun = !ladder?.learned && !(ladder?.successes > 0) && (runtime?.connection || "wireless") === "wireless"
    return { ...base, show: true, phase: "connecting", title: runtime?.label || "Connecting to your car",
      detail: runtime?.detail || "Keep your car’s screen on. We’ll move to the next step automatically.",
      progress: connectionStages(runtime), handshake: handshakeSteps(ladder), exhausted: !!ladder?.exhausted,
      notice: ladder?.exhausted ? EXHAUSTED_NOTICE : firstRun ? FIRST_RUN_NOTICE : "" }
  }
  if (setupDone) return base
  if (!installed) {
    return { ...base, show: true, phase: "package", title: "Start by adding your Android Auto package",
      detail: "Your car only talks to devices that present Google’s Android Auto certificate. Follow step 1 below, then come back here." }
  }
  const choices = CHOICES.map((choice) => ({ ...choice, label: labels[choice.key] || choice.key, selected: choice.key === carType }))
  const known = new Set(selected ? [selected.address] : [])
  const candidates = []
  for (const car of [...(Array.isArray(runtime?.car_candidates) ? runtime.car_candidates : []), ...(Array.isArray(receivers) ? receivers : [])]) {
    if (car && typeof car.address === "string" && typeof car.name === "string" && !known.has(car.address)) {
      known.add(car.address)
      candidates.push({ address: car.address, name: car.name })
    }
  }
  const pairs = carType === "builtin" || carType === "adapter"
  return { ...base, show: true, phase: "setup", title: "How does your car connect to Android Auto?",
    detail: INSTRUCTIONS[carType] || INSTRUCTIONS.builtin, choices, candidates: pairs && !pairingActive ? candidates : [] }
}

export const StarPortGuidedSetup = {
  props: { state: { type: Object, required: true }, busy: { type: Boolean, default: false } },
  emits: ["choose-type", "use-car", "classic"],
  template: `
    <section class="gx-card gx-starport-guided" aria-label="Guided StarPort setup">
      <div class="gx-starport-step__head">
        <div><h3>{{ state.title }}</h3><p>{{ state.detail }}</p></div>
      </div>
      <div class="gx-starport-step__body">
        <div v-if="state.choices.length" class="gx-starport-guided__choices" role="radiogroup" aria-label="How your car connects">
          <button v-for="choice in state.choices" :key="choice.key" type="button" class="gx-btn gx-starport-guided__choice"
              :class="choice.selected ? 'gx-btn--primary' : 'gx-btn--tonal'" role="radio" :aria-checked="choice.selected ? 'true' : 'false'"
              :disabled="busy" @click="$emit('choose-type', choice.key)">
            <strong>{{ choice.label }}</strong><small>{{ choice.detail }}</small>
          </button>
        </div>
        <ul v-if="state.candidates.length" class="gx-starport-list" aria-label="Paired cars that offer Android Auto">
          <li v-for="car in state.candidates" :key="car.address" class="gx-starport-row">
            <i class="bi bi-car-front" aria-hidden="true"></i>
            <span class="gx-starport-row__text"><strong>{{ car.name }}</strong><small>Paired and offers Android Auto</small></span>
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="$emit('use-car', car.address)">Use</button>
          </li>
        </ul>
        <ol v-if="state.progress.length" class="gx-starport-list" aria-label="Connection progress">
          <li v-for="step in state.progress" :key="step.id" class="gx-starport-row" :class="'gx-starport-row--' + step.state"
              :aria-current="step.state === 'active' || step.state === 'retry' ? 'step' : undefined">
            <i v-if="step.state === 'done'" class="bi bi-check-circle-fill gx-starport-ok" aria-hidden="true"></i>
            <span v-else-if="step.state === 'active'" class="gx-spinner gx-starport-orbit gx-starport-orbit--small" aria-hidden="true"></span>
            <i v-else-if="step.state === 'retry'" class="bi bi-arrow-repeat" aria-hidden="true"></i>
            <i v-else class="bi bi-circle" aria-hidden="true"></i>
            <span class="gx-starport-row__text"><strong>{{ step.label }}</strong><small v-if="step.state === 'retry'">Trying again</small></span>
          </li>
        </ol>
        <details v-if="state.handshake.length" class="gx-starport-wiki" open>
          <summary>What we are trying</summary>
          <ol class="gx-starport-list" aria-label="Handshake options">
            <li v-for="step in state.handshake" :key="step.id" class="gx-starport-row" :class="'gx-starport-row--' + step.state">
              <i v-if="step.state === 'worked'" class="bi bi-check-circle-fill gx-starport-ok" aria-hidden="true"></i>
              <i v-else-if="step.state === 'failed'" class="bi bi-x-circle" aria-hidden="true"></i>
              <i v-else-if="step.state === 'skipped'" class="bi bi-dash-circle" aria-hidden="true"></i>
              <span v-else-if="step.state === 'current'" class="gx-spinner gx-starport-orbit gx-starport-orbit--small" aria-hidden="true"></span>
              <i v-else class="bi bi-circle" aria-hidden="true"></i>
              <span class="gx-starport-row__text"><strong>{{ step.label }}</strong></span>
            </li>
          </ol>
        </details>
        <p v-if="state.notice" class="gx-note" role="status">{{ state.notice }}</p>
        <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="$emit('classic')">Use classic setup</button>
      </div>
    </section>`,
}
