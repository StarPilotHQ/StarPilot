import { requestJson } from './startup.js'
import { GxNotice } from './notice.js'
import { BluetoothNotice, bluetoothConnection } from './bluetooth-notice.js'
import { APP_DOWNLOADS } from './app-downloads.js'
import { isIosDevice } from './install-app.js'

const DISMISSED_UPDATE = 'galaxy.companion.dismissedUpdateVersion'

export function newerCompanionVersion(available, installed) {
  const parts = version => typeof version === 'string' && /^\d+(?:\.\d+){1,3}$/.test(version) ? version.split('.').map(Number) : []
  const next = parts(available), current = parts(installed)
  if (!next.length || !current.length || ![...next, ...current].every(Number.isSafeInteger)) return false
  for (let index = 0; index < Math.max(next.length, current.length); index++) {
    const difference = (next[index] || 0) - (current[index] || 0)
    if (difference) return difference > 0
  }
  return false
}

export function validCompanion(value) {
  return value?.protocol === 1 && ['enabled', 'connected', 'active', 'appAvailable', 'updateAvailable'].every(key => typeof value[key] === 'boolean') &&
    Number.isSafeInteger(value.bytes) && value.bytes >= 0 && typeof value.shippedVersion === 'string' &&
    ['active', 'waiting', 'disabled', 'error'].includes(value.state)
}

export const CompanionUpdates = {
  components: { GxNotice, BluetoothNotice },
  props: { mode: String, unauthorized: Function },
  data: () => ({ status: null, error: '', androidApp: APP_DOWNLOADS.android, nativeVersion: window.StarPilotApp?.version?.() || '',
    eligible: !isIosDevice() && !!window.StarPilotApp?.version && window.StarPilotApp?.distribution?.() !== 'play',
    playDistribution: window.StarPilotApp?.distribution?.() === 'play',
    bluetooth: bluetoothConnection(), generation: 0, alive: false, dismissedUpdateVersion: (() => {
      try { return localStorage.getItem(DISMISSED_UPDATE) || '' } catch { return '' }
    })() }),
  computed: {
    availableVersion() { return this.status?.appAvailable ? this.status.shippedVersion : '' },
    showUpdate() { return this.eligible && this.mode === 'local' && this.availableVersion !== this.dismissedUpdateVersion && newerCompanionVersion(this.availableVersion, this.nativeVersion) },
    nativeInstallerAvailable() { return typeof window.StarPilotApp?.installUpdate === 'function' },
  },
  mounted() { this.start() },
  beforeUnmount() { this.stop() },
  watch: { mode() { this.stop(); this.start() } },
  methods: {
    installUpdate() { window.StarPilotApp?.installUpdate?.() },
    dismissUpdate() {
      this.dismissedUpdateVersion = this.availableVersion
      try { localStorage.setItem(DISMISSED_UPDATE, this.dismissedUpdateVersion) } catch {}
    },
    start() { if (this.mode === 'local' && this.eligible) { this.alive = true; this.refresh() } },
    stop() { this.alive = false; this.generation++; clearTimeout(this.timer); this.controller?.abort(); this.status = null },
    adopt(status) {
      if (!validCompanion(status)) throw new Error('Invalid companion status')
      this.status = status
    },
    async refresh() {
      if (!this.alive) return
      clearTimeout(this.timer)
      const generation = ++this.generation
      this.controller = new AbortController()
      try {
        const status = await requestJson('./api/companion/status', { request: { signal: this.controller.signal } })
        if (!this.alive || generation !== this.generation) return
        this.adopt(status); this.error = ''
      } catch (e) {
        if (!this.alive || generation !== this.generation) return
        this.error = e.message
        if (e.status === 401) { this.stop(); this.unauthorized() }
      } finally { if (this.alive && generation === this.generation) this.timer = setTimeout(() => this.refresh(), 5000) }
    },
  },
  template: `<p v-if="playDistribution" class="gx-note">Updates come from Google Play.</p>
    <BluetoothNotice v-else-if="showUpdate && bluetooth" :feature="'Android app update ' + availableVersion">
      Installed: {{ nativeVersion }}. Connect over Wi-Fi to update.
      <button type="button" class="gx-btn gx-btn--tonal" @click="dismissUpdate">Dismiss</button>
    </BluetoothNotice>
    <GxNotice v-else-if="showUpdate" tone="warn" title="Android app update available">
      Version {{ availableVersion }} is available. Installed: {{ nativeVersion }}.
      <template #actions>
        <button v-if="nativeInstallerAvailable" type="button" class="gx-btn" @click="installUpdate">Update app</button>
        <a v-else class="gx-btn" :href="androidApp.href" target="_blank" rel="noopener">Update app</a>
        <button type="button" class="gx-btn gx-btn--tonal" aria-label="Dismiss app update notice" @click="dismissUpdate">Dismiss</button>
      </template>
    </GxNotice>
    <GxNotice v-else-if="eligible && error" tone="danger">{{ error }}</GxNotice>`,
}
