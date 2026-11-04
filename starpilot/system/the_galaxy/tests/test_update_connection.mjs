import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
import test from 'node:test'

const root = process.argv[2] || new URL('../../../../', import.meta.url).pathname
const assets = root + '/starpilot/system/the_galaxy/assets/'
function mobile() {
  const storage = new Map()
  const api = {getUpdateFastStatus: async () => ({running:true,stage:'switching',progressPercent:21})}
  const context = vm.createContext({api, GalaxySection:{}, GxNotice:{}, VersionHistoryPicker:{},
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}})
  const source = fs.readFileSync(assets+'mobile/js/views/SystemTools.js','utf8')
  vm.runInContext(source.replace(/^import .*$/gm,'').replace('export const SystemTools =','globalThis.component ='),context)
  const component = context.component
  const instance = {...component.data(),...component.methods,rebootStorageScope:'test-device'}
  for (const [name,getter] of Object.entries(component.computed)) Object.defineProperty(instance,name,{get:()=>getter.call(instance)})
  return {instance,api,storage}
}
test('accepted installation keeps progress visible and never claims completion', async () => {
  const {instance}=mobile()
  instance.markRebootPending()
  await instance.loadFastStatus()
  assert.equal(instance.updateNoticeTitle,'Update in progress')
  assert.doesNotMatch(instance.updateNoticeText,/complete|finished/i)
  assert.equal(instance.statusRebooting,false)
  assert.equal(instance.fastStatus.progressPercent,21)
})
test('connection loss preserves last stage and does not announce reboot', async () => {
  const {instance,api}=mobile()
  instance.markRebootPending()
  await instance.loadFastStatus()
  api.getUpdateFastStatus=async()=>{throw Error('Failed to fetch')}
  await instance.loadFastStatus()
  assert.equal(instance.fastStatus.stage,'switching')
  assert.equal(instance.updateNoticeTitle,'Reconnecting to device')
  assert.match(instance.updateNoticeText,/not been confirmed/)
  assert.equal(instance.statusRebooting,false)
})
test('failed fetch clears pending state and preserves actual server error', async () => {
  const {instance,api}=mobile()
  instance.markRebootPending()
  instance.statusUnavailable=true
  api.getUpdateFastStatus=async()=>({running:false,stage:'error',lastError:'curl 92 HTTP/2 stream CANCEL'})
  await instance.loadFastStatus()
  assert.equal(instance.rebootPending,false)
  assert.equal(instance.reconnectedNotice,false)
  assert.match(instance.fastStatus.lastError,/curl 92/)
  assert.equal(instance.updateInProgress,false)
})
test('confirmed reboot alone shows completion and persists device-scoped marker', async () => {
  const {instance,api,storage}=mobile()
  api.getUpdateFastStatus=async()=>({running:false,stage:'rebooting'})
  await instance.loadFastStatus()
  assert.equal(instance.updateNoticeTitle,'Device rebooting')
  assert.match(instance.updateNoticeText,/Installation finished/)
  assert.ok(JSON.parse(storage.get('galaxy-update-reboot-pending:test-device')).startedAt>0)
  assert.equal(storage.size,1)
})
test('idle status failure preserves the last received status', async () => {
  const {instance,api}=mobile()
  instance.fastStatus={stage:'idle',running:false}
  api.getUpdateFastStatus=async()=>{throw Error('NetworkError')}
  await instance.loadFastStatus()
  assert.equal(instance.fastStatus.stage,'idle')
  assert.equal(instance.statusUnavailable,true)
  assert.equal(instance.statusPollingNeeded,true)
})

function desktop() {
  const context=vm.createContext({reactive:value=>value,html:()=>'',showSnackbar:()=>{},
    fetch:async()=>{throw Error('Failed to fetch')},setTimeout:()=>1,clearTimeout:()=>{}})
  const source=fs.readFileSync(assets+'components/tools/update_manager.js','utf8')
  vm.runInContext(source.replace(/^import .*$/gm,'').replace('export function UpdateManager','function UpdateManager')+
    '\nglobalThis.hooks={state,fetchStatus,shouldShowRebootNotice}',context)
  return {context,...context.hooks}
}
test('classic connection loss cannot turn a running fetch into completed reboot', async () => {
  const {state,fetchStatus,shouldShowRebootNotice}=desktop()
  state.status={running:true,stage:'switching',progressStep:2,progressPercent:21}
  await fetchStatus(false)
  assert.equal(state.status.stage,'switching')
  assert.equal(state.status.progressPercent,21)
  assert.equal(shouldShowRebootNotice(),false)
  assert.match(state.error,/completion is not confirmed/)
})
test('classic actual reboot retains reboot feedback during connection loss', async () => {
  const {state,fetchStatus,shouldShowRebootNotice}=desktop()
  state.status={running:false,stage:'rebooting'}
  await fetchStatus(false)
  assert.equal(state.status.stage,'rebooting')
  assert.equal(shouldShowRebootNotice(),true)
})
test('classic successful status response exposes a failed transfer', async () => {
  const {context,state,fetchStatus,shouldShowRebootNotice}=desktop()
  state.status={running:true,stage:'switching'}
  await fetchStatus(false)
  context.fetch=async()=>({ok:true,text:async()=>JSON.stringify({running:false,stage:'error',lastError:'early EOF'})})
  await fetchStatus(false)
  assert.equal(state.status.stage,'error')
  assert.equal(state.status.lastError,'early EOF')
  assert.equal(shouldShowRebootNotice(),false)
})
