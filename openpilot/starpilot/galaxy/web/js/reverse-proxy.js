import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"

export const ReverseProxySettings = {
  components: { GxNotice },
  props: { unauthorized: Function, mode: String },
  data: () => ({ status: null, enabled: false, address: "", origin: "", busy: false, error: "", saved: false }),
  mounted() { if (this.mode === "local") this.load() },
  methods: {
    async load() {
      try {
        const data = await requestJson("./api/connect/proxy")
        this.status = data
        this.enabled = data.enabled
        this.address = data.address
        this.origin = data.origin
      } catch (error) { this.failed(error) }
    },
    failed(error) {
      if (error.status === 401) this.unauthorized()
      else this.error = error.message || "Reverse proxy settings are unavailable"
    },
    async save() {
      if (this.busy || !this.status?.canEdit) return
      if (!window.confirm(this.enabled ? "Allow this proxy and HTTPS origin? Proxy access requires your Galaxy password." :
        "Disable reverse proxy access? Direct IP access will remain available.")) return
      this.busy = true
      this.error = ""
      this.saved = false
      try {
        this.status = await requestJson("./api/connect/proxy", { request: { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: this.enabled,
            address: this.address.trim(), origin: this.origin.trim(), revision: this.status.revision, confirmed: true }) } })
        this.saved = true
      } catch (error) { this.failed(error) }
      finally { this.busy = false }
    },
  },
  template: `<section v-if="mode === 'local'" class="gx-card gx-settings__section">
    <div class="gx-section__header"><i class="bi bi-hdd-network" aria-hidden="true"></i><span class="gx-section__title">Reverse proxy</span></div>
    <form @submit.prevent="save">
      <fieldset :disabled="busy || !status?.canEdit" style="border:0; margin:0; padding:0; min-width:0">
        <div class="gx-row"><label for="gx-proxy-enabled" class="gx-row__info"><span class="gx-row__label">Enable reverse proxy</span></label>
          <label class="gx-switch"><input id="gx-proxy-enabled" type="checkbox" role="switch" v-model="enabled" aria-label="Enable reverse proxy"><span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></label></div>
        <div class="gx-row gx-row--stack"><label class="gx-field-group">Proxy IP address
          <input class="gx-field" v-model="address" placeholder="192.168.1.2" maxlength="64" :required="enabled" autocomplete="off" spellcheck="false"></label></div>
        <div class="gx-row gx-row--stack"><label class="gx-field-group">Public HTTPS origin
          <input class="gx-field" type="url" v-model="origin" placeholder="https://galaxy.example.com" maxlength="256" :required="enabled" autocomplete="off" spellcheck="false"></label></div>
        <div class="gx-panel gx-actions"><button class="gx-btn" type="submit" :disabled="enabled && !status?.passwordConfigured"><i class="bi bi-check-lg" aria-hidden="true"></i> Save</button></div>
      </fieldset>
    </form>
    <GxNotice v-if="status && !status.canEdit" tone="warn">Turn off the vehicle to change reverse proxy settings.</GxNotice>
    <GxNotice v-if="status && !status.passwordConfigured" tone="warn">Configure a Galaxy password on the device before enabling reverse proxy access.</GxNotice>
    <GxNotice v-if="saved" tone="info">Reverse proxy settings saved.</GxNotice>
    <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
  </section>`,
}
