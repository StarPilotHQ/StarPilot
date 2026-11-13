"""Developer-only, offroad selection of the next-boot cloud provider."""

from openpilot.selfdrive.ui.mici.widgets.button import BigButton, GreyBigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigDialog, BigConfirmationCircleButton, BigInputDialog
from openpilot.selfdrive.ui.mici.widgets.qr import QR
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.starpilot.connect.provider import PROVIDERS, active_provider, build_custom_config, select_provider, status


class CloudProviderConfirmation(NavScroller):
  def __init__(self, name, revision, completed, custom=None):
    super().__init__()
    custom_config = build_custom_config(custom) if name == 'custom' and custom is not None else None
    label = 'Custom' if name == 'custom' else PROVIDERS[name].label
    detail = f' ({custom_config["api"]})' if custom_config is not None else ''

    def confirm():
      try:
        select_provider(name, revision, ui_state.is_offroad, custom=custom_config)
      except (OSError, ValueError, TypeError) as error:
        message = str(error) if isinstance(error, ValueError) and name == 'custom' else (
          'Cloud selection changed or the vehicle is on. Refresh and try again.')
        gui_app.push_widget(BigDialog('', message))
        return
      self.dismiss(completed)

    accept = BigConfirmationCircleButton('use after\nnext reboot', gui_app.texture('icons_mici/setup/driver_monitoring/dm_check.png', 64, 64), confirm)
    accept.set_enabled(ui_state.is_offroad)
    self._scroller.add_widgets([
      GreyBigButton('Cloud Provider', f'Use {label}{detail} after the next device reboot. No automatic reboot.'),
      GreyBigButton('', 'Cloud accounts stay separate. Returning to comma restores its saved identity.'),
      accept,
    ])


class CloudProviderPage(NavScroller):
  def __init__(self, on_change=None):
    super().__init__()
    try:
      current = status()
    except (OSError, ValueError, TypeError):
      self._scroller.add_widgets([GreyBigButton('Cloud Unavailable', 'Repair the saved provider configuration before choosing a cloud.')])
      return
    active = active_provider()
    providers = {item['id']: item for item in current['providers']}
    next_label = providers[current['selected']]['label']
    widgets = [
      GreyBigButton('Developer: Cloud Provider', f'Active: {active.label}. Next boot: {next_label}.'),
      GreyBigButton('', 'Changes apply after a device reboot. Turn off the vehicle first.'),
    ]
    if active.web:
      widgets.append(QR(active.web))
    self._scroller.add_widgets(widgets)

    def reopen():
      if on_change is not None:
        on_change()
      self.dismiss(lambda: gui_app.push_widget(CloudProviderPage(on_change=on_change)))

    def pick_custom():
      default_url = (current.get('custom') or {}).get('api', '')
      def on_url(text):
        try:
          config = build_custom_config(text)
        except ValueError as error:
          gui_app.push_widget(BigDialog('Invalid URL', str(error)))
          return
        gui_app.push_widget(CloudProviderConfirmation('custom', current['revision'], reopen, custom=config))
      gui_app.push_widget(BigInputDialog('Enter custom server URL', default_text=default_url, confirm_callback=on_url))

    for item in current['providers']:
      name = item['id']
      if name == 'custom':
        subtitle = item['url'] if item['url'] else 'Enter server URL (next boot only)'
        button = BigButton(f'Use {item["label"]}', subtitle)
        button.set_enabled(ui_state.is_offroad)
        button.set_click_callback(pick_custom)
      else:
        button = BigButton(f'Use {item["label"]}', 'Next boot only')
        button.set_enabled(lambda target=name: ui_state.is_offroad() and target != current['selected'])
        button.set_click_callback(lambda target=name: gui_app.push_widget(
          CloudProviderConfirmation(target, current['revision'], reopen)))
      self._scroller.add_widgets([button])
