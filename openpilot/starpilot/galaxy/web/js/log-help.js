import { WikiHelp } from "./wiki-help.js"
import { GxState } from "./state.js"
import { GxNotice } from "./notice.js"
import { BluetoothNotice, bluetoothConnection } from "./bluetooth-notice.js"
import { StarPortLogsFeed, BUNDLE_URL, fileUrl, outcomeLabel } from "./starport-logs.js"
import { requestJson } from "./startup.js"
import { connectionError } from "./polling.js"

export const STAGE_URL = "./api/logs/stage"
export const NOTE_LIMIT = 200
export const MODEL_LIMIT = 80
const POLL_MS = 1200
const KINDS = ["starport", "connection", "controller"]
const PHONES = ["ios", "android"]

export const LOG_TOPICS = [
  {
    id: "starport",
    title: "StarPort / Car Screen",
    subtitle: "Android Auto, wireless adapter, or black screen",
    icon: "bi-display",
    cta: "Send StarPort logs",
    placeholder: "What happened on the car screen?",
    whatWeNeed: "Try connecting to your car once first so the attempt is recorded, then describe what happened on your car screen.",
    extraHint: "",
    attaches: "Last 20 StarPort sessions, StarPort settings, renderer logs, and masked device snapshot.",
  },
  {
    id: "connection",
    title: "Phone App",
    subtitle: "Pairing or connecting the StarPilot phone app",
    icon: "bi-phone",
    cta: "Send phone app logs",
    placeholder: "What happened in the phone app?",
    whatWeNeed: "Pick whether you're on iPhone or Android and tell us what happened in the StarPilot app.",
    extraHint: "Can't reach Galaxy on your comma at all? You can also send logs from the phone app's Logs & support screen.",
    attaches: "Local network interfaces, Bonjour discovery, and Bluetooth bridge events (passwords & MACs masked).",
  },
  {
    id: "controller",
    title: "Controller",
    subtitle: "USB or Bluetooth gamepad / button inputs",
    icon: "bi-controller",
    cta: "Send controller logs",
    placeholder: "What went wrong with the controller?",
    whatWeNeed: "1. Turn on and wake your USB or Bluetooth controller right now so the comma detects it. 2. Enter the controller model below (hardware names are masked in the log).",
    extraHint: "",
    attaches: "USB & Bluetooth input interface report (bluetooth-log.json, device names masked).",
  },
]

export class LogStageClient {
  constructor({
    publish,
    unauthorized = () => {},
    fetcher = (...args) => fetch(...args),
    schedule = (fn, ms) => setTimeout(fn, ms),
    cancel = (id) => clearTimeout(id),
    onReady = () => {},
  }) {
    Object.assign(this, { publish, unauthorized, fetcher, schedule, cancel, onReady })
    this.active = false
    this.timer = null
    this.generation = 0
    this.dismissedUrl = ""
    this.initiated = false
  }

  stop() {
    this.generation++
    this.active = false
    this.initiated = false
    if (this.timer !== null) this.cancel(this.timer)
    this.timer = null
  }

  start() {
    this.stop()
    this.active = true
    return this.poll()
  }

  reset(currentUrl = "") {
    if (currentUrl) this.dismissedUrl = currentUrl
    this.initiated = false
    this.publish({ state: "idle", message: "", claimUrl: "", kind: "", error: "" })
  }

  async call(options, generation) {
    const response = await this.fetcher(STAGE_URL, { cache: "no-store", credentials: "same-origin", ...options })
    if (!this.active || generation !== this.generation) return null
    if (response.status === 401) {
      this.stop()
      this.unauthorized()
      return null
    }
    const body = await response.json().catch(() => null)
    if (!this.active || generation !== this.generation) return null
    if (!response.ok) throw new Error(body?.error || "Sending logs is unavailable")
    return body
  }

