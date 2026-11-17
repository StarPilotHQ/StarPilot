"""Current-controller torque source selection for native controlsd."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path

from opendbc.car import structs
from openpilot.cereal.services import SERVICE_LIST
from openpilot.starpilot.lateral.torque_tuning import TorqueSource, TorqueTuning
from openpilot.starpilot.lateral.torque_supported import BOLT_VEHICLES, GM_VEHICLES
from openpilot.starpilot.lateral.torque_settings import (
  DOCUMENT_KEY, LEGACY_KEYS, MAX_DOCUMENT_BYTES, interpret_legacy, parse_document, resolve_document,
)
LEARNER_VERSION = 1
REFRESH_NS = 1_000_000_000


def _move(old: float, new: float, step: float) -> float:
  return min(max(new, old - step), old + step)


GM_TORQUE_POLICIES = frozenset({'bolt', 'volt', 'ordinary_ascm', 'ordinary_sdgm',
                                    'ordinary_cc', 'ordinary_camera', 'silverado_cc', 'suburban'})


def gm_manual_supported_cp(CP) -> bool:
  """Reuse exact finalized torque policies; no brand-wide or controller-mode admission."""
  if (str(CP.carFingerprint) not in GM_VEHICLES or str(CP.brand) != 'gm' or
      CP.notCar or CP.passive or CP.dashcamOnly or
      CP.steerControlType != structs.CarParams.SteerControlType.torque or CP.lateralTuning.which() != 'torque'):
    return False
  from openpilot.starpilot.lateral.controller_selection import policy_for
  if str(CP.carFingerprint) not in BOLT_VEHICLES:
    from opendbc.car.gm.aol import qualified_gm
    if not qualified_gm(CP):
      return False
  return policy_for(CP) in GM_TORQUE_POLICIES


def factor_edit_supported(CP, mode) -> bool:
  """Fixed source conversion tables do not consume latAccelFactor numerically."""
  from openpilot.starpilot.lateral.controller_selection import ControllerMode, policy_for
  if not gm_manual_supported_cp(CP):
    return False
  identity = str(CP.carFingerprint)
  if identity == 'CHEVROLET_SILVERADO':  # native interface table in both modes
    return False
  if mode == ControllerMode.STANDARD:
    return True
  policy = policy_for(CP)
  return not (policy in ('bolt', 'volt', 'silverado_cc') or
              policy == 'ordinary_sdgm' and identity == 'CADILLAC_XT4' or
              policy == 'ordinary_camera' and identity == 'CHEVROLET_TRAX')


def supported_cp(CP) -> bool:
  fingerprint = str(CP.carFingerprint)
  if fingerprint in BOLT_VEHICLES:
    from openpilot.starpilot.lateral.bolt_policy import supported_cp as bolt_supported_cp
    return bolt_supported_cp(CP)
  if fingerprint in GM_VEHICLES or str(CP.brand) == 'gm':
    return gm_manual_supported_cp(CP)
  from opendbc.car.values import PLATFORMS
  platform = PLATFORMS.get(fingerprint)
  return (platform is not None and platform.__class__.__module__.split('.')[-2] == str(CP.brand) and
          not CP.notCar and not CP.passive and not CP.dashcamOnly and
          CP.steerControlType == structs.CarParams.SteerControlType.torque and
          CP.lateralTuning.which() == 'torque' and
          math.isfinite(float(CP.lateralTuning.torque.latAccelFactor)) and
          float(CP.lateralTuning.torque.latAccelFactor) > 0 and
          math.isfinite(float(CP.lateralTuning.torque.latAccelOffset)) and
          math.isfinite(float(CP.lateralTuning.torque.friction)) and
          float(CP.lateralTuning.torque.friction) >= 0)


def production_supported_cp(CP) -> bool:
  """Admit the current registered torque controller, preserving exact GM policies."""
  return supported_cp(CP) and not CP.passive and not CP.notCar


def manual_overrides_present(CP, params) -> bool:
  """Numeric intent alone admits the custom host and invalidates stale learning."""
  if params is None:
    return False
  if str(CP.carFingerprint) in BOLT_VEHICLES:
    if not runtime_enabled(CP, params):
      return False
  elif not (production_supported_cp(CP) or os.getenv('TORQUE_REPLAY_RUNTIME') == '1' and supported_cp(CP)):
    return False
  settings = read_settings(params, TorqueHost(params, CP).vehicle, CP=CP)
  return settings.valid and (settings.user_factor is not None or settings.user_friction is not None or settings.force_auto)


def runtime_enabled(CP, params=None) -> bool:
  if not supported_cp(CP):
    return False
  if str(CP.carFingerprint) in BOLT_VEHICLES:
    if params is None:
      return False
    settings = read_settings(params, TorqueHost(params, CP).vehicle, CP=CP)
    return settings.valid and (settings.user_friction is not None or settings.force_auto)
  if os.getenv('TORQUE_REPLAY_RUNTIME') == '1':
    return True
  # Preserve native/default startup behavior. A first custom edit is saved for
  # the next drive; a custom host already admitted can refresh/reset live.
  return manual_overrides_present(CP, params)


development_enabled = runtime_enabled


@dataclass(frozen=True)
class TorqueSettings:
  force_auto_off: bool = False
  user_factor: float | None = None
  user_friction: float | None = None
  valid: bool = True
  force_auto: bool = False


def _raw(params, key: str) -> bytes | None:
  try:
    with Path(params.get_param_path(key)).open('rb') as source:
      return source.read(MAX_DOCUMENT_BYTES + 1) if key == DOCUMENT_KEY else source.read()
  except FileNotFoundError:
    return None


def _bool(raw: bytes | None, default: bool) -> bool:
  if raw is None:
    return default
  if raw not in (b'0', b'1'):
    raise ValueError('Malformed torque setting')
  return raw == b'1'


def read_settings(params, vehicle: TorqueTuning, *, allow_learning: bool | None = None, CP=None, automatic_default: bool = False) -> TorqueSettings:
  try:
    force_off = _bool(_raw(params, 'ForceAutoTuneOff'), False) if allow_learning is None else not allow_learning
    basis = (vehicle.lat_accel_factor, vehicle.lat_accel_offset, vehicle.friction)
    document = _raw(params, DOCUMENT_KEY)
    if vehicle.vehicle in GM_VEHICLES and document is None and not automatic_default:
      return TorqueSettings(valid=False)
    if document is None:
      legacy = interpret_legacy({key: _raw(params, key) for key in LEGACY_KEYS}, basis, fingerprint=vehicle.vehicle)
      factor, friction = legacy.factor, legacy.friction
    else:
      factor, friction, review = resolve_document(parse_document(document), vehicle.vehicle, basis)
      if review:
        return TorqueSettings(valid=False)
    force_auto = False
    if CP is not None:
      from openpilot.starpilot.lateral.gm_geometry_runtime import read_geometry
      geometry = read_geometry(params, CP)
      force_off = force_off or geometry.force_off
      force_auto = geometry.force_auto and not force_off
      if force_auto:
        factor = friction = None
    return TorqueSettings(force_off, factor, friction, force_auto=force_auto)
  except (OSError, ValueError, OverflowError):
    return TorqueSettings(valid=False)


class TorqueHost:
  """Choose one complete source and rate-bound changes to the native controller."""
  def __init__(self, params, CP, *, allow_learning: bool | None = None, automatic_interface=None, automatic_controller=None):
    if not supported_cp(CP):
      from openpilot.starpilot.flm.learning_source import conversion_identity
      if automatic_interface is None or conversion_identity(CP, automatic_interface, automatic_controller) is None:
        raise ValueError('Unsupported torque host')
    self.automatic_default = False
    if automatic_interface is not None:
      from openpilot.starpilot.flm.learning_source import conversion_identity
      self.automatic_default = conversion_identity(CP, automatic_interface, automatic_controller) is not None
    self.automatic_interface = automatic_interface
    self.automatic_controller = automatic_controller
    self.native_base: TorqueTuning | None = None
    self.CP = CP
    tune = CP.lateralTuning.torque
    self.vehicle = TorqueTuning(TorqueSource.VEHICLE, str(CP.carFingerprint),
                                float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
    self.params = params
    # An exact controller supplies its startup decision. None preserves the
    # existing live manual preference for other optional replay-host vehicles.
    if allow_learning is None:
      from openpilot.starpilot.lateral.controller_selection import policy_for
      if policy_for(CP) in (None, 'generic'):
        from openpilot.starpilot.lateral.learning_preferences import read_preference
        preference = read_preference(params, CP)
        allow_learning = preference.valid and preference.enabled
    self.allow_learning = allow_learning
    self.settings = TorqueSettings()
    self.last_refresh_ns: int | None = None
    self.last_tick_ns: int | None = None
    self.applied = self.vehicle
    self.selected = self.vehicle
    self.applied_controller = None
    self.last_application = self.vehicle

  def adopt_linear_controller(self, controller) -> None:
    """Transfer the existing scalar owner without reapplying its startup tune."""
    from openpilot.starpilot.flm.learning_source import conversion_identity
    if (conversion_identity(self.CP, self.automatic_interface, self.automatic_controller) is None or
        getattr(getattr(controller, 'starpilot_extension', None), 'parameter_factor', None) is not None):
      raise ValueError('Automatic adoption requires the verified linear parameter domain')
    params = controller.torque_params
    tune = TorqueTuning(TorqueSource.LEARNED, self.vehicle.vehicle,
                        float(params.latAccelFactor), float(params.latAccelOffset), float(params.friction))
    if not self._valid_native(tune):
      raise ValueError('Applied native tune is outside the vehicle envelope')
    self.native_base = self.applied = self.selected = self.last_application = tune
    self.applied_controller = controller

  def _valid_native(self, tune: TorqueTuning) -> bool:
    vehicle = self.vehicle
    return (0.7 * vehicle.lat_accel_factor <= tune.lat_accel_factor <= 1.3 * vehicle.lat_accel_factor and
            abs(tune.lat_accel_offset) <= min(0.5, 0.5 * vehicle.lat_accel_factor) and
            0.5 * vehicle.friction <= tune.friction <= max(0.1, 1.5 * vehicle.friction))

  def _sample_adopted(self, sm, now_ns: int, dt: float, lat_active: bool,
                      automatic: TorqueTuning | None) -> TorqueTuning:
    # The old direct TLS writer retains its last values when the stream is absent.
    # This host becomes its sole writer after adoption; no CP reset is introduced.
    assert self.native_base is not None
    base = self.native_base
    if self.allow_learning and not self.settings.force_auto_off:
      native = self._learned(sm, now_ns)
      if native is not None and self._valid_native(native):
        self.native_base = base = native
    if self.settings.valid and (self.settings.user_factor is not None or self.settings.user_friction is not None):
      target = self._select(sm, now_ns)
    elif automatic is not None and lat_active and self.settings.valid:
      if (automatic.vehicle != base.vehicle or automatic.source != TorqueSource.LEARNED or not
          0.7 * self.vehicle.lat_accel_factor <= automatic.lat_accel_factor <= 1.3 * self.vehicle.lat_accel_factor):
        raise ValueError('Automatic source does not match torque host')
      target = TorqueTuning(TorqueSource.LEARNED, base.vehicle, automatic.lat_accel_factor,
                            base.lat_accel_offset, base.friction)
    else:
      target = base
    self.selected = target
    self.applied = TorqueTuning(target.source, target.vehicle,
      _move(self.applied.lat_accel_factor, target.lat_accel_factor, 0.5 * self.vehicle.lat_accel_factor * dt),
      target.lat_accel_offset, target.friction)
    return self.applied

  def apply(self, controller, tune: TorqueTuning) -> bool:
    """Apply a complete source transition, preserving an untouched vehicle tune.

    This host exclusively owns parameter updates on its controller. Reapplying
    the initial vehicle values is not a harmless refresh: the Ioniq controller
    intentionally retains its original startup PID limits until an actual
    learned/custom update. Source identity matters even when numbers are equal.
    """
    if not isinstance(tune, TorqueTuning) or tune.vehicle != self.vehicle.vehicle:
      raise ValueError('Torque application does not match vehicle')
    if controller is not self.applied_controller:
      self.applied_controller = controller
      self.last_application = self.vehicle
    if tune == self.last_application:
      return False
    controller.update_torque_parameters(*tune.upstream_update())
    self.last_application = tune
    return True

  def _learned(self, sm, now_ns: int) -> TorqueTuning | None:
    name = 'lateralTorqueParameters'
    try:
      stamp = int(sm.logMonoTime[name])
      received = int(sm.recv_time[name] * 1e9)
      maximum_age = int(2e9 / SERVICE_LIST[name].frequency)
      if (not sm.all_checks([name]) or not 0 < stamp <= received <= now_ns or
          now_ns - stamp > maximum_age or now_ns - received > maximum_age):
        return None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return None
    state = sm[name]
    if not (state.useParams or self.settings.force_auto) or not state.valid or int(state.version) != LEARNER_VERSION:
      return None
    factor = float(state.latAccelFactorFiltered)
    offset = float(state.latAccelOffsetFiltered)
    friction = float(state.frictionCoefficientFiltered)
    vehicle = self.vehicle
    if (not all(math.isfinite(v) for v in (factor, offset, friction)) or
        not 0.7 * vehicle.lat_accel_factor <= factor <= 1.3 * vehicle.lat_accel_factor or
        abs(offset) > min(0.5, 0.5 * vehicle.lat_accel_factor) or
        not 0.5 * vehicle.friction <= friction <= max(0.1, 1.5 * vehicle.friction)):
      return None
    return TorqueTuning(TorqueSource.LEARNED, vehicle.vehicle, factor, offset, friction)

  def _select(self, sm, now_ns: int, automatic: TorqueTuning | None = None) -> TorqueTuning:
    vehicle = self.vehicle
    settings = self.settings
    # The new consumer already enforces saved manual/trial precedence and exact
    # source identity; unsupported scalar TLS remains independently disabled.
    if automatic is not None and settings.valid:
      if (automatic.vehicle != vehicle.vehicle or automatic.source != TorqueSource.LEARNED or not
          0.7 * vehicle.lat_accel_factor <= automatic.lat_accel_factor <= 1.3 * vehicle.lat_accel_factor):
        raise ValueError('Automatic source does not match torque host')
      base = self._select(sm, now_ns)
      if base.source == TorqueSource.USER:
        return base
      return TorqueTuning(TorqueSource.LEARNED, vehicle.vehicle, automatic.lat_accel_factor,
                          base.lat_accel_offset, base.friction)
    if not settings.valid:
      return vehicle
    allow_learning = not settings.force_auto_off if self.allow_learning is None else self.allow_learning
    learned = self._learned(sm, now_ns) if allow_learning and not settings.force_auto_off else None
    base = learned or vehicle
    if settings.user_factor is None and settings.user_friction is None:
      return base
    return TorqueTuning(TorqueSource.USER, vehicle.vehicle,
                        settings.user_factor if settings.user_factor is not None else base.lat_accel_factor,
                        vehicle.lat_accel_offset if settings.user_factor is not None else base.lat_accel_offset,
                        settings.user_friction if settings.user_friction is not None else base.friction)

  def sample(self, sm, *, now_ns: int, lat_active: bool, automatic: TorqueTuning | None = None) -> TorqueTuning:
    if self.native_base is not None:
      valid_tick = now_ns > 0 and (self.last_tick_ns is None or 0 < now_ns - self.last_tick_ns <= 50_000_000)
      dt = (now_ns - self.last_tick_ns) / 1e9 if valid_tick and self.last_tick_ns is not None else 0.01
      self.last_tick_ns = now_ns
      if self.last_refresh_ns is None or not valid_tick or now_ns - self.last_refresh_ns >= REFRESH_NS:
        self.settings = read_settings(self.params, self.vehicle, allow_learning=self.allow_learning, CP=self.CP,
                                    automatic_default=self.automatic_default)
        self.last_refresh_ns = now_ns
      return self._sample_adopted(sm, now_ns, dt, lat_active, automatic if valid_tick else None)
    if now_ns <= 0 or (self.last_tick_ns is not None and not 0 < now_ns - self.last_tick_ns <= 50_000_000):
      self.applied = self.vehicle
      self.last_refresh_ns = None
      self.last_tick_ns = now_ns
      self.selected = self.vehicle
      return self.vehicle
    dt = 0.01 if self.last_tick_ns is None else (now_ns - self.last_tick_ns) / 1e9
    self.last_tick_ns = now_ns
    if self.last_refresh_ns is None or now_ns - self.last_refresh_ns >= REFRESH_NS:
      self.settings = read_settings(self.params, self.vehicle, allow_learning=self.allow_learning, CP=self.CP,
                                    automatic_default=self.automatic_default)
      self.last_refresh_ns = now_ns
    if not lat_active or not self.settings.valid:
      self.applied = self.selected = self.vehicle
      return self.vehicle
    self.selected = self._select(sm, now_ns, automatic)
    prior, target = self.applied, self.selected
    factor_step = 0.5 * self.vehicle.lat_accel_factor * dt
    offset_step = 0.1 * dt
    friction_step = max(0.05, self.vehicle.friction) * dt
    self.applied = TorqueTuning(target.source, target.vehicle,
                                _move(prior.lat_accel_factor, target.lat_accel_factor, factor_step),
                                _move(prior.lat_accel_offset, target.lat_accel_offset, offset_step),
                                _move(prior.friction, target.friction, friction_step))
    return self.applied
