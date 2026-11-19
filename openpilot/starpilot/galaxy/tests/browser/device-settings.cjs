const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const errors = [], writes = [], requests = []
    let parked = true, enabled = false, hold = false, pendingRow = 0
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/device-settings-test', route => route.fulfill({ contentType: 'text/html', body:
      '<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="css/galaxy.css"><link rel="stylesheet" href="css/setup.css"><link rel="stylesheet" href="css/settings.css"><main class="gx-content"><div id="test"></div></main>' }))
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      requests.push(path)
      let data
      if (path.endsWith('/connect/provider')) data = { active: 'comma', selected: 'comma', revision: 'a'.repeat(64), restartRequired: false,
        canSelect: parked, providers: [{ id: 'comma', label: 'comma', url: 'https://connect.comma.ai' }] }
      else if (path.endsWith('/connect/proxy')) data = { enabled: false, address: '', origin: '', revision: 'proxy', canEdit: parked, passwordConfigured: true }
      else if (path.endsWith('/pages/hub')) data = { page: 'hub', title: 'Driving Controls', view: 'hub', parked,
        rows: [{ page: 'developer', label: 'Developer', available: true }, { page: 'profiles', label: 'Long Planner', available: true }] }
      else if (path.endsWith('/pages/display')) data = { page: 'display', title: 'Display', view: 'display', parked, rows: [{label:'Screen Brightness',value:'Auto',available:true}] }
      else if (path.endsWith('/pages/data')) data = { page: 'data', title: 'Data & Connections', view: 'data', parked, rows: [{label:'Always Allow Uploads',value:'Off',choices:['Off','On'],available:parked}] }
      else if (path.endsWith('/pages/starport_options')) data = { page: 'starport_options', title: 'StarPort', view: 'starport', parked, rows: [{label:'Stay on Wi-Fi',value:'On',choices:['Off','On'],available:parked}] }
      else if (path.endsWith('/pages/localization')) data = { page: 'localization', title: 'Localization', view: 'localization', parked, rows: [{label:'Language',value:'English',available:true}] }
      else if (path.endsWith('/pages/developer')) data = { page: 'developer', title: 'Developer', view: 'driving', parked,
        rows: [{ label: 'Use VOACC', value: enabled ? 'On' : 'Off', choices: ['Off', 'On'], available: parked, action: true, reason: 'Applies next drive.' }, { label: 'Always Hold Enhanced Stops', value: hold ? 'On' : 'Off', choices: ['Off', 'On'], available: parked, action: true, reason: 'Applies next drive.' }] }
      else if (path.endsWith('/pages/resets')) data = { page: 'resets', title: 'Device resets', view: 'resets', parked,
        rows: ['Reset Driver Monitoring', 'Reset Calibration'].map(label => ({ label, value: 'Ready', confirm: true, available: parked, action: true })) }
      else if (path.endsWith('/preview')) { writes.push(route.request().postDataJSON()); pendingRow = writes.at(-1).row; data = { intent: 'voacc', proposed: 'On', question: 'Set Use VOACC to On?' } }
      else if (path.endsWith('/confirm')) { if (pendingRow === 1) hold = true; else enabled = true; data = { saved: true } }
      else return route.fulfill({ status: 404, json: { error: 'Unexpected endpoint' } })
      return route.fulfill({ json: data })
    })
    await page.goto((process.env.GALAXY_URL || 'http://127.0.0.1:8765/') + 'device-settings-test')
    await page.evaluate(async () => {
      const { createApp } = await import('./vendor/vue/vue.esm-browser.js')
      const { SettingsPage } = await import('./js/settings.js')
      window.mount = props => {
        window.app?.unmount()
        window.app = createApp(SettingsPage, { mode: 'local', unauthorized() {}, ...props })
        window.vm = window.app.mount('#test')
      }
      mount({ initialSection: 'device' })
    })
    assert.equal(await page.getByRole('button', { name: 'Developer', exact: true }).count(), 0)
    await page.locator('.gx-row').filter({has:page.getByText('Data & Connections', {exact:true})}).getByRole('button', {name:'Manage'}).click()
    await page.getByText('Reverse proxy', { exact: true }).waitFor()
    await page.getByRole('switch', {name:'Always Allow Uploads',exact:true}).waitFor()
    assert.equal(await page.getByRole('switch',{name:'Hide app download reminder',exact:true}).count(),0)
    assert.equal(await page.getByRole('switch', { name: 'Use VOACC', exact: true }).count(), 0)
    for (const width of [320, 390, 1280]) {
      await page.setViewportSize({ width, height: 900 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.equal(await page.getByLabel('Proxy IP address', { exact: true }).isVisible(), true)
    }
    await page.evaluate(() => vm.back())
    assert.equal(await page.locator('.gx-settings-tabs button').last().textContent(), 'Device & System')
    assert.equal(await page.getByRole('button',{name:'Localization',exact:true}).count(),0)
    const manage = label => page.locator('.gx-row').filter({has:page.getByText(label,{exact:true})}).getByRole('button',{name:'Manage'})
    assert.equal(await manage('Display').count(), 0)
    await page.getByRole('button', {name:'Visual (Display & UI)',exact:true}).click()
    await manage('Display').click()
    await page.getByText('Screen Brightness',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>vm.activeSection.id),'visual')
    await page.evaluate(()=>vm.back())
    await page.getByRole('button', {name:'Device & System',exact:true}).click()
    await manage('StarPort').click()
    await page.getByRole('switch',{name:'Stay on Wi-Fi',exact:true}).waitFor()
    await page.getByText('Reverse proxy',{exact:true}).waitFor({state:'hidden'})
    await page.evaluate(()=>vm.back())
    await manage('Browser preferences').click()
    const reminder = page.getByRole('switch',{name:'Hide app download reminder',exact:true})
    await reminder.waitFor()
    await reminder.check()
    assert.equal(await page.evaluate(()=>localStorage.getItem('galaxy-hide-app-download-reminder')),'1')
    assert(!requests.some(path=>path.includes('browser_preferences')))
    await page.evaluate(()=>vm.back())
    await manage('Localization').click()
    await page.getByText('Language',{exact:true}).waitFor()
    await page.evaluate(()=>vm.back())
    await page.getByRole('button', { name: 'Longitudinal (Speed & Following)', exact: true }).click()
    assert.equal(await page.getByText('Longitudinal options', {exact:true}).count(), 0)
    assert.equal(await page.getByRole('switch', {name:'Use VOACC',exact:true}).count(), 0)
    await page.evaluate(() => mount({ initialPage: 'developer', title: 'Developer' }))
    const toggle = page.getByRole('switch', { name: 'Use VOACC', exact: true })
    await toggle.waitFor()
    await toggle.click()
    await page.waitForFunction(() => vm.state.data?.rows[0]?.value === 'On')
    assert.equal(writes[0].value, 'On')
    const holdToggle = page.getByRole('switch', { name: 'Always Hold Enhanced Stops', exact: true })
    await holdToggle.click()
    await page.waitForFunction(() => vm.state.data?.rows[1]?.value === 'On')
    assert.equal(writes[1].value, 'On')
    parked = false
    await page.evaluate(() => vm.feed.load('developer'))
    assert.equal(await toggle.isDisabled(), true)
    assert.equal(await holdToggle.isDisabled(), true)
    await page.evaluate(() => mount({ initialPage: 'resets', title: 'Device resets' }))
    await page.getByText('Reset Driver Monitoring', { exact: true }).waitFor()
    await page.getByText('Reset Calibration', { exact: true }).waitFor()
    assert.equal(await toggle.count(), 0)
    assert.deepEqual(errors, [])
    console.log('Device category ordering, merged data/cloud settings, separate StarPort, local browser preferences and Localization navigation passed.')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exit(1) })
