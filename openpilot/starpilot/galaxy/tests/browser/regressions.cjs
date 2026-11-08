const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const layout = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c',
  'import json; from openpilot.starpilot.ui.onroad_customization import default_document, customization_metadata; print(json.dumps(dict(document=default_document(), defaults=default_document(), metadata=customization_metadata(), revision="a"*64, editable=True, valid=True, activeProfile="large")))'], { encoding: 'utf8' }))
;(async () => {
  const browser = await chromium.launch({headless:true})
  const page = await browser.newPage({viewport:{width:1280,height:900}})
  const errors=[]
  page.on('pageerror',e=>errors.push(e.message))
  const jpeg=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=1344;c.height=760;const x=c.getContext('2d');x.fillStyle='#a04020';x.fillRect(0,0,c.width,c.height);return c.toDataURL('image/jpeg').split(',')[1]})
  let confidence='0.85',version=0,saves=0,defaults=0,draws=0,cameraFail=false,disconnected=false
  const row={label:'Detection confidence',value:confidence,minimum:0.8,maximum:1,step:0.01,available:true,action:true,choices:[],defaultValue:'0.90',resetAvailable:true,revision:'r1'}
  await page.route('**/data/runtime.json',r=>r.fulfill({json:{schemaVersion:1,monitor:'local'}}))
  await page.route('**/api/**',async r=>{
    const path=new URL(r.request().url()).pathname
    if(disconnected)return r.abort('internetdisconnected')
    if(path==='/api/device/state')return r.fulfill({json:{state:'parked',maxAgeMs:3000}})
    if(path==='/api/vehicle-selection')return r.fulfill({json:{version:1,parked:true,readable:true,valid:true,selected:null,selectedLabel:'Auto detection',reported:null,view:'vehicle-view',choices:[{platform:'KIA_CEED',make:'Kia',label:'Ceed'}]}})
    if(path==='/api/auth/session') return r.fulfill({json:{state:'configured',authenticated:true,localAccess:true}})
    if(path==='/api/cameras/snapshot'){draws++;if(cameraFail)return r.fulfill({status:503,json:{error:'Camera unavailable'}});return r.fulfill({contentType:'image/jpeg',body:Buffer.from(jpeg,'base64')})}
    if(path.startsWith('/api/settings/pages/')) {
      const pip=path.endsWith('/pip')
      return r.fulfill({json:{page:path.split('/').at(-1),view:'view'+version++,parked:true,editorRow:1,rows:[{...row,value:confidence,revision:confidence},{label:'Camera window regions',available:true,action:true}],editor:pip?{width:1344,height:760,cropSize:300,centerLeft:null,centerRight:[1000,350],invert:true}:{width:1344,height:760,cameraLeft:[],cameraRight:[[800,100],[1200,100],[1200,550],[800,550]]}}})
    }
    if(path==='/api/settings/preview'||path==='/api/settings/reset-default') {defaults+=path.endsWith('reset-default')?1:0;return r.fulfill({json:{intent:'i1',question:'Save?',proposed:'0.9'}})}
    if(path==='/api/settings/confirm') {saves++;return r.fulfill({json:{saved:true}})}
    if(path==='/api/ui/layout') {
      if(r.request().method()==='POST') {
        layout.document=r.request().postDataJSON().document
        layout.revision='b'.repeat(64)
      }
      return r.fulfill({json:layout})
    }
    if(path==='/api/recordings/local')return r.fulfill({json:{schemaVersion:1,source:'local',partialHistory:true,scanIncomplete:false,routes:[{routeId:'00000042--abcdef1234',segmentCount:1,displayName:'Test drive',preserved:false,segments:[{number:0,segmentName:'00000042--abcdef1234--0',files:{rlog:true,qlog:false,fcamera:true,dcamera:true,ecamera:true,qcamera:true},logFiles:['rlog.zst']}]}]}})
    return r.fulfill({status:503,json:{error:'Fixture unavailable'}})
  })
  await page.goto((process.env.GALAXY_URL || 'http://127.0.0.1:8765/') + '#/cameras/vasm')
  await page.getByRole('heading',{name:'Camera Window Regions'}).waitFor()
  await page.waitForFunction(()=>{const c=document.querySelector('.gx-vasm__canvas');return c?.getContext('2d').getImageData(20,20,1,1).data[0]>100})
  assert.equal(await page.getByRole('status').filter({hasText:'Waiting for'}).count(),0)
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/settings/confirm'))
  await page.getByRole('button',{name:'Reset Detection confidence to default'}).click()
  await saved
  await page.waitForFunction(()=>!document.querySelector('[role="dialog"]'))
  assert.equal(defaults,1);assert.equal(saves,1)
  confidence='0.95'
  await page.getByText('0.95',{exact:false}).first().waitFor({timeout:5000})
  await page.screenshot({path:'/tmp/galaxy-regression-vasm.png'})
  await page.evaluate(()=>location.hash='/cameras/pip')
  await page.getByRole('heading',{name:'Camera Crop'}).waitFor()
  await page.getByLabel('Camera crop size',{exact:true}).waitFor()
  await page.waitForFunction(()=>{const c=document.querySelector('.gx-pip .gx-vasm__canvas');return !!c&&c.width>0})
  await page.setViewportSize({width:390,height:844})
  assert.equal(await page.getByLabel('Camera crop size',{exact:true}).count(),1, 'crop size slider sits with the preview')
  assert.equal(await page.locator('.gx-pip__preview').count(),0, 'the separate selected-circle preview is removed')
  const toolbarCenters=await page.locator('.gx-pip .gx-actions button').evaluateAll(buttons=>buttons.map(button=>{const rect=button.getBoundingClientRect();return rect.y+rect.height/2}))
  assert.ok(Math.max(...toolbarCenters)-Math.min(...toolbarCenters)<1,'camera actions occupy one row')
  await page.waitForFunction(()=>document.querySelector('.gx-camera-status')?.textContent==='Snapshot ready')
  const frozenCaptures=draws
  await page.waitForTimeout(1800)
  assert.equal(draws,frozenCaptures,'camera stops fetching after warm-up')
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
  await page.screenshot({path:'/tmp/galaxy-polish-pip-phone.png'})
  await page.setViewportSize({width:1280,height:900})
  await page.evaluate(()=>location.hash='/theme_maker')
  await page.getByRole('button',{name:'PiP left side camera',exact:true}).waitFor()
  await page.getByRole('button',{name:'PiP right side camera',exact:true}).waitFor()
  const rightBefore = structuredClone(layout.document.layouts.large.pip_right)
  await page.getByRole('button',{name:'PiP left side camera',exact:true}).click()
  await page.getByLabel('X',{exact:true}).fill('400')
  await page.getByLabel('X',{exact:true}).dispatchEvent('change')
  await page.getByRole('button',{name:'Remove from layout',exact:true}).click()
  const tray=page.locator('.gx-layout__tray-item').filter({hasText:'PiP left side camera'})
  await tray.getByRole('button',{name:'Add',exact:true}).click()
  const layoutSaved=page.waitForResponse(r=>r.url().endsWith('/api/ui/layout')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'Save changes',exact:true}).click()
  await layoutSaved
  assert.equal(layout.document.layouts.large.pip_left.enabled,true)
  assert.equal(layout.document.layouts.large.pip_left.x,400)
  assert.deepEqual(layout.document.layouts.large.pip_right, rightBefore)
  assert.equal(layout.document.layouts.large.vasm, undefined)
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='Save changes'&&button.disabled))
  await page.screenshot({path:'/tmp/galaxy-regression-layout.png'})
  await page.evaluate(()=>location.hash='/cameras/pip')
  await page.getByLabel('Camera crop size',{exact:true}).waitFor()
  await page.waitForFunction(()=>{const c=document.querySelector('.gx-pip .gx-vasm__canvas');return c?.getContext('2d').getImageData(20,20,1,1).data[0]>100})
  await page.evaluate(()=>location.hash='/recordings')
  await page.getByRole('button',{name:'Watch Test drive',exact:true}).waitFor()
  await page.getByText('Test drive',{exact:true}).waitFor()
  await page.screenshot({path:'/tmp/galaxy-regression-recordings-desktop.png'})
  await page.getByRole('button',{name:'Watch Test drive',exact:true}).click()
  await page.getByRole('dialog',{name:'Camera recording player'}).waitFor()
  assert.equal(await page.getByRole('combobox',{name:'Video segment',exact:true}).count(),1)
  assert.equal(await page.getByRole('link',{name:'Download drive',exact:true}).count(),1)
  await page.screenshot({path:'/tmp/galaxy-regression-recordings-player.png'})
  await page.getByRole('dialog',{name:'Camera recording player'}).getByRole('button',{name:'Close recording player',exact:true}).click()
  await page.getByRole('button',{name:'View drive logs',exact:true}).click()
  assert.equal(await page.getByRole('link',{name:'Download all logs (.tar)',exact:false}).count(),1)
  assert.equal(await page.locator('.gx-recordings input[type="checkbox"]').count(),0)
  await page.setViewportSize({width:390,height:844})
  await page.screenshot({path:'/tmp/galaxy-regression-recordings-phone.png'})
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),false)
  cameraFail=true
  await page.evaluate(()=>location.hash='/cameras/vasm')
  await page.getByRole('alert').filter({hasText:'open its camera preview'}).waitFor()
  assert.equal(await page.getByText('Waiting for a live cabin frame.',{exact:false}).count(),0)
  cameraFail=false
  await page.evaluate(()=>location.hash='/vehicle')
  await page.getByRole('heading',{name:'Vehicle',exact:true}).waitFor()
  const vehicleDefault=page.getByRole('button',{name:'Reset Detection confidence to default'})
  await vehicleDefault.waitFor()
  await page.evaluate(()=>{
    window.buttonDisabledTransitions=[]
    const button=document.querySelector('[aria-label="Reset Detection confidence to default"]')
    window.defaultButtonObserver=new MutationObserver(()=>window.buttonDisabledTransitions.push(button.disabled))
    window.defaultButtonObserver.observe(button,{attributes:true,attributeFilter:['disabled']})
  })
  await page.waitForTimeout(3500)
  assert.deepEqual(await page.evaluate(()=>window.buttonDisabledTransitions),[],'background reads must not toggle Default')
  disconnected=true
  const offlineAlert=page.getByRole('alert').filter({hasText:'Reconnecting automatically'})
  await offlineAlert.first().waitFor()
  await page.evaluate(()=>{
    window.alertDisappearances=0
    const banner=[...document.querySelectorAll('[role="alert"]')].find(element=>element.textContent.includes('Reconnecting automatically'))
    window.alertObserver=new MutationObserver(()=>{if(!banner.isConnected)window.alertDisappearances++})
    window.alertObserver.observe(document.querySelector('.gx-content'),{childList:true,subtree:true})
  })
  await page.waitForTimeout(4500)
  assert.equal(await page.evaluate(()=>window.alertDisappearances),0,'network error must remain during retries')
  assert.equal(await page.getByRole('button',{name:/^(Refresh|Retry)/}).count(),0)
  disconnected=false
  await page.waitForFunction(()=>![...document.querySelectorAll('[role="alert"]')].some(element=>element.textContent.includes('Reconnecting automatically')),{},{timeout:10000})
  await page.waitForFunction(()=>!document.querySelector('[aria-label="Reset Detection confidence to default"]').disabled)
  await page.evaluate(()=>{window.defaultButtonObserver.disconnect();window.alertObserver.disconnect()})
  await page.evaluate(()=>location.hash='/tools')
  await page.getByRole('heading',{name:'Tools',exact:true}).waitFor()
  const sidebar=page.locator('.gx-nav-section').filter({has:page.locator('.gx-nav-section__title',{hasText:/^Tools$/})})
  assert.deepEqual(await sidebar.locator('.gx-nav-item span').allTextContents(), (await page.locator('.gx-menu-tile > span').allTextContents()).filter(name => name !== 'Galaxy Access & Install'))
  assert.deepEqual(errors,[])
  console.log('Browser checks passed: VASM frame/default/reactive updates, PiP crop frame, widget registry, recordings and phone layout. Captures:',draws)
  await browser.close()
})().catch(e=>{console.error(e);process.exit(1)})
