import { GxState } from "./state.js"
import { PollTimer, connectionError } from "./polling.js"
import { requestJson } from "./startup.js"
import { GalaxySelect } from "./galaxy-select.js"
import { GxNotice } from "./notice.js"

const PROVIDER_IDS = ["comma", "konik", "custom"]

const validProviderEntry = p => {
  if (!p || !PROVIDER_IDS.includes(p.id) || typeof p.label !== "string" || typeof p.url !== "string") return false
  if (p.id === "comma") return p.url === "https://connect.comma.ai"
  if (p.id === "konik") return p.url === "https://stable.konik.ai"
  return p.url === "" || /^https:\/\/[^/?#\s]+/.test(p.url)
}

const validCustomConfig = value => value == null ||
  (typeof value === "object" && typeof value.api === "string" && typeof value.athena === "string" && typeof value.web === "string")

export const validProviderStatus = value => Boolean(value && PROVIDER_IDS.includes(value.active) &&
  PROVIDER_IDS.includes(value.selected) && /^[a-f0-9]{64}$/.test(value.revision) &&
  typeof value.restartRequired === "boolean" && typeof value.canSelect === "boolean" &&
  Array.isArray(value.providers) && value.providers.length === 3 &&
  PROVIDER_IDS.every(name => value.providers.filter(p => p.id === name).length === 1) &&
  value.providers.every(validProviderEntry) && validCustomConfig(value.custom))

export const CloudProviderPage = {
  name: "CloudProviderPage",
  components: { GxState, GalaxySelect, GxNotice },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ status: null, selection: null, customEditing: false, customUrl: "", customAthena: "", customWeb: "",
    busy: false, error: "", request: null }),
  mounted() { this.activePage = true; this.poller = new PollTimer({ read: () => this.load() }); this.poller.start(); this.load() },
  beforeUnmount() { this.activePage = false; this.poller.stop(); this.request?.abort() },
  computed: {
    active() { return this.status?.providers.find(p => p.id === this.status.active) },
    selected() { return this.status?.providers.find(p => p.id === this.status.selected) },
    showCustomForm() { return Boolean(this.status && (this.customEditing || this.selection === "custom" || this.status.selected === "custom")) },
  },
  methods: {
    syncCustomFields(value) {
      if (this.customEditing) return
      this.customUrl = value.custom?.api || ""
      this.customAthena = value.custom?.athena || ""
      this.customWeb = value.custom?.web || ""
    },
    async call(body) {
      this.request?.abort()
      const controller = new AbortController()
      this.request = controller
      try {
        const value = await requestJson("./api/connect/provider", { request: { signal: controller.signal,
          ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) } })
        if (this.request !== controller || !this.activePage) return
        if (!validProviderStatus(value)) throw new Error("Cloud provider status is unavailable")
        this.error = ""
        this.status = value
        this.syncCustomFields(value)
      } finally {
        if (this.request === controller) this.request = null
      }
    },
    async load() {
      if (this.mode !== "local" || this.busy || this.request) return
      try { await this.call() } catch (error) { if (error.status === 401) this.unauthorized(); else this.error = connectionError(error) }
    },
    choose(name) {
      if (name === "custom") {
        this.error = ""
        this.customEditing = true
        this.selection = "custom"
        this.customUrl = this.status?.custom?.api || this.customUrl
        this.customAthena = this.status?.custom?.athena || this.customAthena
        this.customWeb = this.status?.custom?.web || this.customWeb
        return
      }
      this.customEditing = false
      return this.select(name)
    },
    async select(name) {
      if (!this.status?.canSelect || this.busy || !this.status.providers.some(p => p.id === name)) return
      const label = this.status.providers.find(p => p.id === name).label
      if (!window.confirm(`Use ${label} after the next device reboot? The current session stays unchanged. Returning to comma restores its saved cloud identity.`)) {
        this.selection = null
        return
      }
      this.busy = true
      this.selection = name
      this.error = ""
      try { await this.call({ provider: name, revision: this.status.revision, confirmed: true }) }
      catch (error) { if (error.status === 401) this.unauthorized(); else this.error = connectionError(error) }
      finally { this.selection = null; this.busy = false }
    },
    async saveCustom() {
      if (!this.status?.canSelect || this.busy) return
      const url = this.customUrl.trim()
      if (!url) { this.error = "Enter a custom server URL"; return }
      const custom = { url }
      if (this.customAthena.trim()) custom.athena = this.customAthena.trim()
      if (this.customWeb.trim()) custom.web = this.customWeb.trim()
      if (!window.confirm(`Use Custom (${url}) after the next device reboot? The current session stays unchanged. Returning to comma restores its saved cloud identity.`)) return
      this.busy = true
      this.selection = "custom"
      this.error = ""
      try {
        await this.call({ provider: "custom", custom, revision: this.status.revision, confirmed: true })
        this.customEditing = false
      } catch (error) { if (error.status === 401) this.unauthorized(); else this.error = connectionError(error) }
      finally { this.selection = null; this.busy = false }
    },
  },
  template: `<div class="gx-settings__developer-body">
    <GxState v-if="mode !== 'local'">Open this Developer page on your device to choose a cloud provider.</GxState>
    <template v-else>
      <GxNotice v-if="status && !status.canSelect" tone="warn">Turn off the vehicle to change this Developer setting.</GxNotice>
      <section class="gx-card gx-settings__section" :inert="busy" :aria-busy="busy || undefined"><div class="gx-section__header"><i class="bi bi-code-slash" aria-hidden="true"></i><span class="gx-section__title">Developer</span></div><div class="gx-row"><div class="gx-row__info">
      <label class="gx-settings__developer-provider">Cloud provider
        <GalaxySelect class="gx-field gx-field--full" aria-label="Cloud provider" :value="selection ?? status?.selected ?? ''"
          :disabled="!status?.canSelect" @change="choose($event.target.value)">
          <option v-for="provider in status?.providers || []" :key="provider.id" :value="provider.id">{{ provider.label }}</option>
        </GalaxySelect>
      </label></div></div>
      <form v-if="showCustomForm" @submit.prevent="saveCustom">
        <div class="gx-row"><label class="gx-settings__developer-provider">Custom server URL
          <input class="gx-field" type="url" v-model="customUrl" placeholder="https://cloud.example.com" maxlength="256" required :disabled="!status?.canSelect" autocomplete="off" spellcheck="false"></label></div>
        <div class="gx-row"><label class="gx-settings__developer-provider">Athena WebSocket URL (optional)
          <input class="gx-field" v-model="customAthena" placeholder="wss://cloud.example.com" maxlength="256" :disabled="!status?.canSelect" autocomplete="off" spellcheck="false"></label></div>
        <div class="gx-row"><label class="gx-settings__developer-provider">Web URL (optional)
          <input class="gx-field" type="url" v-model="customWeb" placeholder="https://cloud.example.com" maxlength="256" :disabled="!status?.canSelect" autocomplete="off" spellcheck="false"></label></div>
        <div class="gx-row"><button class="gx-btn" type="submit" :disabled="!status?.canSelect || busy"><i class="bi bi-check-lg" aria-hidden="true"></i> Use Custom Cloud</button></div>
      </form></section>
      <GxNotice v-if="status?.restartRequired" tone="warn">{{ selected?.label }}<template v-if="selected?.id === 'custom' && selected?.url"> ({{ selected.url }})</template> applies after the next reboot. No automatic reboot will occur.</GxNotice>

      <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
    </template></div>`,
}
