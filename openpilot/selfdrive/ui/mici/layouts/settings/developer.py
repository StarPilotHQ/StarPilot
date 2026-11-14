from collections.abc import Callable
from openpilot.common.params import Params
from openpilot.common.time_helpers import system_time_valid
from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigToggle, BigParamControl, BigCircleParamControl, GreyBigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigDialog, BigInputDialog, BigConfirmationCircleButton
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.settings.common import restart_needed_callback
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.starpilot.car.hyundai.aol import ioniq6_alpha_long_available
from openpilot.starpilot.car.ford.longitudinal_preferences import release_long_available as ford_release_long_available
from opendbc.car.volkswagen.meb_modes import MEB_CARS, settings_capable as meb_settings_capable
from openpilot.starpilot.car.volkswagen.preferences import stock_long_requested
from openpilot.selfdrive.ui.widgets.ssh_key import SshKeyFetcher


class AlphaLongConfirmPage(NavScroller):
  def __init__(self, on_confirm: Callable[[], None], *, system_long_default: bool = False):
    super().__init__()

    accept = BigConfirmationCircleButton("enable alpha\nlongitudinal",
                                         gui_app.texture("icons_mici/setup/driver_monitoring/dm_check.png", 64, 64),
                                         lambda: self.dismiss(on_confirm))

    self._scroller.add_widgets([
      GreyBigButton("enabling alpha longitudinal", "scroll to continue",
                    gui_app.texture("icons_mici/setup/warning.png", 64, 64)),
      GreyBigButton("", "WARNING: alpha longitudinal control may disable Automatic Emergency Braking (AEB)"),
      GreyBigButton("", "Use StarPilot speed control instead of stock adaptive cruise control." if system_long_default else
                    "On this car, openpilot defaults to the stock system's built-in ACC."),
      GreyBigButton("", "This requests openpilot longitudinal control after a verified vehicle takeover at startup."),
      accept,
    ])


