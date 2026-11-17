const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')

;(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) })
  try {
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/settings-state-test', route => route.fulfill({ contentType: 'text/html', body:
      '<link rel="stylesheet" href="css/galaxy.css"><link rel="stylesheet" href="css/setup.css"><link rel="stylesheet" href="css/settings.css"><div id="test"></div>' }))
    await page.goto((process.env.GALAXY_URL || 'http://127.0.0.1:8765/') + 'settings-state-test')
    const pages = await page.evaluate(async () => {
      const vue = await import('./vendor/vue/vue.esm-browser.js')
      const settings = await import('./js/settings.js')
      window.mount = (component, props = {}) => {
        window.app?.unmount()
        window.app = vue.createApp(component, { mode: 'local', unauthorized() {}, ...props })
        window.vm = window.app.mount('#test')
      }
      window.requests = []
      window.fetch = (url, options = {}) => url === './api/sounds'
        ? Promise.resolve({ ok: true, status: 200, json: async () => ({ parked: true, job: null, packs: [{ id: 'one', name: 'Test pack', installed: false }] }) })
        : new Promise(resolve => requests.push({ url, options, resolve }))
      window.reply = async (body, status = 200) => {
        const response = { ok: status === 200, status, json: async () => body, clone() { return this } }
        requests.shift().resolve(response)
        await vue.nextTick()
      }
      window.snapshot = name => ({ page: name, title: name, view: 'view', parked: true, rows: [
        { key: 'switch', label: 'Test switch', value: 'Off', choices: ['Off', 'On'], available: true, action: true },
        { key: 'slider', label: 'Test slider', value: '1', defaultValue: '0', resetAvailable: true, minimum: 0, maximum: 10, step: 1, available: true, action: true },
        { key: 'select', label: 'Test select', value: 'Stock', choices: ['Stock', 'Custom'], available: true, action: true },
      ] })
      window.SettingsPage = settings.SettingsPage
      return [...new Set(settings.SETTINGS_SECTIONS.flatMap(section => section.pages))]
        .filter(name => !['ui_layout', 'favorites', 'developer'].includes(name))
        .concat(['sentry', 'vasm', 'standard/acceleration', 'standard/braking', 'standard/following'])
    })
    async function waiting(path) {
      await page.waitForFunction(path => requests[0]?.url.includes(path), path)
    }
    async function verify(selector, value) {
      // Several paint frames must retain the pending value and full opacity.
      const result = await page.evaluate(async ({ selector, value }) => {
        for (let i = 0; i < 6; i++) {
          await new Promise(requestAnimationFrame)
          const input = document.querySelector(selector)
          if (input.disabled || !input.closest('[inert]') || (input.type === 'checkbox' ? input.checked : input.value) !== value) return false
          const visible = input.type === 'checkbox' ? input.nextElementSibling : input
          if (getComputedStyle(visible).opacity !== '1') return false
          for (const button of document.querySelectorAll('.gx-setting-default, [aria-label="Download Test pack"]')) {
            if (button.disabled || getComputedStyle(button).opacity !== '1') return false
          }
        }
        return true
      }, { selector, value })
      assert.equal(result, true, `Pending ${selector} stays stable`)
    }
    for (const name of pages) {
      await page.evaluate(name => { requests = []; mount(SettingsPage, { initialPage: name }) }, name)
      await waiting('/pages/')
      await page.evaluate(name => reply(snapshot(name)), name)
      await page.getByRole('switch', { name: 'Test switch' }).waitFor()
      const saved = await page.evaluate(name => snapshot(name), name)
      for (const [index, selector, value] of [[0, '[role="switch"]', true], [1, 'input[type="range"]', '7'], [2, '.gx-select select', 'Custom']]) {
        if (index === 0) await page.locator(selector).click()
        else if (index === 1) await page.locator(selector).evaluate(input => {
          input.value = '7'
          input.dispatchEvent(new Event('input', { bubbles: true }))
          input.dispatchEvent(new Event('change', { bubbles: true }))
        })
        else {
          await page.getByRole('combobox', { name: 'Test select' }).click()
          await page.getByRole('option', { name: 'Custom', exact: true }).click()
        }
        await waiting('/preview')
        if (index < 2) await verify(selector, value)
        else assert.equal(await page.getByRole('combobox', { name: 'Test select' }).textContent(), 'Custom')
        await page.evaluate(() => reply({ intent: 'intent', question: 'Save?' }))
        await waiting('/confirm')
        if (index < 2) await verify(selector, value)
        saved.rows[index].value = index === 0 ? 'On' : value
        await page.evaluate(() => reply({ saved: true }))
        await waiting('/pages/')
        if (index < 2) await verify(selector, value)
        await page.evaluate(saved => reply(saved), saved)
        await page.waitForFunction(selector => !document.querySelector(selector).closest('[inert]'), selector)
      }
    }
    // Error recovery must restore the confirmed value and retain the row node.
    await page.locator('[role="switch"]').click()
    await waiting('/preview')
    await verify('[role="switch"]', false)
    await page.evaluate(() => { window.rowNode = document.querySelector('.gx-row'); return reply({ error: 'Rejected' }, 400) })
    await page.waitForFunction(() => document.querySelector('[role="switch"]').checked)
    assert.equal(await page.evaluate(() => rowNode === document.querySelector('.gx-row')), true)
    // Reset Default is the same guarded preview/confirm/readback sequence.
    await page.evaluate(() => { vm.feed.load() })
    await waiting('/pages/')
    await page.evaluate(name => reply(snapshot(name)), pages.at(-1))
    await page.getByRole('button', { name: 'Reset Test slider to default' }).click()
    await waiting('/reset-default')
    await verify('input[type="range"]', '1')
    await page.evaluate(() => reply({ intent: 'reset', question: 'Reset?' }))
    await waiting('/confirm')
    await verify('input[type="range"]', '1')
    await page.evaluate(() => reply({ saved: true }))
    await waiting('/pages/')
    await verify('input[type="range"]', '1')
    await page.evaluate(name => { const data = snapshot(name); data.rows[1].value = '0'; return reply(data) }, pages.at(-1))
    await page.waitForFunction(() => !document.querySelector('.gx-setting-default'))
    const layout = JSON.parse(execFileSync(process.env.PYTHON || 'python3', ['-c',
      'import json; from openpilot.starpilot.ui.onroad_customization import default_document, customization_metadata; print(json.dumps(dict(document=default_document(), defaults=default_document(), metadata=customization_metadata(), revision="a"*64, editable=True, valid=True, activeProfile="large")))'], { encoding: 'utf8' }))
    const audited = await page.evaluate(async layout => {
      const { nextTick } = await import('./vendor/vue/vue.esm-browser.js')
      const clone = value => JSON.parse(JSON.stringify(value))
      const prepareLayout = vm => {
        vm.feed.publish({ status: 'ready', data: clone(layout), draft: clone(layout.document) })
        vm.state.draft.clock24Hour = !vm.state.draft.clock24Hour
      }
      const prepareModels = vm => Object.assign(vm, { loading: false, selectionUncertain: false,
        activeSmallModel: 'one', activeBigModel: '', models: ['one', 'two'].map(value => ({ value, label: value, requiresGpu: false, installed: true, selectable: true })),
        capabilities: { select: true, download: true, downloadAll: true, refresh: true, favorites: true },
        status: { isOnroad: false, downloading: false, randomizer: false, models: [], capabilities: { select: true, download: true, downloadAll: true, refresh: true, favorites: true } } })
      const cases = [
        ['pip', 'PipPage', vm => {
          const data = { ...snapshot('pip'), editorRow: 0, editor: { width: 1344, height: 760, cropSize: 300, centerLeft: [350, 350], centerRight: [1000, 350], invert: false } }
          vm.feed.publish({ status: 'ready', data }); vm.state.imageName = 'Cabin snapshot'
        }],
        ['vasm', 'VasmPage', vm => {
          const data = { ...snapshot('vasm'), editorRow: 0, editor: { width: 1344, height: 760, cameraLeft: [[100, 100], [500, 100], [500, 500]], cameraRight: [[800, 100], [1000, 100], [1000, 500]] } }
          vm.feed.publish({ status: 'ready', data }); vm.state.imageName = 'Cabin snapshot'
        }],
        ['longitudinal-curves', 'LongitudinalCurvesPage', vm => vm.feed.publish({ status: 'ready', data: snapshot('standard/acceleration') })],
        ['favorites', 'FavoritesPage', vm => vm.feed.publish({ status: 'ready', data: { editable: true, valid: true,
          slots: Array.from({ length: 3 }, () => ({ key: 'test', label: 'Test', enabled: true, show_onroad: true })),
          options: [{ key: 'test', label: 'Test', section: 'Visual' }], states: Array.from({ length: 3 }, () => ({ stateLabel: 'Off', reason: '' })) } })],
        ['controllers', 'ControllersPage', vm => vm.feed.publish({ status: { editable: true, available: true, revision: 'a'.repeat(64), enabled: true,
          slots: Array.from({ length: 13 }, (_, index) => ({ index, key: null, label: `Button ${index}` })),
          devices: [], options: [], learning: null, testing: false, bindings: [], lastPress: null }, busy: false })],
        ['cloud-provider', 'CloudProviderPage', vm => { vm.status = { selected: 'comma', active: 'comma', canSelect: true,
          providers: [{ id: 'comma', label: 'comma' }, { id: 'konik', label: 'konik' }] } }],
        ['software-status', 'SoftwarePage', vm => vm.feed.publish({ status: 'ready', data: { installed: { branch: 'Dom', version: 'Test', commit: 'a'.repeat(40) },
          updater: { state: 'idle', finalizedUpdateReady: false }, operations: { parked: true, selectedTarget: 'Dom', availableBranches: ['Dom'],
            canCheck: true, canFastUpdate: true, canDownload: true, canSelect: true, canConfigure: true, automaticDownloads: true, request: null } } })],
        ['starport', 'StarPortPage', vm => Object.assign(vm, { setup: { enabled: true, parked: true, installReady: true, serviceReady: true,
          bluetoothEnabled: true, identity: { installed: true }, import: { state: 'idle' } }, runtime: { running: false, auto_connect: true }, pairing: { active: false, devices: [] } })],
        ['offline-road-maps', 'OfflineRoadMapsPanel', vm => { vm.data = { areas: [], settings: { saveDriven: true }, service: {}, position: null,
          constants: { minRadiusKm: 2, maxRadiusKm: 200, defaultRadiusKm: 25, maxAreas: 24 } } }],
        ['onroad-layout', 'OnroadLayoutPage', prepareLayout],
        ['onroad-layout', 'OnroadLayoutPage', prepareLayout, { projection: true }],
        ['models', 'ModelsPage', prepareModels],
        ['models', 'ModelsPage', vm => { prepareModels(vm); Object.assign(vm, { pairView: 'pair',
          labConfig: { enabled: false, lateralModel: 'one', longitudinalModel: 'two' }, lab: {
            configuration: { enabled: false, lateralModel: 'one', longitudinalModel: 'two' },
            isOnroad: false, runtimeSupported: true, chestnutReady: true, runtime: {}, download: { downloading: false }, summary: { published: 2 },
            models: ['one', 'two'].map(value => ({ value, label: value, small: true, modelLabEligible: true, modelLabArtifactAvailable: true, modelLabArtifactInstalled: true })),
            capabilities: { configure: true, download: true, delete: true, cancel: true, refresh: true } } }) }],
        ['sentry-notifications', 'SentryNotifications', vm => { vm.notifications = { subscriptionCount: 0, subscriptions: [],
          channels: Object.fromEntries(vm.channels.map(name => [name, { enabled: true, configured: true, pending: 0, lastState: 'idle', lastError: '' }])) } }],
        ['drive-state', 'DriveStatePanel', vm => { vm.state = { mode: 'auto', available: true, effective: 'offroad', overrideAllowed: true } }],
        ['bluetooth', 'BluetoothPage', vm => { vm.status = { parked: true, available: true, powered: true, saved: [], nearby: [], pairing: null } }],
        ['toggle-backup', 'ToggleBackup', () => {}],
        ['flm', 'FlmPage', vm => Object.assign(vm, { inventoryStatus: 'ready', inventory: { segments: [] }, selected: ['test--0'] })],
        ['recording-actions', 'RecordingActions', () => {}, { recording: { displayName: 'Test drive', routeId: 'test', preserved: false } }],
        ['vehicle-controls', 'VehicleControlsPage', vm => { vm.state.status = 'ready'; vm.state.data = { parked: true, readable: true, selected: null, vehicles: [] } }],
        ['galaxy', 'GalaxyPage', vm => Object.assign(vm, { loading: false, paired: true, url: 'https://galaxy.test' })],
        ['navigation', 'NavigationPage', vm => { vm.data = { enabled: true, hasKey: false, favorites: [], recents: [] } }],
        ['map-operations', 'MapOperationsPanel', vm => { vm.setup = { parked: true, packageReady: true, snapshotReady: true }; vm.catalog = { regions: [] } }],
      ]
      for (const [file, name, prepare, props] of cases) {
        const component = (await import(`./js/${file}.js`))[name]
        mount({ ...component, mounted() {}, beforeUnmount() {} }, { go() {}, localAccess: true, ...props })
        prepare(vm)
        await nextTick()
        const controls = [...document.querySelectorAll('#test button, #test input, #test select, #test .gx-switch__track, #test .gx-row__label')]
          .map(node => ({ node, disabled: node.disabled, opacity: getComputedStyle(node).opacity }))
        if (!controls.length) throw new Error(`${name}: no controls rendered`)
        const setBusy = value => {
          if (name === 'SentryNotifications') vm.notificationBusy = value
          else if (['ControllersPage', 'VehicleControlsPage'].includes(name)) vm.state.busy = value
          else if (vm.state?.status) vm.state.status = value ? 'saving' : 'ready'
          else vm.busy = value
        }
        setBusy(true)
        await nextTick()
        const saving = controls.map(({ node }) => ({ node, disabled: node.disabled, opacity: getComputedStyle(node).opacity }))
        for (let frame = 0; frame < 4; frame++) {
          await new Promise(requestAnimationFrame)
          for (const { node, disabled, opacity } of saving) {
            if (!node.isConnected || node.disabled !== disabled || getComputedStyle(node).opacity !== opacity)
              throw new Error(`${name}: control flickered during saving: ${node.getAttribute('aria-label') || node.textContent}`)
          }
        }
        setBusy(false)
        await nextTick()
        for (const { node, disabled } of controls) {
          if (!node.isConnected || node.disabled !== disabled) throw new Error(`${name}: controls did not recover after saving`)
        }
      }
      const component = (await import('./js/models.js')).ModelsPage
      mount({ ...component, mounted() {}, beforeUnmount() {} })
      prepareModels(vm)
      vm.manager.action = (action, model) => new Promise(resolve => {
        window.finishModel = success => {
          if (success) vm.activeSmallModel = model.value
          resolve(success ? { message: 'Selection saved.' } : null)
        }
      })
      await nextTick()
      return cases.map(([, name]) => name)
    }, layout)
    for (const success of [false, true]) {
      await page.getByRole('button', { name: 'Set Active Small' }).click()
      await page.waitForFunction(() => !!vm.busy)
      assert.equal(await page.locator('.gx-model-summary-item__value').first().textContent(), 'two')
      await page.evaluate(success => finishModel(success), success)
      await page.waitForFunction(() => !vm.busy)
      assert.equal(await page.locator('.gx-model-summary-item__value').first().textContent(), success ? 'two' : 'one')
    }
    assert.deepEqual(errors, [])
    console.log(`Settings state: ${pages.length} settings pages and ${audited.length} specialized pages; delayed preview/save/readback, Default/download buttons, stable control nodes/opacity and failed-save recovery passed.`)
    await page.evaluate(() => app.unmount())
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
