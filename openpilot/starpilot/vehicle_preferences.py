from dataclasses import dataclass, field
from openpilot.starpilot.car.subaru.smoothing import Snapshot, read_snapshot, prepare as prepare_smoothing

from opendbc.car.gm.startup_preferences import prepare_disable_longitudinal
from opendbc.car.toyota.interface import apply_toyota_auto_hold
from opendbc.car.toyota.values import ToyotaFlags
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.car.tesla.radar_settings import RadarSettings
from openpilot.starpilot.car.tesla.pedal_preferences import PedalSettings


def bolt_disable_supported(cp) -> bool:
  from opendbc.car.gm.values import is_bolt_cc_profile, is_bolt_pedal_profile, is_bolt_pedal_stock_denied
  identities = ("CHEVROLET_BOLT_CC_2017", "CHEVROLET_BOLT_CC_2018_2021", "CHEVROLET_BOLT_CC_2022_2023",
                "CHEVROLET_BOLT_ACC_2022_2023_PEDAL")
  return cp.carFingerprint in identities and (is_bolt_cc_profile(cp) or is_bolt_pedal_profile(cp) or
                                               is_bolt_pedal_profile(cp, stock_only=True) or is_bolt_pedal_stock_denied(cp))


@dataclass(frozen=True)
class VehicleStartupPreferences:
  toyota_auto_hold: bool = False
  toyota_sng: bool = field(default=False, kw_only=True)
  jeep_brake_hold: bool = field(default=False, kw_only=True)
  turn_assist: bool = False
  gm_long_pitch: bool = True
  disable_bolt_long: bool = False
  honda_bosch_a_radar: bool = True
  tesla_preap_stock: bool = True
  tesla_preap_radar: RadarSettings | None = field(default=None, kw_only=True)
  tesla_preap_pedal: PedalSettings | None = field(default=None, kw_only=True)
  subaru_smoothing: Snapshot | None = field(default=None, kw_only=True)
  subaru_avh_startup: bool = field(default=False, kw_only=True)
  subaru_stop_start_off: bool = field(default=False, kw_only=True)
  subaru_sng: bool = field(default=False, kw_only=True)
  subaru_software_cruise: bool = field(default=False, kw_only=True)
  volt_sng: bool = False
  gm_auto_hold: bool = False
  volt_one_pedal: bool = False
  tesla_screen: bool = False
  tesla_screen_brake: bool = False
  gm_camera_pedal: bool = True
  toyota_filter: bool = True
  gm_longitudinal_tune: int = 0
  ford_bluepilot: tuple[tuple[str, str], ...] = field(default=(("disable_BP_lat_UI", "1"),), kw_only=True)
  toyota_lock_doors: bool = False
  toyota_unlock_doors: bool = False
  toyota_radar_mode: int = 0
  gm_cancel_remap: bool = False
  vw_stock_long: bool = field(default=False, kw_only=True)

  @classmethod
  def read(cls, params, *, enabled: bool):
    from openpilot.starpilot.car.toyota.radar_preferences import selected as toyota_radar_mode
    from openpilot.starpilot.car.gm.tune_preferences import selected_tune
    from openpilot.starpilot.car.tesla.preap_preferences import stock_configuration
    from openpilot.starpilot.car.tesla.radar_settings import read_radar_settings
    from openpilot.starpilot.car.tesla.pedal_preferences import read_pedal_settings
    from openpilot.starpilot.car.ford.bluepilot import startup_params as ford_bluepilot
    smoothing = read_snapshot(params, enabled=enabled)
    radar = read_radar_settings(params)
    pedal = read_pedal_settings(params)
    preap_stock = stock_configuration(params, radar, pedal)
    from openpilot.starpilot.car.volkswagen.preferences import stock_long_requested
    vw_stock_long = stock_long_requested(params)
    try:
      raw_radar, radar_readable = read_saved(params, "HondaBoschARadar", 8)
      honda_radar = radar_readable and raw_radar in (None, b"1")
    except (OSError, TypeError, ValueError):
      honda_radar = False
    try:
      raw, readable = read_saved(params, "DisableOpenpilotLongitudinal", 8)
      disable_bolt = not readable or raw not in (None, b"0")
    except (OSError, TypeError, ValueError):
      disable_bolt = True
    try:
      requested = read_saved(params, "ToyotaAutoHold", 8) == (b"1", True)
      safe, readable = read_saved(params, "SafeMode", 8)
      toyota = bool(enabled and requested and readable and safe in (None, b"0"))
      jeep_hold = bool(enabled and read_saved(params, "JeepBrakeHold", 8) == (b"1", True) and
                       readable and safe in (None, b"0"))
      volt_sng = bool(enabled and read_saved(params, "VoltSNG", 8) == (b"1", True) and
                      readable and safe in (None, b"0"))
      gm_auto_hold = bool(enabled and not disable_bolt and read_saved(params, "GMAutoHold", 8) == (b"1", True) and
                          readable and safe in (None, b"0"))
      volt_one_pedal = bool(enabled and not disable_bolt and read_saved(params, "VoltOnePedalMode", 8) == (b"1", True) and
                            readable and safe in (None, b"0"))
    except (OSError, TypeError, ValueError):
      return cls(vw_stock_long=vw_stock_long,
                 subaru_smoothing=smoothing, disable_bolt_long=disable_bolt, honda_bosch_a_radar=honda_radar, tesla_preap_stock=preap_stock,
                 tesla_preap_radar=radar, tesla_preap_pedal=pedal,
                 gm_camera_pedal=False, toyota_filter=False)
    try:
      pitch, pitch_readable = read_saved(params, "LongPitch", 8)
      pitch_enabled = not (enabled and pitch_readable and pitch == b"0" and readable and safe in (None, b"0"))
    except (OSError, TypeError, ValueError):
      pitch_enabled = True
    try:
      assist_raw, assist_readable = read_saved(params, "TurnAssist", 8)
      assist = assist_readable and assist_raw in (None, b"1")
    except (OSError, TypeError, ValueError):
      assist = False
    screen = bool(enabled and readable and safe in (None, b"0") and
                  read_saved(params, 'AlwaysOnLateral', 8) == (b'1', True) and
                  read_saved(params, 'TeslaAOLScreenTap', 8) == (b'1', True))
    screen_brake = read_saved(params, 'TeslaAOLDisengageOnBrake', 8) == (b'1', True)
    return cls(vw_stock_long=vw_stock_long,
               subaru_smoothing=smoothing, subaru_avh_startup=bool(enabled and readable and safe in (None, b"0") and
                    read_saved(params, "IsReleaseBranch", 8) in ((None, True), (b"0", True)) and
                    read_saved(params, "SubaruAvhStartup", 8) == (b"1", True)),
               subaru_stop_start_off=bool(enabled and readable and safe in (None, b"0") and
                                         read_saved(params, "SubaruStopStartOff", 8) == (b"1", True)),
               subaru_sng=bool(enabled and readable and safe in (None, b"0") and read_saved(params, "SubaruSNG", 8) == (b"1", True)),
               subaru_software_cruise=bool(enabled and readable and safe in (None, b"0") and
                   read_saved(params, "SubaruRedneckCruise", 8) == (b"1", True)), toyota_radar_mode=toyota_radar_mode(params, enabled=enabled),
               ford_bluepilot=tuple(ford_bluepilot(params, enabled=enabled).items()), tesla_screen=screen,
               tesla_screen_brake=screen_brake, toyota_auto_hold=toyota,
               toyota_sng=bool(enabled and readable and safe in (None, b"0") and
                               read_saved(params, "SNGHack", 8) == (b"1", True)), jeep_brake_hold=jeep_hold,
               gm_longitudinal_tune=selected_tune(params, enabled=enabled),
               toyota_lock_doors=bool(enabled and readable and safe in (None, b"0") and
                                     read_saved(params, "LockDoors", 8) == (b"1", True)),
               toyota_unlock_doors=bool(enabled and readable and safe in (None, b"0") and
                                       read_saved(params, "UnlockDoors", 8) == (b"1", True)),
               gm_cancel_remap=bool(enabled and not disable_bolt and readable and safe in (None, b"0") and
                                    read_saved(params, "RemapCancelToDistance", 8) == (b"1", True)),
               volt_sng=volt_sng, gm_auto_hold=gm_auto_hold, volt_one_pedal=volt_one_pedal,
               gm_camera_pedal=bool(enabled and not disable_bolt and readable and safe in (None, b"0")),
               toyota_filter=bool(enabled and not disable_bolt and readable and safe in (None, b"0")),
               turn_assist=bool(enabled and assist and readable and safe in (None, b"0")),
               gm_long_pitch=pitch_enabled, disable_bolt_long=disable_bolt, honda_bosch_a_radar=honda_radar,
               tesla_preap_stock=preap_stock, tesla_preap_radar=radar, tesla_preap_pedal=pedal)

  def _prepare_honda_radar(self, cp) -> None:
    if cp.brand != "honda":
      return
    from opendbc.car.honda.bosch_a_radar import VERIFIED_BOSCH_A_CARS
    from opendbc.car.honda.values import HondaFlags
    if (cp.brand == "honda" and cp.carFingerprint in VERIFIED_BOSCH_A_CARS and cp.flags & HondaFlags.BOSCH and
        not cp.flags & (HondaFlags.BOSCH_RADARLESS | HondaFlags.BOSCH_CANFD | HondaFlags.BOSCH_ALT_RADAR)):
      cp.radarUnavailable = not self.honda_bosch_a_radar

  def _prepare_bolt(self, cp, fingerprints=None) -> None:
    if self.disable_bolt_long and bolt_disable_supported(cp):
      from opendbc.car.gm.values import (CAR, GMSafetyFlags, is_bolt_pedal_profile, is_bolt_pedal_stock_denied,
                                       is_bolt_pedal_removed_profile, BOLT_PEDAL_REMOVED_STOCK_WORDS)
      from opendbc.car.structs import CarParams
      pedal = is_bolt_pedal_profile(cp) or is_bolt_pedal_profile(cp, stock_only=True) or is_bolt_pedal_stock_denied(cp)
      stock_qualified = is_bolt_pedal_profile(cp, stock_only=True)
      removed_pedal = is_bolt_pedal_removed_profile(cp) or is_bolt_pedal_removed_profile(cp, stock_only=True)
      denied = is_bolt_pedal_stock_denied(cp)
      if removed_pedal:
        cp.safetyConfigs[0].safetyParam = BOLT_PEDAL_REMOVED_STOCK_WORDS[cp.carFingerprint]
        cp.openpilotLongitudinalControl = False
        cp.pcmCruise = True
        cp.autoResumeSng = False
        return
      cp.openpilotLongitudinalControl = False
      if pedal:
        cp.pcmCruise = True
        if cp.carFingerprint == CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL:
          if stock_qualified or denied:
            return
          camera = fingerprints.get(2, {}) if isinstance(fingerprints, dict) else {}
          length = camera.get(0x180) if isinstance(camera, dict) else None
          if type(length) is int and length == 4:
            cp.safetyConfigs[0].safetyParam = int(GMSafetyFlags.HW_CAM | GMSafetyFlags.EV)
          else:
            safety = CarParams.SafetyConfig()
            safety.safetyModel = CarParams.SafetyModel.noOutput
            cp.safetyConfigs = [safety]
            cp.passive = True
            cp.dashcamOnly = True

  def _prepare_camera_pedal(self, cp) -> None:
    from opendbc.car.gm.values import camera_acc_pedal_profile, volt_cc_pedal_profile
    if not self.gm_camera_pedal and (camera_acc_pedal_profile(cp) is not None or volt_cc_pedal_profile(cp) is not None):
      prepare_disable_longitudinal(cp, True)

  def prepare(self, cp, *, fingerprints=None):
    from opendbc.car.volkswagen.meb_modes import prepare_stock as prepare_meb_stock
    prepare_meb_stock(cp, self.disable_bolt_long or self.vw_stock_long)
    from opendbc.car.subaru.software_cruise import prepare
    cp = prepare(cp, self.subaru_software_cruise)
    from opendbc.car.subaru.sng import prepare as prepare_sng
    cp = prepare_sng(cp, self.subaru_sng)
    from opendbc.car.subaru.startup import prepare as prepare_startup
    cp = prepare_startup(cp, self.subaru_stop_start_off)
    from opendbc.car.subaru.avh import prepare as prepare_avh
    cp = prepare_avh(cp, self.subaru_avh_startup)
    from opendbc.car.gm.values import apply_gm_auto_hold, apply_volt_one_pedal
    from openpilot.starpilot.car.tesla.preap_preferences import prepare_stock
    prepare_stock(cp, self.tesla_preap_stock, self.tesla_preap_radar, self.tesla_preap_pedal)
    from opendbc.car.tesla.screen_button import apply_screen_button
    apply_screen_button(cp, self.tesla_screen, self.tesla_screen_brake)
    self._prepare_honda_radar(cp)
    from opendbc.car.chrysler.brake_hold import apply_brake_hold
    apply_brake_hold(cp, self.jeep_brake_hold)
    if not self.toyota_filter:
      from opendbc.car.toyota.prius_longitudinal import prepare_stock as prepare_prius_stock
      prepare_prius_stock(cp)
      from opendbc.car.toyota.rav4h_filter import prepare_stock as prepare_rav4h_stock
      prepare_rav4h_stock(cp)
      from opendbc.car.toyota.pedal import prepare_stock as prepare_pedal_stock
      prepare_pedal_stock(cp)
      from opendbc.car.toyota.radar_filter import prepare_stock as prepare_radar_filter_stock
      prepare_radar_filter_stock(cp)
      from opendbc.car.toyota.advert_filter import prepare_stock as prepare_advert_filter_stock
      prepare_advert_filter_stock(cp)
      from opendbc.car.toyota.angle_control import prepare_stock as prepare_angle_stock
      prepare_angle_stock(cp)
      from opendbc.car.toyota.dsu_bypass import prepare_stock as prepare_bypass_stock
      prepare_bypass_stock(cp)
    self._prepare_bolt(cp, fingerprints)
    prepare_disable_longitudinal(cp, self.disable_bolt_long)
    self._prepare_camera_pedal(cp)
    apply_gm_auto_hold(cp, self.gm_auto_hold)
    apply_volt_one_pedal(cp, self.volt_one_pedal and not self.disable_bolt_long, self.gm_auto_hold)
    from opendbc.car.gm.cancel_remap import prepare as prepare_cancel_remap
    prepare_cancel_remap(cp, self.gm_cancel_remap)
    if cp.brand == "toyota":
      apply_toyota_auto_hold(cp, self.toyota_auto_hold)
      from opendbc.car.toyota.ars510_preferences import apply_radar_mode
      apply_radar_mode(cp, self.toyota_radar_mode)
    from opendbc.bluepilot_lateral.hosts.starpilot.safety_param import init_ford_safety_param_starpilot
    init_ford_safety_param_starpilot(cp, dict(self.ford_bluepilot))
    return cp

  def finalize(self, cp) -> None:
    from opendbc.car.volkswagen.meb_modes import prepare_stock as prepare_meb_stock
    prepare_meb_stock(cp, self.disable_bolt_long or self.vw_stock_long)
    from opendbc.car.gm.values import apply_gm_auto_hold, is_gm_auto_hold, apply_volt_one_pedal, is_volt_one_pedal
    admitted_one_pedal = is_volt_one_pedal(cp)
    admitted_hold = is_gm_auto_hold(cp)
    from openpilot.starpilot.car.tesla.preap_preferences import prepare_stock
    prepare_stock(cp, self.tesla_preap_stock, self.tesla_preap_radar, self.tesla_preap_pedal)
    from opendbc.car.tesla.screen_button import apply_screen_button
    apply_screen_button(cp, self.tesla_screen, self.tesla_screen_brake)
    self._prepare_honda_radar(cp)
    from opendbc.car.chrysler.brake_hold import apply_brake_hold
    apply_brake_hold(cp, self.jeep_brake_hold)
    if not self.toyota_filter:
      from opendbc.car.toyota.prius_longitudinal import prepare_stock as prepare_prius_stock
      prepare_prius_stock(cp)
      from opendbc.car.toyota.rav4h_filter import prepare_stock as prepare_rav4h_stock
      prepare_rav4h_stock(cp)
      from opendbc.car.toyota.pedal import prepare_stock as prepare_pedal_stock
      prepare_pedal_stock(cp)
      from opendbc.car.toyota.radar_filter import prepare_stock as prepare_radar_filter_stock
      prepare_radar_filter_stock(cp)
      from opendbc.car.toyota.advert_filter import prepare_stock as prepare_advert_filter_stock
      prepare_advert_filter_stock(cp)
      from opendbc.car.toyota.angle_control import prepare_stock as prepare_angle_stock
      prepare_angle_stock(cp)
      from opendbc.car.toyota.dsu_bypass import prepare_stock as prepare_bypass_stock
      prepare_bypass_stock(cp)
    self._prepare_bolt(cp)
    prepare_disable_longitudinal(cp, self.disable_bolt_long)
    self._prepare_camera_pedal(cp)
    apply_gm_auto_hold(cp, self.gm_auto_hold and admitted_hold)
    apply_volt_one_pedal(cp, self.volt_one_pedal and admitted_one_pedal and not self.disable_bolt_long, self.gm_auto_hold and admitted_hold)
    from opendbc.car.gm.cancel_remap import prepare as prepare_cancel_remap
    prepare_cancel_remap(cp, self.gm_cancel_remap)
    if cp.brand == "toyota":
      admitted = bool(cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD)
      apply_toyota_auto_hold(cp, self.toyota_auto_hold and admitted)
      from opendbc.car.toyota.ars510_preferences import apply_radar_mode
      apply_radar_mode(cp, self.toyota_radar_mode)

    prepare_smoothing(cp, self.subaru_smoothing)

  def configure_controller(self, ci) -> None:
    from opendbc.car.gm.values import (CAR, is_bolt_euv_longitudinal, is_volt_longitudinal, is_gm_auto_hold,
                                     is_volt_one_pedal, camera_acc_pedal_profile, volt_cc_pedal_profile)
    cp = ci.CP
    if ci.CC is not None and cp.brand == "toyota" and not cp.passive and not cp.dashcamOnly and not cp.notCar:
      from opendbc.car.toyota.sng import eligible
      ci.CC.sng_hack = self.toyota_sng and eligible(cp)
      ci.CC.door_locks.lock = self.toyota_lock_doors
      ci.CC.door_locks.unlock = self.toyota_unlock_doors
    if ci.CC is not None and is_volt_longitudinal(cp):
      ci.CC.volt_sng = self.volt_sng
    if ci.CC is not None and cp.brand == "gm":
      ci.CC.gm_auto_hold = self.gm_auto_hold and is_gm_auto_hold(cp)
      ci.CC.volt_one_pedal = self.volt_one_pedal and is_volt_one_pedal(cp)
    pedal = camera_acc_pedal_profile(cp) or volt_cc_pedal_profile(cp)
    if ci.CC is not None and (is_bolt_euv_longitudinal(cp) or is_volt_longitudinal(cp) or cp.carFingerprint == CAR.CHEVROLET_SUBURBAN or
                             pedal is not None and pedal.longitudinal):
      ci.CC.long_pitch = self.gm_long_pitch
