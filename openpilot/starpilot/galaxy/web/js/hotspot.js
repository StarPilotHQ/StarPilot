import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { GxState } from "./state.js"
import { GalaxySettingRow, switchRow } from "./galaxy-setting-row.js"
import { beginSave, finishSave } from "./save-feedback.js"

const validPassword = password => typeof password === 'string' && (password === '' || /^[\x21-\x7e]{12,63}$/.test(password))

export function validHotspot(value) {
  return value?.version === 1 && /^[a-f0-9]{64}$/.test(value.revision) &&
    typeof value.editable === 'boolean' && typeof value.active === 'boolean' &&
    ['active', 'starting', 'waiting', 'disabled', 'error', 'unavailable'].includes(value.state) &&
    typeof value.reason === 'string' && value.reason.length <= 256 &&
    value.url === 'http://172.31.254.1:8082/' && typeof value.config?.enabled === 'boolean' &&
    /^TheGalaxy-[a-f0-9]{4}$/.test(value.config.ssid) &&
    validPassword(value.config.password)
}

export const GalaxyHotspot = {
  components: { GxNotice, GxState, GalaxySettingRow },
  props: { mode: String, unauthorized: Function },
  data: () => ({ snapshot: null, draft: null, revision: '', busy: false, error: '', saveError: '', notice: '',
    showPassword: false, timer: null, controller: null, generation: 0, active: false }),
  computed: {
    dirty() { return JSON.stringify(this.draft) !== JSON.stringify(this.snapshot?.config) },
    passwordValid() { return validPassword(this.draft?.password) },
  },
  mounted() { this.start() },
  beforeUnmount() { this.stop() },
  watch: { mode() { this.stop(); this.start() } },
  methods: {
    switchRow,
    start() { if (this.mode === 'local') { this.active = true; this.refresh() } },
    stop() {
      this.active = false; this.generation++; clearTimeout(this.timer); this.controller?.abort()
      this.snapshot = this.draft = null; this.revision = ''; this.showPassword = false; this.busy = false; this.saveError = ''
    },
    adopt(value, reset = false) {
      if (!validHotspot(value)) throw new Error('Invalid hotspot status')
      const preserve = this.draft && this.dirty && !reset
      this.snapshot = value
      if (!preserve) { this.draft = { ...value.config }; this.revision = value.revision }
    },
    async refresh() {
      if (!this.active || this.busy) return
      clearTimeout(this.timer); this.controller?.abort()
      const generation = ++this.generation
      this.controller = new AbortController()
      try {
        const value = await requestJson('./api/galaxy/hotspot', { request: { signal: this.controller.signal } })
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
    async save() {
      if (!this.active || this.busy || !this.snapshot?.editable || !this.passwordValid) return
      const feedback = beginSave()
      clearTimeout(this.timer); this.controller?.abort()
      const generation = ++this.generation
      this.controller = new AbortController(); this.busy = true; this.error = ''; this.saveError = ''; this.notice = ''
      try {
        const value = await requestJson('./api/galaxy/hotspot', { request: { signal: this.controller.signal,
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ revision: this.revision, config: this.draft }),
        } })
        if (!this.active || generation !== this.generation) return
        this.adopt(value, true)
        this.notice = value.config.enabled ? 'Settings saved. Join the network once its status is active.' : 'Hotspot disabled.'
        finishSave(feedback, true)
      } catch (error) {
        finishSave(feedback, false)
        if (!this.active || generation !== this.generation) return
        if (error.status === 401) { this.stop(); this.unauthorized(); return }
        this.saveError = `${error.message} If Wi-Fi disconnected, reconnect and reload to check whether settings saved.`
      } finally {
        if (this.active && generation === this.generation) { this.busy = false; this.timer = setTimeout(() => this.refresh(), 1000) }
      }
    },
  },
  template: `<section class="gx-card gx-settings__section gx-hotspot"><div class="gx-section__header"><i class="bi bi-broadcast-pin" aria-hidden="true"></i><span class="gx-section__title">Galaxy Wi-Fi Hotspot</span><span v-if="snapshot" class="gx-chip">{{ snapshot.config.ssid }}</span></div>
    <GxState v-if="mode !== 'local'">Connect to your comma to configure the hotspot.</GxState>
    <GxState v-else-if="!snapshot && !error" loading>Loading hotspot settings…</GxState>
    <template v-if="snapshot && draft">
      <GxNotice v-if="['starting', 'error', 'unavailable'].includes(snapshot.state)" class="gx-inset" :busy="snapshot.state === 'starting'" :tone="['error', 'unavailable'].includes(snapshot.state) ? 'warn' : 'info'">Status: {{ snapshot.state }} {{ snapshot.reason }}</GxNotice>
      <GalaxySettingRow feedback="draft" :row="switchRow('Broadcast hotspot automatically', draft.enabled, snapshot.config.ssid + ' · Status: ' + snapshot.state + (snapshot.reason ? ' (' + snapshot.reason + ')' : ''))" :index="0"
        :busy="busy" :disabled="!snapshot.editable" :save-value="(index, value) => { draft.enabled = value === 'On' }" />
      <div class="gx-row gx-hotspot__row">
        <div class="gx-row__info">
          <label for="gx-hotspot-password" class="gx-row__label">Password</label>
          <span class="gx-row__desc" :class="{ 'gx-note--danger': !passwordValid }">12–63 characters without spaces, or leave blank to generate one.</span>
        </div>
        <div class="gx-actions gx-hotspot__controls">
          <input id="gx-hotspot-password" class="gx-field gx-hotspot__field" :type="showPassword ? 'text' : 'password'" v-model="draft.password" :disabled="busy || !snapshot.editable" maxlength="63" autocomplete="new-password" placeholder="Blank generates a password" @input="saveError = ''" />
          <button type="button" class="gx-icon-btn" :aria-label="showPassword ? 'Hide password' : 'Show password'" :aria-pressed="showPassword" :disabled="busy || !snapshot.editable" @click="showPassword = !showPassword"><i class="bi" :class="showPassword ? 'bi-eye-slash' : 'bi-eye'" aria-hidden="true"></i></button>
          <button class="gx-btn" type="button" :disabled="!dirty || busy || !snapshot.editable || !passwordValid" @click="save">{{ busy ? 'Saving…' : 'Save hotspot' }}</button>
        </div>
      </div>
      <div class="gx-row">
        <div class="gx-row__info">
          <span class="gx-row__label">Local Galaxy address</span>
          <span class="gx-row__desc">Join {{ snapshot.config.ssid }} (keep connected if warned of no Internet), then open or bookmark this link.</span>
        </div>
        <a class="gx-btn gx-btn--tonal" :href="snapshot.url">Open {{ snapshot.url }}</a>
      </div>
      <details class="gx-hotspot__details gx-starport-wiki">
        <summary>How hotspot access works</summary>
        <ul class="gx-install-benefits">
          <li><i class="bi bi-power" aria-hidden="true"></i><span>Starts automatically when enabled. Needs the comma on and Wi-Fi available.</span></li>
          <li><i class="bi bi-wifi" aria-hidden="true"></i><span>Works standalone, or shares the channel of connected Wi-Fi or Android Auto.</span></li>
          <li><i class="bi bi-phone" aria-hidden="true"></i><span>Local access without Internet; no Internet sharing.</span></li>
          <li><i class="bi bi-arrow-repeat" aria-hidden="true"></i><span>Network or channel changes may interrupt access. Changing the password or disabling the hotspot disconnects phones.</span></li>
          <li><i class="bi bi-globe" aria-hidden="true"></i><span>The public galaxy.firestar.link address still needs Internet and its tunnel.</span></li>
        </ul>
      </details>
    </template>
    <GxNotice v-if="error || saveError" class="gx-inset" tone="danger">{{ saveError || error }}</GxNotice><GxNotice v-if="notice" class="gx-inset" tone="success">{{ notice }}</GxNotice>
  </section>`,
}
