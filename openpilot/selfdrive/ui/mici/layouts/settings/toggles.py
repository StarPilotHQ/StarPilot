from openpilot.cereal import log

from openpilot.system.ui.widgets.scroller import NavScroller
from openpilot.selfdrive.ui.mici.widgets.button import BigParamControl, BigMultiParamToggle, BigToggle
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.settings.common import restart_needed_callback
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.layouts.settings.initial_cruise_speed import InitialCruiseSpeedButton
from openpilot.starpilot.ui.slc_offset_feature import SlcOffsetOwner, native_parked
from openpilot.starpilot.galaxy.localization_settings import LocalizationSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest

PERSONALITY_TO_INT = log.LongitudinalPersonality.schema.enumerants




class TogglesLayoutMici(NavScroller):
  def __init__(self):
    super().__init__()
    self._slc_offsets = SlcOffsetOwner(ui_state.params, lambda: native_parked(ui_state), lambda: ui_state.CP)
    self._metric_source = self._slc_offsets._raw("IsMetric")
    self._localization = LocalizationSettingsOwner(ui_state.params, lambda: True, lambda: native_parked(ui_state))
    self._rhd_source = self._slc_offsets._raw("IsRhdDetected")
    self._rhd_toggle = BigToggle("right hand driving", initial_state=ui_state.params.get_bool("IsRhdDetected"),
                                 toggle_callback=self._on_rhd,
                                 description="On selects right-hand; Off selects left-hand for the next drive. Automatic side detection continues.")
    self._rhd_toggle.set_enabled(lambda: self._localization.snapshot().rows[1].available)

    self._personality_toggle = BigMultiParamToggle("driving personality", "LongitudinalPersonality", ["aggressive", "standard", "relaxed"],
                                                   description="Standard is recommended.\n" +
                                                               "Aggressive follows closer, with firmer gas and braking.\n" +
                                                               "Relaxed leaves more space.\n" +
                                                               "Use the steering wheel distance button on supported cars.")
    self._safe_mode_btn = BigParamControl("safe mode", "SafeMode", toggle_callback=restart_needed_callback)
    self._experimental_btn = BigParamControl("experimental mode", "ExperimentalMode",
                                       description_icon=gui_app.texture("icons_mici/experimental_mode.png", 64, 64),
                                       description="Let the driving model control gas and brakes.\n" +
                                                   "Includes stopping for red lights and stop signs.\n" +
                                                   "Set speed is a maximum, not a target.\n" +
                                                   "These are alpha features. Expect mistakes.\n" +
                                                   "The path colors show acceleration and braking.")
    disengage_on_accelerator = BigParamControl("disengage on accelerator", "DisengageOnAccelerator",
                                              description="When enabled, pressing the accelerator pedal will disengage openpilot.")
    self._initial_cruise_speed = InitialCruiseSpeedButton()
    self._initial_cruise_speed.set_visible(lambda: ui_state.CP is not None and ui_state.has_longitudinal_control and not ui_state.CP.pcmCruise)
    is_metric_toggle = BigToggle("use metric units", initial_state=ui_state.params.get_bool("IsMetric"),
                                 toggle_callback=self._on_metric)
    self._metric_toggle = is_metric_toggle
    ldw_toggle = BigParamControl("lane departure warnings", "IsLdwEnabled",
                                 description="Warn when you drift across a detected lane line.\n" +
                                             "Only above 31 mph (50 km/h), with no turn signal.")
    always_on_dm_toggle = BigParamControl("always-on driver monitor", "AlwaysOnDM", description="Monitor the driver even when openpilot is not engaged.")
    record_front = BigParamControl("record & upload cabin camera", "RecordFront",
                                   description_icon=gui_app.texture("icons_mici/settings/device/cameras.png", 64, 64),
                                   toggle_callback=restart_needed_callback, description="Upload cabin camera data to help improve driver monitoring.")
    record_mic = BigToggle("record & upload mic audio", description_icon=gui_app.texture("icons_mici/microphone.png", 64, 64),
                                 initial_state=ui_state.params.get_bool("RecordAudio"), toggle_callback=self._on_record_audio,
                                 description="Record microphone audio while driving.\n" +
                                             "Audio is included in dashcam videos in comma connect.")
    enable_openpilot = BigParamControl("enable openpilot", "OpenpilotEnabledToggle", toggle_callback=restart_needed_callback,
                                       description="Enable to use openpilot driver assistance.\n" +
                                                   "Disable to use your car's stock driver assistance.")
    mazda_torqueinterceptorenabled = BigParamControl("torque interceptor", "TorqueInterceptorEnabled", toggle_callback=restart_needed_callback)
    mazda_radarinterceptorenabled = BigParamControl("radar interceptor", "RadarInterceptorEnabled", toggle_callback=restart_needed_callback)
    mazda_nomrcc = BigParamControl("no stock MRCC", "NoMRCC", toggle_callback=restart_needed_callback)
    mazda_nofsc = BigParamControl("no stock FSC", "NoFSC", toggle_callback=restart_needed_callback)
    mazda_blendedacc = BigParamControl("blended ACC", "BlendedACC", toggle_callback=restart_needed_callback)
    mazda_manualtransmission = BigParamControl("manual transmission", "ManualTransmission", toggle_callback=restart_needed_callback)

    self._scroller.add_widgets([
      self._personality_toggle,
      self._safe_mode_btn,
      self._experimental_btn,
      disengage_on_accelerator,
      self._initial_cruise_speed,
      is_metric_toggle,
      ldw_toggle,
      always_on_dm_toggle,
      self._rhd_toggle,
      record_front,
      record_mic,
      enable_openpilot,
      mazda_torqueinterceptorenabled,
      mazda_radarinterceptorenabled,
      mazda_nomrcc,
      mazda_nofsc,
      mazda_blendedacc,
      mazda_manualtransmission,
    ])

    # Toggle lists
    self._refresh_toggles = (
      ("SafeMode", self._safe_mode_btn),
      ("ExperimentalMode", self._experimental_btn),
      ("DisengageOnAccelerator", disengage_on_accelerator),
      ("IsMetric", is_metric_toggle),
      ("IsLdwEnabled", ldw_toggle),
      ("AlwaysOnDM", always_on_dm_toggle),
      ("IsRhdDetected", self._rhd_toggle),
      ("RecordFront", record_front),
      ("RecordAudio", record_mic),
      ("OpenpilotEnabledToggle", enable_openpilot),
      ("TorqueInterceptorEnabled", mazda_torqueinterceptorenabled),
      ("RadarInterceptorEnabled", mazda_radarinterceptorenabled),
      ("NoMRCC", mazda_nomrcc),
      ("NoFSC", mazda_nofsc),
      ("BlendedACC", mazda_blendedacc),
      ("ManualTransmission", mazda_manualtransmission),
    )

    enable_openpilot.set_enabled(lambda: not ui_state.engaged)
    always_on_dm_toggle.set_enabled(not ui_state.params.get_bool("AlwaysOnDMLock"))
    mazda_torqueinterceptorenabled.set_visible(lambda: ui_state.CP is not None and ui_state.CP.brand == "mazda")
    mazda_radarinterceptorenabled.set_visible(lambda: ui_state.CP is not None and ui_state.CP.brand == "mazda")
    mazda_nomrcc.set_visible(lambda: ui_state.CP is not None and ui_state.CP.brand == "mazda")
    mazda_nofsc.set_visible(lambda: ui_state.CP is not None and ui_state.CP.brand == "mazda")
    mazda_blendedacc.set_visible(lambda: ui_state.CP is not None and ui_state.CP.brand == "mazda")
    mazda_manualtransmission.set_visible(lambda: ui_state.CP is not None and ui_state.CP.brand == "mazda")
    record_front.set_enabled(False if ui_state.params.get_bool("RecordFrontLock") else (lambda: not ui_state.engaged))
    record_mic.set_enabled(lambda: not ui_state.engaged)

    if ui_state.params.get_bool("ShowDebugInfo"):
      gui_app.set_show_touches(True)
      gui_app.set_show_fps(True)

    ui_state.add_engaged_transition_callback(self._update_toggles)

  def _update_state(self):
    super()._update_state()

    if ui_state.sm.updated["selfdriveState"]:
      personality = PERSONALITY_TO_INT[ui_state.sm["selfdriveState"].personality]
      if personality != ui_state.personality and ui_state.started:
        self._personality_toggle.set_value(self._personality_toggle._options[personality])
      ui_state.personality = personality

  def show_event(self):
    super().show_event()
    self._update_toggles()

  def _update_toggles(self):
    ui_state.update_params()
    self._initial_cruise_speed.refresh()

    safe_mode = ui_state.params.get_bool("SafeMode")
    self._experimental_btn.set_enabled(not safe_mode)
    self._personality_toggle.set_enabled(not safe_mode)
    if safe_mode:
      if ui_state.params.get_bool("ExperimentalMode"):
        ui_state.params.put_bool("ExperimentalMode", False, block=True)
      if ui_state.params.get("LongitudinalPersonality", return_default=True) != int(log.LongitudinalPersonality.relaxed):
        ui_state.params.put("LongitudinalPersonality", int(log.LongitudinalPersonality.relaxed), block=True)
      self._experimental_btn.set_checked(False)
      self._personality_toggle.set_value("relaxed")

    # CP gating for experimental mode
    if ui_state.CP is not None:
      if ui_state.has_longitudinal_control:
        self._experimental_btn.set_visible(True)
        self._personality_toggle.set_visible(True)
      else:
        # Unavailable now, but retain the saved choice for a qualified drive.
        self._experimental_btn.set_visible(False)
        self._personality_toggle.set_visible(False)

    # Refresh toggles from params to mirror external changes
    for key, item in self._refresh_toggles:
      item.set_checked(ui_state.params.get_bool(key))
    self._metric_source = self._slc_offsets._raw("IsMetric")
    self._rhd_source = self._slc_offsets._raw("IsRhdDetected")

  def _on_metric(self, desired: bool) -> None:
    self._slc_offsets.change_units(desired, self._metric_source)
    self._metric_source = self._slc_offsets._raw("IsMetric")
    self._metric_toggle.set_checked(ui_state.params.get_bool("IsMetric"))
    self._update_toggles()

  def _on_rhd(self, desired: bool) -> None:
    self._localization.apply(FeatureSettingsRequest("IsRhdDetected", self._rhd_source, "On" if desired else "Off"))
    self._rhd_source = self._localization.snapshot().rows[1].source
    self._rhd_toggle.set_checked(ui_state.params.get_bool("IsRhdDetected"))

  def _on_record_audio(self, desired: bool) -> None:
    # Keep filesystem waits off the UI thread, and persist the preference before
    # publishing the restart request through the same Params writer queue.
    ui_state.params.put_bool("RecordAudio", desired)
    ui_state.params.put_bool("OnroadCycleRequested", True)


  def request_personality(self, index: int) -> bool:
    self._update_toggles()
    if ui_state.CP is None or not ui_state.has_longitudinal_control:
      return False
    return self._personality_toggle.request_index(index, block=True)

  def request_experimental(self) -> bool:
    self._update_toggles()
    if ui_state.CP is None or not ui_state.has_longitudinal_control or not self._experimental_btn.enabled:
      return False
    desired = not ui_state.sm["selfdriveState"].experimentalMode
    ui_state.params.put_bool("ExperimentalMode", desired)
    self._experimental_btn.set_checked(desired)
    return True
