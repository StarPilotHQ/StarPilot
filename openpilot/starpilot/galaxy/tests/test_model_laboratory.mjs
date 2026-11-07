import assert from "node:assert/strict"
import { LaboratoryFeed, ModelsPage, laboratoryModels, laboratoryReady, laboratorySelectionError, laboratoryActionAllowed, laboratoryDraft, laboratoryStatusLabel } from "../web/js/models.js"

const model = (value, version = 'v9') => ({ value, label: `Model ${value}`, version, small: true, modelLabEligible: true, modelLabArtifactAvailable: true, modelLabArtifactInstalled: true })
const payload = () => ({ schemaVersion: 1, isOnroad: false, chestnutReady: true, runtimeSupported: true,
  configuration: { enabled: false, lateralModel: 'a', longitudinalModel: 'b' }, runtime: { active: false, requested: false },
  download: { downloading: false, jobId: null, progress: '' }, models: [model('a','v8'), model('b','v9')],
  capabilities: { configure: true, download: true, delete: true, cancel: true, refresh: true } })
const p = payload()
assert.equal(laboratorySelectionError(p,p.configuration), '') // mixed versions allowed
assert.equal(laboratoryActionAllowed(p,'enable',null,p.configuration), true)
assert.match(laboratorySelectionError({...p,runtimeSupported:false,runtimeUnavailableReason:'Pair runtime pending'},p.configuration), /Pair runtime pending/)
assert.match(laboratorySelectionError({...p,chestnutReady:false},p.configuration), /Chestnut/)
assert.match(laboratorySelectionError({...p,isOnroad:true},p.configuration), /Turn off the vehicle/)
assert.match(laboratorySelectionError(p,{lateralModel:'a',longitudinalModel:'a'}), /different/)
assert.match(laboratorySelectionError(p,{lateralModel:'a',longitudinalModel:'unknown'}), /downloaded/)
assert.equal(laboratorySelectionError(null,p.configuration), 'Waiting for parked device status')
const incompatible = [model('big'), model('qcom'), model('old')]
incompatible[0].small=false; incompatible[1].modelLabArtifactAvailable=false; incompatible[2].modelLabEligible=false
assert.equal(laboratoryModels({...p,models:incompatible}).length,3)
assert.equal(laboratoryReady({...p,models:incompatible}).length,0)
for (const row of incompatible) assert.equal(laboratoryActionAllowed(p,'download',row),false)
assert.equal(laboratoryReady({...p,models:[{...model('a'),modelLabArtifactInstalled:false}]}).length,0)
assert.equal(laboratoryActionAllowed({...p,configuration:{...p.configuration,enabled:true}},'delete',p.models[0]),false)
assert.equal(laboratoryActionAllowed(p,'delete',p.models[0]),true)
const missing = {...model('c'),modelLabArtifactInstalled:false}
assert.equal(laboratoryActionAllowed({...p,chestnutReady:false,models:[missing]},'download',missing),true)
assert.equal(laboratoryActionAllowed({...p,download:{downloading:true,jobId:'job'}},'cancel'),true)
assert.equal(laboratoryActionAllowed({...p,download:{downloading:true,jobId:'job'}},'enable'),false)
assert.deepEqual(laboratoryDraft(p,{lateralModel:'b',longitudinalModel:'a'},true),{enabled:false,lateralModel:'b',longitudinalModel:'a'})
assert.deepEqual(laboratoryDraft({...p,configuration:{enabled:false,lateralModel:'',longitudinalModel:''}},null,false),p.configuration)

