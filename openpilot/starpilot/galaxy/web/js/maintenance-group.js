// Shared compact layout for related System Tools actions.
export const MaintenanceGroup = {
  props: { title: String, description: String },
  template: `<section class="gx-maintenance__content">
    <h3 class="gx-row__label">{{ title }}</h3>
    <p class="gx-note">{{ description }}</p>
    <div class="gx-actions"><slot /></div>
  </section>`,
}
