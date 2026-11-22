import { WikiHelp } from './wiki-help.js'
import { navigate } from "./router.js"
import { MenuTile } from "./menu-tile.js"

export const Tools = {
  name: "Tools",
  components: { WikiHelp, MenuTile },
  props: { tools: { type: Array, required: true }, mode: { type: String, required: true } },
  methods: {
    open(tool) { navigate(tool.path) },
    availability(tool) {
      if (this.mode === "local" || tool.availability === "always") return ""
      return tool.availability === "partial-preview" ? "Sample available" : "Connect to your device to use this tool"
    },
  },
  template: `
    <div class="gx-view">
      <header class="gx-settings__header gx-page-header"><div><h2>Tools</h2></div><WikiHelp /></header>
      <div class="gx-grid">
        <MenuTile v-for="tool in tools" :key="tool.path" :icon="tool.icon" :title="tool.name"
          :description="tool.description" :availability="availability(tool)" @select="open(tool)" />
      </div>
    </div>
  `,
}