class DeveloperLayoutMici(NavScroller):
  def __init__(self, on_spacepilot_exit: Callable[[], None] | None = None):
    super().__init__()
    if not isinstance(ui_state.params, Params):
      raise RuntimeError("Developer settings require the device UI")
    self._ssh_fetcher = SshKeyFetcher(ui_state.params)
    # Only the native C4 shell supplies the immediate return-to-driving owner.
    if on_spacepilot_exit is not None:
      from openpilot.common.hardware import HARDWARE
      if HARDWARE.get_device_type() == 'mici':
        from openpilot.starpilot.ui.spacepilot.native import VersionButton
        self._scroller.add_widget(VersionButton(on_spacepilot_exit))

    def github_username_callback(username: str):
      if username:
        self._ssh_keys_btn.set_value("Loading...")
        self._ssh_keys_btn.set_enabled(False)

        def on_response(error):
          self._ssh_keys_btn.set_enabled(True)
          if error is None:
            self._ssh_keys_btn.set_value(username)
          else:
            self._ssh_keys_btn.set_value("Not set")
            gui_app.push_widget(BigDialog("", error))

        self._ssh_fetcher.fetch(username, on_response)
      else:
        self._ssh_fetcher.clear()
        self._ssh_keys_btn.set_value("Not set")

    def ssh_keys_callback():
      github_username = ui_state.params.get("GithubUsername") or ""
      dlg = BigInputDialog("enter GitHub username...", github_username, minimum_length=0, confirm_callback=github_username_callback)
      if not system_time_valid():
        dlg = BigDialog("", "Please connect to Wi-Fi to fetch your key.")
        gui_app.push_widget(dlg)
        return
      gui_app.push_widget(dlg)

    txt_ssh = gui_app.texture("icons_mici/settings/developer/ssh.png", 56, 64)
    github_username = ui_state.params.get("GithubUsername") or ""
    self._ssh_keys_btn = BigButton("SSH keys", "Not set" if not github_username else github_username, icon=txt_ssh,
                                   description="Grant SSH access to all public keys in your GitHub settings. Only enter your own username.")
    self._ssh_keys_btn.set_click_callback(ssh_keys_callback)

    from openpilot.starpilot.connect.settings import CloudProviderPage
    from openpilot.starpilot.connect.provider import status
    self._cloud_provider_btn = BigButton('Cloud Provider', 'Changes apply after the next reboot')
    def refresh_cloud():
      try:
        current = status()
        labels = {item['id']: item['label'] for item in current['providers']}
        selected = current['selected']
        label = labels.get(selected, 'Unavailable')
        if selected == 'custom' and current.get('custom'):
          label = current['custom']['api']
        self._cloud_provider_btn.set_value(label)
      except (OSError, ValueError, TypeError, KeyError):
        self._cloud_provider_btn.set_value('Unavailable')
    def choose_cloud():
      gui_app.push_widget(CloudProviderPage(on_change=refresh_cloud))
    self._cloud_provider_btn.set_click_callback(choose_cloud)
    self._cloud_provider_btn.set_enabled(ui_state.is_offroad)
    refresh_cloud()

    # adb, ssh, ssh keys, debug mode, joystick debug mode, ip address
    # ******** Main Scroller ********
    self._adb_toggle = BigCircleParamControl(gui_app.texture("icons_mici/adb_short.png", 82, 82), "AdbEnabled", icon_offset=(0, 12),
                                             description="Use Android Debug Bridge (ADB) over USB or the network.", title="enable ADB")
    self._ssh_toggle = BigCircleParamControl(gui_app.texture("icons_mici/ssh_short.png", 82, 82), "SshEnabled", icon_offset=(0, 12),
                                             description="Access the device remotely using your SSH keys.", title="enable SSH")
    self._joystick_toggle = BigToggle("joystick debug\nmode", initial_state=ui_state.params.get_bool("JoystickDebugMode"),
                                      toggle_callback=self._on_joystick_debug_mode, description="Control the car with a joystick for debugging.")
    self._alpha_long_toggle = BigToggle("alpha longitudinal", initial_state=ui_state.params.get_bool("AlphaLongitudinalEnabled"),
                                        toggle_callback=self._on_alpha_long_enabled,
                                        description="Use alpha openpilot longitudinal control instead of stock ACC. This may disable Automatic Emergency " +
                                                    "Braking (AEB).")
    self._navigation_toggle = BigParamControl("navigation (alpha)", "NavigationEnabled",
                                              description="Turn-by-turn navigation with a route map. Set a destination from comma connect.")
    self._debug_mode_toggle = BigParamControl("ui debug mode", "ShowDebugInfo",
                                              toggle_callback=lambda checked: (gui_app.set_show_touches(checked), gui_app.set_show_fps(checked)),
                                              description="Show touch locations and the UI frame rate.")
    self._cellular_off_toggle = BigParamControl("disable cellular", "CellularDisabled",
                                                description="Hang up cellular data and don't redial. The modem stays registered and the " +
                                                            "SIM is untouched. Remote access over cellular data is unavailable while this is enabled.")

    self._scroller.add_widgets([
      self._adb_toggle,
      self._ssh_toggle,
      self._ssh_keys_btn,
      self._cloud_provider_btn,
      self._joystick_toggle,
      self._alpha_long_toggle,
      self._debug_mode_toggle,
      self._navigation_toggle,
      self._cellular_off_toggle,
    ])

    # Toggle lists
    self._refresh_toggles = (
      ("AdbEnabled", self._adb_toggle),
      ("SshEnabled", self._ssh_toggle),
      ("JoystickDebugMode", self._joystick_toggle),
      ("AlphaLongitudinalEnabled", self._alpha_long_toggle),
      ("ShowDebugInfo", self._debug_mode_toggle),
      ("NavigationEnabled", self._navigation_toggle),
      ("CellularDisabled", self._cellular_off_toggle),
    )
    onroad_blocked_toggles = (self._adb_toggle, self._joystick_toggle)
    release_blocked_toggles = (self._joystick_toggle, self._alpha_long_toggle, self._navigation_toggle)

    # Hide non-release toggles on release builds
    for item in release_blocked_toggles:
      item.set_visible(not ui_state.is_release)

    # Disable toggles that require offroad
    for item in onroad_blocked_toggles:
      item.set_enabled(lambda: ui_state.is_offroad())

    self._alpha_long_toggle.set_enabled(lambda: ui_state.is_offroad())

    # Set initial state
    if ui_state.params.get_bool("ShowDebugInfo"):
      gui_app.set_show_touches(True)
      gui_app.set_show_fps(True)

    ui_state.add_offroad_transition_callback(self._update_toggles)

  def _update_state(self):
    super()._update_state()
    self._ssh_fetcher.update()

  def show_event(self):
    super().show_event()
    self._update_toggles()

  def _update_toggles(self):
    ui_state.update_params()

    # CP gating
    if ui_state.CP is not None:
      alpha_avail = (meb_settings_capable(ui_state.CP) if ui_state.CP.brand == 'volkswagen' and ui_state.CP.carFingerprint in MEB_CARS
                     else ui_state.CP.alphaLongitudinalAvailable or ioniq6_alpha_long_available(ui_state.CP))
      if not alpha_avail or (ui_state.is_release and not (meb_settings_capable(ui_state.CP) or ford_release_long_available(ui_state.CP))):
        self._alpha_long_toggle.set_visible(False)
      else:
        self._alpha_long_toggle.set_visible(True)

    else:
      self._alpha_long_toggle.set_visible(False)

    # Refresh toggles from params to mirror external changes
    for key, item in self._refresh_toggles:
      value = (not stock_long_requested(ui_state.params) if key == "AlphaLongitudinalEnabled" and meb_settings_capable(ui_state.CP)
               else ui_state.params.get_bool(key))
      item.set_checked(value)

  def _on_joystick_debug_mode(self, state: bool):
    ui_state.params.put_bool("JoystickDebugMode", state, block=True)
    ui_state.params.put_bool("LongitudinalManeuverMode", False, block=True)
    ui_state.params.put_bool("LateralManeuverMode", False, block=True)

  def _on_alpha_long_enabled(self, state: bool):
    def do_toggle(_state: bool):
      if (not ui_state.is_offroad() or (_state and (ui_state.CP is None or
              not (meb_settings_capable(ui_state.CP) if ui_state.CP.brand == 'volkswagen' and ui_state.CP.carFingerprint in MEB_CARS
                     else ui_state.CP.alphaLongitudinalAvailable or ioniq6_alpha_long_available(ui_state.CP)) or
              ui_state.is_release and not (meb_settings_capable(ui_state.CP) or ford_release_long_available(ui_state.CP))))):
        self._update_toggles()
        return
      ui_state.params.put_bool("AlphaLongitudinalEnabled", _state, block=True)
      restart_needed_callback()
      self._update_toggles()

    if state:
      # Don't show enabled state until confirm
      self._alpha_long_toggle.set_checked(False)
      page = (AlphaLongConfirmPage(lambda: do_toggle(True), system_long_default=True) if meb_settings_capable(ui_state.CP)
              else AlphaLongConfirmPage(lambda: do_toggle(True)))
      gui_app.push_widget(page)
    else:
      do_toggle(False)
