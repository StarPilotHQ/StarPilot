import { route } from "./route-state.js"
export { route } from "./route-state.js"
import { createRouteLeave } from "./route-leave.js"

const start = () => {
  const hash = location.hash.slice(1) || "/"
  let path
  try { path = decodeURIComponent(hash) } catch { path = hash }
  return path === "/vehicle" ? "/settings/vehicle" : path
}
route.path = start()
const index = () => Number.isInteger(history.state?.galaxyIndex) ? history.state.galaxyIndex : null
const url = (path) => `${location.pathname}${location.search}#${encodeURI(path)}`
let navigation
let pendingGuard = null
export function navigate(path, before = null) { navigation.navigate(path, before) }
export function ensureBackEntry() { navigation.ensureBackEntry() }
export function navigateBack() { navigation.back() }
export function setRouteLeaveGuard(guard) { pendingGuard = guard; navigation?.setGuard(guard) }
export function startRouter() {
  history.scrollRestoration = "manual"
  history.replaceState({ ...history.state, galaxyIndex: index() ?? 0 }, "", url(route.path))
  let position = index()
  const views = new Map([[position, route.view]])
  navigation = createRouteLeave({
    read: () => ({ path: start(), index: index() }),
    push: (path, position) => {
      for (const key of views.keys()) if (key >= position) views.delete(key)
      views.set(position, {})
      history.pushState({ galaxyIndex: position }, "", url(path))
    },
    replace: (path, position) => {
      if (path !== route.path) views.set(position, {})
      history.replaceState({ galaxyIndex: position }, "", url(path))
    },
    go: (delta) => history.go(delta),
    show: () => {
      const path = start()
      if (path !== route.path) {
        route.view.scroll = { x: window.scrollX || 0, y: window.scrollY || 0 }
        route.view = views.get(index()) || {}
      }
      views.set(index(), route.view)
      route.direction = index() < position ? -1 : 1
      position = index()
      route.path = path
      window.scrollTo(0, 0)
    },
  })
  navigation.setGuard(pendingGuard)
  window.addEventListener("popstate", () => navigation.changed())
  window.addEventListener("hashchange", () => navigation.changed())
}
