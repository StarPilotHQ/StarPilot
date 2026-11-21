const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage({viewport:{width:1280,height:1000}})
    const errors = [], mutations = []
    const parked = true
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/maintenance-test', route => route.fulfill({contentType:'text/html',body:
      '<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="css/galaxy.css"><link rel="stylesheet" href="css/setup.css"><link rel="stylesheet" href="css/settings.css"><main class="gx-content"><div id="test"></div></main>'}))
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      if (route.request().method() === 'POST') mutations.push({path,body:route.request().postDataJSON()})
      let data
      if (path.endsWith('/settings/backup')) data = {format:'galaxy-toggles',version:1,settings:[]}
      else if (path.endsWith('/settings/restore')) data = {complete:true,restored:1,unchanged:0,skipped:[]}
      else if (path.endsWith('/settings/non-defaults')) data = {view:'defaults',count:1,rows:[{id:'setting',resetAvailable:parked}]}
      else if (path.endsWith('/recovery/preview')) data = {intent:'defaults',question:'Reset one saved setting to default?'}
      else if (path.endsWith('/recovery/confirm')) data = {saved:true,applied:['setting']}
      else if (path.endsWith('/recordings/action')) data = {deleted:2}
      else return route.fulfill({status:404,json:{error:'Unexpected endpoint'}})
      return route.fulfill({json:data})
    })
    await page.goto((process.env.GALAXY_URL || 'http://127.0.0.1:8765/')+'maintenance-test')
    await page.evaluate(async () => {
      const {createApp} = await import('./vendor/vue/vue.esm-browser.js')
      const {SystemMaintenance} = await import('./js/system-maintenance.js')
      window.maintenance = createApp(SystemMaintenance,{unauthorized(){}}).mount('#test')
    })
    const card = page.getByRole('region',{name:'Backup, reset and cleanup'})
    assert.equal(await card.evaluate(element=>element.open),false)
    const summary = card.locator('summary')
    await summary.focus()
    await page.keyboard.press('Enter')
    await page.getByRole('button',{name:'Back up toggles',exact:true}).waitFor()
    assert.equal(await card.getByRole('button',{name:/Reset (Calibration|Driver Monitoring)/}).count(),0)
    await summary.click()
    assert.equal(await card.evaluate(element=>element.open),false)
    await summary.click()
    assert.equal(await card.locator('.gx-maintenance__content').count(),2)
    assert.equal(await card.locator('.gx-card').count(),0)
    assert.equal(await card.getByText(/Vehicle not identified|Ready/).count(),0)
    assert.equal(await card.getByRole('button',{name:/Search/}).count(),0)
    for (const theme of ['light','dark']) for (const width of [320,390,1280]) {
      await page.setViewportSize({width,height:1100})
      await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme)
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
      const actions = card.locator('.gx-maintenance__content .gx-actions > button')
      assert.equal(await actions.count(),4)
      assert(await actions.evaluateAll(buttons=>buttons.every(button=>{
        const rect=button.getBoundingClientRect()
        return rect.left>=0 && rect.right<=innerWidth
      })), 'grouped actions fit the viewport')
    }
    await page.screenshot({path:'/tmp/galaxy-system-maintenance-desktop.png',fullPage:true})
    await page.setViewportSize({width:390,height:1100})
    await page.screenshot({path:'/tmp/galaxy-system-maintenance-mobile.png',fullPage:true})
    assert(await card.getByRole('button',{name:'Reset toggles to default',exact:true}).evaluate(button=>button.classList.contains('gx-btn--danger')))
    const download = page.waitForEvent('download')
    await card.getByRole('button',{name:'Back up toggles',exact:true}).click()
    assert.equal((await download).suggestedFilename(),'toggle-backup.json')
    assert.equal(mutations.length,0)
    await card.locator('input[type=file]').setInputFiles({name:'backup.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({format:'galaxy-toggles',version:1,settings:[]}))})
    await page.getByRole('dialog',{name:'Restore toggles?'}).getByRole('button',{name:'Restore',exact:true}).click()
    await page.getByText(/1 toggle restored/).waitFor()
    await card.getByRole('button',{name:'Reset toggles to default',exact:true}).click()
    await page.getByRole('dialog',{name:'Reset toggles to default?'}).getByRole('button',{name:'Reset toggles',exact:true}).click()
    await page.getByText('1 setting reset to default.',{exact:true}).waitFor()
    await card.getByRole('button',{name:'Delete all driving routes',exact:true}).click()
    assert(!mutations.some(item=>item.path.endsWith('/recordings/action')))
    await page.getByRole('alertdialog',{name:'Delete all driving routes?'}).getByRole('button',{name:'Delete routes',exact:true}).click()
    await page.getByText('2 driving routes deleted.',{exact:true}).waitFor()
    const deletion = mutations.find(item=>item.path.endsWith('/recordings/action'))
    assert.deepEqual(deletion.body,{action:'delete-all',confirmed:true,includePreserved:true})
    assert.deepEqual(errors,[])
    console.log('Unified maintenance layout, download/restore, toggle resets and deletion confirmation passed.')
  } finally { await browser.close() }
})().catch(error=>{console.error(error);process.exit(1)})
