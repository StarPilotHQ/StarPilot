import assert from "node:assert/strict"
import { createHash, webcrypto } from "node:crypto"
import { decodeLayoutBackup, encodeLayoutBackup, sha256 } from "../web/js/layout-backup.js"
import { ToggleBackup } from "../web/js/toggle-backup.js"

const layout = { version: 4, palette: { text: "#FFFFFFFF" }, layouts: { large: {}, compact: {} } }
const encoded = await encodeLayoutBackup(layout, webcrypto)
assert.deepEqual(await decodeLayoutBackup(encoded, webcrypto), layout)
assert.deepEqual(await decodeLayoutBackup(encoded, {}), layout) // Plain HTTP has no crypto.subtle.
assert.equal(await encodeLayoutBackup(layout, {}), encoded)
for (const value of ["", "abc", "a".repeat(55), "a".repeat(56), "a".repeat(64), "a".repeat(1024),
  "Café 🚗", "x".repeat(16384)]) {
  const expected = createHash("sha256").update(value).digest("hex")
  assert.equal(await sha256(value, {}), expected)
  assert.equal(await sha256(value, webcrypto), expected)
}
assert.deepEqual(Object.keys(JSON.parse(encoded)), ["format", "version", "document", "sha256"])
assert.doesNotMatch(encoded, /calibration|credential|model/i)

for (const invalid of [
  encoded.replace("#FFFFFFFF", "#000000FF"),
  encoded.replace('"version":1', '"version":2'),
  encoded.replace('"sha256":', '"extra":1,"sha256":'),
  encoded.trimEnd(),
  "x".repeat(32769),
]) {
  await assert.rejects(decodeLayoutBackup(invalid, webcrypto))
}

// The page obtains a fresh revision immediately before the existing owner performs the restore.
const calls = []
const context = { draft: layout, legacy: true, busy: false, notice: "", error: "",
  request: async (...args) => { calls.push(args); return args.length === 2 ? {valid:true} : {editable:true,revision:"current-revision"} } }
await ToggleBackup.methods.restoreBackup.call(context)
assert.deepEqual(calls, [["./api/ui/layout"], ["./api/ui/layout", {document:layout, revision:"current-revision"}]])
assert.equal(context.notice,"Layout and colors restored.")
assert.equal(context.draft,null)
let writes = 0
const moving = { draft:layout,legacy:true,busy:false,notice:"",error:"",
  request:async () => {writes++;return {editable:false,revision:"old"}} }
await ToggleBackup.methods.restoreBackup.call(moving)
assert.equal(writes,1)
assert.match(moving.error,/Park/)
console.log("Portable layout files and legacy restore through Toggle backup passed")

assert.match(ToggleBackup.template, /Reset toggles to default/)
assert.ok(ToggleBackup.template.indexOf('Restore toggles') < ToggleBackup.template.indexOf('Reset toggles to default'))
const resetCalls = []
const reset = { ...ToggleBackup.data(), request: async (path, body) => {
  resetCalls.push([path, body])
  if (path.endsWith('non-defaults')) return {view:'fresh-view', count:3, rows:[
    {id:0,resetAvailable:true}, {id:1,resetAvailable:false}, {id:2,resetAvailable:true}]}
  if (path.endsWith('preview')) return {intent:'fresh-intent', question:'Reset 2 settings?'}
  return {saved:true,applied:['First','Second']}
} }
await ToggleBackup.methods.reviewResetDefaults.call(reset)
assert.deepEqual(resetCalls, [['./api/settings/non-defaults',undefined],
  ['./api/settings/recovery/preview',{view:'fresh-view',selected:[0,2]}]])
assert.equal(reset.resetDraft.intent,'fresh-intent')
assert.equal(resetCalls.some(([path]) => path.endsWith('confirm')),false,'Review must not change settings')
await Promise.all([ToggleBackup.methods.resetDefaults.call(reset),ToggleBackup.methods.resetDefaults.call(reset)])
assert.deepEqual(resetCalls.at(-1),['./api/settings/recovery/confirm',{intent:'fresh-intent',confirmed:true}])
assert.equal(resetCalls.filter(([path]) => path.endsWith('confirm')).length,1)
assert.equal(reset.resetDraft,null)
assert.equal(reset.notice,'2 settings reset to default.')

const alreadyDefault = {...ToggleBackup.data(),request:async path => {
  assert.equal(path,'./api/settings/non-defaults'); return {count:0,rows:[]}
} }
await ToggleBackup.methods.reviewResetDefaults.call(alreadyDefault)
assert.match(alreadyDefault.notice,/already use their defaults/)
assert.equal(alreadyDefault.resetDraft,null)

const cannotReset = {...ToggleBackup.data(),request:async path => {
  assert.equal(path,'./api/settings/non-defaults');return {count:1,rows:[{id:0,resetAvailable:false}]}
} }
await ToggleBackup.methods.reviewResetDefaults.call(cannotReset)
assert.match(cannotReset.error,/Park/)
assert.equal(cannotReset.resetDraft,null)

const interrupted = {...ToggleBackup.data(),resetDraft:{intent:'approved'},request:async () =>
  ({saved:false,applied:['First'],reason:'Vehicle state changed'})}
await ToggleBackup.methods.resetDefaults.call(interrupted)
assert.match(interrupted.error,/1 settings reset.*Vehicle state changed/)
assert.equal(interrupted.notice,'')
assert.equal(interrupted.resetDraft,null)

const failedReview = {...ToggleBackup.data(),request:async () => {throw new Error('Sign in to Galaxy')}}
await ToggleBackup.methods.reviewResetDefaults.call(failedReview)
assert.equal(failedReview.error,'Sign in to Galaxy')
assert.equal(failedReview.resetDraft,null)
assert.equal(failedReview.busy,false)

let busyRequests=0
const busyReset = {...ToggleBackup.data(),busy:true,resetDraft:{intent:'old'},request:async () => {busyRequests++}}
await ToggleBackup.methods.reviewResetDefaults.call(busyReset)
await ToggleBackup.methods.resetDefaults.call(busyReset)
assert.equal(busyRequests,0)
console.log('Software-screen default reset uses guarded preview and explicit confirmation')
