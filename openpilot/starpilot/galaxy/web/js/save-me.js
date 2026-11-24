import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { MaintenanceGroup } from "./maintenance-group.js"
import { requestJson } from "./startup.js"

const CONFIRMATION = "ERASE STARPILOT DATA"

export const SaveMe = {
  components: { GxDialog, GxNotice, MaintenanceGroup },
  props: { unauthorized: { type: Function, required: true } },
  data: () => ({ confirmOpen: false, typed: "", busy: false, requested: false, error: "" }),
  computed: { confirmed() { return this.typed === CONFIRMATION } },
  methods: {
    review() {
      this.typed = ""
      this.error = ""
      this.confirmOpen = true
    },
    async erase() {
      if (this.busy || this.requested || !this.confirmed) return
      this.busy = true
      this.error = ""
      try {
        const result = await requestJson("./api/software/action", {
          request: { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "save-me", confirmation: this.typed }) },
        })
        if (result?.resetRequested !== true) throw new Error()
        this.requested = true
        this.confirmOpen = false
      } catch (error) {
        if (error.status === 401) this.unauthorized()
        else this.error = error.status ? error.message : "Connection lost. Check whether the comma is restarting before trying again."
      } finally { this.busy = false }
    },
  },
  template: `
    <div class="gx-maintenance__group gx-save-me">
      <MaintenanceGroup title="Danger zone" description="Erase StarPilot data and restart. Wi-Fi and installed software are kept.">
        <button type="button" class="gx-btn gx-btn--danger" :disabled="requested" @click="review">
          <i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i> Save Me
        </button>
      </MaintenanceGroup>
      <GxNotice v-if="requested" tone="danger">Save Me requested. Galaxy will disconnect during cleanup and restart.</GxNotice>
      <GxDialog v-if="confirmOpen" labelledby="gx-save-me-title" :alert="true" @close="!busy && (confirmOpen=false)">
        <h3 id="gx-save-me-title">Erase StarPilot data?</h3>
        <p>This permanently erases settings, calibration, driving routes, downloaded maps and models, themes, backups, and Galaxy pairing. Back up anything you need first.</p>
        <p>Park before continuing. Wi-Fi, AGNOS, and installed StarPilot stay in place. You will need to pair Galaxy again.</p>
        <label for="gx-erase-starpilot">Type ERASE STARPILOT DATA to continue</label>
        <input id="gx-erase-starpilot" class="gx-field" v-model="typed" autocomplete="off" autocapitalize="off" spellcheck="false" :disabled="busy">
        <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
        <div class="gx-actions">
          <button type="button" class="gx-btn gx-btn--tonal" @click="confirmOpen=false" :disabled="busy">Cancel</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="erase" :disabled="busy || !confirmed">Erase data and restart</button>
        </div>
      </GxDialog>
    </div>`,
}
