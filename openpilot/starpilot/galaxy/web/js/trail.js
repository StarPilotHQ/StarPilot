// Navigation tree (breadcrumb): where this page sits, with each level above it one tap away.
// Settings pages show it in their card header; Tools pages show it above the page heading.
export const GxTrail = {
  name: "GxTrail",
  props: {
    items: { type: Array, required: true },  // [{ id, label }], top level first; the last item is this page
    disabled: { type: Boolean, default: false },
    label: { type: String, default: "Page hierarchy" },
  },
  emits: ["select"],
  template: `<nav class="gx-settings-path" :aria-label="label"><ol>
    <li v-for="(item, index) in items" :key="item.id">
      <span v-if="index" class="gx-settings-path__separator" aria-hidden="true">›</span>
      <span v-if="index === items.length - 1" class="gx-settings-path__current" aria-current="page">{{ item.label }}</span>
      <button v-else type="button" class="gx-settings-path__parent" :disabled="disabled" @click="$emit('select', item.id)">{{ item.label }}</button>
    </li>
  </ol></nav>`,
}

const words = segment => segment.replace(/[-_]+/g, " ").replace(/^./, letter => letter.toLocaleUpperCase())

// Sub-pages reached from a tool that are not in the catalog's child list.
const EXTRA_PAGES = { "/cameras/sentry-settings": "Sentry settings", "/theme_maker/starport": "StarPort layout" }

// Trail for a page opened from Tools: Tools › tool › sub-page. Empty for pages outside Tools.
export function toolTrail(path, tools) {
  if (!path || path === "/tools" || !Array.isArray(tools)) return []
  const childOf = tool => (tool.children || []).find(child => child.path === path)
  const tool = tools.find(tool => path === tool.path || path.startsWith(tool.path + "/")) || tools.find(childOf)
  if (!tool) return []
  const items = [{ id: "/tools", label: "Tools" }, { id: tool.path, label: tool.name }]
  if (path === tool.path) return items
  const child = childOf(tool)
  return [...items, { id: path, label: child?.name || EXTRA_PAGES[path] || words(path.split("/").at(-1)) }]
}
