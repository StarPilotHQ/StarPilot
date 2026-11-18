import { GxCollapse } from './disclosure.js'
import { GxDialog } from "./dialog.js"
import { GxNotice } from "./notice.js"
import { requestJson } from "./startup.js"

export const RecordingActions = {
  components: { GxCollapse, GxDialog, GxNotice },
  props: { recording: { type: Object, required: true }, all: Boolean, compact: Boolean, collapsed: Boolean,
    unauthorized: { type: Function, required: true } },
  emits: ["changed"],
  data: () => ({ action: "", managementOpen: false, name: "", busy: false, error: "", includePreserved: false }),
  methods: {
    edit(action) {
      this.action = action
      this.name = this.recording.displayName || this.recording.routeId
      this.error = ""
    },
    async save(action = this.action) {
      if (this.busy) return
      this.busy = true
      this.error = ""
      const payload = this.all ? { action: "delete-all", confirmed: true, includePreserved: this.includePreserved } : { action, routeId: this.recording.routeId,
        ...(action === "rename" ? { name: this.name } : action === "preserve" ? { preserved: !this.recording.preserved } : { confirmed: true }) }
      try {
        await requestJson("./api/recordings/action", {
          request: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
        })
        this.action = ""
        this.$emit("changed", action)
      } catch (error) {
        if (error.status === 401) this.unauthorized()
        else this.error = error.message || "Recording action failed. Refresh the library before trying again."
      } finally { this.busy = false }
    },
  },
  template: `
    <div class="gx-recordings__actions gx-actions" :inert="busy" :aria-busy="busy || undefined">
      <template v-if="!all">
        <button class="gx-icon-btn" :aria-label="recording.preserved ? 'Unpreserve drive' : 'Preserve drive'" :title="recording.preserved ? 'Unpreserve' : 'Preserve'" @click="save('preserve')"><i class="bi" :class="recording.preserved ? 'bi-heart-fill' : 'bi-heart'"></i></button>
        <slot></slot>
        <button v-if="!compact && collapsed" class="gx-icon-btn" aria-label="More route actions" title="More route actions" :aria-expanded="managementOpen" @click="managementOpen=!managementOpen" @keydown.esc="managementOpen=false"><i class="bi bi-three-dots" aria-hidden="true"></i></button>
        <GxCollapse><div v-if="!compact && (!collapsed || managementOpen)" class="gx-actions">
        <button class="gx-icon-btn" aria-label="Rename drive" title="Rename" @click="edit('rename')"><i class="bi bi-pencil"></i></button>
        <button class="gx-icon-btn gx-recordings__danger" aria-label="Delete drive" title="Delete" @click="edit('delete')"><i class="bi bi-trash"></i></button>
        </div></GxCollapse>
      </template>
      <template v-else>
        <button class="gx-btn gx-btn--tonal" @click="includePreserved=false; edit('delete')">Delete non-preserved</button>
        <button class="gx-btn gx-btn--tonal gx-recordings__danger" @click="includePreserved=true; edit('delete')">Delete all including preserved</button>
      </template>
      <GxNotice tone="danger" v-if="error && !action">{{ error }}</GxNotice>
      <GxDialog v-if="action" :inert="busy" labelledby="gx-recording-action-title" :alert="action === 'delete'" @close="!busy && (action='')">
        <form class="gx-stack" @submit.prevent="save()">
          <h3 id="gx-recording-action-title">{{ action === 'rename' ? 'Rename recording' : 'Delete recording?' }}</h3>
          <label v-if="action === 'rename'">Name<input class="gx-field" v-model="name" maxlength="128" required /></label>
          <p v-else>Delete {{ recording.displayName || recording.routeId }}? This cannot be undone.</p>
          <p v-if="all">{{ includePreserved ? "Preserved drives will also be deleted." : "Preserved drives will be kept." }}</p>
          <GxNotice tone="danger" v-if="error">{{ error }}</GxNotice>
          <div class="gx-settings__controls gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="action=''">Cancel</button>
            <button class="gx-btn">{{ action === 'rename' ? 'Save name' : 'Delete recording' }}</button></div>
        </form>
      </GxDialog>
    </div>`,
}
