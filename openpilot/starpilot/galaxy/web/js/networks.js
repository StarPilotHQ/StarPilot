import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { GxState } from "./state.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"
import { GalaxyHotspot } from "./hotspot.js"

const METERED = ['Auto', 'Metered', 'Unmetered']
const LINKS = { wifi: 'Wi-Fi', cellular: 'Cellular', none: 'Offline', other: 'Other', unknown: 'Unknown' }

export function validNetworks(value) {
  return value?.version === 1 && typeof value.editable === 'boolean' && ['wifi', 'cellular'].includes(value.prefer) &&
    typeof value.pauseHotspot === 'boolean' && typeof value.cellular?.enabled === 'boolean' &&
    typeof value.link?.link === 'string' && Array.isArray(value.networks) && value.networks.every(network =>
      /^[0-9a-f-]{36}$/.test(network.uuid) && typeof network.name === 'string' && typeof network.autoJoin === 'boolean' &&
      typeof network.whileDriving === 'boolean' && typeof network.connected === 'boolean' && METERED.includes(network.metered))
}

export const choiceRow = (label, value, choices, reason = '') => ({ label, value, reason, choices, available: true, action: true })

export const NetworksPage = {
  name: "NetworksPage",
  components: { GxNotice, GxState, GalaxySettingRow, GalaxyHotspot },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ snapshot: null, busy: false, error: '', openUuid: null, timer: null, controller: null, generation: 0, active: false }),
  computed: {
    linkName() { return LINKS[this.snapshot?.link.link] || 'Unknown' },
    changed() {
      const at = this.snapshot?.link.changedAt
      return at ? new Date(at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''
    },
    cellularDetail() {
      const cellular = this.snapshot.cellular
      if (!cellular.enabled) return 'Off · comma goes online over Wi-Fi only.'
      return cellular.connected ? `Connected${cellular.operator ? ' to ' + cellular.operator : ''}${cellular.type ? ' (' + cellular.type.toUpperCase() + ')' : ''}.` : 'Not connected right now.'
    },
  },
  mounted() { this.start() },
  beforeUnmount() { this.stop() },
  watch: { mode() { this.stop(); this.start() } },
  methods: {
    switchRow, choiceRow,
    start() { if (this.mode === 'local') { this.active = true; this.refresh() } },
    stop() { this.active = false; this.generation++; clearTimeout(this.timer); this.controller?.abort(); this.snapshot = null; this.openUuid = null; this.busy = false },
    adopt(value) {
      if (!validNetworks(value)) throw new Error('Invalid network status')
      this.snapshot = value
      if (this.openUuid && !value.networks.some(n => n.uuid === this.openUuid)) this.openUuid = null
    },
    isOpen(network) { return this.openUuid === network.uuid || this.snapshot?.networks.length === 1 },
    toggleNetwork(uuid) { this.openUuid = this.openUuid === uuid ? null : uuid },
    networkSummary(network) {
      const parts = [this.signalText(network), network.autoJoin ? (network.whileDriving ? 'Auto-join' : 'Parked only') : 'Manual']
      if (network.metered !== 'Auto') parts.push(network.metered)
      return parts.join(' · ')
    },
    async refresh() {
      if (!this.active || this.busy) return
      clearTimeout(this.timer); this.controller?.abort()
      const generation = ++this.generation
      this.controller = new AbortController()
      try {
        const value = await requestJson('./api/galaxy/networks', { request: { signal: this.controller.signal } })
        if (!this.active || generation !== this.generation) return
        this.adopt(value); this.error = ''
      } catch (error) {
        if (!this.active || generation !== this.generation) return
        if (error.status === 401) { this.stop(); this.unauthorized(); return }
        this.error = error.message
      } finally {
        if (this.active && generation === this.generation) this.timer = setTimeout(() => this.refresh(), 5000)
      }
    },
    async send(body) {
      if (!this.active || this.busy || !this.snapshot?.editable) return false
      clearTimeout(this.timer); this.controller?.abort()
      const generation = ++this.generation
      this.controller = new AbortController(); this.busy = true; this.error = ''
      try {
        const value = await requestJson('./api/galaxy/networks', { request: { signal: this.controller.signal,
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } })
        if (!this.active || generation !== this.generation) return false
        this.adopt(value)
        return true
      } catch (error) {
        if (!this.active || generation !== this.generation) return false
        if (error.status === 401) { this.stop(); this.unauthorized(); return false }
        this.error = error.message
        return false
      } finally {
        if (this.active && generation === this.generation) { this.busy = false; this.timer = setTimeout(() => this.refresh(), 2000) }
      }
    },
    move(index, step) {
      const uuids = this.snapshot.networks.map(network => network.uuid), target = index + step
      if (target < 0 || target >= uuids.length) return
      uuids.splice(target, 0, ...uuids.splice(index, 1))
      return this.send({ action: 'order', uuids })
    },
    setNetwork(network, setting, value) { return this.send({ action: 'network', uuid: network.uuid, setting, value }) },
    signalText(network) {
      if (network.connected) return 'Connected'
      return network.signal == null ? 'Not in range' : `In range, signal ${network.signal}%`
    },
  },
  template: `<section class="gx-driving gx-networks" aria-label="Networks">
    <header class="gx-settings__header gx-page-header"><div><h2>Networks</h2><p>Wi-Fi priority, cellular data, and local hotspot access.</p></div></header>
    <GxState v-if="mode !== 'local'">Connect to your comma to manage its networks.</GxState>
    <GxState v-else-if="!snapshot && !error" loading>Loading network settings…</GxState>
    <template v-if="snapshot">
      <GxNotice v-if="!snapshot.editable" tone="warn">Park before changing network settings.</GxNotice>
      <section class="gx-card gx-settings__section"><div class="gx-section__header"><i class="bi bi-arrow-left-right" aria-hidden="true"></i><span class="gx-section__title">Connection</span><span class="gx-chip">{{ linkName }}<template v-if="changed"> · {{ changed }}</template></span></div>
        <GalaxySettingRow :row="choiceRow('Prefer', snapshot.prefer === 'cellular' ? 'Cellular' : 'Wi-Fi', ['Wi-Fi', 'Cellular'], 'Used when both are available; apps reconnect automatically.')" :index="0"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'prefer', value: value === 'Cellular' ? 'cellular' : 'wifi' })" />
        <GalaxySettingRow :row="switchRow('Cellular data', snapshot.cellular.enabled, cellularDetail)" :index="1"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'cellular', value: value === 'On' })" />
      </section>
      <section class="gx-card gx-settings__section"><div class="gx-section__header"><i class="bi bi-wifi" aria-hidden="true"></i><span class="gx-section__title">Saved Wi-Fi networks</span><span v-if="snapshot.networks.length" class="gx-section__count">{{ snapshot.networks.length }}</span></div>
        <p v-if="!snapshot.networks.length" class="gx-empty">No saved Wi-Fi networks yet. Add or forget networks on the comma's Wi-Fi screen.</p>
        <p v-else class="gx-note gx-networks__caption">Joined in priority order when in range. Add or forget networks on the comma's Wi-Fi screen.</p>
        <div v-for="(network, index) in snapshot.networks" :key="network.uuid" class="gx-networks__item">
          <div class="gx-row gx-networks__row">
            <button type="button" class="gx-row__info gx-networks__toggle" :aria-expanded="isOpen(network)" @click="toggleNetwork(network.uuid)">
              <span class="gx-row__label">{{ index + 1 }}. {{ network.name }}</span>
              <span class="gx-row__desc">{{ networkSummary(network) }}</span>
            </button>
            <div class="gx-actions">
              <button type="button" class="gx-icon-btn" :aria-label="'Move ' + network.name + ' up'" :disabled="busy || !snapshot.editable || index === 0" @click="move(index, -1)"><i class="bi bi-arrow-up" aria-hidden="true"></i></button>
              <button type="button" class="gx-icon-btn" :aria-label="'Move ' + network.name + ' down'" :disabled="busy || !snapshot.editable || index === snapshot.networks.length - 1" @click="move(index, 1)"><i class="bi bi-arrow-down" aria-hidden="true"></i></button>
              <button v-if="snapshot.networks.length > 1" type="button" class="gx-icon-btn" :aria-label="(isOpen(network) ? 'Collapse ' : 'Configure ') + network.name" :aria-expanded="isOpen(network)" @click="toggleNetwork(network.uuid)"><i class="bi" :class="isOpen(network) ? 'bi-chevron-up' : 'bi-chevron-down'" aria-hidden="true"></i></button>
            </div>
          </div>
          <div v-show="isOpen(network)" class="gx-networks__details">
            <GalaxySettingRow :row="switchRow('Join automatically', network.autoJoin)" :index="index"
              :busy="busy" :disabled="!snapshot.editable" :save-value="(i, value) => setNetwork(network, 'autoJoin', value === 'On')" />
            <GalaxySettingRow :row="switchRow('Join while StarPort is on', network.whileDriving, 'When off, stays on the current connection until the drive ends.')" :index="index"
              :busy="busy" :disabled="!snapshot.editable || !network.autoJoin" :save-value="(i, value) => setNetwork(network, 'whileDriving', value === 'On')" />
            <GalaxySettingRow :row="choiceRow('Data limit', network.metered, ['Auto', 'Metered', 'Unmetered'], 'Metered networks skip large uploads.')" :index="index"
              :busy="busy" :disabled="!snapshot.editable" :save-value="(i, value) => setNetwork(network, 'metered', value)" />
          </div>
        </div>
        <GalaxySettingRow :row="switchRow('Pause Galaxy hotspot while joining Wi-Fi', snapshot.pauseHotspot, 'Briefly pauses the hotspot during a drive so joining Wi-Fi does not disturb StarPort.')" :index="0"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'pauseHotspot', value: value === 'On' })" />
      </section>
    </template>
    <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
    <GalaxyHotspot v-if="mode === 'local'" :mode="mode" :unauthorized="unauthorized" />
  </section>`,
}
