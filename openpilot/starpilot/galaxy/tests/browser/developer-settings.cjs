const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')

;(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) })
  try {
    const page = await browser.newPage()
    const errors = [], saves = []
    let enabled = false, parked = true
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/developer-settings-test', route => route.fulfill({ contentType: 'text/html', body:
      '<link rel="stylesheet" href="css/galaxy.css"><link rel="stylesheet" href="css/setup.css"><link rel="stylesheet" href="css/settings.css"><div id="test"></div>' }))
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname
      let data
      if (path.endsWith('/connect/provider')) data = { active: 'comma', selected: 'comma', revision: 'a'.repeat(64),
        restartRequired: false, canSelect: parked, providers: [
          { id: 'comma', label: 'comma', url: 'https://connect.comma.ai' },
          { id: 'konik', label: 'Konik', url: 'https://stable.konik.ai' }] }
      else if (path.endsWith('/connect/proxy')) data = { enabled: false, address: '', origin: '', revision: 'proxy', canEdit: parked, passwordConfigured: true }
      else if (path.endsWith('/pages/developer')) data = { page: 'developer', title: 'Developer driving options',
        view: 'developer', parked, rows: [{ label: 'Use VOACC', value: enabled ? 'On' : 'Off', choices: ['Off', 'On'],
          available: parked, action: true, reason: 'Vision-only lead selection. Applies next drive.', defaultValue: 'Off' }] }
      else if (path.endsWith('/pages/hub')) data = { page: 'hub', title: 'Driving Controls', view: 'hub', parked,
        rows: [['slc', 'Speed Limit Controller'], ['curve', 'Curve Speed Controller'], ['conditional', 'Conditional Driving Modes'],
          ['profiles', 'Long Planner'], ['aggressive', 'Aggressive Personality']].map(([page, label]) => ({ page, label, available: true })) }
      else if (path.endsWith('/preview')) {
        saves.push(route.request().postDataJSON())
        data = { intent: 'voacc', proposed: 'On', question: 'Set Use VOACC to On?' }
      } else if (path.endsWith('/confirm')) { enabled = true; data = { saved: true } }
      else return route.fulfill({ status: 404, json: { error: 'Unexpected endpoint' } })
      return route.fulfill({ json: data })
    })
    await page.goto((process.env.GALAXY_URL || 'http://127.0.0.1:8765/') + 'developer-settings-test')
    await page.evaluate(async () => {
      const { createApp } = await import('./vendor/vue/vue.esm-browser.js')
      const { SettingsPage } = await import('./js/settings.js')
      window.mount = section => {
        window.app?.unmount()
        window.app = createApp(SettingsPage, { mode: 'local', unauthorized() {}, initialSection: section })
        window.vm = window.app.mount('#test')
      }
      mount('developer')
    })
    const toggle = page.getByRole('switch', { name: 'Use VOACC', exact: true })
    await toggle.waitFor()
    assert.equal(await toggle.isChecked(), false)
    await toggle.click()
    await page.waitForFunction(() => vm.state.status === 'ready' && vm.state.data.rows[0].value === 'On')
    assert.equal(await toggle.isChecked(), true)
    assert.equal(saves[0].value, 'On')
    for (const width of [320, 1280]) {
      await page.setViewportSize({ width, height: 900 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      for (const label of ['Proxy IP address', 'Public HTTPS origin']) {
        const field = page.getByLabel(label, { exact: true })
        const layout = await field.evaluate(element => ({ width: element.getBoundingClientRect().width,
          parent: element.closest('.gx-row').getBoundingClientRect().width }))
        assert.ok(layout.width > layout.parent * .75, `${label} fills its settings row`)
      }
      const inset = await page.locator('form .gx-panel.gx-actions').evaluate(element => parseFloat(getComputedStyle(element).paddingLeft))
      assert.ok(inset > 0, 'proxy Save uses the shared panel inset')
    }
    await page.getByRole('button', { name: 'Longitudinal (Speed & Following)', exact: true }).click()
    await page.locator('.gx-panel-enter-active').waitFor()
    assert.ok(await page.locator('.gx-panel-enter-active').evaluate(element => new DOMMatrix(getComputedStyle(element).transform).m41) < 0)
    await page.getByText('Long Planner', { exact: true }).waitFor()
    await page.waitForFunction(() => document.querySelectorAll('.gx-panel-stage .gx-row').length === 5)
    const order = await page.locator('.gx-panel-stage .gx-row').allTextContents()
    assert.equal(order.length, 5)
    for (const [index, label] of ['Long Planner', 'Conditional Driving Modes', 'Curve Speed Controller',
      'Speed Limit Controller', 'Aggressive Personality'].entries()) assert.ok(order[index].includes(label))
    assert.equal(await toggle.count(), 0)
    parked = false
    await page.getByRole('button', { name: 'Developer', exact: true }).click()
    const developerPanel = page.locator('.gx-panel-enter-active')
    await developerPanel.waitFor()
    assert.ok(await developerPanel.evaluate(element => new DOMMatrix(getComputedStyle(element).transform).m41) > 0)
    assert.equal(await developerPanel.getByText('Reverse proxy', { exact: true }).count(), 1, 'the proxy card enters with Developer')
    await toggle.waitFor()
    assert.equal(await toggle.isDisabled(), true)
    await page.waitForFunction(() => !document.querySelector('.gx-panel-enter-active, .gx-panel-leave-active'))
    await page.getByRole('button', { name: 'Vehicle', exact: true }).click()
    await page.locator('.gx-panel-enter-active').waitFor()
    await page.waitForFunction(() => !document.querySelector('.gx-panel-enter-active, .gx-panel-leave-active'))
    await page.getByRole('button', { name: 'Developer', exact: true }).click()
    await page.locator('.gx-panel-enter-active').getByText('Reverse proxy', { exact: true }).waitFor()
    await page.waitForFunction(() => !document.querySelector('.gx-panel-enter-active, .gx-panel-leave-active'))
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.getByRole('button', { name: 'Longitudinal (Speed & Following)', exact: true }).click()
    await page.getByText('Long Planner', { exact: true }).waitFor()
    await page.waitForTimeout(50)
    assert.equal(await page.locator('.gx-panel-enter-active, .gx-panel-leave-active').count(), 0)
    assert.deepEqual(errors, [])
    console.log('Developer VOACC: saved toggle, parked guard, phone/desktop layout and longitudinal ordering passed.')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exit(1) })
