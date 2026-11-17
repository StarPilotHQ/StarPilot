import assert from 'node:assert/strict'
import { DriveStatePanel, validDriveState } from '../web/js/drive-state.js'
const snapshot=(mode='auto',revision='a'.repeat(32))=>({mode,revision,available:true,effective:'offroad',overrideAllowed:true})
const response=(value,status=200)=>({ok:status===200,json:async()=>value})
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve()}
assert(validDriveState(snapshot()))
assert(!validDriveState({...snapshot(),mode:'driving'}))
assert(!validDriveState({...snapshot(),effective:'driving'}))
const requests=[]
globalThis.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}))
function panel(){const value={...DriveStatePanel.data(),active:true,generation:0};for(const [key,fn] of Object.entries(DriveStatePanel.methods))value[key]=fn.bind(value);return value}
const value=panel();value.state=snapshot()
const old=value.load();assert.equal(requests.length,1)
const changed=value.change('onroad');assert.equal(requests.length,2)
assert.deepEqual(JSON.parse(requests[1].options.body),{mode:'onroad',revision:'a'.repeat(32)})
assert.equal(requests[0].options.signal.aborted,true)
requests[1].resolve(response(snapshot('onroad','b'.repeat(32))));await flush()
assert.equal(value.state.mode,'onroad');assert.equal(requests.length,3)
requests[0].resolve(response(snapshot('offroad','c'.repeat(32))));await old
assert.equal(value.state.mode,'onroad')
requests[2].resolve(response(snapshot('onroad','b'.repeat(32))));await changed;await flush()
const pending=value.load();const last=requests.at(-1)
DriveStatePanel.beforeUnmount.call(value)
assert.equal(last.options.signal.aborted,true)
last.resolve(response(snapshot('offroad')));await pending
assert.equal(value.state.mode,'onroad')
const denied=panel();const loading=denied.load();requests.at(-1).resolve(response({error:'Sign in to Galaxy'},401));await loading
assert.equal(denied.state,null)
assert.equal(denied.statusError,'Drive state unavailable')
console.log('PASS: strict status; captured revision; aborted/out-of-order status; requested versus effective; unmount; unauthorized state')

const onroad=panel();onroad.state={...snapshot(),effective:'onroad',overrideAllowed:false}
let before=requests.length
const forceOffroad=onroad.change('offroad')
assert.equal(requests.length,before+1)
assert.equal(JSON.parse(requests.at(-1).options.body).mode,'offroad')
requests.at(-1).resolve(response({error:'Park the vehicle'},409));await flush()
requests.at(-1).resolve(response(snapshot()));await forceOffroad;await flush()
assert.match(DriveStatePanel.template,/role="switch"/)
before=requests.length
// A missing parked admission must not disable Galaxy Force Onroad.
onroad.state.overrideAllowed=false
const forceOnroad=onroad.change('onroad')
assert.equal(requests.length,before+1)
assert.deepEqual(JSON.parse(requests.at(-1).options.body),{mode:'onroad',revision:'a'.repeat(32)})
const live={...snapshot('onroad','d'.repeat(32)),effective:'onroad',overrideAllowed:false}
requests.at(-1).resolve(response(live));await flush()
requests.at(-1).resolve(response(live));await forceOnroad;await flush()
assert.equal(onroad.state.mode,'onroad')
assert.equal(DriveStatePanel.computed.pending.call(onroad),false)
console.log('PASS: Galaxy Offroad dispatch uses server admission; Force Onroad remains available')

for (const [offroadOnly, lockout, mode, expected] of [
  [true,true,'auto',true], [true,false,'auto',false], [true,false,'offroad',true], [false,false,'auto',true],
]) assert.equal(DriveStatePanel.computed.visible.call({offroadOnly,lockout,state:snapshot(mode)}),expected)
assert.match(DriveStatePanel.template,/Force device offroad/)

// A rejected enable must not look enabled or lose its error during status polling.
const rejectedToggle=panel();rejectedToggle.offroadOnly=true;rejectedToggle.lockout=false;rejectedToggle.state=snapshot()
const input={checked:true}
const attempt=rejectedToggle.toggleOffroad({target:input})
assert.equal(input.checked,false,'wait for the server to accept the mode')
requests.at(-1).resolve(response({error:'Current speed and gear are unavailable'},409));await flush()
requests.at(-1).resolve(response(snapshot()));await attempt;await flush()
assert.equal(input.checked,false)
assert.equal(rejectedToggle.state.mode,'auto')
assert.equal(rejectedToggle.error,'Current speed and gear are unavailable')
assert.equal(DriveStatePanel.computed.visible.call(rejectedToggle),true,'keep the rejected action visible')
input.checked=true
const retry=rejectedToggle.toggleOffroad({target:input})
assert.equal(rejectedToggle.error,'')
requests.at(-1).resolve(response(snapshot('offroad','e'.repeat(32))));await flush()
requests.at(-1).resolve(response(snapshot('offroad','e'.repeat(32))));await retry;await flush()
assert.equal(input.checked,true)
assert.equal(rejectedToggle.state.mode,'offroad')
