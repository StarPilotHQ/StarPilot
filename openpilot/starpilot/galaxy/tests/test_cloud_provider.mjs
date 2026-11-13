import test from "node:test"
import assert from "node:assert/strict"

import { validProviderStatus } from "../web/js/cloud-provider.js"
import { connectRouteUrl } from "../web/js/recording-data.js"

const REV = "a".repeat(64)

const baseStatus = () => ({
  active: "comma",
  selected: "comma",
  revision: REV,
  restartRequired: false,
  canSelect: true,
  custom: null,
  providers: [
    { id: "comma", label: "comma connect", url: "https://connect.comma.ai" },
    { id: "konik", label: "Konik", url: "https://stable.konik.ai" },
    { id: "custom", label: "Custom", url: "" },
  ],
})

test("validProviderStatus accepts default and custom cloud configurations", () => {
  assert.equal(validProviderStatus(baseStatus()), true)

  const customStatus = {
    ...baseStatus(),
    selected: "custom",
    restartRequired: true,
    custom: {
      api: "https://cloud.example.test:8443",
      athena: "wss://cloud.example.test:8443",
      web: "https://cloud.example.test:8443",
    },
    providers: [
      { id: "comma", label: "comma connect", url: "https://connect.comma.ai" },
      { id: "konik", label: "Konik", url: "https://stable.konik.ai" },
      { id: "custom", label: "Custom", url: "https://cloud.example.test:8443" },
    ],
  }
  assert.equal(validProviderStatus(customStatus), true)
})

test("validProviderStatus rejects missing custom entry or bad provider urls", () => {
  const twoOnly = baseStatus()
  twoOnly.providers = twoOnly.providers.slice(0, 2)
  assert.equal(validProviderStatus(twoOnly), false)

  const badCustomUrl = baseStatus()
  badCustomUrl.providers[2].url = "http://insecure.example.test"
  assert.equal(validProviderStatus(badCustomUrl), false)
})

test("connectRouteUrl validates comma, konik, and custom connect URLs", () => {
  assert.equal(
    connectRouteUrl({
      routeId: "2026-10-01--12-00-00",
      provider: "comma",
      connectUrl: "https://connect.comma.ai/0123456789abcdef/2026-10-01--12-00-00",
    }),
    "https://connect.comma.ai/0123456789abcdef/2026-10-01--12-00-00",
  )

  assert.equal(
    connectRouteUrl({
      routeId: "2026-10-01--12-00-00",
      provider: "custom",
      connectUrl: "https://cloud.example.test:8443/app/0123456789abcdef/2026-10-01--12-00-00",
    }),
    "https://cloud.example.test:8443/app/0123456789abcdef/2026-10-01--12-00-00",
  )

  assert.equal(
    connectRouteUrl({
      routeId: "2026-10-01--12-00-00",
      provider: "custom",
      connectUrl: "http://cloud.example.test/0123456789abcdef/2026-10-01--12-00-00",
    }),
    null,
  )
})