  apply(body) {
    if (!this.active || !body) return
    const dismissed = body.state === "ready" && !!body.claimUrl && body.claimUrl === this.dismissedUrl
    const nextState = dismissed ? "idle" : (body.state || "idle")
    const claimUrl = dismissed ? "" : (body.claimUrl || "")
    const message = dismissed ? "" : (body.message || "")
    const kind = dismissed ? "" : (body.kind || "")
    this.publish({
      state: nextState,
      message,
      claimUrl,
      kind,
      available: body.available !== false,
      fork: body.fork === true,
      noteLimit: body.noteLimit || NOTE_LIMIT,
      modelLimit: body.modelLimit || MODEL_LIMIT,
      error: "",
    })
    if (this.initiated && nextState === "ready" && claimUrl) {
      this.initiated = false
      this.onReady(claimUrl)
    }
    this.queuePoll(nextState === "staging")
  }

  queuePoll(pending = true) {
    if (this.timer !== null) this.cancel(this.timer)
    this.timer = this.active && pending ? this.schedule(() => { this.timer = null; this.poll() }, POLL_MS) : null
  }

  async poll() {
    if (!this.active) return
    const generation = this.generation
    try {
      this.apply(await this.call({}, generation))
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.publish({ error: error?.message || "Sending logs is unavailable" })
        this.queuePoll(false)
      }
    }
  }

  async stage(payload) {
    if (!this.active) return false
    const kind = payload?.kind
    if (!KINDS.includes(kind)) {
      this.publish({ error: "Choose what you are sending logs for." })
      return false
    }
    const note = String(payload?.note ?? "").trim()
    if (!note) {
      this.publish({ error: "Add a short note about what happened." })
      return false
    }
    if (note.length > NOTE_LIMIT) {
      this.publish({ error: `Keep the note to ${NOTE_LIMIT} characters.` })
      return false
    }
    const bodyPayload = { kind, note }
    if (kind === "connection") {
      if (!PHONES.includes(payload?.phone)) {
        this.publish({ error: "Choose iPhone or Android." })
        return false
      }
      bodyPayload.phone = payload.phone
    } else if (kind === "controller") {
      const model = String(payload?.model ?? "").trim()
      if (!model) {
        this.publish({ error: "Enter your controller make and model." })
        return false
      }
      if (model.length > MODEL_LIMIT) {
        this.publish({ error: `Keep the controller model to ${MODEL_LIMIT} characters.` })
        return false
      }
      bodyPayload.model = model
    }

    this.dismissedUrl = ""
    this.initiated = true
    const generation = ++this.generation
    this.queuePoll(false)
    this.publish({ state: "staging", message: "Collecting logs", claimUrl: "", kind, error: "" })
    try {
      const body = await this.call({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bodyPayload),
      }, generation)
      if (body === null) return false
      this.apply(body)
      return true
    } catch (error) {
      if (this.active && generation === this.generation) {
        this.initiated = false
        this.publish({ state: "idle", error: error?.message || "Sending logs is unavailable" })
        this.queuePoll(false)
      }
      return false
    }
  }
}

export async function captureZip(kind, { fetcher = (...args) => fetch(...args), unauthorized = () => {}, request = {} } = {}) {
  const controller = kind === "controller"
  const endpoint = controller ? "./api/controllers/log/capture" : "./api/local-access/capture"
  try {
    const data = await requestJson(endpoint, { fetcher, request })
    if (data?.schemaVersion !== 1 || typeof data.zipBase64 !== "string" || data.zipBase64.length > 131072) {
      throw new Error("Invalid diagnostic capture")
    }
    const bytes = Uint8Array.from(atob(data.zipBase64), character => character.charCodeAt(0))
    if (bytes.length < 22 || bytes[0] !== 80 || bytes[1] !== 75 || bytes[2] !== 3 || bytes[3] !== 4) {
      throw new Error("Invalid diagnostic capture")
    }
    return {
      blob: new Blob([bytes], { type: "application/zip" }),
      filename: controller ? "starpilot-controller-logs.zip" : "starpilot-connection-logs.zip",
    }
  } catch (error) {
    if (error.status === 401 || ["setup_required", "access_unavailable"].includes(error.code)) {
      unauthorized()
      return null
    }
    throw new Error(connectionError(error))
  }
}