function fixture() {
  const requests=[],updates=[],timers=new Map();let next=0,unauthorized=0
  const feed=new LaboratoryFeed({publish:u=>updates.push(u),unauthorized:()=>unauthorized++,
    fetcher:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),
    later:(fn,ms)=>{timers.set(++next,{fn,ms});return next},cancel:id=>timers.delete(id)})
  const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()}
  const reply=async(index,body,status=200)=>{requests[index].resolve({ok:status===200,status,json:async()=>body});await flush()}
  return {feed,requests,updates,timers,reply,flush,get unauthorized(){return unauthorized}}
}
const f=fixture();f.feed.start();assert.equal(f.requests[0].url,'./api/models/laboratory');await f.reply(0,p)
assert.equal(f.feed.data.runtimeSupported,true)
const action=f.feed.action('enable',null,{enabled:false,lateralModel:'b',longitudinalModel:'a'})
assert.equal(f.requests[1].url,'./api/models/laboratory')
assert.equal(f.requests[1].options.method,'POST')
assert.equal(f.requests[1].options.credentials,'same-origin')
assert.deepEqual(JSON.parse(f.requests[1].options.body),{enabled:true,lateralModel:'b',longitudinalModel:'a'})
await f.reply(1,{...p,message:'Pair requested'});assert.equal(f.requests[2].url,'./api/models/laboratory');await f.reply(2,p);await action
const expiry=[...f.timers.values()].find(t=>t.ms===6000);expiry.fn()
assert.equal(f.feed.data,null);assert.match(f.updates.at(-1).error,/refresh delayed/)
assert.equal(await f.feed.action('enable',null,p.configuration),null)
f.feed.stop()
const invalid=fixture();invalid.feed.start();await invalid.reply(0,{...p,runtimeSupported:undefined});assert.equal(invalid.feed.data,null)
const retired=fixture();retired.feed.start();retired.feed.stop();await retired.reply(0,p);assert.equal(retired.feed.data,null)
const auth=fixture();auth.feed.start();await auth.reply(0,{message:'Unauthorized'},401);assert.equal(auth.unauthorized,1);assert.equal(auth.feed.active,false)
const download=fixture();download.feed.start();await download.reply(0,{...p,models:[missing]})
const downloading=download.feed.action('download',missing)
assert.equal(download.requests[1].url,'./api/models/laboratory/download');assert.deepEqual(JSON.parse(download.requests[1].options.body),{model:'c'})
await download.reply(1,{message:'Starting download'});await download.reply(2,{...p,download:{downloading:true,jobId:'job',progress:'40%'}});await downloading
const canceling=download.feed.action('cancel');assert.equal(download.requests[3].url,'./api/models/cancel');assert.deepEqual(JSON.parse(download.requests[3].options.body),{jobId:'job'})
await download.reply(3,{message:'Cancelling'});await download.reply(4,p);await canceling;download.feed.stop()
// Model Laboratory lives on the Model Manager page: pair controls after Controls, eGPU variants on the model cards
const template = ModelsPage.template
const labCard = template.indexOf('id="gx-model-laboratory"')
// one Choose models card: single model first, then the Laboratory pair, then the model list
assert.ok(template.indexOf('>Choose models<') < template.indexOf('Single model') && template.indexOf('Single model') < labCard && labCard < template.indexOf('gx-model-card'))
assert.doesNotMatch(template, />Controls</)
assert.match(template,/aria-label="Lateral model"/)
assert.match(template,/aria-label="Longitudinal model"/)
// Galaxy: the Single model / Model pair selector turns the pair on and off (it replaced the Enable model pair switch).
assert.ok(template.includes(`@click="setDriveMode('pair')"><i aria-hidden="true" class="bi bi-bezier2"></i> Model pair</button>`))
assert.match(template,/Verified downloads/)
assert.match(template,/eGPU variant/)
assert.match(template,/v-model="pairableFilter"> <span class="gx-model-tag gx-model-tag--pair">Pairable<\/span>/) // Galaxy: the Pairable filter lives in the funnel menu
assert.doesNotMatch(template,/Available models|Compose a pair|Catalog models/)
assert.equal(laboratoryStatusLabel({modelLabStatus:"runtime-unavailable"}), "Downloaded")
assert.equal(laboratoryStatusLabel({modelLabStatus:"unsupported"}), "Not supported")
assert.doesNotMatch(template,/20 Hz/)
const save=fixture();save.feed.start();await save.reply(0,p)
const saving=save.feed.action('save',null,{enabled:true,lateralModel:'b',longitudinalModel:'a'})
assert.equal(save.requests[1].url,'./api/models/laboratory')
assert.deepEqual(JSON.parse(save.requests[1].options.body),{enabled:false,lateralModel:'b',longitudinalModel:'a'})
await save.reply(1,{...p,message:'saved'});await save.reply(2,p);await saving;save.feed.stop()
console.log('Model Laboratory contracts passed')
