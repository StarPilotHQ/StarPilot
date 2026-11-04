import { t } from "../i18n.js"

export const GalaxyModal = {
  name: "GalaxyModal",
  props: {
    modelValue: { type: Boolean, default: false },
    title: { type: String, default: "Are you sure?" },
    message: { type: String, default: "" },
    confirmLabel: { type: String, default: "Confirm" },
    cancelLabel: { type: String, default: "Cancel" },
    danger: { type: Boolean, default: false },
    sheet: { type: Boolean, default: true },
    input: { type: Boolean, default: false },
    inputValue: { type: String, default: "" },
    inputPlaceholder: { type: String, default: "" },
    inputRequired: { type: Boolean, default: false },
  },
  emits: ["update:modelValue", "confirm", "cancel"],
  data() { return { value: this.inputValue } },
  mounted() { this.focusInput() },
  methods: {
    focusInput() {
      if (this.input) this.$nextTick(() => this.$refs.input?.focus())
    },
    close() { this.$emit("update:modelValue", false) },
    cancel() { this.close(); this.$emit("cancel") },
    confirm() {
      if (this.inputRequired && !String(this.value || "").trim()) return
      this.$emit("confirm", this.input ? this.value : undefined)
      this.close()
    },
  },
  template: `
    <transition name="gx-fade">
      <div v-if="modelValue" class="gx-scrim" @click.self="cancel">
        <transition name="gx-slide" appear>
          <div class="gx-sheet" role="dialog" :aria-label="title">
            <h3 class="gx-sheet__title">{{ title }}</h3>
            <p v-if="message" style="color: var(--text-muted); line-height: 1.5; white-space: pre-line; overflow-wrap: anywhere;">{{ message }}</p>
            <input v-if="input" ref="input" v-model="value" class="gx-field gx-field--full" type="text"
              :placeholder="inputPlaceholder" @keyup.enter="confirm" />
            <div class="gx-dialog__actions">
              <button type="button" class="gx-btn gx-btn--text" @click="cancel">{{ cancelLabel }}</button>
              <button type="button" class="gx-btn" :disabled="input && inputRequired && !String(value || '').trim()" :style="danger ? 'background: var(--error); color: var(--on-error);' : ''" @click="confirm">{{ confirmLabel }}</button>
            </div>
          </div>
        </transition>
      </div>
    </transition>
  `,
}

export function GalaxyConfirm({ title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false } = {}) {
  return new Promise((resolve) => {
    const host = document.createElement("div")
    document.body.appendChild(host)
    const { createApp, h } = window.__galaxyVue
    let app
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
      try { app?.unmount?.() } catch (e) {}
      host.remove()
    }
    app = createApp({
      render() {
        // This host is outside #galaxy-app, so the DOM translator cannot reach it.
        return h(GalaxyModal, {
          modelValue: true,
          title: title === undefined ? undefined : t(title),
          message: t(message),
          confirmLabel: t(confirmLabel),
          cancelLabel: t(cancelLabel),
          danger,
          "onUpdate:modelValue": (v) => { if (!v) finish(false) },
          onConfirm: () => finish(true),
          onCancel: () => finish(false),
        })
      },
    })
    app.mount(host)
  })
}

export function GalaxyPrompt({ title, message, initialValue = "", placeholder = "", confirmLabel = "Confirm" } = {}) {
  return new Promise((resolve) => {
    const host = document.createElement("div")
    document.body.appendChild(host)
    const { createApp, h } = window.__galaxyVue
    let app
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
      try { app?.unmount?.() } catch (e) {}
      host.remove()
    }
    app = createApp({
      render() {
        return h(GalaxyModal, {
          modelValue: true,
          title,
          message,
          input: true,
          inputValue: String(initialValue || ""),
          inputPlaceholder: placeholder,
          inputRequired: true,
          confirmLabel,
          "onUpdate:modelValue": (v) => { if (!v) finish(null) },
          onConfirm: (value) => finish(String(value || "").trim()),
          onCancel: () => finish(null),
        })
      },
    })
    app.mount(host)
  })
}
