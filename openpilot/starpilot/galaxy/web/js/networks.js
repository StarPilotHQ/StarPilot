import { WikiHelp } from './wiki-help.js'
import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { GxState } from "./state.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"
import { GalaxyHotspot } from "./hotspot.js"
import { GxDialog } from "./dialog.js"

const METERED = ['Auto', 'Metered', 'Unmetered']
const SECURITY = ['open', 'wpa', 'unsupported']
const JOIN_STATES = ['joining', 'joined', 'failed']
const SLOW_ACTIONS = ['scan', 'join', 'connect']
const LINKS = { wifi: 'Wi-Fi', cellular: 'Cellular', none: 'Offline', other: 'Other', unknown: 'Unknown' }

export function validNetworks(value) {
  return value?.version === 1 && typeof value.editable === 'boolean' && ['wifi', 'cellular'].includes(value.prefer) &&
    typeof value.pauseHotspot === 'boolean' && typeof value.cellular?.enabled === 'boolean' &&
    typeof value.link?.link === 'string' && Array.isArray(value.networks) && value.networks.every(network =>
      /^[0-9a-f-]{36}$/.test(network.uuid) && typeof network.name === 'string' && typeof network.autoJoin === 'boolean' &&
      typeof network.whileDriving === 'boolean' && typeof network.connected === 'boolean' && METERED.includes(network.metered)) &&
    (value.available === undefined || (Array.isArray(value.available) && value.available.every(item =>
      typeof item?.ssid === 'string' && Number.isInteger(item.signal) && SECURITY.includes(item.security)))) &&
    (value.lastJoin == null || (typeof value.lastJoin.ssid === 'string' && JOIN_STATES.includes(value.lastJoin.state) &&
      typeof value.lastJoin.error === 'string'))
}

export const choiceRow = (label, value, choices, reason = '') => ({ label, value, reason, choices, available: true, action: true })

