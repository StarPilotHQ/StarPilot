import { GxDialog } from './dialog.js'
import { GxNotice } from './notice.js'
import { requestJson } from './startup.js'

export const MAX_TUNE_BYTES = 64 * 1024
export function parseTune(text) {
  if (new TextEncoder().encode(text).length > MAX_TUNE_BYTES) throw new Error('Tune backup is too large.')
  let value
  try { value = JSON.parse(text) } catch { throw new Error('Choose a valid JSON tune backup.') }
  if (value?.format !== 'starpilot-flm-tune' || ![1, 2].includes(value.version) || !Array.isArray(value.manual) || value.manual.length > 30 ||
      !value.learned || typeof value.learned !== 'object' || Array.isArray(value.learned) || Object.keys(value.learned).length > 8 ||
      typeof value.vehicle !== 'string' || value.vehicle.length > 128) throw new Error('Choose a supported FLM tune backup.')
  return value
}

export const FlmBackup = {
  name: 'FlmBackup', components: { GxDialog, GxNotice },
  props: { unauthorized: { type: Function, required: true }, parked: { type: Boolean, default: false }, bluetooth: { type: Boolean, default: false } },
  emits: ['restored'],
  data: () => ({ busy: false, draft: null, mode: 'learned', pending: null, error: '', notice: '' }),
  methods: {
    async request(path, body) {
      try { return await requestJson(path, { timeout: 30000, request: body === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } }) }
      catch (error) { if (error.status === 401) this.unauthorized();throw error }
    },
    async downloadTune() {
      if (this.busy || this.bluetooth) return
      this.busy = true;this.error = this.notice = ''
      try {
        const text = JSON.stringify(await this.request('./api/flm/backup'))
        parseTune(text)
        const file = new File([text], 'starpilot-flm-tune.json', { type: 'application/json' })
        const url = URL.createObjectURL(file), link = document.createElement('a')
        try { link.href = url;link.download = file.name;document.body.append(link);link.click() }
        finally { link.remove();setTimeout(() => URL.revokeObjectURL(url), 1000) }
        this.notice = 'Tune backup download started.'
      } catch (error) { this.error = error.message || 'Could not download the tune.' }
      finally { this.busy = false }
    },
    async chooseFile(event) {
      const file = event.target.files?.[0];event.target.value = ''
      if (!file || this.busy || !this.parked || this.bluetooth) return
      this.error = this.notice = '';this.pending = null
      try {
        if (file.size > MAX_TUNE_BYTES) throw new Error('Tune backup is too large.')
        this.draft = parseTune(await file.text());this.mode = Object.keys(this.draft.learned).length ? 'learned' : 'manual'
      } catch (error) { this.draft = null;this.error = error.message || 'Could not read the backup.' }
    },
    async review() {
      if (this.busy || !this.draft || !this.parked || this.bluetooth) return
      this.busy = true;this.error = ''
      try {
        const result = await this.request('./api/settings/flm-backup/preview', { backup: this.draft, mode: this.mode })
        if (typeof result?.intent !== 'string' || typeof result.question !== 'string' || !Number.isSafeInteger(result.count) ||
            !Array.isArray(result.changes) || result.changes.length !== result.count) throw new Error('Restore preview is unavailable.')
        this.pending = result
      } catch (error) { this.error = error.message || 'Could not review this backup.' }
      finally { this.busy = false }
    },
    async restore() {
      if (this.busy || !this.pending || !this.parked || this.bluetooth) return
      const intent = this.pending.intent;this.busy = true;this.error = this.notice = ''
      try {
        const result = await this.request('./api/settings/flm-backup/confirm', { intent, confirmed: true })
        if (result.complete !== true) throw new Error(result.error || 'Restore stopped. Refresh your tune before retrying.')
        this.pending = this.draft = null;this.notice = result.manual ? 'Tune recovered. Manual values stay locked; other settings can keep learning.' : result.restored ? `${result.restored} ${result.restored === 1 ? 'setting' : 'settings'} restored. Current application is shown separately.` : 'Your saved settings already match this backup.'
        this.$emit('restored')
      } catch (error) { this.pending = null;this.error = error.message || 'Could not confirm the restore. Refresh your tune before retrying.';this.$emit('restored') }
      finally { this.busy = false }
    },
  },
  template: `<details class="gx-card gx-tuner__section gx-tuner__disclosure" data-role="flm-backup"><summary>Back up or restore your tune</summary>
    <p>Download manual sliders and saved Auto Learn setups as a backup file. Import reviews compatibility before anything changes.</p>
    <p v-if="bluetooth">Connect over Wi-Fi to transfer tune files.</p><div v-else class="gx-actions">
      <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="downloadTune()">Download tune</button>
      <button type="button" class="gx-btn gx-btn--tonal" :disabled="busy || !parked" @click="$refs.file.click()">Import tune</button>
      <input ref="file" class="gx-sr-only" type="file" accept=".json,application/json" aria-label="Choose FLM tune backup" @change="chooseFile"></div>
    <p v-if="!parked">Park and switch off the vehicle to restore settings.</p><p v-if="busy" role="status">Working…</p>
    <GxNotice v-if="notice" tone="success">{{ notice }}</GxNotice><GxNotice v-if="error" tone="danger">{{ error }}</GxNotice>
    <GxDialog v-if="draft && !pending" labelledby="flm-import-title" @close="!busy && (draft=null)"><h3 id="flm-import-title">Review imported tune</h3>
      <p>{{ draft.vehicle.replaceAll('_', ' ') }} · {{ draft.manual.length }} manual {{ draft.manual.length === 1 ? 'setting' : 'settings' }} · {{ Object.keys(draft.learned).length }} Auto Learn setups</p>
      <div class="gx-flm-backup-choices" role="radiogroup" aria-label="Restore section"><label><input name="flm-import-mode" type="radio" value="learned" v-model="mode" :disabled="!Object.keys(draft.learned).length || busy"> Auto Learn setup</label>
      <label><input name="flm-import-mode" type="radio" value="manual" v-model="mode" :disabled="!draft.manual.length || busy"> Manual sliders</label>
      <label><input name="flm-import-mode" type="radio" value="recovery" v-model="mode" :disabled="draft.version !== 2 || busy"> Recover as manual tune</label></div>
      <p>Auto Learn setup restores a setup this comma already knows. On a wiped or replacement comma, choose Recover as manual tune. Recovered manual values stay locked; accepted findings can keep learning.</p>
      <GxNotice v-if="error" tone="danger">{{ error }}</GxNotice><div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="draft=null">Cancel</button>
      <button type="button" class="gx-btn" :disabled="busy || !parked" @click="review">Review restore</button></div></GxDialog>
    <GxDialog v-if="pending" labelledby="flm-restore-title" @close="!busy && (pending=null)"><h3 id="flm-restore-title">Restore this tune?</h3>
      <p>{{ pending.question }}</p><p v-if="busy" role="status">Restoring…</p><p>{{ pending.count }} {{ pending.count === 1 ? 'setting' : 'settings' }} would change.</p>
      <details><summary>Changes</summary><p v-for="(row, index) in pending.changes" :key="index">{{ row[0] }}: {{ row[1] ?? 'not saved' }} → {{ row[2] }}</p></details>
      <div class="gx-actions"><button type="button" class="gx-btn gx-btn--tonal" :disabled="busy" @click="pending=null">Back</button>
      <button type="button" class="gx-btn" :disabled="busy || !parked" @click="restore">Restore settings</button></div></GxDialog></details>`,
}
