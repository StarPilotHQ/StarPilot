import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { requestJson } from "./startup.js"

export const RouteCleanup = {
  components: { GxDialog, GxNotice },
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
    <section class="gx-card gx-panel gx-stack gx-route-cleanup" :inert="busy" :aria-busy="busy || undefined">
      <div class="gx-section__header"><i class="bi bi-trash3" aria-hidden="true"></i><span class="gx-section__title">Driving routes</span></div>
      <p class="gx-note">Remove all saved driving routes and their logs. This does not reset settings or software.</p>
      <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
      <GxNotice v-if="notice" tone="success">{{ notice }}</GxNotice>
      <div class="gx-actions"><button type="button" class="gx-btn gx-btn--danger" @click="confirmOpen=true" :disabled="busy">Delete all driving routes</button></div>
      <GxDialog v-if="confirmOpen" labelledby="gx-route-cleanup-title" :alert="true" @close="!busy && (confirmOpen=false)">
        <h3 id="gx-route-cleanup-title">Delete all driving routes?</h3>
        <p>This permanently removes every saved route, including preserved routes and their logs. Settings and software are not changed.</p>
        <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="confirmOpen=false" :disabled="busy">Cancel</button>
          <button type="button" class="gx-btn gx-btn--danger" @click="removeAll" :disabled="busy">Delete routes</button></div>
      </GxDialog>
    </section>`,
}
