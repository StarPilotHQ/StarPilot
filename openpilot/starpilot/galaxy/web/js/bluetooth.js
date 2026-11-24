import { ConnectionLogs, ReportingGuide } from "./local-access.js"
import { WikiHelp } from './wiki-help.js'
import { UnidenPanel } from './uniden.js'
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { reportSave } from "./save-feedback.js"
import { connectionError } from "./polling.js"
import { BluetoothTabs } from "./bluetooth-tabs.js"
import { BluetoothDeviceList } from "./bluetooth-devices.js"
import { GxDialog } from "./dialog.js"

const ADDRESS = /^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$/
const OPERATIONS = new Set(["power", "scan", "stop_scan", "connect", "disconnect", "forget", "pair", "accept_pair", "pairing_response", "cancel_pair"])
const PAIR_STATES = new Set(["pairing", "paired", "failed", "canceled"])
const BLUETOOTH_ERRORS = {
  radio_unavailable: "Bluetooth radio support is unavailable on this device.",
  adapter_unavailable: "No Bluetooth adapter was detected. Turn Bluetooth on, then refresh.",
  service_unavailable: "The device's Bluetooth service could not be reached. Refresh to reconnect.",
  radio_preference_unavailable: "The Bluetooth power setting could not be read. Try again after restarting the device.",
  busy: "Bluetooth is being used by another operation. Finish pairing or stop searching, then try again.",
  park_required: "Park before changing Bluetooth connections.",
  projection_active: "StarPort is using this device. Stop StarPort, then try again.",
  session_expired: "The pairing session expired. Refresh and start pairing again.",
  changed: "The Bluetooth device or pairing request changed. Refresh and try again.",
  unreachable: "The device didn't respond. Make sure it's on, nearby and not connected to another phone or app (like the Uniden app), then try again.",
}
export const bluetoothError = (code) => BLUETOOTH_ERRORS[code] || "Bluetooth could not complete the request. Refresh to try again."
const PROMPT_KINDS = new Set(["pin", "passkey", "confirmation", "authorization", "display_pin", "display_passkey"])

export function validPairInput(prompt, value) {
  if (prompt?.kind === "pin") return /^[\x20-\x7E]{1,16}$/.test(value)
  if (prompt?.kind === "passkey") return /^[0-9]{1,6}$/.test(value)
  return ["confirmation", "authorization"].includes(prompt?.kind)
}

function validPairing(value) {
  if (value === null) return true
  const prompt = value?.prompt
  // A window opened for a phone has no address until the first phone asks to pair.
  const incoming = value?.incoming === true
  return (incoming ? value.address === "" || ADDRESS.test(value.address) : ADDRESS.test(value?.address)) &&
    (!incoming || typeof value.name === "string" && value.name.length <= 80) && PAIR_STATES.has(value?.state) &&
    (prompt === null || (typeof prompt === "object" && /^[0-9a-f]{32}$/.test(prompt.id) &&
      PROMPT_KINDS.has(prompt.kind) && typeof prompt.value === "string" && prompt.value.length <= 16 &&
      typeof prompt.displayOnly === "boolean"))
}

function validClaim(value) {
  return value === undefined || typeof value?.open === "boolean" && Number.isInteger(value.left) && value.left >= 0 && value.left <= 600 &&
    typeof value.claimed === "string" && value.claimed.length <= 80 && typeof value.paired === "boolean"
}

export function validBluetoothStatus(value) {
  return value?.version === 1 && typeof value.available === "boolean" && typeof value.parked === "boolean" &&
    typeof value.powered === "boolean" && typeof value.discovering === "boolean" &&
    (value.errorCode === null || ["radio_unavailable", "adapter_unavailable", "service_unavailable", "radio_preference_unavailable"].includes(value.errorCode)) &&
    ["adapterAddress", "adapterName"].every((key) => value[key] === undefined || typeof value[key] === "string" && value[key].length <= 80) &&
    validClaim(value.claim) && validPairing(value.pairing ?? null) && Array.isArray(value.devices) && value.devices.length <= 64 && value.devices.every((device) =>
      ADDRESS.test(device?.address) && typeof device.name === "string" && device.name.length <= 80 &&
      [device.paired, device.connected, device.trusted].every((flag) => typeof flag === "boolean"))
}

