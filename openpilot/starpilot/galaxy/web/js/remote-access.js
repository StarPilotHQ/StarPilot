import { GxState } from './state.js'
import { GxNotice } from './notice.js'
import { requestJson } from './startup.js'
import { BluetoothNotice, bluetoothConnection } from './bluetooth-notice.js'

export const RemoteQR = {
  props: { endpoint: {type: String, required: true}, alt: {type: String, required: true} },
  data: () => ({image: '', failed: false, request: null}),
  mounted() { this.load() },
  beforeUnmount() { this.request?.abort(); if (this.image) URL.revokeObjectURL(this.image) },
  methods: {
    async load() {
      this.request?.abort()
      const request = new AbortController()
      this.request = request
      this.failed = false
      try {
        const response = await fetch(this.endpoint, {credentials: 'same-origin', cache: 'no-store', signal: request.signal})
        if (!response.ok || response.headers.get('content-type')?.split(';')[0] !== 'image/svg+xml') throw Error()
        const blob = await response.blob()
        if (request.signal.aborted) return
        if (this.image) URL.revokeObjectURL(this.image)
        this.image = URL.createObjectURL(blob)
      } catch { if (!request.signal.aborted) this.failed = true }
    },
  },
  template: '<div><button v-if="failed" class="gx-btn" @click="load">Retry QR code</button><img v-else-if="image" :src="image" :alt="alt" class="gx-tunnel__qr" @error="failed = true" /></div>',
}

