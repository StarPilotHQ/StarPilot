"""Run browser migration regressions with Node without native device dependencies."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is required for browser module tests')
class TestBrowserMigration(unittest.TestCase):
  def run_node(self, source):
    result = subprocess.run(['node', '--experimental-vm-modules', '--input-type=module', '-e', source],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_cleanup_preserves_pairing_other_caches_and_worker(self):
    self.run_node(r'''
import assert from "node:assert/strict"
import { retireLegacyPhone } from "./web/js/connection-migration.js"
const caches = new Map([["galaxy-offline-v1", "old shell"], ["map-downloads", "maps"], ["other", "keep"]])
const storage = new Map([["galaxy-auth", "paired"], ["galaxy-offline", "old"], ["unrelated", "keep"]])
let updates = 0
const target = {
  location: { href: "https://galaxy.example/ExistingGalaxy01/" },
  document: { get cookie() { return "galaxy_session=keep" }, set cookie(_) { assert.fail("pairing cookie changed") } },
  localStorage: { removeItem: key => storage.delete(key), clear: () => assert.fail("storage cleared") },
  caches: { delete: async name => caches.delete(name) },
  navigator: { serviceWorker: { getRegistration: async url => ({
    active: { scriptURL: url }, update: async () => { updates++ },
    unregister: () => assert.fail("push registration removed"),
  }) } },
}
await retireLegacyPhone(target)
assert.deepEqual([...caches.keys()], ["map-downloads", "other"])
assert.deepEqual([...storage.keys()], ["galaxy-auth", "unrelated"])
assert.equal(target.document.cookie, "galaxy_session=keep")
assert.equal(updates, 1)
await retireLegacyPhone(target)
assert.equal(updates, 2)
target.caches.delete = async () => { throw new Error("cache denied") }
await retireLegacyPhone(target)
assert.equal(updates, 3)
await retireLegacyPhone({})
''')

  def test_boot_opens_after_migration_timeout_or_failure(self):
    self.run_node(r'''
import assert from "node:assert/strict"
import fs from "node:fs"
import vm from "node:vm"
const source = fs.readFileSync("./web/js/boot.js", "utf8")
for (const mode of ["import-hangs", "cleanup-hangs", "import-rejects", "success"]) {
  const timers = new Map()
  let nextTimer = 0, appLoads = 0, cleaned = false
  const context = vm.createContext({
    setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, ms }); return id },
    clearTimeout: id => timers.delete(id), document: { querySelector: () => ({}) },
    location: { reload: () => assert.fail("unexpected reload") },
  })
  async function makeModule(values) {
    const module = new vm.SyntheticModule(Object.keys(values), function() {
      for (const [key, value] of Object.entries(values)) this.setExport(key, value)
    }, { context })
    await module.link(() => {})
    await module.evaluate()
    return module
  }
  const boot = new vm.SourceTextModule(source, { context, importModuleDynamically: async name => {
    if (name === "./connection-migration.js") {
      if (mode === "import-hangs") return new Promise(() => {})
      if (mode === "import-rejects") throw new Error("network failure")
      return makeModule({ retireLegacyPhone: async () => {
        if (mode === "cleanup-hangs") return new Promise(() => {})
        cleaned = true
      } })
    }
    assert.equal(name, "./app.js")
    if (mode === "success") assert.equal(cleaned, true)
    appLoads++
    return makeModule({})
  } })
  await boot.link(() => makeModule({
    savedUpdateRestart: () => false, requestJson: async () => ({}), bootScreenMode: () => "opening",
    dismissBootScreen: () => {}, showBootScreen: () => {}, showRestartScreen: () => {},
  }))
  await boot.evaluate()
  await new Promise(setImmediate)
  if (mode.endsWith("hangs")) {
    assert.equal(appLoads, 0)
    const timeout = [...timers.values()].find(timer => timer.ms === 1500)
    assert.ok(timeout, "timeout must include loading the migration module")
    timeout.fn()
    await new Promise(setImmediate)
  }
  assert.equal(appLoads, 1, mode)
}
''')


if __name__ == '__main__':
  unittest.main()
