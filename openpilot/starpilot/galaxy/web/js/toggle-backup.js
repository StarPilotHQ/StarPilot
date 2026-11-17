import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { requestJson } from "./startup.js"
import { decodeLayoutBackup, MAX_LAYOUT_BACKUP_BYTES } from "./layout-backup.js"

export const MAX_TOGGLE_BACKUP_BYTES = 256 * 1024
export const ToggleBackup = {
  name: "ToggleBackup",
  components: { GxDialog, GxNotice },
  props: { unauthorized: { type: Function, required: true } },
  data: () => ({ busy: false, draft: null, resetDraft: null, legacy: false, error: "", notice: "" }),
  methods: {
    async request(path, payload) {
      try {
        return await requestJson(path, { timeout: 30000, request: payload === undefined ? {} : {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        } })
      } catch (error) { if (error.status === 401) this.unauthorized(); throw error }
    },
    async exportBackup() {
      if (this.busy) return
      this.busy = true; this.error = this.notice = ""
      try {
        const backup = await this.request("./api/settings/backup")
        const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }))
        const link = document.createElement("a")
        try {
          link.href = url; link.download = "toggle-backup.json"
          document.body.append(link); link.click(); link.remove()
        } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000) }
        this.notice = `${backup.settings.length} ${backup.settings.length === 1 ? "toggle" : "toggles"} backed up${backup.layout ? ", including layout and colors" : ""}.`
      } catch (error) { this.error = error.message || "Could not create toggle backup." }
      finally { this.busy = false }
    },
    async chooseFile(event) {
      const file = event.target.files?.[0]
      event.target.value = ""
      if (!file || this.busy) return
      this.error = this.notice = ""
      try {
        if (file.size > MAX_TOGGLE_BACKUP_BYTES) throw new Error("Toggle backup is too large.")
        const text = await file.text(), value = JSON.parse(text)
        if (!value || typeof value !== "object") throw new Error("Choose a Galaxy toggle backup.")
        this.legacy = value.format === "starpilot-visual-layout"
        if (this.legacy) {
          if (file.size > MAX_LAYOUT_BACKUP_BYTES) throw new Error("Layout backup is too large.")
          this.draft = await decodeLayoutBackup(text)
        } else {
          if (value.format !== "galaxy-toggles" || value.version !== 1 || !Array.isArray(value.settings)) throw new Error("Choose a Galaxy toggle backup.")
          this.draft = value
        }
      } catch (error) { this.draft = null; this.error = error.message || "Could not read toggle backup." }
    },
    async reviewResetDefaults() {
      if (this.busy) return
      this.busy = true; this.resetDraft = null; this.error = this.notice = ""
      try {
        const snapshot = await this.request("./api/settings/non-defaults")
        const selected = snapshot.rows.filter(row => row.resetAvailable === true).map(row => row.id)
        if (!selected.length) {
          if (snapshot.count) throw new Error("Park the vehicle before resetting available toggles.")
          this.notice = "All available toggles already use their defaults."
          return
        }
        this.resetDraft = await this.request("./api/settings/recovery/preview", { view: snapshot.view, selected })
      } catch (error) { this.error = error.message || "Could not prepare toggle reset." }
      finally { this.busy = false }
    },
    async resetDefaults() {
      if (this.busy || !this.resetDraft) return
      const pending = this.resetDraft
      this.resetDraft = null; this.busy = true; this.error = this.notice = ""
      try {
        const result = await this.request("./api/settings/recovery/confirm", { intent: pending.intent, confirmed: true })
        if (!result.saved) throw new Error(`${result.applied.length} settings reset. ${result.reason || "Review Toggles before retrying."}`)
        this.notice = `${result.applied.length} ${result.applied.length === 1 ? "setting" : "settings"} reset to default.`
      } catch (error) { this.error = error.message || "Could not reset toggles. Review Toggles before retrying." }
      finally { this.busy = false }
    },
    async restoreBackup() {
      if (this.busy || !this.draft) return
      const draft = this.draft, legacy = this.legacy
      this.draft = null; this.busy = true; this.error = this.notice = ""
      try {
        if (legacy) {
          const current = await this.request("./api/ui/layout")
          if (current.editable !== true) throw new Error("Park the vehicle before restoring the layout.")
          const result = await this.request("./api/ui/layout", { revision: current.revision, document: draft })
          if (!result.valid) throw new Error("Layout restore could not be verified.")
          this.notice = "Layout and colors restored."
        } else {
          const result = await this.request("./api/settings/restore", draft)
          if (!result.complete) throw new Error(result.error || "Restore could not be completed. Review Toggles.")
          this.notice = `${result.restored} ${result.restored === 1 ? "toggle" : "toggles"} restored${result.unchanged ? ` · ${result.unchanged} already matched` : ""}${result.skipped.length ? ` · ${result.skipped.length} unavailable settings skipped` : ""}${draft.layout ? ". Layout and colors restored" : ""}.`
        }
      } catch (error) { this.error = error.message || "Could not restore toggle backup." }
      finally { this.busy = false }
    },
  },
  template: `<section class="gx-card gx-panel gx-stack" :inert="busy" :aria-busy="busy || undefined">
    <div class="gx-section__header"><i class="bi bi-cloud-arrow-down" aria-hidden="true"></i><span class="gx-section__title">Toggle backup</span></div><p class="gx-note">Save your editable toggles, onroad layout and colors. Restore compatible settings while parked.</p>
    <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="exportBackup">Back up toggles</button>
      <button type="button" class="gx-btn gx-btn--tonal" @click="$refs.file.click()">Restore toggles</button>
      <button type="button" class="gx-btn gx-btn--tonal" @click="reviewResetDefaults">Reset toggles to default</button>
      <input ref="file" type="file" accept=".json,application/json" class="gx-sr-only" tabindex="-1" aria-label="Choose toggle backup" @change="chooseFile"></div>
    <p v-if="busy" class="gx-note" role="status"><span class="gx-spinner" aria-hidden="true"></span> Working…</p>
    <GxNotice v-if="notice" tone="success">{{ notice }}</GxNotice><GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
    <GxDialog v-if="draft" :inert="busy" labelledby="gx-toggle-restore-title" @close="draft=null"><h3 id="gx-toggle-restore-title">Restore {{ legacy ? 'layout' : 'toggles' }}?</h3>
      <p>{{ legacy ? 'Replace your saved layout and colors?' : 'Apply the compatible saved toggles, layout and colors from this backup? Unavailable settings will be skipped.' }} Keep the vehicle parked.</p>
      <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="draft=null">Cancel</button><button type="button" class="gx-btn" @click="restoreBackup">Restore</button></div>
    </GxDialog>
    <GxDialog v-if="resetDraft" :inert="busy" labelledby="gx-toggle-reset-title" @close="resetDraft=null">
      <h3 id="gx-toggle-reset-title">Reset toggles to default?</h3>
      <p>{{ resetDraft.question }}</p>
      <p>Keep the vehicle parked. Settings unavailable for this vehicle are left unchanged. Calibration, downloaded models, accounts and network credentials are kept.</p>
      <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="resetDraft=null">Cancel</button>
        <button type="button" class="gx-btn" @click="resetDefaults">Reset toggles</button></div>
    </GxDialog>
  </section>`,
}