export const RemoteAccess = {
  name: 'RemoteAccess',
  components: { GxState, GxNotice, BluetoothNotice, RemoteQR },
  props: { title: { type: String, default: 'Remote access' }, step: { type: Number, default: 1 }, mode: { type: String, required: true }, unauthorized: { type: Function, required: true } },
  emits: ['link'],
  data: () => ({ loading: true, paired: false, url: '', tunnelClientAvailable: false, legacyPassword: false,
    appKey: '', keyVisible: false, legacyPairingAvailable: false, password: '', busy: false, error: '', generation: 0, bluetooth: bluetoothConnection() }),
  mounted() { this.load() },
  watch: { mode() {
    this.generation++
    this.busy = false
    this.error = ''
    this.password = ''
    this.setPairing({ paired: false, url: '' })
    this.load()
  } },
  beforeUnmount() { this.generation++; this.appKey = ''; this.keyVisible = false },
  methods: {
    setPairing({ paired, url }) {
      this.paired = paired
      this.url = paired ? url : ''
      this.appKey = ''
      this.keyVisible = false
      this.$emit('link', this.url)
    },
    async showAppKey() {
      const url = this.url
      try { const data = await this.request('./api/galaxy/session'); if (this.url !== url || !this.paired) return; this.appKey = data.paired ? decodeURIComponent(data.sessionToken) : ''; this.keyVisible = !!this.appKey }
      catch (error) { if (error.name !== 'AbortError') this.error = error.message }
    },
    async copyAppKey() {
      try { await navigator.clipboard.writeText(this.appKey) } catch { this.error = 'Select and copy the displayed key.' }
    },
    async request(path, body) {
      const generation = this.generation
      try {
        const result = await requestJson(path, { request: body === undefined ? {} : {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        } })
        if (generation !== this.generation) throw new DOMException('Access page changed', 'AbortError')
        return result
      } catch (error) {
        if (generation !== this.generation) throw new DOMException('Access page changed', 'AbortError')
        if (generation === this.generation && error.status === 401) this.unauthorized()
        throw error
      }
    },
    async load() {
      const generation = this.generation
      this.loading = true
      if (this.mode !== "local") { this.loading = false; return }
      try {
        const data = await this.request("./api/galaxy/status")
        this.setPairing(data)
        this.tunnelClientAvailable = data.tunnelClientAvailable === true
        this.legacyPassword = data.legacyPassword === true
        this.legacyPairingAvailable = data.legacyPairingAvailable === true
      } catch (error) { if (error.name !== 'AbortError') this.error = error.message }
      finally { if (generation === this.generation) this.loading = false }
    },
    async pair() {
      if (this.mode !== 'local' || this.busy) return
      this.busy = true
      this.error = ""
      try {
        const data = await this.request("./api/galaxy/pair", { password: this.password })
        this.setPairing(data)
        this.password = ""
      } catch (error) { if (error.name !== 'AbortError') this.error = error.message }
      finally { this.busy = false }
    },
    async unpair() {
      if (this.mode !== 'local' || this.busy) return
      if (!window.confirm("Unpair from Galaxy? Remote access will stop until you pair again.")) return
      this.busy = true
      this.error = ""
      try {
        await this.request("./api/galaxy/unpair", {})
        this.setPairing({ paired: false, url: '' })
      } catch (error) { if (error.name !== 'AbortError') this.error = error.message }
      finally { this.busy = false }
    },
  },
  template: `<li class="gx-card gx-step" :class="{'gx-step--done': paired}" :inert="busy" :aria-busy="busy || undefined">
    <div class="gx-step__head">
      <span class="gx-step__num" aria-hidden="true"><i v-if="paired" class="bi bi-check-lg"></i><template v-else>{{ step }}</template></span>
      <div><h3>{{ title }}</h3><p>Use a Firestar Link in the app or browser when you are away from your comma. Both devices need Internet.</p></div>
    </div>
    <div class="gx-step__body">
      <GxState v-if="mode !== 'local'">Connect to your comma to set up remote access.</GxState>
      <GxState v-else-if="loading" loading>Checking pairing status…</GxState>
      <template v-else>
        <template v-if="paired">
          <GxNotice v-if="!tunnelClientAvailable" tone="warn">Remote access is not ready on this comma yet.</GxNotice>
          <BluetoothNotice v-if="bluetooth" feature="Galaxy QR code">Your Galaxy link remains available below.</BluetoothNotice>
          <RemoteQR v-else :key="url" endpoint="./api/galaxy/qr.svg" alt="QR code for your Galaxy link" />
          <p><a :href="url" target="_blank" rel="noopener" class="gx-btn gx-btn--tonal gx-wrap">Open your Galaxy link</a></p>
          <section class="gx-card gx-panel gx-stack"><h3>App key</h3><p>Use this key to add remote access in the phone app. Keep it private.</p>
            <div v-if="!keyVisible" class="gx-actions"><button class="gx-btn" @click="showAppKey">Show</button></div>
            <template v-else><code class="gx-wrap">{{ appKey }}</code><div class="gx-actions"><button class="gx-btn" @click="copyAppKey">Copy</button></div>
              <RemoteQR endpoint="./api/galaxy/session/qr.svg" alt="Add remote access to Galaxy" /></template>
          </section>
          <div class="gx-actions"><button type="button" class="gx-btn gx-btn--danger" @click="unpair">{{ busy ? 'Disconnecting…' : 'Turn off remote access' }}</button></div>
        </template>
        <template v-else>
          <p v-if="legacyPassword || legacyPairingAvailable">Use your old password or choose a new one with at least 8 characters.</p>
          <p v-else>Choose a password to pair your comma.</p>
          <div class="gx-actions"><input class="gx-field gx-filter-field" type="password" v-model="password" :minlength="legacyPassword || legacyPairingAvailable ? 6 : 8" maxlength="255" autocomplete="new-password" :placeholder="legacyPassword || legacyPairingAvailable ? 'Existing or new Galaxy password' : 'New password (at least 8 characters)'" @keydown.enter="pair" />
            <button type="button" class="gx-btn" :disabled="password.trim().length < (legacyPassword || legacyPairingAvailable ? 6 : 8)" @click="pair">{{ busy ? 'Setting up…' : 'Set up remote access' }}</button></div>
        </template>
        <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
      </template>
    </div>
  </li>`,
}
