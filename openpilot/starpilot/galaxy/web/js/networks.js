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
  data: () => ({ snapshot: null, busy: false, error: '', timer: null, controller: null, generation: 0, active: false }),
  computed: {
    linkName() { return LINKS[this.snapshot?.link.link] || 'Unknown' },
    changed() {
      const at = this.snapshot?.link.changedAt
      return at ? new Date(at * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''
    },
    cellularDetail() {
      const cellular = this.snapshot.cellular
      if (!cellular.enabled) return 'Cellular data is off. The comma only goes online over Wi-Fi.'
      return cellular.connected ? `Connected${cellular.operator ? ' to ' + cellular.operator : ''}${cellular.type ? ' (' + cellular.type.toUpperCase() + ')' : ''}.` : 'Not connected right now.'
    },
  },
  mounted() { this.start() },
  beforeUnmount() { this.stop() },
  watch: { mode() { this.stop(); this.start() } },
  methods: {
    switchRow, choiceRow,
    start() { if (this.mode === 'local') { this.active = true; this.refresh() } },
    stop() { this.active = false; this.generation++; clearTimeout(this.timer); this.controller?.abort(); this.snapshot = null; this.busy = false },
    adopt(value) {
      if (!validNetworks(value)) throw new Error('Invalid network status')
      this.snapshot = value
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
  template: `<div class="gx-stack">
    <GxState v-if="mode !== 'local'">Connect to your comma to manage its networks.</GxState>
    <GxState v-else-if="!snapshot && !error" loading>Loading network settings…</GxState>
    <template v-if="snapshot">
      <section class="gx-card gx-panel gx-stack"><div class="gx-section__header"><i class="bi bi-arrow-left-right" aria-hidden="true"></i><span class="gx-section__title">Connection</span></div>
        <p>Online through: <strong>{{ linkName }}</strong><span v-if="changed"> · last switched at {{ changed }}</span></p>
        <GalaxySettingRow :row="choiceRow('Prefer', snapshot.prefer === 'cellular' ? 'Cellular' : 'Wi-Fi', ['Wi-Fi', 'Cellular'], 'Which connection the comma uses when both are available. Wi-Fi stays connected either way.')" :index="0"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'prefer', value: value === 'Cellular' ? 'cellular' : 'wifi' })" />
        <GalaxySettingRow :row="switchRow('Cellular data', snapshot.cellular.enabled, cellularDetail)" :index="1"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'cellular', value: value === 'On' })" />
        <p class="gx-note">When the comma changes networks, apps such as Spotify reconnect on the new one within a few seconds, without restarting.</p>
      </section>
      <section class="gx-card gx-panel gx-stack"><div class="gx-section__header"><i class="bi bi-wifi" aria-hidden="true"></i><span class="gx-section__title">Saved Wi-Fi networks</span></div>
        <p>The comma joins the highest network on this list that is in range. Add or forget networks on the comma's own Wi-Fi screen.</p>
        <GxState v-if="!snapshot.networks.length">No saved Wi-Fi networks yet.</GxState>
        <div v-for="(network, index) in snapshot.networks" :key="network.uuid" class="gx-card gx-stack">
          <div class="gx-row gx-row--borderless"><div class="gx-row__info"><span class="gx-row__label">{{ index + 1 }}. {{ network.name }}</span><span class="gx-row__desc">{{ signalText(network) }}</span></div>
            <div class="gx-actions">
              <button type="button" class="gx-icon-btn" :aria-label="'Move ' + network.name + ' up'" :disabled="busy || !snapshot.editable || index === 0" @click="move(index, -1)"><i class="bi bi-arrow-up" aria-hidden="true"></i></button>
              <button type="button" class="gx-icon-btn" :aria-label="'Move ' + network.name + ' down'" :disabled="busy || !snapshot.editable || index === snapshot.networks.length - 1" @click="move(index, 1)"><i class="bi bi-arrow-down" aria-hidden="true"></i></button>
            </div></div>
          <GalaxySettingRow :row="switchRow('Join automatically', network.autoJoin)" :index="index"
            :busy="busy" :disabled="!snapshot.editable" :save-value="(i, value) => setNetwork(network, 'autoJoin', value === 'On')" />
          <GalaxySettingRow :row="switchRow('Join while StarPort is on', network.whileDriving, 'Off: the comma stays on its current connection during a drive and joins this network once the car link ends.')" :index="index"
            :busy="busy" :disabled="!snapshot.editable || !network.autoJoin" :save-value="(i, value) => setNetwork(network, 'whileDriving', value === 'On')" />
          <GalaxySettingRow :row="choiceRow('Data limit', network.metered, ['Auto', 'Metered', 'Unmetered'], 'Metered networks are not used for large uploads.')" :index="index"
            :busy="busy" :disabled="!snapshot.editable" :save-value="(i, value) => setNetwork(network, 'metered', value)" />
        </div>
        <GalaxySettingRow :row="switchRow('Pause Galaxy hotspot while joining Wi-Fi', snapshot.pauseHotspot, 'During a drive the hotspot stops for a few seconds while the comma joins a network, so the join does not disturb the car link. Phones on the hotspot reconnect afterwards.')" :index="0"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'pauseHotspot', value: value === 'On' })" />
      </section>
      <GxNotice v-if="!snapshot.editable">Park before changing network settings.</GxNotice>
    </template>
    <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
    <GalaxyHotspot :mode="mode" :unauthorized="unauthorized" />
  </div>`,
}
