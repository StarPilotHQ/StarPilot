const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/js/boot.js', route => route.fulfill({ body: '' }))
  await page.goto(process.env.GALAXY_URL || 'http://127.0.0.1:8765/')
  await page.evaluate(() => {
    document.body.innerHTML = '<div class="gx-app"><header class="gx-appbar">Header</header><main class="gx-content"><div id="fixture"></div></main></div>'
  })
  await page.evaluate(async () => {
    const { createApp } = await import('./vendor/vue/vue.esm-browser.js')
    const { SoftwarePage } = await import('./js/software-status.js')
    const { GxState } = await import('./js/state.js')
    const { GxNotice } = await import('./js/notice.js')
    const { GxDialog } = await import('./js/dialog.js')
    const { GxSummary } = await import('./js/summary.js')
    const installed = { version: 'Fixture', branch: 'SecretGoodStarPilot', commit: 'a'.repeat(40) }
    const updater = { state: 'idle', lastCheckedAt: null, lastFetchAt: null, targetChangeFound: false }
    const operations = { availableBranches: ['StarPilot', 'Dom'], selectedTarget: 'StarPilot', request: null,
      canCheck: true, canFastUpdate: true, canDownload: true, canSelect: true, canInstall: false }
    const software = { ...SoftwarePage, created() {}, mounted() {}, beforeUnmount() {},
      data: () => ({ ...SoftwarePage.data(), status: 'ready', data: { installed, updater, operations },
        notice: 'Updating SecretGoodStarPilot. Waiting for completion or reconnect.', uncertain: true }) }
    window.fixture = createApp({ components: { software, GxState, GxNotice, GxDialog, GxSummary },
      data: () => ({ dialog: false }),
      template: `<software mode="local" :unauthorized="() => {}" /><div class="gx-card-grid"><section class="gx-card gx-panel">Card</section><section class="gx-card gx-panel">Card</section></div>
        <div class="gx-home__totals"><section class="gx-card"><h2>Time on the Road</h2><p>Saved history</p></section></div><section class="gx-card gx-starport-step"><h2>Android Auto</h2><p>Connect your car</p></section><GxSummary :items="[{label: 'Installed', value: 1}, {label: 'Missing', value: 0}, {label: 'Total', value: 1}]" /><GxState loading>Reading your device…</GxState><GxState>Nothing here yet.</GxState>
        <GxNotice tone="success" title="Saved">Preferences saved.</GxNotice><GxNotice tone="danger">An error occurred.</GxNotice>
        <div class="gx-tabs"><button class="gx-btn" aria-pressed="true">Selected</button><button class="gx-btn gx-btn--tonal">Other</button></div>
        <button class="gx-btn" @click="dialog=true">Open dialog</button>
        <GxDialog v-if="dialog" labelledby="fixture-title" @close="dialog=false"><h3 id="fixture-title">Confirm preference</h3><input class="gx-field" aria-label="Name" /><div class="gx-actions"><button class="gx-btn" @click="dialog=false">Cancel</button><button class="gx-btn">Save</button></div></GxDialog>`,
    }).mount('#fixture')
  })
  for (const theme of ['dark', 'light']) {
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme)
    for (const width of [320, 390, 768, 1280, 1920, 3440]) {
      await page.setViewportSize({ width, height: 900 })
      for (const pinned of [false, true]) {
        await page.evaluate(pinned => document.querySelector('.gx-app').classList.toggle('gx-nav-pinned', pinned), pinned)
        const geometry = await page.evaluate(() => {
          const content = document.querySelector('.gx-content').getBoundingClientRect()
          const header = document.querySelector('.gx-appbar').getBoundingClientRect()
          const notice = document.querySelector('.gx-update-progress').getBoundingClientRect()
          const track = document.querySelector('.gx-update-progress__track').getBoundingClientRect()
          const text = document.querySelector('.gx-update-progress__heading').getBoundingClientRect()
          return { width: content.width, left: content.left, right: content.right, header: header.width,
            notice: notice.width, container: document.querySelector('.gx-software-card').clientWidth - 2 * parseFloat(getComputedStyle(document.querySelector('.gx-software-card')).paddingLeft), aligned: track.top >= text.bottom,
            overflow: document.documentElement.scrollWidth > innerWidth }
        })
        assert.equal(geometry.overflow, false, `${theme}/${width}/${pinned}: no overflow`)
        assert.ok(geometry.width <= 1200 && geometry.header <= 1200)
        assert.equal(await page.locator('.gx-tabs').last().evaluate(element => getComputedStyle(element).display), 'flex')
        assert.equal(await page.locator('.gx-card-grid').evaluate(element => getComputedStyle(element).display), 'grid')
        const summary = await page.locator('.gx-summary > div').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().top))
        assert.ok(summary.every(top => Math.abs(top - summary[0]) < 1), 'summary counts share one row')
        assert.ok(Math.abs(geometry.notice - geometry.container) < 1 && geometry.aligned, 'progress fills the padded panel with the track below its label')
        for (const selector of ['.gx-panel', '.gx-message', '.gx-home__totals > section', '.gx-starport-step']) {
          const padding = await page.locator(selector).first().evaluate(element => { const style = getComputedStyle(element); return [parseFloat(style.paddingLeft), parseFloat(style.paddingRight), parseFloat(style.paddingTop), parseFloat(style.paddingBottom)] })
          assert.ok(padding[0] >= 16 && padding[1] >= 16, `${selector}: horizontal padding survives all themes and widths`)
          assert.ok(padding[2] >= 16 && padding[3] >= 16, `${selector}: vertical padding`)
        }
        assert.equal(await page.locator('.gx-message').first().evaluate(element => element.offsetWidth), await page.locator('#fixture').evaluate(element => element.clientWidth), 'page states fill the available width')
        const inset = pinned && width >= 768 ? 320 : 0
        assert.ok(Math.abs(geometry.left - inset - (width - geometry.right)) < 1, 'centered in available space')
      }
    }
  }
  await page.evaluate(() => { document.querySelector('.gx-app').classList.remove('gx-nav-pinned'); document.documentElement.dataset.theme = 'dark' })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/galaxy-design-desktop.png' })
  await page.setViewportSize({ width: 390, height: 900 })
  await page.screenshot({ path: '/tmp/galaxy-design-phone.png' })
  const trigger = page.getByRole('button', { name: 'Open dialog', exact: true })
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: 'Confirm preference' })
  await dialog.waitFor()
  assert.equal(await dialog.evaluate(element => element.contains(document.activeElement)), true)
  for (let i = 0; i < 5; i++) await page.keyboard.press('Tab')
  assert.equal(await dialog.evaluate(element => element.contains(document.activeElement)), true, 'Tab stays in dialog')
  await page.keyboard.press('Escape')
  assert.equal(await dialog.count(), 0)
  assert.equal(await trigger.evaluate(element => element === document.activeElement), true, 'focus restored')
  await trigger.click()
  await page.mouse.click(2, 2)
  assert.equal(await dialog.count(), 0, 'backdrop cancels')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  assert.equal(await page.locator('.gx-spinner').first().evaluate(element => getComputedStyle(element).animationName), 'none')
  await page.evaluate(async () => {
    const { createApp } = await import('./vendor/vue/vue.esm-browser.js')
    const { NavigationMap } = await import('./js/navigation-map.js')
    const wrapper = document.createElement('div')
    wrapper.className = 'gx-navigation--fullscreen'
    wrapper.innerHTML = '<div id="map-fixture"></div>'
    document.body.appendChild(wrapper)
    createApp({ ...NavigationMap, mounted() {}, beforeUnmount() {},
      data: () => ({ ...NavigationMap.data(), error: 'Map tiles are unavailable.' }) },
      { data: {}, stale: false }).mount('#map-fixture')
  })
  for (const width of [320, 1280, 3440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.locator('.gx-navigation--fullscreen').scrollIntoViewIfNeeded()
    const bounds = await page.getByRole('alert').filter({ hasText: 'Map tiles are unavailable.' }).boundingBox()
    assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= width && bounds.y + bounds.height <= 900,
      'map errors remain visible above the canvas')
  }
  assert.deepEqual(errors, [])
  // Exercise the real software feed: controls wait for the automatic check,
  // history is fetched on demand, and secondary actions retain confirmations.
  const softwareRequests = []
  const snapshot = { schemaVersion: 1,
    installed: {version:'Fixture',branch:'StarPilot',commit:'a'.repeat(40)},
    updater: {state:'idle',targetBranch:'StarPilot',lastSuccessAt:null,lastFetchAt:null,
      targetChangeFound:true,finalizedUpdateReady:true,failedCount:0},
    operations: {parked:true,availableBranches:['StarPilot','Dom'],selectedTarget:'StarPilot',
      canCheck:true,canFastUpdate:true,canDownload:true,canSelect:true,canInstall:true,
      canRollback:true,canConfigure:true,automaticDownloads:true,reason:null,request:null} }
  await page.route('**/api/software/**', async route => {
    const url = new URL(route.request().url())
    softwareRequests.push(url.search)
    await new Promise(resolve => setTimeout(resolve,250))
    if (route.request().method() === 'POST' && route.request().postDataJSON().action === 'versions') snapshot.operations.selectedTarget = route.request().postDataJSON().branch
    const operations = {...snapshot.operations}
    if (route.request().method() === 'GET' && url.search !== '?history=0') operations.history = {installed:[{hash:'a'.repeat(40),date:'2026-10-01T12:00:00Z',subject:'Installed fixture'}],downloaded:[],
      previous:{hash:'b'.repeat(40),date:'2026-09-28T12:00:00Z',subject:'Previous fixture',branch:'Dom'},
      recent:{branch:operations.selectedTarget,head:'0'.repeat(40),entries:Array.from({length:20}, (_, index) => ({hash:index.toString(16).padStart(40,'0'),date:'2026-10-07T12:00:00Z',subject:index ? `Version ${index}: rendering and update fixes` : ''}))},
      currentReleaseNotes:'Fixture release notes',downloadedReleaseNotes:null}
    await route.fulfill({json:{...snapshot,operations}})
  })
  await page.evaluate(async () => {
    window.fixture.$.appContext.app.unmount()
    document.querySelector('.gx-navigation--fullscreen')?.remove()
    const {createApp} = await import('./vendor/vue/vue.esm-browser.js')
    const {SoftwarePage} = await import('./js/software-status.js')
    window.fixture = createApp(SoftwarePage,{mode:'local',unauthorized:()=>{}}).mount('#fixture')
  })
  const check = page.getByRole('button',{name:'Check for updates',exact:true})
  await page.getByRole('progressbar').waitFor()
  assert.equal(await check.count(),0,'no update controls before the automatic check finishes')
  assert.equal(await page.getByRole('button',{name:'Back up toggles',exact:true}).isEnabled(),true,'backup does not wait for updater status')
  await check.waitFor()
  assert.equal(await check.isEnabled(),true)
  assert.deepEqual(softwareRequests.slice(0,2),['?history=0',''])
  for (const width of [320,390,1280,3440]) {
    await page.setViewportSize({width,height:900})
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,'minimal software stays within viewport')
  }
  assert.equal(await page.getByRole('button',{name:'Refresh software status',exact:true}).count(),0)
  assert.equal(await page.getByText('Update activity',{exact:true}).count(),0)
  for (const [width,path] of [[1280,'/tmp/galaxy-updates-clean-desktop.png'],[390,'/tmp/galaxy-updates-clean-phone.png']]) {
    await page.setViewportSize({width,height:900})
    await page.screenshot({path,fullPage:true})
  }
  assert.equal(await page.getByRole('button',{name:'Fast Update',exact:true}).count(),0)
  assert.equal(await page.getByRole('button',{name:'Normal Update',exact:true}).count(),0)
  for (const name of ['Update']) {
    await page.getByRole('button',{name,exact:true}).click()
    await page.getByRole('dialog').waitFor()
    await page.getByRole('button',{name:'Cancel',exact:true}).click()
  }
  snapshot.updater.finalizedUpdateReady = false
  snapshot.operations.canInstall = false
  await page.evaluate(() => window.fixture.feed.load())
  await page.getByRole('button',{name:'Update',exact:true}).click()
  await page.getByRole('dialog',{name:'Update software'}).waitFor()
  await page.getByRole('button',{name:'Cancel',exact:true}).click()
  await page.getByText('Advanced options',{exact:true}).click()
  await page.getByText('Fixture release notes',{exact:true}).waitFor()
  assert.equal(await page.locator('.gx-version-row').count(),3)
  assert.equal(await page.locator('.gx-version-row strong').first().textContent(),'00000000')
  assert.equal(await page.locator('.gx-version-list').evaluate(element=>getComputedStyle(element).listStyleType),'none')
  assert.equal(await page.getByRole('button',{name:'No changes selected',exact:true}).isEnabled(),false)
  await page.getByRole('button',{name:'Load 10 of 18 collapsed versions',exact:true}).click()
  assert.equal(await page.locator('.gx-version-row').count(),13)
  await page.getByRole('button',{name:'Load 8 collapsed versions',exact:true}).click()
  assert.equal(await page.locator('.gx-version-row').count(),21)
  await page.getByRole('button',{name:'Select previous version',exact:true}).click()
  await page.getByRole('combobox',{name:'Version branch',exact:true}).filter({hasText:'Development — Dom'}).waitFor()
  assert.equal(await page.getByRole('dialog').count(),0,'selecting previous does not install it')
  await page.getByRole('button',{name:'Install selected version',exact:true}).click()
  await page.getByRole('dialog',{name:'Previous version'}).waitFor()
  await page.getByRole('button',{name:'Cancel',exact:true}).click()
  await page.getByRole('combobox',{name:'Version branch',exact:true}).click()
  await page.getByRole('option',{name:/Stable — StarPilot/}).click()
  await page.getByText('Fixture release notes',{exact:true}).waitFor()
  await page.getByRole('button',{name:'Load 10 of 18 collapsed versions',exact:true}).click()
  await page.getByRole('button',{name:'Load 8 collapsed versions',exact:true}).click()
  const scroll = await page.locator('.gx-version-scroll').evaluate(element=>({height:element.clientHeight,content:element.scrollHeight,viewport:innerHeight}))
  assert.ok(scroll.content>scroll.height && scroll.height<=scroll.viewport*.65+1)
  assert.equal(await page.getByRole('button',{name:'Downloaded',exact:true}).count(),0)
  assert.equal(await check.isEnabled(),true,'history refresh keeps update controls enabled')
  const backup = {format:'galaxy-toggles',version:1,createdAt:'2026-10-07T12:00:00Z',metric:false,vehicle:null,
    settings:[{page:'data',key:'AlwaysAllowUploads',value:'Off'}],layout:null}
  const restores = []
  await page.route('**/api/settings/backup', route => route.fulfill({json:backup}))
  await page.route('**/api/settings/restore', route => {
    restores.push(route.request().postDataJSON())
    return route.fulfill({json:{complete:true,restored:1,unchanged:0,skipped:[]}})
  })
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button',{name:'Back up toggles',exact:true}).click()
  const download = await downloadPromise
  assert.equal(download.suggestedFilename(),'toggle-backup.json')
  const file = {name:'toggle-backup.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(backup))}
  await page.getByLabel('Choose toggle backup',{exact:true}).setInputFiles(file)
  await page.getByRole('dialog',{name:'Restore toggles?'}).waitFor()
  await page.getByRole('button',{name:'Cancel',exact:true}).click()
  assert.equal(restores.length,0,'choosing a file alone never changes settings')
  await page.getByLabel('Choose toggle backup',{exact:true}).setInputFiles(file)
  await page.getByRole('dialog').getByRole('button',{name:'Restore',exact:true}).click()
  await page.getByText('1 toggle restored.',{exact:true}).waitFor()
  assert.deepEqual(restores,[backup])
  await page.evaluate(() => window.fixture.$.appContext.app.unmount())
  assert.deepEqual(errors,[])
  console.log('Design system: six widths, both themes, pinned layout, update banner, state cards, modal keyboard/focus/backdrop and reduced motion passed.')
  await browser.close()
})().catch(error => { console.error(error); process.exit(1) })
