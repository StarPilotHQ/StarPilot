"""Startup-selected manual gain ownership; torque parameter learning stays independent."""

from __future__ import annotations

import math
import time

from opendbc.car.interfaces import get_torque_params
from openpilot.starpilot.car.volkswagen.model_smoothing import supported as factory_manual_golf

from openpilot.starpilot.lateral.torque_runtime import gm_manual_supported_cp, production_supported_cp
from openpilot.starpilot.lateral.torque_supported import BOLT_VEHICLES, GM_VEHICLES
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, MAX_DOCUMENT_BYTES, GainBasis, gain_bounds, parse_document
from openpilot.starpilot.saved_source import read_saved

REFRESH_NS = 1_000_000_000


def gain_supported(cp) -> bool:
  if gm_manual_supported_cp(cp) or factory_manual_golf(cp):
    return True
  if str(cp.brand) in ('gm', 'ford') or not production_supported_cp(cp):
    return False
  from openpilot.starpilot.lateral.controller_selection import policy_for
  return policy_for(cp) in (None, 'generic')


def pid_torque_basis(cp) -> tuple[float, float, float]:
  # Use the platform's catalog basis in the shared tuning document. PID
  # consumes only the gain; the factor/friction controls stay unavailable.
  tune = get_torque_params()[str(cp.carFingerprint)]
  return float(tune['LAT_ACCEL_FACTOR']), 0.0, float(tune['FRICTION'])


def pid_gain_basis(cp) -> GainBasis:
  tune = cp.lateralTuning.pid
  return GainBasis('pid', (tuple(tune.kpBP), tuple(tune.kpV)), pid_torque_basis(cp))


def gain_basis(cp, controller) -> GainBasis:
  if cp.lateralTuning.which() == "pid":
    return pid_gain_basis(cp)
  tune = cp.lateralTuning.torque
  return GainBasis(str(controller.controller_mode),
                   (tuple(controller.pid._k_p[0]), tuple(controller.pid._k_p[1])),
                   (tune.latAccelFactor, tune.latAccelOffset, tune.friction))


def fixed_kp_from_profile(cp, selection, profile) -> float | None:
  if profile is None or profile.proportional_gain.mode != 'custom' or str(cp.carFingerprint) in GM_VEHICLES or not gain_supported(cp):
    return None
  if cp.lateralTuning.which() == 'pid':
    basis = pid_gain_basis(cp)
  else:
    from openpilot.selfdrive.controls.lib.latcontrol_torque import INTERP_SPEEDS, KP_INTERP, STARPILOT_KP
    tune = cp.lateralTuning.torque
    table = ((0.,), (STARPILOT_KP,)) if selection.mode.value == 'starpilot' else (tuple(INTERP_SPEEDS), tuple(KP_INTERP))
    basis = GainBasis(selection.mode.value, table, (tune.latAccelFactor, tune.latAccelOffset, tune.friction))
  return profile.proportional_gain.custom_value if profile.gain_basis == basis else None


def install_fixed_kp(cp, controller, value) -> None:
  if not gain_supported(cp) or str(cp.carFingerprint) in GM_VEHICLES or type(value) not in (int, float) or not math.isfinite(value):
    raise ValueError('Invalid fixed proportional gain')
  basis = gain_basis(cp, controller)
  low, high = gain_bounds(str(cp.carFingerprint), basis)
  if not low <= value <= high:
    raise ValueError('Out-of-range fixed proportional gain')
  speeds, gains = basis.source_table
  controller.pid._k_p = [list(speeds), [gain * value / gains[-1] for gain in gains]]
  if hasattr(controller, 'native_learning_baseline'):
    from openpilot.starpilot.flm.runtime_adapter_registry import baseline_values
    controller.native_learning_baseline = baseline_values(controller)


def _selected(params, cp, basis: GainBasis) -> float | None:
  try:
    if str(cp.carFingerprint) in BOLT_VEHICLES:
      advanced, readable = read_saved(params, "AdvancedLateralTune", 64)
      if not readable or advanced != b"1":
        return None
    raw, readable = read_saved(params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    if not readable or raw is None:
      return None
    profile = parse_document(raw).get(str(cp.carFingerprint))
    if profile is None or profile.gain_basis != basis or profile.proportional_gain.mode != "custom":
      return None
    return profile.proportional_gain.custom_value
  except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
    return None


class GenericGainOwner:
  def __init__(self, params, cp, controller, basis: GainBasis, value: float):
    self.params = params
    self.cp = cp
    self.pid = controller.pid
    self.basis = basis
    self.last_refresh_ns: int | None = None
    self.current_table = basis.source_table
    if str(cp.carFingerprint) in GM_VEHICLES:
      self._apply(value)
    else:
      install_fixed_kp(cp, controller, value)

  def _apply(self, value: float | None) -> None:
    table = self.basis.source_table if value is None else ((0.0,), (value,))
    if table != self.current_table:
      self.pid._k_p = [list(row) for row in table]
      self.current_table = table

  def refresh(self, *, now_ns: int | None = None) -> None:
    if str(self.cp.carFingerprint) not in GM_VEHICLES:
      return
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    if self.last_refresh_ns is not None and 0 <= now_ns - self.last_refresh_ns < REFRESH_NS:
      return
    self.last_refresh_ns = now_ns
    self._apply(_selected(self.params, self.cp, self.basis))


def create_gain_owner(params, cp, controller) -> GenericGainOwner | None:
  if not gain_supported(cp):
    return None
  basis = gain_basis(cp, controller)
  value = _selected(params, cp, basis)
  return None if value is None else GenericGainOwner(params, cp, controller, basis, value)
