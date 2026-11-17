let nextId = 0

// Keep the native select as the value/event adapter; presentation belongs to Galaxy.
export const GalaxySelect = {
  name: "GalaxySelect",
  inheritAttrs: false,
  props: { value: { default: undefined }, modelValue: { default: undefined }, disabled: Boolean, showDescription: Boolean },
  emits: ["change", "update:modelValue"],
  data() { return { uid: `gx-select-${++nextId}`, items: [], selected: "", label: "", open: false, name: "Choose an option", typeahead: "", typedAt: 0 } },
  computed: {
    selectedDescription() { return this.items.find(item => item.value === this.selected)?.description || "" },
    current() { return this.modelValue !== undefined ? this.modelValue : this.value },
    buttonId() { return this.$attrs.id || this.uid },
    nativeAttrs() {
      const attrs = { ...this.$attrs }
      for (const key of ["class", "style", "id", "aria-label", "aria-labeledby", "aria-describedby"]) delete attrs[key]
      return attrs
    },
  },
  methods: {
    sync() {
      const native = this.$refs.native
      if (!native) return
      if (this.current !== undefined) native.value = String(this.current ?? "")
      const items = [...native.options].map((option, index) => ({ index, value: option.value, label: option.label, description: option.dataset?.description || "", disabled: option.disabled || (option.parentElement?.tagName === "OPTGROUP" && option.parentElement.disabled), group: option.parentElement?.tagName === "OPTGROUP" ? option.parentElement.label : "" }))
      if (JSON.stringify(items) !== JSON.stringify(this.items)) this.items = items
      this.selected = native.value
      this.label = native.selectedOptions[0]?.dataset?.collapsedLabel || native.selectedOptions[0]?.label || "Choose an option"
      const button = this.$refs.button
      const labeled = this.$attrs["aria-labeledby"]?.split(/\s+/).map(id => document.getElementById(id)?.textContent || "").join(" ")
      const labels = [...new Set([...(button?.labels || []), button?.closest("label")].filter(Boolean))].map(label => {
        const copy = label.cloneNode(true)
        copy.querySelectorAll(".gx-select, select, button").forEach(node => node.remove())
        return copy.textContent.trim()
      }).filter(Boolean).join(" ")
      this.name = this.$attrs["aria-label"] || labeled || labels || button?.closest(".gx-row")?.querySelector(".gx-row__label")?.textContent || this.$attrs.title || "Choose an option"
      if (this.disabled && this.open) this.close()
    },
    async show(event) {
      if (this.disabled || this.open) return
      this.sync()
      this.open = true
      await this.$nextTick()
      const menu = this.$refs.menu
      try {
        menu.showModal()
      } catch (error) {
        this.open = false
        return
      }
      this.position()
      const options = [...menu.querySelectorAll('[role="option"]:not(:disabled)')]
      const selected = options.find(option => option.dataset.value === this.selected)
      const first = event?.key === "End" ? options.at(-1) : options[0]
      ;(selected || first)?.focus()
    },
    close() {
      if (!this.open) return
      this.open = false
      this.$refs.menu?.close()
      if (this.$refs.button?.isConnected) this.$refs.button.focus({ preventScroll: true })
    },
    position() {
      if (!this.open) return
      const rect = this.$refs.button.getBoundingClientRect()
      const viewport = window.visualViewport
      const width = viewport?.width || window.innerWidth
      const height = viewport?.height || window.innerHeight
      const menu = this.$refs.menu
      let zoom = 1
      for (let node = menu; node; node = node.parentElement) zoom *= Number.parseFloat(getComputedStyle(node).zoom) || 1
      const menuWidth = Math.min(Math.max(rect.width, 240), width - 24)
      menu.style.width = `${menuWidth / zoom}px`
      menu.style.maxHeight = `${(height - 24) / zoom}px`
      const menuHeight = Math.min(menu.scrollHeight * zoom + 2, height - 24)
      const below = height - rect.bottom - 12
      const top = below >= Math.min(menuHeight, 220) ? Math.min(rect.bottom + 6, height - menuHeight - 12) : Math.max(12, rect.top - menuHeight - 6)
      menu.style.left = `${Math.max(12, Math.min(rect.left, width - menuWidth - 12)) / zoom}px`
      menu.style.top = `${Math.max(12, top) / zoom}px`
    },
    select(item) {
      if (this.disabled) return this.close()
      const native = this.$refs.native
      const option = native.options[item.index]
      if (!option || option.value !== item.value || option.disabled || option.parentElement?.disabled) return this.close()
      this.close()
      if (native.value === option.value) return
      native.value = option.value
      native.dispatchEvent(new Event("change", { bubbles: true }))
      this.sync()
    },
    change(event) {
      this.$emit("update:modelValue", event.target.value)
      this.$emit("change", event)
    },
    buttonKey(event) {
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) { event.preventDefault(); this.show(event) }
    },
    menuKey(event) {
      if (event.key === "Tab") { event.preventDefault(); this.close(); return }
      const options = [...this.$refs.menu.querySelectorAll('[role="option"]:not(:disabled)')]
      const index = options.indexOf(document.activeElement)
      let target
      if (event.key === "ArrowDown") target = options[(index + 1) % options.length]
      if (event.key === "ArrowUp") target = options[(index - 1 + options.length) % options.length]
      if (event.key === "Home") target = options[0]
      if (event.key === "End") target = options.at(-1)
      if (event.key.length === 1 && event.key !== " " && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const now = performance.now()
        this.typeahead = (now - this.typedAt < 700 ? this.typeahead : "") + event.key.toLocaleLowerCase()
        this.typedAt = now
        target = options.find(option => option.textContent.trim().toLocaleLowerCase().startsWith(this.typeahead))
      }
      if (target) { event.preventDefault(); target.focus() }
    },
  },
  mounted() {
    this.sync()
    window.addEventListener("resize", this.position)
    window.visualViewport?.addEventListener("resize", this.position)
  },
  updated() { this.sync() },
  beforeUnmount() {
    this.$refs.menu?.close()
    window.removeEventListener("resize", this.position)
    window.visualViewport?.removeEventListener("resize", this.position)
  },
  template: `
    <span class="gx-select" :class="$attrs.class" :style="$attrs.style">
      <select ref="native" v-bind="nativeAttrs" hidden tabindex="-1" aria-hidden="true" :disabled="disabled" @change="change"><slot /></select>
      <button ref="button" :id="buttonId" class="gx-select__button" type="button" role="combobox" aria-haspopup="listbox" :aria-expanded="open" :aria-controls="uid + '-list'"
        :aria-label="name" :aria-describedby="$attrs['aria-describedby']" :disabled="disabled" @click="show" @keydown="buttonKey">
        <span class="gx-grow gx-wrap"><span>{{ label }}</span><small v-if="showDescription && selectedDescription" class="gx-note gx-select__description">{{ selectedDescription }}</small></span><i class="bi bi-chevron-down" aria-hidden="true"></i>
      </button>
      <dialog ref="menu" class="gx-select-menu" @cancel.prevent="close" @click.self="close" @keydown="menuKey">
        <div :id="uid + '-list'" role="listbox" :aria-label="name">
          <template v-for="(item, index) in items" :key="item.index">
            <div v-if="item.group && item.group !== items[index - 1]?.group" class="gx-select-menu__group">{{ item.group }}</div>
            <button type="button" role="option" :data-value="item.value" :aria-selected="item.value === selected" :disabled="item.disabled" @click="select(item)">
              <span class="gx-grow gx-wrap"><span>{{ item.label }}</span><small v-if="item.description" class="gx-note gx-select__description">{{ item.description }}</small></span><i v-if="item.value === selected" class="bi bi-check-lg" aria-hidden="true"></i>
            </button>
          </template>
        </div>
      </dialog>
    </span>
  `,
}
