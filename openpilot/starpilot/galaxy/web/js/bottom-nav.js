// Phone bar: Home · Settings · Routes. Settings opens the menu (menu.js); every other page sits under it.
export const NAV = Object.freeze([
  { name: "Home", path: "/", icon: "bi-house-fill" },
  { name: "Settings", path: "/settings", icon: "bi-gear-fill" },
  { name: "Routes", path: "/recordings", icon: "bi-camera-reels" },
])

export function primaryTab(path) {
  if (path === "/") return 0
  if (path === "/recordings" || path.startsWith("/recordings/")) return 2
  return 1
}

export const BottomNav = {
  props: { path: { type: String, required: true } },
  emits: ["navigate"],
  computed: { activeTab() { return primaryTab(this.path) } },
  data: () => ({ tabs: NAV }),
  template: `<nav class="blur-nav" aria-label="Primary navigation" :style="{'--active-tab': activeTab}">
    <span class="gx-nav-indicator" aria-hidden="true"></span>
    <button v-for="(item, index) in tabs" :key="item.path" type="button" class="nav-item"
      :class="{active: index === activeTab}" :aria-current="index === activeTab ? 'page' : null" @click="$emit('navigate', item.path)">
      <i class="bi" :class="item.icon" aria-hidden="true"></i><span>{{ item.name }}</span>
    </button>
  </nav>`,
}