export async function downloadCaptureZip(kind, options = {}) {
  const result = await captureZip(kind, options)
  if (!result) return false
  const url = URL.createObjectURL(result.blob)
  const link = document.createElement("a")
  link.href = url
  link.download = result.filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return true
}

export const LogHelp = {
  name: "LogHelp",
  components: { WikiHelp, GxState, GxNotice, BluetoothNotice },
  props: {
    mode: { type: String, required: true },
    unauthorized: { type: Function, required: true },
    topic: { type: String, default: "starport" },
  },
  data() {
    return {
      selected: KINDS.includes(this.topic) ? this.topic : "starport",
      stage: { state: "idle", message: "", claimUrl: "", kind: "", available: true, fork: false, noteLimit: NOTE_LIMIT, modelLimit: MODEL_LIMIT, error: "" },
      starportLogs: { sessions: [], others: [], status: "idle", error: "" },
      sessionsOpen: false,
      bluetooth: bluetoothConnection(),
      notes: { starport: "", connection: "", controller: "" },
      phone: "ios",
      controllerModel: "",
      zipBusy: false,
      zipError: "",
      zipSignal: null,
    }
  },
  computed: {
    topics() { return LOG_TOPICS },
    currentTopic() { return LOG_TOPICS.find(item => item.id === this.selected) || LOG_TOPICS[0] },
    bundleUrl() { return BUNDLE_URL },
    currentNote() { return this.notes[this.selected] || "" },
    canSend() {
      if (!this.currentNote.trim()) return false
      if (this.selected === "controller" && !this.controllerModel.trim()) return false
      return true
    },
    claimReady() {
      return this.stage.state === "ready" && !!this.stage.claimUrl && this.stage.kind === this.selected
    },
    missingHint() {
      if (this.selected === "controller") return "Enter your controller model and a short note above to send."
      return "Add a short note about what happened above to send."
    },
  },
  created() {
    this.stageClient = new LogStageClient({
      publish: update => Object.assign(this.stage, update),
      unauthorized: this.unauthorized,
      onReady: url => {
        try { window.open(url, "_blank", "noopener,noreferrer") } catch {}
      },
    })
    this.starportFeed = new StarPortLogsFeed({
      publish: update => Object.assign(this.starportLogs, update),
      unauthorized: this.unauthorized,
    })
  },
  mounted() {
    if (this.mode === "local") this.stageClient.start()
  },
  beforeUnmount() {
    this.zipSignal?.abort()
    this.stageClient.stop()
    this.starportFeed.stop()
  },
  watch: {
    topic(next) {
      if (KINDS.includes(next)) this.selected = next
    },
    mode(next) {
      if (next === "local") {
        this.stageClient.start()
        if (this.sessionsOpen && this.selected === "starport") this.starportFeed.start()
      } else {
        this.stageClient.stop()
        this.starportFeed.stop()
      }
    },
    selected(next) {
      this.zipError = ""
      this.stage.error = ""
      if (next !== "starport") this.starportFeed.stop()
      else if (this.sessionsOpen && this.mode === "local") this.starportFeed.start()
    },
  },
  methods: {
    fileUrl,
    outcomeLabel,
    reportDate(seconds) { return new Date(seconds * 1000).toLocaleString() },
    reportSize(bytes) { return `${(bytes / 1024).toFixed(1)} KiB` },
    selectTopic(id) {
      this.selected = id
    },
    updateNote(value) {
      this.notes[this.selected] = value
      if (this.stage.error) this.stage.error = ""
    },
    resetStage() {
      this.stageClient.reset(this.stage.claimUrl)
    },
    async submitReport() {
      if (!this.canSend || this.stage.state === "staging" || this.zipBusy) return
      const payload = { kind: this.selected, note: this.currentNote }
      if (this.selected === "connection") payload.phone = this.phone
      if (this.selected === "controller") payload.model = this.controllerModel
      await this.stageClient.stage(payload)
    },
    async saveZip() {
      if (this.zipBusy || this.selected === "starport") return
      this.zipSignal?.abort()
      const controller = new AbortController()
      this.zipSignal = controller
      this.zipBusy = true
      this.zipError = ""
      try {
        await downloadCaptureZip(this.selected, { unauthorized: this.unauthorized, request: { signal: controller.signal } })
      } catch (error) {
        if (error?.name !== "AbortError") this.zipError = error?.message || "Could not prepare diagnostic ZIP."
      } finally {
        if (this.zipSignal === controller) {
          this.zipSignal = null
          this.zipBusy = false
        }
      }
    },
    onSessionsToggle(event) {
      this.sessionsOpen = !!event.target?.open
      if (this.sessionsOpen && this.mode === "local" && this.selected === "starport") this.starportFeed.start()
      else this.starportFeed.stop()
    },
  },
  template: `
    <section class="gx-settings gx-log-help" aria-labelledby="gx-log-help-title">
      <header class="gx-settings__header gx-page-header">
        <div>
          <h2 id="gx-log-help-title">Send logs &amp; get help</h2>
          <p>Choose what you're sending logs for. Your comma prepares the logs and hands off once to Discord.</p>
        </div>
        <WikiHelp path="/logs/help" />
      </header>

      <div class="gx-log-help__tabs" role="tablist" aria-label="Log report type">
        <button v-for="item in topics" :key="item.id" type="button" role="tab"
          class="gx-card gx-log-help__tab"
          :class="{ 'gx-log-help__tab--active': selected === item.id }"
          :aria-selected="selected === item.id"
          @click="selectTopic(item.id)">
          <span class="gx-log-help__tab-icon" aria-hidden="true"><i class="bi" :class="item.icon"></i></span>
          <span class="gx-log-help__tab-text">
            <strong>{{ item.title }}</strong>
            <small>{{ item.subtitle }}</small>
          </span>
        </button>
      </div>

      <section class="gx-card gx-panel gx-stack gx-log-help__card" :aria-label="currentTopic.title">
        <div class="gx-section__header">
          <i class="bi" :class="currentTopic.icon" aria-hidden="true"></i>
          <div>
            <h3 class="gx-section__title">{{ currentTopic.title }}</h3>
            <p class="gx-note">{{ currentTopic.subtitle }}</p>
          </div>
        </div>

        <p v-if="mode !== 'local'" class="gx-note">Connect to your comma to send or download diagnostic logs.</p>
        <template v-else>
          <GxNotice v-if="selected === 'starport' && stage.fork" class="gx-starport-send__fork" tone="warn" title="This device is running a StarPilot fork">
            Custom forks may have changed StarPort or be out of date. Make sure your branch is up to date with StarPilotHQ before sending, or share the logs with your branch's maintainer or in the StarPilot Discord.
          </GxNotice>
          <BluetoothNotice v-if="bluetooth" feature="ZIP downloads">Direct log sending from the comma remains available.</BluetoothNotice>

          <div v-if="claimReady" class="gx-log-help__ready gx-stack" role="status">
            <strong>Almost done: your report is not posted yet</strong>
            <p>Your comma uploaded the logs. Finish on Discord to post them:</p>
            <ol class="gx-log-help__claim-steps">
              <li>Tap <strong>Continue on Discord support</strong>. It may already be open in a new tab.</li>
              <li>If Discord asks, sign in and tap <strong>Authorize</strong>.</li>
              <li>Under <strong>Post to</strong>, pick a new or existing thread, then tap <strong>Post report</strong>.</li>
            </ol>
            <p class="gx-note">Only staff receive the diagnostic ZIP.</p>
            <div class="gx-actions">
              <a class="gx-btn" :href="stage.claimUrl" target="_blank" rel="noopener noreferrer"><i class="bi bi-box-arrow-up-right" aria-hidden="true"></i><span>Continue on Discord support</span></a>
              <button type="button" class="gx-btn gx-btn--tonal" @click="resetStage">Send another report</button>
            </div>
          </div>

          <template v-else>
            <div class="gx-log-help__callout" role="note">
              <i class="bi bi-info-circle" aria-hidden="true"></i>
              <div class="gx-stack">
                <strong>What we need</strong>
                <p>Before collecting fresh logs, park and update your comma to the latest testing build from StarPilotHQ/StarPilot. Try the connection again.</p>
                <p>{{ currentTopic.whatWeNeed }}</p>
                <p v-if="currentTopic.extraHint" class="gx-note">{{ currentTopic.extraHint }}</p>
              </div>
            </div>

            <div v-if="selected === 'connection'" class="gx-log-help__group" role="group" aria-label="Phone type">
              <label class="gx-log-help__label">Phone</label>
              <div class="gx-log-help__pills">
                <button type="button" class="gx-chip" :class="{ 'gx-chip--selected': phone === 'ios' }" :aria-pressed="phone === 'ios'" @click="phone = 'ios'">iPhone</button>
                <button type="button" class="gx-chip" :class="{ 'gx-chip--selected': phone === 'android' }" :aria-pressed="phone === 'android'" @click="phone = 'android'">Android</button>
              </div>
            </div>

            <div v-if="selected === 'controller'" class="gx-log-help__group">
              <label class="gx-log-help__label" for="gx-log-help-model">Controller make &amp; model</label>
              <input id="gx-log-help-model" class="gx-field" type="text" v-model="controllerModel" :maxlength="stage.modelLimit"
                placeholder="e.g. 8BitDo Micro, Xbox Wireless, PS5 DualSense" :disabled="stage.state === 'staging'" @keydown.enter="submitReport">
            </div>

            <div class="gx-log-help__group">
              <label class="gx-log-help__label" for="gx-log-help-note">What happened?</label>
              <div class="gx-starport-send__field">
                <input id="gx-log-help-note" class="gx-field" type="text" :value="currentNote" :maxlength="stage.noteLimit"
                  :placeholder="currentTopic.placeholder" :disabled="stage.state === 'staging'"
                  @input="updateNote($event.target.value)" @keydown.enter="submitReport">
                <span class="gx-starport-send__count" aria-live="polite">{{ currentNote.length }}/{{ stage.noteLimit }}</span>
              </div>
            </div>

            <p class="gx-log-help__attaches">
              <i class="bi bi-check-circle" aria-hidden="true"></i>
              <span><strong>Automatically attaches:</strong> {{ currentTopic.attaches }}</span>
            </p>

            <div class="gx-actions">
              <button v-if="stage.available" type="button" class="gx-btn"
                :disabled="!canSend || stage.state === 'staging' || zipBusy" @click="submitReport">
                <i class="bi bi-send" aria-hidden="true"></i>
                <span>{{ stage.state === 'staging' ? (stage.message || 'Uploading logs') + '…' : currentTopic.cta }}</span>
              </button>
              <template v-if="!bluetooth">
                <a v-if="selected === 'starport'" class="gx-btn gx-btn--tonal" :href="bundleUrl" download>
                  <i class="bi bi-download" aria-hidden="true"></i><span>Download ZIP instead</span>
                </a>
                <button v-else type="button" class="gx-btn gx-btn--tonal" :disabled="zipBusy || stage.state === 'staging'" @click="saveZip">
                  <i class="bi bi-download" aria-hidden="true"></i><span>{{ zipBusy ? 'Preparing ZIP…' : 'Download ZIP instead' }}</span>
                </button>
              </template>
            </div>

            <p v-if="!bluetooth" class="gx-note">Comma offline? Save the ZIP on your phone and send it when you have internet. Include the branch and version you captured.</p>
            <p v-if="stage.available && !canSend && stage.state !== 'staging'" class="gx-note">{{ missingHint }}</p>
            <GxState v-if="stage.state === 'staging'" loading>{{ stage.message || 'Uploading logs' }}…</GxState>
            <GxNotice v-if="stage.state === 'failed' || stage.error || zipError" tone="danger">{{ stage.error || stage.message || zipError }}</GxNotice>
          </template>

          <details v-if="selected === 'starport'" class="gx-log-help__sessions" @toggle="onSessionsToggle">
            <summary>Individual session files</summary>
            <div class="gx-stack gx-log-help__sessions-body">
              <div class="gx-actions">
                <button type="button" class="gx-btn gx-btn--tonal" @click="starportFeed.load()"><i class="bi bi-arrow-clockwise" aria-hidden="true"></i><span>Refresh</span></button>
              </div>
              <GxState v-if="starportLogs.status === 'loading'" loading>Loading StarPort logs…</GxState>
              <GxNotice v-else-if="starportLogs.status === 'unavailable'" tone="danger">{{ starportLogs.error || 'StarPort logs are unavailable.' }}</GxNotice>
              <GxState v-else-if="starportLogs.status === 'ready' && !starportLogs.sessions.length">No StarPort sessions have been logged yet.</GxState>
              <div v-if="starportLogs.status === 'ready'" class="gx-starport-log-list">
                <article v-for="log in starportLogs.sessions" :key="log.name" class="gx-card gx-starport-log">
                  <div class="gx-starport-log__body">
                    <strong>{{ outcomeLabel(log.outcome) }}</strong>
                    <span>{{ log.car || 'Car not identified' }}{{ log.transport ? ' · ' + log.transport : '' }}{{ log.trigger ? ' · ' + log.trigger : '' }}</span>
                    <span>{{ reportDate(log.modifiedAt) }} · {{ reportSize(log.size) }}</span>
                  </div>
                  <a v-if="!bluetooth" class="gx-icon-btn gx-starport-log__download" :href="fileUrl(log.name)" :download="log.name" :aria-label="'Download ' + log.name" :title="'Download ' + log.name"><i class="bi bi-download" aria-hidden="true"></i></a>
                </article>
              </div>
              <template v-if="starportLogs.status === 'ready' && starportLogs.others.length">
                <h4 class="gx-starport-logs__section-title">Renderer logs</h4>
                <div class="gx-starport-log-list">
                  <article v-for="log in starportLogs.others" :key="log.name" class="gx-card gx-starport-log">
                    <div class="gx-starport-log__body"><strong>{{ log.name }}</strong><span>{{ reportDate(log.modifiedAt) }} · {{ reportSize(log.size) }}</span></div>
                    <a v-if="!bluetooth" class="gx-icon-btn gx-starport-log__download" :href="fileUrl(log.name)" :download="log.name" :aria-label="'Download ' + log.name" :title="'Download ' + log.name"><i class="bi bi-download" aria-hidden="true"></i></a>
                  </article>
                </div>
              </template>
            </div>
          </details>
        </template>
      </section>

      <section class="gx-card gx-panel gx-log-help__discord" aria-label="Discord community support">
        <div class="gx-log-help__discord-copy">
          <i class="bi bi-discord" aria-hidden="true"></i>
          <div>
            <h3>Driving behavior or general questions?</h3>
            <p class="gx-note">Log uploads here are only for StarPort, the phone app, and controllers. For driving behavior, alerts, or anything else, ask in the StarPilot Discord (if it's about a drive, share your car model and route link).</p>
          </div>
        </div>
        <a class="gx-btn gx-btn--tonal" href="https://firestar.link/discord" target="_blank" rel="noopener noreferrer">Join Discord</a>
      </section>
    </section>
  `,
}
