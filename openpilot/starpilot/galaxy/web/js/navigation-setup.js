// Pieces of the navigation setup page: a compact M3 choice group with an info popover and the Mapbox/Google comparison.
export const ChoiceGroup = {
  name: "ChoiceGroup",
  props: { label: { type: String, required: true }, hint: { type: String, default: "" }, modelValue: { type: String, default: "" },
    options: { type: Array, required: true }, disabled: { type: Boolean, default: false } },
  emits: ["update:modelValue"],
  data() { return { infoOpen: false } },
  computed: {
    selectedOption() { return this.options.find((option) => option.value === this.modelValue) || this.options[0] || null },
  },
  methods: {
    pick(event, option) {
      event.target.checked = this.modelValue === option.value
      if (!option.disabled && !this.disabled) this.$emit("update:modelValue", option.value)
    },
  },
  template: `
    <fieldset class="gx-choice" :disabled="disabled">
      <div class="gx-choice__head">
        <legend class="gx-choice__legend">{{ label }}</legend>
        <button type="button" class="gx-choice__info-btn" :aria-expanded="infoOpen" :aria-label="'More info about ' + label"
          :title="'More info about ' + label" @click="infoOpen = !infoOpen">
          <i class="bi" :class="infoOpen ? 'bi-info-circle-fill' : 'bi-info-circle'" aria-hidden="true"></i>
        </button>
      </div>
      <p v-if="hint" class="gx-choice__hint">{{ hint }}</p>
      <div class="gx-choice__grid">
        <label v-for="option in options" :key="option.value" class="gx-choice__card"
          :class="{ 'gx-choice__card--on': modelValue === option.value, 'gx-choice__card--off': option.disabled }">
          <input type="radio" class="gx-sr-only" :name="label" :value="option.value" :checked="modelValue === option.value"
            :disabled="option.disabled || disabled" @change="pick($event, option)">
          <span class="gx-choice__title">
            <i class="bi" :class="modelValue === option.value ? 'bi-check-circle-fill' : 'bi-circle'" aria-hidden="true"></i>
            <strong>{{ option.label }}</strong>
            <span v-if="option.badge" class="gx-starport-pill gx-starport-pill--soon">{{ option.badge }}</span>
          </span>
        </label>
      </div>
      <p v-if="selectedOption?.detail && !infoOpen" class="gx-choice__active-summary">{{ selectedOption.detail }}</p>
      <div v-if="infoOpen" class="gx-choice__popover" role="region" :aria-label="label + ' details'">
        <div class="gx-choice__popover-head">
          <strong>{{ label }} options</strong>
          <button type="button" class="gx-choice__popover-close" aria-label="Close details" @click="infoOpen = false">
            <i class="bi bi-x-lg" aria-hidden="true"></i>
          </button>
        </div>
        <dl class="gx-choice__popover-list">
          <div v-for="option in options" :key="option.value" class="gx-choice__popover-item">
            <dt><strong>{{ option.label }}</strong> <span v-if="option.badge" class="gx-starport-pill gx-starport-pill--soon">{{ option.badge }}</span></dt>
            <dd>{{ option.detail }} <span v-if="option.limit" class="gx-choice__limit">{{ option.limit }}</span></dd>
          </div>
        </dl>
      </div>
    </fieldset>`,
}

// What each service does and where it stops. Mapbox always does search and offline roads.
export const COMPARE_ROWS = [
  ["Place search and addresses", "Yes", "No, Mapbox does this"],
  ["Offline road maps", "Yes", "No, Google tiles can't be saved"],
  ["Lane arrows on the turn card", "Yes", "No"],
  ["Traffic in route times", "Yes", "Yes, with a correction every 7½ minutes"],
  ["Map picture", "Road lines", "Street map with traffic colors"],
  ["Needs", "Mapbox token", "Google key with billing on"],
  ["Free monthly allowance", "Set by Mapbox; counted for this comma", "100,000 map tiles, 10,000 routes, 5,000 traffic routes"],
  ["When the allowance runs out", "Typing suggestions, address lookups and offline downloads pause until the 1st", "The comma stops at 99% and uses Mapbox until it resets"],
]

export const SourceCompare = {
  name: "SourceCompare",
  data() { return { rows: COMPARE_ROWS } },
  template: `
    <details class="gx-starport-wiki"><summary>Compare Mapbox and Google</summary>
      <div class="gx-compare" role="table" aria-label="Mapbox compared with Google">
        <div class="gx-compare__row gx-compare__row--head" role="row"><span role="columnheader">What you get</span><span role="columnheader">Mapbox</span><span role="columnheader">Google</span></div>
        <div v-for="row in rows" :key="row[0]" class="gx-compare__row" role="row">
          <span role="rowheader">{{ row[0] }}</span><span role="cell">{{ row[1] }}</span><span role="cell">{{ row[2] }}</span></div>
      </div>
      <p class="gx-note">Counts cover this comma only. Mapbox and Google bill everything that uses your key, including other apps.</p>
    </details>`,
}
