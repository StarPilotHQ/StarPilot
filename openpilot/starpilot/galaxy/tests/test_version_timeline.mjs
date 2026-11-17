import assert from 'node:assert/strict'
import { compile } from '../web/vendor/vue/vue.esm-browser.js'
import { SoftwarePage } from '../web/js/software-status.js'

const rows = Array.from({ length: 20 }, (_, i) => ({ hash: (i + 1).toString(16).padStart(40, '0'),
  date: new Date(Date.UTC(2026, 9, 29 - i)).toISOString(), subject: `Version ${20 - i}` }))
const vm = { ...SoftwarePage.data(), initialChecking: false, mode: 'local',
  draftBranch: 'Dom', data: { installed: { branch: 'Dom', commit: rows[15].hash },
    updater: { state: 'idle', targetBranch: 'Dom', finalizedUpdateReady: true }, operations: {
      parked: true, canFastUpdate: true, canInstall: true, selectedTarget: 'Dom', availableBranches: ['Dom'],
      history: { installed: rows.slice(15), downloaded: rows.slice(3), currentReleaseNotes: 'Installed notes',
        downloadedReleaseNotes: 'Downloaded notes', downloadedCommits: [rows[3].hash, rows[4].hash], recent: { branch: 'Dom', head: rows[0].hash, entries: rows } } } } }
for (const [name, getter] of Object.entries(SoftwarePage.computed))
  Object.defineProperty(vm, name, { get: () => getter.call(vm) })
for (const [name, method] of Object.entries(SoftwarePage.methods)) vm[name] = method.bind(vm)

assert.equal(vm.shortCommit('1234567890abcdef'), '12345678')
assert.equal(vm.versionEntries.length, 20, 'merge overlapping sources without duplicates')
assert.equal(vm.visibleVersions.length, 4)
assert.equal(vm.visibleVersions[0].hash, rows[0].hash, 'keep newest at the top')
assert.equal(vm.visibleVersions.at(-2).hash, rows[15].hash, 'keep installed visible')
assert.equal(vm.visibleVersions.at(-1).hash, rows[16].hash, 'show one older version')
assert.equal(vm.hiddenNewerCount, 13)
assert.equal(vm.hiddenOlderCount, 3)
assert.equal(vm.selectedVersion.hash, rows[15].hash)
assert.equal(vm.selectedVersionAction, 'No changes selected')
assert.equal(vm.canInstallSelection, false)
assert.equal(vm.selectedReleaseNotes, 'Installed notes')

vm.version = rows[3].hash
assert.equal(vm.selectedVersionAction, 'Install selected version')
assert.equal(vm.selectedReleaseNotes, 'Downloaded notes')
assert.equal(vm.canInstallSelection, true)
vm.askSelectedVersion()
assert.deepEqual([vm.dialog.action, vm.dialog.selectedCommit, vm.dialog.expectedCommit],
  ['install', rows[3].hash, rows[15].hash])

vm.version = rows[0].hash
assert.equal(vm.selectedVersionAction, 'Download and install selected version')
assert.equal(vm.canInstallSelection, true)
assert.equal(vm.selectedReleaseNotes, null)
vm.askSelectedVersion()
assert.equal(vm.dialog.action, 'version')
assert.equal(vm.dialog.selectedCommit, rows[0].hash)

vm.data.updater.finalizedUpdateReady = false
vm.version = rows[3].hash
assert.equal(vm.selectedVersionAction, 'Install selected version', 'complete cached files remain downloaded when staging is not ready')
vm.version = rows[4].hash
assert.equal(vm.selectedVersionAction, 'Install selected version', 'cached commits need not be the prepared head')
vm.askSelectedVersion()
assert.equal(vm.dialog.action, 'version', 'cached versions still pass through pinned validation')
vm.newerVersionLimit += 10
assert.equal(vm.hiddenNewerCount, 3)
assert.equal(vm.visibleVersions.length, 14, 'newer paging must not reveal older rows')
assert.equal(vm.hiddenOlderCount, 3)
vm.newerVersionLimit += 10
vm.olderVersionLimit += 10
assert.equal(vm.visibleVersions.length, 20)
assert.equal(vm.hiddenOlderCount, 0)
vm.newerVersionLimit = 2
vm.olderVersionLimit = 1
assert.equal(vm.visibleVersions.length, 4)

