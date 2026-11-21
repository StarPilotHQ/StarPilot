import assert from 'node:assert/strict'
import { File } from 'node:buffer'
import { FlmBackup } from '../web/js/flm-backup.js'

globalThis.File = File
const backup = { format: 'starpilot-flm-tune', version: 2, vehicle: 'test', manual: [], learned: {} }
let downloads = 0, fetched = 0
const link = { click() { downloads++ }, remove() {} }
globalThis.document = { body: { append() {} }, createElement: () => link }
const vm = { busy: false, bluetooth: false, error: '', notice: '', request: async () => { fetched++; return backup } }
await FlmBackup.methods.downloadTune.call(vm)
assert.equal(vm.notice, 'Tune backup download started.')
assert.equal(downloads, 1)
assert.equal(link.download, 'starpilot-flm-tune.json')
assert.deepEqual(await (await fetch(link.href)).json(), backup)
vm.bluetooth = true
await FlmBackup.methods.downloadTune.call(vm)
assert.equal(fetched, 1)
assert.equal(downloads, 1)
assert.doesNotMatch(FlmBackup.template, /Share tune|exportTune|canShare/)
assert.equal(FlmBackup.methods.exportTune, undefined)
console.log('Tune download preserves backup contents and transfer guards; sharing is removed')