export class BluetoothFeed {
  constructor({ publish, unauthorized = () => {}, fetcher = (...args) => fetch(...args),
                later = (fn, ms) => setTimeout(fn, ms), cancelTimer = (id) => clearTimeout(id) }) {
    Object.assign(this, { publish, unauthorized, fetcher, later, cancelTimer })
    this.active = false
    this.generation = 0
    this.request = null
    this.timer = null
    this.pollTimer = null
    this.busy = false
    this.status = null
    this.deviceList = new BluetoothDeviceList()
    this.error = ""
  }

  emit() { this.publish({ status: this.status, busy: this.busy, error: this.error }) }
  stop(cancelPair = false) {
    // A phone window has to survive the page being hidden: the phone is opened to tap the comma.
    if (cancelPair && this.active && this.status?.pairing?.state === "pairing" && !this.status.pairing.incoming) {
      this.fetcher("./api/bluetooth/action", { method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation: "cancel_pair" }) }).catch(() => {})
    }
    this.active = false
    this.generation++
    this.request?.abort()
    if (this.timer !== null) this.cancelTimer(this.timer)
    if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
    this.request = this.timer = this.pollTimer = null
    this.busy = false
    this.status = null
    this.deviceList.reset()
    this.error = ""
    this.emit()
  }
  start() { this.stop(); this.active = true; return this.refresh() }

  retire(generation, request) {
    if (!this.active || this.generation !== generation || this.request !== request) return false
    if (this.timer !== null) this.cancelTimer(this.timer)
    this.timer = null
    this.request = null
    this.busy = false
    if (this.pollTimer === null) this.pollTimer = this.later(() => this.refresh(), 2000)
    this.emit()
    return true
  }

  async run(url, options, timeout, background = false) {
    if (!this.active || this.busy || background && this.request !== null) return null
    if (!background) {
      // The user action supersedes any in-flight poll, including its JSON body.
      this.generation++
      this.request?.abort()
      if (this.timer !== null) this.cancelTimer(this.timer)
      if (this.pollTimer !== null) this.cancelTimer(this.pollTimer)
      this.request = this.timer = this.pollTimer = null
    }
    const generation = this.generation
    const request = new AbortController()
    this.request = request
    if (!background) { this.busy = true; this.emit() }
    this.timer = this.later(() => {
      if (!this.retire(generation, request)) return
      request.abort()
      this.generation++
      this.error = "Bluetooth took too long to respond. Refresh to check its current state."
      this.emit()
    }, timeout)
    try {
      const response = await this.fetcher(url, { ...options, signal: request.signal, credentials: "same-origin", cache: "no-store" })
      if (!this.active || this.generation !== generation || this.request !== request || request.signal.aborted) return null
      if (response.status === 401) { this.stop(); this.unauthorized(); return null }
      const data = await response.json().catch(() => { throw new Error("Galaxy could not reach Bluetooth. Refresh to reconnect.") })
      if (!this.active || this.generation !== generation || this.request !== request || request.signal.aborted) return null
      if (response.status === 503 && ["setup_required", "access_unavailable"].includes(data?.code)) {
        this.stop(); this.unauthorized(); return null
      }
      if (!response.ok) throw new Error(bluetoothError(data?.code))
      if (!validBluetoothStatus(data)) throw new Error("Galaxy received an invalid Bluetooth response. Refresh to reconnect.")
      this.status = { ...data, devices: this.deviceList.update(data.devices) }
      this.error = ""
      return data
    } catch (error) {
      if (this.active && this.generation === generation && this.request === request) {
        this.error = connectionError(error)
      }
      return null
    } finally {
      this.retire(generation, request)
    }
  }

  async refresh() {
    if (!this.active || this.busy || this.request !== null) return
    if (this.pollTimer !== null) { this.cancelTimer(this.pollTimer); this.pollTimer = null }
    await this.run("./api/bluetooth/status", {}, 12000, true)
  }

  async action(operation, fields = {}) {
    if (!OPERATIONS.has(operation) || !this.active || this.busy || (operation === 'power' && !this.status?.parked)) return false
    const payload = { operation, ...fields }
    const result = await this.run("./api/bluetooth/action", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    }, operation === "power" ? 35000 : ["connect", "disconnect"].includes(operation) ? 30000 : 15000)
    if (result) await this.refresh()
    return !!result
  }
}