// Separate gaps remain exact across multiple pages of ten.
const longRows = Array.from({length: 60}, (_, i) => ({hash:(i + 100).toString(16).padStart(40, '0'),date:'',subject:`Build ${i}`}))
vm.data.operations.history.recent.entries = longRows
vm.data.operations.history.installed = longRows.slice(35)
vm.data.installed.commit = longRows[35].hash
assert.equal(vm.hiddenNewerCount, 33)
assert.equal(vm.hiddenOlderCount, 23)
for (const remaining of [23, 13, 3, 0]) {
  const before = vm.visibleVersions.length
  const count = Math.min(10, vm.hiddenNewerCount)
  vm.newerVersionLimit += 10
  assert.equal(vm.visibleVersions.length - before, count)
  assert.equal(vm.hiddenNewerCount, remaining)
  assert.equal(vm.hiddenOlderCount, 23)
}
for (const remaining of [13, 3, 0]) {
  const before = vm.visibleVersions.length
  const count = Math.min(10, vm.hiddenOlderCount)
  vm.olderVersionLimit += 10
  assert.equal(vm.visibleVersions.length - before, count)
  assert.equal(vm.hiddenOlderCount, remaining)
}
vm.data.operations.history.recent.entries = rows
vm.data.operations.history.installed = rows.slice(15)
vm.data.installed.commit = rows[15].hash
vm.data.operations.history.recent.entries = rows.slice(0, 10)
vm.version = rows[19].hash
assert.equal(vm.canInstallSelection, true, 'older installed history remains selectable outside the recent list')
vm.data.operations.history.recent.entries = rows
vm.newerVersionLimit = 2
vm.olderVersionLimit = 1
vm.data.installed.commit = 'f'.repeat(40)
vm.version = ''
assert.equal(vm.visibleVersions.at(-1).hash, 'f'.repeat(40), 'retain installed outside fetched history on its own branch')
assert.equal(vm.selectedVersionAction, 'No changes selected')
vm.busy = true
vm.version = rows[0].hash
assert.equal(vm.canInstallSelection, false)
vm.busy = false
vm.data.operations.parked = false
assert.equal(vm.canInstallSelection, false)

compile(SoftwarePage.template, { decodeEntities: value => value })
console.log('Version timeline selection, limits, expansion, and template passed')

// Pin the actual previous installed commit with independent gaps above and below it.
vm.data.operations.parked = true
vm.data.operations.canRollback = true
vm.data.installed.commit = longRows[15].hash
vm.data.operations.history.recent.entries = longRows
vm.data.operations.history.installed = longRows.slice(15)
vm.data.operations.history.previous = longRows[40]
vm.newerVersionLimit = 2
vm.olderVersionLimit = 1
vm.collapsedOlderLimit = 0
vm.version = ''
assert.deepEqual(vm.visibleVersions.map(row => row.hash), [...longRows.slice(0, 2), longRows[15], longRows[40]].map(row => row.hash))
assert.equal(vm.hiddenNewerCount, 13)
assert.equal(vm.hiddenCollapsedOlderCount, 24)
assert.equal(vm.hiddenOlderCount, 19)
for (const remaining of [14, 4, 0]) {
  const before = vm.visibleVersions.length
  const count = Math.min(10, vm.hiddenCollapsedOlderCount)
  vm.collapsedOlderLimit += count
  assert.equal(vm.visibleVersions.length - before, count)
  assert.equal(vm.hiddenCollapsedOlderCount, remaining)
  assert.equal(vm.hiddenOlderCount, 19)
}
vm.olderVersionLimit += 10
assert.equal(vm.hiddenOlderCount, 9)
assert.equal(vm.visibleVersions.at(-1).hash, longRows[50].hash)
vm.version = longRows[40].hash
assert.equal(vm.selectedVersionAction, 'Install selected version')
assert.equal(vm.canInstallSelection, true)
vm.askSelectedVersion()
assert.equal(vm.dialog.action, 'rollback')
vm.data.operations.history.previous = longRows[0]
assert.equal(vm.visibleVersions.filter(row => row.hash === longRows[0].hash).length, 1, 'previous version may be newer after a rollback')

// Diverged branches contain neither installed history nor a previous-version anchor.
vm.data.operations.history.previous = { ...longRows[40], branch:'Dom' }
vm.draftBranch = 'StarPilot'
vm.data.operations.history.recent = {branch:'StarPilot',head:rows[0].hash,entries:rows.slice(0,10)}
vm.newerVersionLimit = 2
vm.version = ''
assert.equal(vm.hasInstalledVersion, false)
assert.equal(vm.listedPreviousVersion, null)
assert.deepEqual(vm.visibleVersions.map(row => row.hash), rows.slice(0,2).map(row => row.hash))
assert.equal(vm.hiddenNewerCount,8)
vm.newerVersionLimit += 10
assert.equal(vm.visibleVersions.length,10,'diverged history remains pageable without installed anchors')
vm.syncDraftBranch(vm.previousVersion.branch)
vm.version = vm.previousVersion.hash
assert.equal(vm.listedPreviousVersion.hash,vm.previousVersion.hash,'explicit local previous selection needs no metadata refresh')

// Glow only while a different selectable commit is waiting to be installed.
assert.equal(vm.selectionNeedsInstall, true)
vm.data.updater.state = 'updating...'
assert.equal(vm.selectionNeedsInstall, false)
vm.data.updater.state = 'idle'
vm.feed = {installBaseline:{}}
assert.equal(vm.selectionNeedsInstall, false)
vm.feed.installBaseline = null
assert.equal(vm.selectionNeedsInstall, true)
vm.busy = true
assert.equal(vm.selectionNeedsInstall, false)
vm.busy = false
vm.version = vm.data.installed.commit
assert.equal(vm.selectionNeedsInstall, false)
