import { MaintenanceGroup } from "./maintenance-group.js"
import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { requestJson } from "./startup.js"

export const RouteCleanup = {
  components: { GxDialog, GxNotice, MaintenanceGroup },
  props: { unauthorized: { type: Function, required: true } },
  data: () => ({ confirmOpen: false, busy: false, error: "", notice: "" }),
  methods: {
    async removeAll() {
      if (this.busy) return
      this.busy = true
      this.error = ""
      this.notice = ""
      try {
        const result = await requestJson("./api/recordings/action", {
          request: { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "delete-all", confirmed: true, includePreserved: true }) },
        })
        this.confirmOpen = false
        const count = Number.isSafeInteger(result?.deleted) ? result.deleted : 0
        this.notice = `${count} ${count === 1 ? "driving route" : "driving routes"} deleted.`
      } catch (error) {
        if (error.status === 401) this.unauthorized()
        else this.error = error.message || "Driving routes could not be deleted."
      } finally { this.busy = false }
    },
  },
  template: `
    <div class="gx-maintenance__group gx-route-cleanup" :inert="busy" :aria-busy="busy || undefined">
      <MaintenanceGroup title="Driving routes" description="Delete all saved routes and logs, including preserved routes.">
        <button type="button" class="gx-btn gx-btn--danger" :disabled="busy" @click="confirmOpen=true">Delete all driving routes</button>
      </MaintenanceGroup>
      <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
      <GxNotice v-if="notice" tone="success">{{ notice }}</GxNotice>
      <GxDialog v-if="confirmOpen" labelledby="gx-route-cleanup-title" :alert="true" @close="!busy && (confirmOpen=false)">
        <h3 id="gx-route-cleanup-title">Delete all driving routes?</h3>
        <p>This permanently removes every saved route, including preserved routes and their logs. Settings and software are not changed.</p>
        <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="confirmOpen=false" :disabled="busy">Cancel</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="removeAll" :disabled="busy">Delete routes</button></div>
      </GxDialog>
    </div>`,
}