export const BluetoothPage = {
  components: { WikiHelp, UnidenPanel, GxState, GxNotice, BluetoothTabs, GxDialog, ConnectionLogs, ReportingGuide },
  props: { go: { type: Function, default: path => { location.hash = path } }, unidenSettings: Boolean, mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ status: null, busy: false, error: "", pairValue: "", now: Date.now(), claimAt: Date.now(), forgetting: null, adding: false, dismissedPairing: null }),
  mounted() {
    this.feed = new BluetoothFeed({ publish: (value) => Object.assign(this, value), unauthorized: this.unauthorized })
    this.visibility = () => this.mode !== "local" || document.hidden ? this.feed.stop(true) : this.feed.start()
    document.addEventListener("visibilitychange", this.visibility)
    this.clock = setInterval(() => { this.now = Date.now() }, 1000)
    if (this.mode === "local" && !document.hidden) this.feed.start()
  },
  beforeUnmount() { clearInterval(this.clock); document.removeEventListener("visibilitychange", this.visibility); this.feed?.stop(true) },
  watch: {
    mode(value) { if (value === "local" && !document.hidden) this.feed?.start(); else this.feed?.stop(true) },
    'status.pairing.prompt.id'() { this.pairValue = "" },
    'status.claim'() { this.claimAt = Date.now(); this.now = this.claimAt },
  },
  computed: {
    pairingKey() { return this.status?.pairing ? `${this.status.pairing.address}:${this.status.pairing.state}` : null },
    addingDevice() { return this.adding || !!this.status?.discovering },
    saved() { return this.status?.devices?.filter((device) => device.paired || device.trusted) || [] },
    // A phone running the StarPilot app is connected to comma's Bluetooth bridge but not bonded: list it apart from real pairings.
    appLinks() { return this.status?.devices?.filter((device) => device.connected && !device.paired && !device.trusted) || [] },
    nearby() { return this.status?.devices?.filter((device) => !device.paired && !device.trusted && !device.connected) || [] },
    claimLeft() { return Math.max(0, (this.status?.claim?.left ?? 0) - Math.floor((this.now - this.claimAt) / 1000)) },
    canAllowPair() { return validPairInput(this.status?.pairing?.prompt, this.pairValue) },
  },
  methods: {
    reportSave,
    bluetoothError,
    // Unsaved Uniden settings hold navigation the way the theme maker does.
    requestLeave(proceed, presentation) {
      if (this.$refs.uniden) this.$refs.uniden.requestLeave(proceed, presentation)
      else proceed()
    },
    async openAdd() {
      this.adding = true
      await this.$nextTick()
      this.$refs.addDevice?.scrollIntoView({ block: 'nearest' })
    },
    async closeAdd() {
      if (this.status?.discovering && !await this.action('stop_scan')) return
      this.adding = false
    },
    refresh() { return this.feed.refresh() },
    async action(operation, fields) {
      if (['pair', 'accept_pair'].includes(operation)) this.dismissedPairing = null
      const accepted = await this.feed.action(operation, fields)
      if (accepted && ['pair', 'accept_pair'].includes(operation)) {
        await this.$nextTick()
          ; (this.$refs.phonePairing || this.$refs.devicePairing)?.scrollIntoView({ block: 'nearest' })
      }
      return accepted
    },
    respond(accepted) {
      const prompt = this.status?.pairing?.prompt
      if (!prompt || prompt.displayOnly || accepted && !validPairInput(prompt, this.pairValue)) return
      const value = accepted && ["pin", "passkey"].includes(prompt.kind) ? this.pairValue : ""
      return this.action("pairing_response", { promptId: prompt.id, accepted, value })
    },
    async confirmForget() {
      if (!["confirm", "failed"].includes(this.forgetting?.state) || this.busy) return
      this.forgetting.state = "working"
      const forgotten = await this.action("forget", { address: this.forgetting.address })
      if (this.forgetting) this.forgetting.state = forgotten ? "done" : "failed"
    },
    closeForget() { if (this.forgetting?.state !== "working") this.forgetting = null },
  },
  template: `
    <section class="gx-driving gx-bluetooth" :aria-label="unidenSettings ? 'Uniden settings' : 'Bluetooth'" :inert="busy" :aria-busy="busy || undefined">
      <header class="gx-settings__header gx-page-header"><div><h2>{{ unidenSettings ? 'Uniden' : 'Bluetooth' }}</h2>
        <p>{{ unidenSettings ? 'Detector preferences and custom frequency rules.' : 'Pair and manage devices here. Use the tabs for integration settings.' }}</p></div><WikiHelp /></header>
      <BluetoothTabs :active="unidenSettings ? 'uniden' : 'devices'" :go="go" />
      <GxState v-if="mode !== 'local'">Bluetooth requires the device. No radio was checked in preview.</GxState>
      <template v-else>
        <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
        <GxState v-if="!status && !error" loading>Checking Bluetooth…</GxState>
        <template v-else-if="status && !unidenSettings">
          <section class="gx-card"><div class="gx-section__header"><i class="bi bi-bluetooth" aria-hidden="true"></i><span class="gx-section__title">Bluetooth radio</span></div>
            <div class="gx-panel gx-stack gx-bluetooth__radio">
              <GxNotice v-if="status.errorCode" tone="danger">{{ bluetoothError(status.errorCode) }}</GxNotice>
              <GxNotice v-else-if="!status.parked" tone="warn">Park to pair devices or change Bluetooth connections.</GxNotice>
              <div class="gx-row gx-row--borderless"><div class="gx-row__info"><span class="gx-row__label">{{ status.powered ? 'Bluetooth on' : 'Bluetooth off' }}</span><span class="gx-row__desc">Connected devices · {{ saved.filter(device => device.connected).length }}</span></div>
                <label class="gx-switch"><input type="checkbox" role="switch" aria-label="Bluetooth power" :checked="status.powered" :disabled="!status.parked || !status.available" @change="$event.target.checked=status.powered; reportSave(() => action('power', {enabled: !status.powered}))"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
              <button v-if="!addingDevice" class="gx-btn" :disabled="!status.powered || !status.available || !status.parked" @click="openAdd">Add a device</button>
            </div></section>
          <div ref="phonePairing" v-if="status.claim?.open || addingDevice && status.claim?.claimed" class="gx-card gx-driving__intro gx-bt-card" role="status"><h3>Pair phone</h3>
            <p v-if="status.claim.open && !status.claim.claimed">Open the StarPilot app and tap <strong>Pair with comma</strong>. No app? Tap <strong>{{ status.adapterName || 'this comma' }}</strong> in your phone’s Bluetooth settings.</p>
            <p v-else-if="status.claim.claimed && !status.claim.paired">App paired ({{ status.claim.claimed }}). iPhone: tap <strong>{{ status.adapterName || 'this comma' }}</strong> in Settings → Bluetooth to add music. Android pairs music on its own.</p>
            <p v-else-if="status.claim.claimed">All set. {{ status.claim.claimed }} is paired for the app and music.</p>
            <p>App: {{ status.claim.claimed || (status.claim.open ? 'waiting' : 'not paired') }} · Music: {{ status.claim.paired || status.pairing?.state === 'paired' ? 'paired' : (status.claim.open ? 'waiting' : 'not paired') }}</p>
            <p v-if="status.claim.open && claimLeft > 0"><strong>{{ Math.floor(claimLeft / 60) }}:{{ String(claimLeft % 60).padStart(2, '0') }}</strong> left. Accepts one phone.</p></div>
          <section ref="devicePairing" v-if="status.pairing && (status.pairing.state === 'pairing' || dismissedPairing !== pairingKey)" class="gx-card" role="status"><div class="gx-section__header"><i class="bi bi-link-45deg" aria-hidden="true"></i><span class="gx-section__title">Pairing</span><span class="gx-chip">{{ status.pairing.name || status.pairing.address }}</span></div>
            <div class="gx-panel gx-stack"><p v-if="status.pairing.incoming && status.pairing.state === 'pairing' && !status.pairing.address">Open Bluetooth settings on your phone and tap <strong>{{ status.adapterName || 'this comma' }}</strong>.</p>
            <p v-else-if="status.pairing.state === 'pairing'">Waiting for the device and any confirmation below.</p>
            <GxNotice v-else :tone="status.pairing.state === 'paired' ? 'success' : status.pairing.state === 'failed' ? 'danger' : 'info'">{{ status.pairing.state === 'paired' ? 'Device paired.' : status.pairing.state === 'canceled' ? 'Pairing canceled.' : 'Pairing did not complete.' }}</GxNotice>
            <div v-if="status.pairing.prompt" class="gx-stack"><p>{{ status.pairing.incoming && status.pairing.prompt.displayOnly && status.pairing.prompt.kind === 'confirmation' ? 'Accepted automatically. Tap Pair on your phone if it shows this code:' :
              status.pairing.prompt.kind === 'confirmation' ? 'Confirm this code matches the device:' :
              status.pairing.prompt.kind === 'pin' ? 'Enter the PIN shown by the device.' :
              status.pairing.prompt.kind === 'passkey' ? 'Enter the device passkey.' :
              status.pairing.prompt.kind === 'authorization' ? 'Allow this device to pair?' : 'Use this code on the device:' }}</p>
              <strong v-if="status.pairing.prompt.value">{{ status.pairing.prompt.value }}</strong>
              <input v-if="['pin', 'passkey'].includes(status.pairing.prompt.kind)" v-model="pairValue" class="gx-field" type="text" maxlength="16" autocomplete="off" placeholder="PIN or passkey" />
              <div v-if="!status.pairing.prompt.displayOnly" class="gx-driving__actions gx-actions"><button class="gx-btn gx-btn--tonal" @click="respond(false)">Reject</button>
                <button class="gx-btn" :disabled="!canAllowPair" @click="respond(true)">{{ status.pairing.prompt.kind === 'pin' ? 'Submit PIN' : status.pairing.prompt.kind === 'passkey' ? 'Submit Passkey' : 'Allow' }}</button></div></div>
            <button v-if="status.pairing.state === 'pairing'" class="gx-btn gx-btn--tonal" @click="action('cancel_pair')">Cancel Pairing</button><button v-else class="gx-btn gx-btn--tonal" @click="dismissedPairing = pairingKey">Dismiss</button></div></section>
          <section class="gx-card"><div class="gx-section__header"><i class="bi bi-hdd-stack" aria-hidden="true"></i><span class="gx-section__title">Saved devices</span></div>
            <p v-if="!saved.length" class="gx-note gx-inset">No saved devices yet. Choose Add a device to pair one.</p>
            <div v-for="device in saved" :key="device.address" class="gx-row gx-bluetooth__device">
              <div class="gx-row__info"><span class="gx-row__label">{{ device.name || device.address }}</span>
                <span class="gx-row__desc">{{ device.connected ? 'Connected' : 'Disconnected' }}</span></div>
              <div class="gx-actions">
                <button class="gx-btn gx-btn--tonal" :disabled="!status.powered || !status.parked" @click="action(device.connected ? 'disconnect' : 'connect', {address:device.address})">{{ device.connected ? 'Disconnect' : 'Connect' }}</button>
                <button class="gx-btn gx-btn--tonal" :disabled="!status.parked" @click="forgetting = { address: device.address, name: device.name, state: 'confirm' }">Forget</button>
              </div>
            </div></section>
          <section v-if="appLinks.length" class="gx-card"><div class="gx-section__header"><i class="bi bi-phone" aria-hidden="true"></i><span class="gx-section__title">App Pairings</span></div>
            <p class="gx-note gx-inset">Phones connected through the StarPilot app. These are not Bluetooth pairings.</p>
            <div v-for="device in appLinks" :key="device.address" class="gx-row"><div class="gx-row__info"><span class="gx-row__label">{{ device.name }}</span>
                <span class="gx-row__desc">Connected through the app</span></div>
              <div class="gx-actions"><button class="gx-btn gx-btn--tonal" @click="forgetting = { address: device.address, name: device.name, state: 'confirm' }">Forget</button></div></div></section>
          <section ref="addDevice" v-if="addingDevice" class="gx-card" aria-label="Add a device">
            <div class="gx-section__header"><i class="bi bi-plus-circle" aria-hidden="true"></i><span class="gx-section__title">Add a device</span>
              <button class="gx-btn gx-btn--tonal" @click="closeAdd">Done</button></div>
            <div class="gx-panel gx-stack">
              <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Phone</span><span class="gx-row__desc">Connect the StarPilot app and phone audio.</span></div>
                <button class="gx-btn gx-btn--tonal" :disabled="!status.powered || !status.parked || status.discovering || status.pairing?.state === 'pairing'" @click="action('accept_pair')">Pair phone</button></div>
              <div class="gx-row"><div class="gx-row__info"><span class="gx-row__label">Detector, controller or audio device</span><span class="gx-row__desc">Put the device in pairing mode, then search.</span></div>
                <button class="gx-btn gx-btn--tonal" :disabled="!status.powered || !status.parked || status.pairing?.state === 'pairing'" @click="action(status.discovering ? 'stop_scan' : 'scan')">{{ status.discovering ? 'Stop search' : 'Search' }}</button></div>
              <details><summary class="gx-link">Pairing a Uniden detector?</summary><p class="gx-note">Enable Bluetooth and open BT Pairing on the detector. Close other apps connected to it. After pairing, open the Uniden tab to select it.</p></details>
              <p class="gx-note">Android Auto pairing is found on the <a class="gx-link" href="#/starport">StarPort page</a>.</p>
            </div>
            <div v-if="status.discovering || nearby.length" class="gx-section__header"><span class="gx-section__title">Nearby devices</span></div>
            <p v-if="status.discovering && !nearby.length" class="gx-note gx-inset" role="status">Searching for devices…</p>
            <div v-for="device in nearby" :key="device.address" class="gx-row"><div class="gx-row__info"><span class="gx-row__label">{{ device.name || device.address }}</span><span class="gx-row__desc">{{ device.address }}</span></div>
              <button class="gx-btn" :disabled="!status.powered || !status.parked || status.pairing?.state === 'pairing'" @click="action('pair', {address:device.address})">Pair</button></div>
          </section>
        </template>
      </template>
      <UnidenPanel ref="uniden" v-if="unidenSettings && mode === 'local' && status" :settings="unidenSettings" :devices="status.devices" :parked="status.parked" :unauthorized="unauthorized" />
      <GxDialog v-if="forgetting" labelledby="gx-bt-forget-title" describedby="gx-bt-forget-body" alert @close="closeForget">
        <template v-if="forgetting.state === 'done'">
          <h3 id="gx-bt-forget-title">{{ forgetting.name }} forgotten</h3>
          <p id="gx-bt-forget-body">Also remove the comma from the device's Bluetooth list, or it may keep trying to reconnect or fail to pair again later.</p>
          <div class="gx-settings__controls gx-actions"><button class="gx-btn" @click="closeForget">Done</button></div>
        </template>
        <template v-else>
          <h3 id="gx-bt-forget-title">Forget {{ forgetting.name }}?</h3>
          <p id="gx-bt-forget-body">The comma will remove it and stop connecting to it. To use it again, you'll need to pair it again.</p>
          <GxNotice v-if="forgetting.state === 'failed'" tone="danger">{{ error || 'The device could not be forgotten. Try again.' }}</GxNotice>
          <div class="gx-settings__controls gx-actions"><button class="gx-btn gx-btn--tonal" :disabled="forgetting.state === 'working'" @click="closeForget">Cancel</button>
            <button class="gx-btn gx-btn--danger" :disabled="forgetting.state === 'working'" @click="confirmForget">{{ forgetting.state === 'working' ? 'Forgetting…' : forgetting.state === 'failed' ? 'Try again' : 'Forget' }}</button></div>
        </template>
      </GxDialog>
      <ConnectionLogs v-if="mode === 'local' && !unidenSettings" panel show-controller :on-unauthorized="unauthorized" @help="go('/logs/help')" />
    <ReportingGuide v-if="mode === 'local' && !unidenSettings" />
  </section>`,
}
