import { savedUpdateRestart } from "./update-restart.js"
import { requestJson } from "./startup.js"
import { bootScreenMode, dismissBootScreen, showBootScreen, showRestartScreen } from "./boot-screen.js"

if (savedUpdateRestart()) showRestartScreen()
let probe = null, sawOffline = false, opened = false
function reloadWhenBack() {
  if (probe !== null || opened) return
  probe = setTimeout(() => requestJson("./data/runtime.json")
    .then(() => true, () => false)
    .then(online => {
      probe = null
      if (opened) return
      if (online && sawOffline) location.reload()
      else { sawOffline ||= !online; reloadWhenBack() }
    }), 3000)
}
function failed(force = false) {
  reloadWhenBack()
  if (!force && bootScreenMode() !== "opening") return
  showBootScreen("failed", "Galaxy could not finish opening. Check your connection and try again.", true)
}
const timer = setTimeout(failed, 10000)
// Local pages use their local origin; public pages use the authenticated tunnel.
// Do not intercept fetch or ask a public page to probe private network addresses.
const migration = Promise.race([
  import("./connection-migration.js").then(module => module.retireLegacyPhone()),
  new Promise(resolve => setTimeout(resolve, 1500)),
]).catch(() => {})
migration.then(() => import("./app.js")).then(() => {
    clearTimeout(timer)
    if (document.querySelector("#galaxy-app .gx-app")) {
      opened = true; clearTimeout(probe); probe = null
      if (bootScreenMode() === "opening" || bootScreenMode() === "failed") dismissBootScreen()
    }
    else failed(true)
  }).catch(() => { clearTimeout(timer); failed(true) })
