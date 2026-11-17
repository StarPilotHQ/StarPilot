import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { DRIVING_GROUPS, DRIVING_PAGES, DrivingPage, drivingPage } from "../web/js/driving.js"
import { SettingsFeed } from "../web/js/settings.js"

const items = DRIVING_GROUPS.flatMap((group) => group.items)
assert.deepEqual(new Set(items.map((item) => item.page)), new Set(Object.keys(DRIVING_PAGES)))
assert.equal(items.find((item) => item.page === "traffic")?.title, "Traffic Mode")
for (const item of items) {
  assert.equal(drivingPage(`/driving/${item.page}`), item.page)
  assert.match(DrivingPage.template, /go\('\/driving\/' \+ item\.page\)/)
}
for (const path of ["/driving", "/driving/unknown", "/driving/aol/extra", "/driving/../aol", "/settings/aol"]) {
  assert.equal(drivingPage(path), null)
}

const app = readFileSync(new URL("../web/js/app.js", import.meta.url), "utf8")
assert.match(app, /drivingSettingsPage\(\) \{ return drivingPage\(route\.path\) \}/)
assert.match(app, /<SettingsPage[^>]* v-else-if="drivingSettingsPage"[^>]*:initial-page="drivingSettingsPage"/)
assert.match(app, /<DrivingPage[^>]* v-else-if="route\.path === '\/driving'"/)
assert(app.includes("<FlmPage v-else-if=\"['/tuning', '/tuning/flm', '/tuning/plots'].includes(route.path)\""))
assert(app.includes(':initial-view="route.path === \'/tuning/plots\' ? \'plots\' : \'overview\'"'))

for (const page of ["aol", "conditional", "curve", "slc", "torque", "profiles", "traffic"]) {
  const requests = []
  const feed = new SettingsFeed({ publish: () => {}, fetcher: (url) => {
    requests.push(url)
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ page, rows: [], view: "saved-view" }) })
  } })
  await feed.start(drivingPage(`/driving/${page}`))
  assert.deepEqual(requests, [`./api/settings/pages/${page}`])
  assert.equal(feed.data.page, page)
  feed.stop()
}
