import { SettingsFeed, settingsBusy } from './settings.js'
import { ToggleBackup } from './toggle-backup.js'
import { RouteCleanup } from './route-cleanup.js'
import { MaintenanceGroup } from './maintenance-group.js'
import { GxDialog } from './dialog.js'
import { GxNotice } from './notice.js'

export const SystemMaintenance = {
  components: { ToggleBackup, RouteCleanup, MaintenanceGroup, GxDialog, GxNotice },
  props: { unauthorized: { type: Function, required: true } },
  data() {
    const state = { status: 'idle', data: null, pending: null, error: '' }
    return { state, feed: null }
  },
  computed: { busy() { return settingsBusy(this.state) } },
  mounted() {
    this.feed = new SettingsFeed({ publish: update => Object.assign(this.state, update), unauthorized: this.unauthorized })
    this.feed.start('resets')
  },
  beforeUnmount() { this.feed?.stop() },
  template: `<details role="region" class="gx-card gx-maintenance" aria-label="Backup, reset and cleanup">
    <summary class="gx-section__header"><i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i><span class="gx-section__title">Backup, Reset &amp; Cleanup</span><i class="bi bi-chevron-down gx-maintenance__chevron" aria-hidden="true"></i></summary>
    <ToggleBackup :unauthorized="unauthorized" />
    <div class="gx-maintenance__group" :aria-busy="busy || undefined">
      <p v-if="!state.data && !state.error" class="gx-note gx-maintenance__feedback" role="status">Loading device resets…</p>
      <MaintenanceGroup v-if="state.data" title="Device resets" description="Clear saved driver monitoring or calibration. Relearns on the next drive.">
        <button v-for="(row, index) in state.data.rows" :key="row.label" type="button" class="gx-btn gx-btn--tonal"
          :disabled="busy || !!state.error || !row.available" @click="feed.preview(index, 0)">{{ row.label }}</button>
      </MaintenanceGroup>
      <GxNotice v-if="state.error" tone="danger">{{ state.error }}</GxNotice>
    </div>
    <RouteCleanup :unauthorized="unauthorized" />
    <GxDialog v-if="state.pending" labelledby="gx-maintenance-confirm-title" @close="feed.cancel()">
      <h3 id="gx-maintenance-confirm-title">Confirm reset</h3><p>{{ state.pending.question }}</p>
      <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" @click="feed.cancel()">Cancel</button>
        <button type="button" class="gx-btn" @click="feed.confirm()">Reset</button></div>
    </GxDialog>
  </details>`,
}
