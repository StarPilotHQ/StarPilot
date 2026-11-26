const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const web = path.resolve(__dirname, '../../web')

;(async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage({ viewport: { width: 900, height: 1000 } })
    const errors = []
    let deviceMode = 'simple', failNextSave = false
    page.on('pageerror', error => errors.push(error.message))
    await page.route('http://levels.test/**', route => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname === '/api/settings/mode') {
        if (failNextSave && route.request().method() === 'POST') { failNextSave = false; return route.fulfill({ status: 503, json: { error: 'Save failed' } }) }
        if (route.request().method() === 'POST') deviceMode = route.request().postDataJSON().mode
        return route.fulfill({ json: { mode: deviceMode } })
      }
      if (pathname.startsWith('/api/')) {
        const name = decodeURIComponent(pathname.split('/').at(-1))
        const rows = name === 'lane_change' ? [
          { id: 'enabled', label: 'Enable assistance', value: 'On', choices: ['Off', 'On'], available: true, action: true },
          { id: 'speed', label: 'Minimum speed', value: '42', minimum: 0, maximum: 100, step: 1, available: true, action: true, level: 'advanced' },
          { id: 'experiment', label: 'Turn experiment', value: 'Off', choices: ['Off', 'On'], available: true, action: true, level: 'advanced' },
        ] : name === 'hub' ? [{ label: 'Lane Changes', page: 'lane_change', available: true, value: 'Configure lane changes', levels: ['simple', 'advanced'] },
            { label: 'Developer', page: 'torque/developer', available: true, levels: ['advanced'] }]
          : [{ id: 'ExperimentalSpeedNudge', label: 'Default control', value: 'Off', choices: ['Off', 'On'], available: true, action: true, ...(name === 'torque/developer' ? { level: 'advanced' } : {}) }]
        return route.fulfill({ json: { page: name, title: name, view: 'test-view', parked: true, rows, settingsMode: deviceMode, ...(name === 'torque/developer' ? { levels: ['advanced'] } : {}),
          ...(name === 'hub' ? { pageLevels: { aol: [], lane: [], torque: [], 'torque/developer': ['advanced'], lane_change: ['simple', 'advanced'], wheel: ['advanced'] } } : {}) } })
      }
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><head>
        <link rel="stylesheet" href="/css/galaxy.css"><link rel="stylesheet" href="/css/settings.css"></head>
        <body><main id="fixture"></main><script type="module">
        import { createApp } from '/vendor/vue/vue.esm-browser.js'
        import { SettingsPage } from '/js/settings.js'
        window.mount = initialPage => { window.app?.unmount(); window.app = createApp(SettingsPage, {mode:'local', unauthorized(){}, initialPage}); window.vm = app.mount('#fixture') }; mount('lane_change')
        </script></body></html>` })
      return route.fulfill({ body: readFileSync(path.join(web, pathname)), contentType: pathname.endsWith('.js') ? 'text/javascript' : 'text/css' })
    })
    await page.goto('http://levels.test/')
    const mode = page.getByRole('button', { name: 'Settings mode', exact: true })
    async function toggleMode() {
      await page.waitForFunction(() => document.querySelectorAll('.gx-settings__mode').length === 1)
      await mode.click()
      await page.waitForFunction(() => !vm.state.settingsPreference.saving)
    }
    const slider = page.getByRole('slider', { name: 'Minimum speed', includeHidden: true })
    const experiment = page.getByRole('switch', { name: 'Turn experiment', includeHidden: true })
    await page.getByRole('switch', { name: 'Enable assistance' }).waitFor()
    assert.equal(await slider.isVisible(), false)
    assert.equal(await experiment.isVisible(), false)
    const pill = await mode.boundingBox()
    await page.mouse.move(pill.x + pill.width / 2, pill.y + pill.height / 2)
    await page.mouse.down()
    await page.waitForTimeout(180)
    assert(await mode.evaluate(button => {
      const transform = getComputedStyle(button).transform
      return transform !== 'none' && new DOMMatrixReadOnly(transform).a < 1
    }), 'mode pill uses the shared Galaxy button press animation')
    await page.mouse.move(0, 0)
    await page.mouse.up()
    await page.evaluate(() => { window.originalSlider = document.querySelector('input[type=range]') })
    await toggleMode()
    await page.waitForTimeout(35)
    assert(await slider.evaluate(node => node.closest('.gx-row').parentElement.getAnimations().length > 0))
    await page.waitForTimeout(300)
    assert.equal(await slider.inputValue(), '42')
    assert.equal(await experiment.isVisible(), true)
    assert.equal(await page.getByRole('img', { name: 'Advanced control' }).count(), 2)
    assert.equal(await mode.locator('.bi-chevron-contract').count(), 1)
    assert.equal(await page.getByRole('listbox').count(), 0)
    assert.equal((await mode.innerText()).trim(), 'Advanced')
    failNextSave = true
    await toggleMode()
    assert.equal((await mode.innerText()).trim(), 'Advanced', 'failed saves keep the device mode selected')
    assert.equal(deviceMode, 'advanced')
    assert.equal(await slider.isVisible(), true)
    await mode.press('Space')
    await page.waitForFunction(() => !vm.state.settingsPreference.saving)
    await page.waitForTimeout(35)
    assert(await slider.evaluate(node => node.closest('.gx-row').parentElement.getAnimations().length > 0))
    await page.waitForTimeout(300)
    assert.equal(await slider.isVisible(), false)
    assert(await page.evaluate(() => originalSlider === document.querySelector('input[type=range]')))
    await toggleMode()
    await page.evaluate(() => localStorage.clear())
    await page.reload()
    await page.getByRole('switch', { name: 'Enable assistance' }).waitFor()
    assert.equal(await slider.isVisible(), true)
    await page.evaluate(() => vm.selectSection(vm.sections.find(section => section.id === 'longitudinal')))
    await page.waitForFunction(() => vm.state.data?.page === 'hub')
    assert.equal((await mode.innerText()).trim(), 'Advanced')
    assert.equal(await page.locator('.gx-row').filter({ hasText: 'Additional settings' }).count(), 0)
    await page.evaluate(() => vm.open('conditional/cem'))
    await page.getByRole('switch', { name: 'Default control' }).waitFor()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await toggleMode()
    assert.equal(await page.getByRole('switch', { name: 'Default control' }).isVisible(), true,
      'missing metadata defaults to Simple regardless of technical-looking route or control ID')
    await page.evaluate(() => mount('hub'))
    await page.waitForFunction(() => vm.state.data?.page === 'hub')
    const developerCategory = page.locator('.gx-row').filter({ hasText: /^Developer/ })
    assert.equal(await developerCategory.isVisible(), false)
    assert.equal(await page.getByRole('button', { name: 'Wheel Controls', exact: true }).count(), 0)
    await toggleMode()
    await developerCategory.waitFor()
    await developerCategory.getByRole('button', { name: 'Manage' }).click()
    await page.getByRole('switch', { name: 'Default control' }).waitFor()
    await toggleMode()
    await page.waitForFunction(() => vm.state.data?.page === 'hub')
    assert.equal(await developerCategory.isVisible(), false, 'collapse exits an ordinary category whose controls are all advanced')
    await toggleMode()
    await page.getByRole('button', { name: 'Wheel Controls', exact: true }).waitFor()
    await page.getByRole('button', { name: 'Wheel Controls', exact: true }).click()
    await page.getByRole('switch', { name: 'Default control' }).waitFor()
    await toggleMode()
    await page.waitForFunction(() => vm.activeSection.id !== 'wheel')
    assert.equal(await page.getByRole('button', { name: 'Wheel Controls', exact: true }).count(), 0)
    await page.locator('.gx-row').filter({ hasText: 'Lane Changes' }).getByRole('button', { name: 'Manage' }).click()
    await page.getByRole('switch', { name: 'Enable assistance' }).waitFor()
    await toggleMode()
    assert(await mode.evaluate(button => !!button.closest('.gx-section__header')))
    assert.equal(deviceMode, 'advanced')
    await page.setViewportSize({ width: 390, height: 844 })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.evaluate(() => { mount('hub'); vm.feed.load('hub') })
    await page.waitForFunction(() => vm.state.data?.page === 'hub')
    assert(await page.evaluate(() => {
      const header = document.querySelector('.gx-settings__header')
      const mode = header.querySelector('.gx-settings__mode').getBoundingClientRect()
      const book = header.querySelector('.gx-wiki-help').getBoundingClientRect()
      return Math.abs(mode.y + mode.height / 2 - book.y - book.height / 2) < 1
    }), 'mode pill and wiki action share a vertical center')
    assert.deepEqual(errors, [])
    console.log('Shared row metadata: category defaults, empty navigation, preserved controls, device persistence, inline motion and outlined one-click mode toggle passed')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