export const NetworksPage = {
  name: "NetworksPage",
  components: { WikiHelp, GxNotice, GxState, GalaxySettingRow, GalaxyHotspot, GxDialog },
  props: { mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  data: () => ({ snapshot: null, busy: false, error: '', openUuid: null, adding: false, joiningSsid: '', joinPassword: '',
    hiddenOpen: false, hiddenSsid: '', showJoinPassword: false, forgetUuid: null, timer: null, controller: null, generation: 0, active: false }),
  computed: {
    linkName() { return LINKS[this.snapshot?.link.link] || 'Unknown' },
    availableNetworks() { return this.snapshot?.available || [] },
    joining() { return this.snapshot?.lastJoin?.state === 'joining' },
    joinOpen() { return this.hiddenOpen || !!this.joiningSsid },
    canSubmitJoin() {
      const ssid = this.hiddenOpen ? this.hiddenSsid.trim() : this.joiningSsid
      if (!ssid || ssid.length > 64) return false
      return this.hiddenOpen
        ? (this.joinPassword === '' || (this.joinPassword.length >= 8 && this.joinPassword.length <= 64))
        : (this.joinPassword.length >= 8 && this.joinPassword.length <= 64)
    },
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
    stop() {
      this.active = false; this.generation++; clearTimeout(this.timer); this.controller?.abort()
      this.snapshot = null; this.openUuid = null; this.forgetUuid = null; this.adding = false; this.cancelJoin(); this.busy = false
    },
    adopt(value) {
      if (!validNetworks(value)) throw new Error('Invalid network status')
      this.snapshot = value
      if (this.openUuid && !value.networks.some(n => n.uuid === this.openUuid)) this.openUuid = null
    },
    isOpen(network) { return this.openUuid === network.uuid || this.snapshot?.networks.length === 1 },
    toggleNetwork(uuid) { this.openUuid = this.openUuid === uuid ? null : uuid; this.forgetUuid = null },
    networkSummary(network) {
      const parts = [this.signalText(network), network.autoJoin ? (network.whileDriving ? 'Auto-join' : 'Parked only') : 'Manual']
      if (network.metered !== 'Auto') parts.push(network.metered)
      return parts.join(' · ')
    },
    openScan() {
      this.adding = true
      this.cancelJoin()
      return this.scanNetworks()
    },
    closeScan() {
      this.adding = false
      this.cancelJoin()
    },
    scanNetworks() { return this.send({ action: 'scan' }) },
    cancelJoin() {
      this.joiningSsid = ''
      this.joinPassword = ''
      this.hiddenOpen = false
      this.hiddenSsid = ''
      this.showJoinPassword = false
    },
    selectAvailable(item) {
      if (item.security === 'unsupported') return
      if (item.security === 'open') {
        this.cancelJoin()
        return this.send({ action: 'join', ssid: item.ssid, password: '' })
      }
      this.hiddenOpen = false
      this.joiningSsid = item.ssid
      this.joinPassword = ''
      this.showJoinPassword = false
    },
    openHidden() {
      this.joiningSsid = ''
      this.joinPassword = ''
      this.hiddenSsid = ''
      this.showJoinPassword = false
      this.hiddenOpen = true
    },
    closeJoin() { if (!this.busy) this.cancelJoin() },
    async submitJoin() {
      const ssid = this.hiddenOpen ? this.hiddenSsid.trim() : this.joiningSsid
      if (!ssid) return false
      const joined = await this.send({ action: 'join', ssid, password: this.joinPassword, hidden: this.hiddenOpen })
      if (joined) this.cancelJoin()
      return joined
    },
    connectSaved(network) { return this.send({ action: 'connect', uuid: network.uuid }) },
    forgetSaved(network) {
      if (this.forgetUuid !== network.uuid) { this.forgetUuid = network.uuid; return false }
      this.forgetUuid = null
      return this.send({ action: 'forget', uuid: network.uuid })
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
        this.error = this.joining ? '' : error.message
      } finally {
        if (this.active && generation === this.generation) this.timer = setTimeout(() => this.refresh(), this.joining ? 3000 : 5000)
      }
    },
    async send(body) {
      if (!this.active || this.busy || this.joining || !this.snapshot?.editable) return false
      clearTimeout(this.timer); this.controller?.abort()
      const generation = ++this.generation
      this.controller = new AbortController(); this.busy = true; this.error = ''
      try {
        const value = await requestJson('./api/galaxy/networks', { timeout: SLOW_ACTIONS.includes(body.action) ? 60000 : 8000, request: { signal: this.controller.signal,
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
    <header class="gx-settings__header gx-page-header"><div><h2>Networks</h2><p>Wi-Fi priority, cellular data, and local hotspot access.</p></div><WikiHelp /></header>
    <GxState v-if="mode !== 'local'">Connect to your comma to manage its networks.</GxState>
    <GxState v-else-if="!snapshot && !error" loading>Loading network settings…</GxState>
    <template v-if="snapshot">
      <GxNotice v-if="!snapshot.editable" tone="warn">Park before changing network settings.</GxNotice>
      <GxNotice v-if="snapshot.lastJoin?.state === 'joining'" tone="info" busy>Joining {{ snapshot.lastJoin.ssid }}. The Galaxy hotspot pauses for up to 30 seconds; your phone should reconnect on its own and this page will update.</GxNotice>
      <GxNotice v-else-if="snapshot.lastJoin?.state === 'joined'" tone="success">Joined {{ snapshot.lastJoin.ssid }}.</GxNotice>
      <GxNotice v-else-if="snapshot.lastJoin?.state === 'failed'" tone="danger">Couldn't join {{ snapshot.lastJoin.ssid }}. {{ snapshot.lastJoin.error }}</GxNotice>
      <section class="gx-card gx-settings__section"><div class="gx-section__header"><i class="bi bi-arrow-left-right" aria-hidden="true"></i><span class="gx-section__title">Connection</span><span class="gx-chip">{{ linkName }}<template v-if="changed"> · {{ changed }}</template></span></div>
        <GalaxySettingRow :row="choiceRow('Prefer', snapshot.prefer === 'cellular' ? 'Cellular' : 'Wi-Fi', ['Wi-Fi', 'Cellular'], 'Used when both are available; apps reconnect automatically.')" :index="0"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'prefer', value: value === 'Cellular' ? 'cellular' : 'wifi' })" />
        <GalaxySettingRow :row="switchRow('Cellular data', snapshot.cellular.enabled, cellularDetail)" :index="1"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'cellular', value: value === 'On' })" />
      </section>
      <section class="gx-card gx-settings__section"><div class="gx-section__header"><i class="bi bi-wifi" aria-hidden="true"></i><span class="gx-section__title">Saved Wi-Fi networks</span><span v-if="snapshot.networks.length" class="gx-section__count">{{ snapshot.networks.length }}</span></div>
        <p v-if="!snapshot.networks.length" class="gx-empty">No saved Wi-Fi networks yet. Scan for nearby networks below to join one.</p>
        <p v-else class="gx-note gx-networks__caption">Joined in priority order when in range.</p>
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
            <div class="gx-row gx-networks__saved-actions">
              <div class="gx-row__info"><span class="gx-row__label">Connection</span><span class="gx-row__desc">{{ forgetUuid === network.uuid ? (network.connected ? 'The comma will leave this network now.' : 'Tap again to forget this network.') : network.connected ? 'Currently active' : network.signal != null ? 'Available in range' : 'Out of range' }}</span></div>
              <div class="gx-actions">
                <button v-if="!network.connected && network.signal != null" type="button" class="gx-btn gx-btn--tonal" :disabled="busy || !snapshot.editable" @click="connectSaved(network)">Connect</button>
                <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy || !snapshot.editable" @click="forgetSaved(network)">{{ forgetUuid === network.uuid ? 'Confirm forget' : 'Forget' }}</button>
                <button v-if="forgetUuid === network.uuid" type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="forgetUuid = null">Keep</button>
              </div>
            </div>
          </div>
        </div>
        <GalaxySettingRow :row="switchRow('Pause Galaxy hotspot while joining Wi-Fi', snapshot.pauseHotspot, 'Briefly pauses the hotspot during a drive so joining Wi-Fi does not disturb StarPort.')" :index="0"
          :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => send({ action: 'pauseHotspot', value: value === 'On' })" />
      </section>
      <section class="gx-card gx-settings__section"><div class="gx-section__header"><i class="bi bi-broadcast" aria-hidden="true"></i><span class="gx-section__title">Nearby networks</span><span v-if="adding && availableNetworks.length" class="gx-section__count">{{ availableNetworks.length }}</span>
          <div class="gx-actions gx-networks__header-actions">
            <button type="button" class="gx-btn gx-btn--tonal gx-networks__header-btn" :disabled="busy || !snapshot.editable" @click="adding ? scanNetworks() : openScan()"><i class="bi bi-arrow-clockwise" aria-hidden="true"></i> {{ busy && adding ? 'Scanning…' : adding ? 'Rescan' : 'Scan' }}</button>
            <button v-if="adding" type="button" class="gx-btn gx-btn--tonal gx-networks__header-btn" :disabled="busy" @click="closeScan">Done</button>
          </div></div>
        <p v-if="!adding" class="gx-empty">Scan to find Wi-Fi networks to join.</p>
        <p v-else-if="!availableNetworks.length" class="gx-empty" role="status">{{ busy ? 'Scanning for nearby Wi-Fi networks…' : 'No new Wi-Fi networks found nearby.' }}</p>
        <template v-else><div v-for="item in availableNetworks" :key="item.ssid" class="gx-row gx-networks__row">
          <div class="gx-row__info">
            <span class="gx-row__label"><i class="bi" :class="item.security === 'open' ? 'bi-wifi' : 'bi-lock-fill'" aria-hidden="true"></i> {{ item.ssid }}</span>
            <span class="gx-row__desc">Signal {{ item.signal }}% · {{ item.security === 'open' ? 'Open' : item.security === 'unsupported' ? 'Enterprise Wi-Fi (unsupported)' : 'Password required' }}</span>
          </div>
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy || !snapshot.editable || item.security === 'unsupported'" @click="selectAvailable(item)">Join</button>
        </div></template>
        <div class="gx-networks__subhead"><span class="gx-row__desc">Network not listed?</span><button type="button" class="gx-link gx-networks__link-btn" :disabled="busy || !snapshot.editable" @click="openHidden">Join hidden network</button></div>
      </section>
      <GxDialog v-if="joinOpen" :inert="busy" labelledby="gx-networks-join-title" describedby="gx-networks-join-note" @close="closeJoin">
        <form class="gx-networks__dialog" @submit.prevent="canSubmitJoin && submitJoin()">
          <h3 id="gx-networks-join-title">{{ hiddenOpen ? 'Join hidden network' : 'Join ' + joiningSsid }}</h3>
          <p id="gx-networks-join-note" class="gx-row__desc">Joining pauses the Galaxy hotspot for up to 30 seconds. If your phone is on it, it reconnects afterwards.</p>
          <label v-if="hiddenOpen" class="gx-networks__label">Network name (SSID)
            <input class="gx-field" type="text" v-model="hiddenSsid" maxlength="64" autocomplete="off" autofocus />
          </label>
          <label class="gx-networks__label">{{ hiddenOpen ? 'Password (blank if open)' : 'Password · 8–64 characters' }}
            <span class="gx-networks__password">
              <input class="gx-field" :type="showJoinPassword ? 'text' : 'password'" v-model="joinPassword" maxlength="64" autocomplete="new-password" :autofocus="!hiddenOpen" />
              <button type="button" class="gx-icon-btn" :aria-label="showJoinPassword ? 'Hide password' : 'Show password'" :aria-pressed="showJoinPassword" @click="showJoinPassword = !showJoinPassword"><i class="bi" :class="showJoinPassword ? 'bi-eye-slash' : 'bi-eye'" aria-hidden="true"></i></button>
            </span>
          </label>
          <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
          <div class="gx-actions gx-settings__controls"><button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="cancelJoin">Cancel</button>
            <button type="submit" class="gx-btn" :disabled="busy || !snapshot.editable || !canSubmitJoin">{{ busy ? 'Joining…' : 'Join' }}</button></div>
        </form>
      </GxDialog>
    </template>
    <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
    <GalaxyHotspot v-if="mode === 'local'" :mode="mode" :unauthorized="unauthorized" />
  </section>`,
}
