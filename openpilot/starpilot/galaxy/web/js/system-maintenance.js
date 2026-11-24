import { ToggleBackup } from './toggle-backup.js'
import { RouteCleanup } from './route-cleanup.js'
import { SaveMe } from './save-me.js'

export const SystemMaintenance = {
  components: { ToggleBackup, RouteCleanup, SaveMe },
  props: { unauthorized: { type: Function, required: true } },
  template: `<details role="region" class="gx-card gx-maintenance" aria-label="Backup, reset and cleanup">
    <summary class="gx-section__header"><i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i><span class="gx-section__title">Backup, Reset &amp; Cleanup</span><i class="bi bi-chevron-down gx-maintenance__chevron" aria-hidden="true"></i></summary>
    <ToggleBackup :unauthorized="unauthorized" />
    <RouteCleanup :unauthorized="unauthorized" />
    <SaveMe :unauthorized="unauthorized" />
  </details>`,
}
