import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { MaintenanceGroup } from "./maintenance-group.js"
import { requestJson } from "./startup.js"

const CONFIRMATION = "ERASE DEVICE"

export const FactoryReset = {
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
    async startReset() {
      if (this.busy || this.requested || !this.confirmed) return
      this.busy = true
      this.error = ""
      try {
        const result = await requestJson("./api/software/action", {
          request: { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "factory-reset", confirmation: this.typed }) },
        })
        if (result?.resetRequested !== true) throw new Error()
        this.requested = true
        this.confirmOpen = false
      } catch (error) {
        if (error.status === 401) this.unauthorized()
        else this.error = error.status ? error.message : "Check the comma's screen before trying again."
      } finally { this.busy = false }
    },
  },
  template: `
    <div class="gx-maintenance__group gx-factory-reset">
      <MaintenanceGroup title="Danger zone" description="Factory reset the comma and return to initial setup. Requires a direct local connection and a parked vehicle.">
        <button type="button" class="gx-btn gx-btn--danger" :disabled="requested" @click="review">
          <i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i> Factory Reset (Save Me)
        </button>
      </MaintenanceGroup>
      <GxNotice v-if="requested" tone="danger">The comma is restarting. Confirm the erase on the device screen to continue.</GxNotice>
      <GxDialog v-if="confirmOpen" labelledby="gx-factory-reset-title" :alert="true" @close="!busy && (confirmOpen=false)">
        <h3 id="gx-factory-reset-title">Factory reset this comma?</h3>
        <p>This erases internal user data, including routes, settings, calibration, downloaded maps and models, backups, and pairing. Back up anything you need and disconnect removable drives you want to keep.</p>
        <p>Galaxy will disconnect. Confirm the erase on the comma, then reinstall StarPilot from setup. AGNOS, the separate system cache, and cloud copies are not erased.</p>
        <label for="gx-erase-device">Type ERASE DEVICE to continue</label>
        <input id="gx-erase-device" class="gx-field" v-model="typed" autocomplete="off" autocapitalize="off" spellcheck="false" :disabled="busy">
        <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
        <div class="gx-actions">
          <button type="button" class="gx-btn gx-btn--tonal" @click="confirmOpen=false" :disabled="busy">Cancel</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="startReset" :disabled="busy || !confirmed">Restart to factory reset</button>
        </div>
      </GxDialog>
    </div>`,
}
