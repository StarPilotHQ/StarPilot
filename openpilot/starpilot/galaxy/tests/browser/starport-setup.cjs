const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined}); const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/js/boot.js',r=>r.fulfill({body:''}));
await page.goto(process.env.GALAXY_URL || 'http://127.0.0.1:8765/');
await page.evaluate(async()=>{
const {createApp,reactive}=await import('./vendor/vue/vue.esm-browser.js');const {StarPortPage}=await import('./js/starport.js');
document.querySelector('#galaxy-boot').remove();
window.fixture=reactive({...StarPortPage.data(),setup:{enabled:true,bluetoothEnabled:true,parked:true,installReady:true,serviceReady:true,identity:{installed:true,message:'Support ready'},import:{state:'idle'},maxUploadBytes:200000000},runtime:{connection:'wireless',car_type:'builtin',state:'wifi_info',running:true},selected:{name:'IONIQ 6',address:'AA:BB:CC:DD:EE:FF'}});
createApp({...StarPortPage,data:()=>window.fixture,mounted(){},beforeUnmount(){},watch:{}},{mode:'local',localAccess:true,unauthorized(){}}).mount('#galaxy-app');
document.querySelector('#galaxy-app').className='gx-content';
});
for (const width of [390,1280]) {
await page.setViewportSize({width,height:1100});
for(const [car_type,connection] of [['builtin','wireless'],['adapter','wireless'],['usb','wired'],['app','headunitpad'],['app','openheadunit']]){
await page.evaluate(({car_type,connection})=>{Object.assign(fixture.runtime,{car_type,connection,car_profile:'aawireless',car_profile_source:'override',state:connection==='wired'?'waiting_for_usb':connection==='headunitpad'?'waiting_for_ipad':connection==='openheadunit'?'waiting_for_tablet':'wifi_info'});}, {car_type,connection});
assert.equal(await page.locator('[aria-label="Connection progress"]:visible').count(),1);
assert.equal(await page.getByRole('button',{name:'Disconnect',exact:true}).count(),1);
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width} ${connection} overflow`);
await page.evaluate(()=>{fixture.showSteps=true;});
assert.equal(await page.locator('[aria-label="Connection progress"]:visible').count(),1,'expanded setup has one progress panel');
assert.equal(await page.getByRole('button',{name:'Disconnect',exact:true}).filter({visible:true}).count(),1);
await page.evaluate(()=>{fixture.showSteps=false;});
}
}
await page.evaluate(()=>{Object.assign(fixture.runtime,{car_type:'builtin',connection:'wireless',running:false,state:'idle'});fixture.pairing={active:true,state:'pairing',approved:false,receiver:{name:'IONIQ 6'},prompt:{kind:'confirmation',value:'123456',id:'a'.repeat(32)},devices:[]};});
assert.equal(await page.getByRole('button',{name:'Confirm',exact:true}).isEnabled(),true);
assert.equal(await page.locator('.gx-starport-row--active').count(),1);

await page.evaluate(()=>{fixture.pairing.state='failed';fixture.pairing.error='Pairing timed out';});
assert.equal(await page.locator('.gx-starport-row--active').count(),0);
await page.evaluate(()=>{fixture.pairing=null;fixture.selected=null;fixture.runtime.state='idle';fixture.runtime.running=false;});
assert.equal(await page.locator('button').filter({hasText: /^\s*Find car\s*$/}).isEnabled(),true);
assert.equal(await page.getByRole('button',{name:'Connect to car',exact:true}).isEnabled(),false);
await page.evaluate(()=>{fixture.runtime.state='wifi_info';fixture.runtime.running=true;});
await page.emulateMedia({reducedMotion:'reduce'});
assert.equal(await page.locator('.gx-guided [aria-label="Connection progress"] .gx-spinner:visible').evaluate(el=>getComputedStyle(el).animationName),'none');
assert.deepEqual(errors,[]);await browser.close();console.log('All five device options at phone/desktop sizes, confirmation, failure and single progress/action layout passed.');
})().catch(e=>{console.error(e);process.exit(1)});
