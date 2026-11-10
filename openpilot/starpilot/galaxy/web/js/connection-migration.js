// Retire only the old phone router and downloaded app shell. Keep map downloads,
// Galaxy authentication, push subscriptions and all other browser preferences.
export async function retireLegacyPhone(target = globalThis) {
  try {
    for (const key of ["galaxy-companion-link", "galaxy-companion-ble-device", "galaxy-connection-preference", "galaxy-offline", "galaxy-home-screen-added"])
      target.localStorage?.removeItem(key)
  } catch { /* Private browsing can deny storage. */ }
  try {
    await target.caches?.delete("galaxy-offline-v1")
  } catch { /* Keep opening Galaxy if browser cache access is unavailable. */ }
  try {
    const workerURL = new URL("./sentry-push-worker.js", target.location.href).href
    const registration = await target.navigator?.serviceWorker?.getRegistration(workerURL)
    if (registration?.active?.scriptURL === workerURL) await registration.update()
  } catch { /* Never delay opening Galaxy for a worker update. */ }
}
