"""Fail-closed dispatch to vehicle-owned AOL capabilities."""

from functools import lru_cache

from capnp.lib.capnp import _DynamicStructBuilder, _DynamicStructReader
from opendbc.car.structs import CarParams

from openpilot.starpilot.aol.policy import AolVehiclePolicy
from openpilot.starpilot.aol.intent import AolCardIntent
from openpilot.starpilot.car.honda import aol as honda
from openpilot.starpilot.car.hyundai import aol as hyundai
from openpilot.starpilot.car.gm import aol as gm
from openpilot.starpilot.car.ford import aol as ford
from openpilot.starpilot.car.mazda import aol as mazda
from openpilot.starpilot.car.chrysler import aol as chrysler
from openpilot.starpilot.car.subaru import aol as subaru
from openpilot.starpilot.car.tesla import aol as tesla
from openpilot.starpilot.car.toyota import aol as toyota
from openpilot.starpilot.car.rivian import aol as rivian
from openpilot.starpilot.car.volkswagen import aol as volkswagen
from openpilot.starpilot.car.nissan import aol as nissan
from openpilot.starpilot.car.volvo import aol as volvo

_PORTS = {'honda': honda, 'hyundai': hyundai, 'gm': gm, 'ford': ford, 'mazda': mazda,
          'tesla': tesla, 'toyota': toyota, 'chrysler': chrysler, 'nissan': nissan, 'volkswagen': volkswagen, 'subaru': subaru, 'rivian': rivian,
          'volvo': volvo}
_POLICY_CACHE_SIZE = 8
_POLICY_CACHE_MAX_BYTES = 64 * 1024


def _policy_for_uncached(CP) -> AolVehiclePolicy:
  try:
    port = _PORTS.get(CP.brand)
    return port.policy_for(CP) if port is not None else AolVehiclePolicy()
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return AolVehiclePolicy()


def _car_params_bytes(CP) -> bytes | None:
  # Unknown ducktypes keep the original dispatch behavior without serialization.
  cp_type = type(CP)
  if cp_type is not _DynamicStructBuilder and cp_type is not _DynamicStructReader:
    return None
  try:
    if CP.schema != CarParams.schema:
      return None
    # Never serialize the original builder: to_bytes changes its write state.
    snapshot = CP.copy() if cp_type is _DynamicStructBuilder else CP.as_builder()
    if type(snapshot) is not _DynamicStructBuilder:
      return None
    key = snapshot.to_bytes()
    return key if len(key) <= _POLICY_CACHE_MAX_BYTES else None
  except Exception:
    return None


@lru_cache(maxsize=_POLICY_CACHE_SIZE)
def _policy_for_bytes(key: bytes) -> AolVehiclePolicy:
  # Evaluate the same immutable bytes as the key, never a mutable caller alias.
  with CarParams.from_bytes(key) as CP:
    if type(CP) is not _DynamicStructReader or CP.schema != CarParams.schema:
      raise TypeError('Unsupported CarParams snapshot')
    return _policy_for_uncached(CP)


def policy_for(CP) -> AolVehiclePolicy:
  key = _car_params_bytes(CP)
  if key is not None:
    try:
      # Only the frozen static policy is reused. Live authority stays uncached.
      return _policy_for_bytes(key)
    except Exception:
      pass
  return _policy_for_uncached(CP)


def native_profile_supported(model: int, param: int) -> bool:
  return any(port.native_profile_supported(model, param) for port in _PORTS.values())


def native_matches_cp(CP, model: int, param: int) -> bool:
  try:
    if CP.brand == 'hyundai':
      config = hyundai.aol_safety_config(CP)
      return bool(config is not None and hyundai.native_accepts_cp(CP, model, param) and
                  model == int(config.safetyModel.raw) and param == int(config.safetyParam))
    if CP.brand == 'ford':
      config = ford.aol_safety_config(CP)
      return bool(config is not None and ford.native_accepts_cp(CP, model, param) and
                  model == int(config.safetyModel.raw) and param == int(config.safetyParam))
    if CP.brand == 'honda' and len(CP.safetyConfigs) == 2:
      config = honda.aol_safety_config(CP)
      return bool(config is not None and honda.native_accepts_cp(CP, model, param) and
                  model == int(config.safetyModel.raw) and param == int(config.safetyParam))
    if CP.brand == 'volkswagen':
      config = volkswagen.aol_safety_config(CP)
      return bool(config is not None and volkswagen.native_accepts_cp(CP, model, param) and
                  model == int(config.safetyModel.raw) and param == int(config.safetyParam))
    port = _PORTS.get(CP.brand)
    return bool(port is not None and port.native_accepts_cp(CP, model, param) and
                model == int(CP.safetyConfigs[0].safetyModel.raw) and
                param == int(CP.safetyConfigs[-1].safetyParam))
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return False


def native_admission_key(CP):
  """Bind structural reuse to the complete mutable vehicle configuration."""
  try:
    if CP.brand == 'hyundai':
      existing = hyundai.native_admission_key(CP)
      if existing is not None:
        return existing
    if CP.brand in _PORTS:
      # Reuse only one exact packet's structural admission, for every owner.
      # Full CP bytes invalidate every mutable field; live gates stay per call.
      key = _car_params_bytes(CP)
      if key is not None:
        return ('vehicle-full-cp', key)
    return None
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return None


def native_latch_rejected(CP, native, *, state=None) -> bool:
  if native is None:
    return False
  try:
    port = _PORTS.get(CP.brand)
    retain = getattr(port, 'retain_on_native_denial', None)
    rejected = getattr(port, 'native_latch_rejected', None)
    if retain is None and rejected is None:
      return False
    matched = native_matches_cp(CP, native.safetyModel, native.safetyParam)
    if retain is not None and matched and retain(CP, native, state):
      return False
    return bool(rejected is not None and matched and rejected(CP, native))
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return False


def create_intent(CP, settings, policy):
  port = _PORTS.get(CP.brand)
  factory = getattr(port, 'create_intent', None)
  if factory is not None:
    return factory(CP, settings)
  return AolCardIntent(settings, explicit_latch=policy.explicit_latch)


def ordinary_axis_request_allowed(CP, CS) -> bool:
  """Required ordinary transport has no permission without its port owner."""
  try:
    port = _PORTS.get(CP.brand)
    allowed = getattr(port, 'ordinary_axis_request_allowed', None)
    return bool(allowed is not None and allowed(CP, CS))
  except (AttributeError, TypeError, ValueError, OverflowError, IndexError):
    return False


def allow_lateral_onset(CP, *, requested, normal_enabled, steering_pressed, previous_active):
  """Optional vehicle-owned onset restriction; never creates permission."""
  port = _PORTS.get(CP.brand)
  owner = getattr(port, 'allow_lateral_onset', None)
  if owner is None:
    return bool(requested)
  return bool(requested and owner(CP, requested=requested, normal_enabled=normal_enabled,
                                  steering_pressed=steering_pressed, previous_active=previous_active))


def configuration_settings_policy(CP):
  from openpilot.starpilot.car.hyundai.settings import configuration_wheel_policy
  return configuration_wheel_policy(CP) or toyota.configuration_settings_policy(CP) or gm.configuration_settings_policy(CP)


def native_bootstrap_supported(CP):
  """Dispatch only the newly owned Chrysler profile; preserve existing GM admission."""
  try:
    if CP.brand == 'chrysler':
      return chrysler.native_bootstrap_supported(CP)
    from opendbc.car.gm.aol import native_bootstrap_supported as gm_bootstrap
    return gm_bootstrap(CP)
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def intent_disarming_fault(CP, events, state, **context):
  if CP.brand == 'volkswagen':
    return volkswagen.intent_disarming_fault(CP, events, state, **context)
  if CP.brand == 'chrysler':
    return chrysler.intent_disarming_fault(CP, events, state, **context)
  return gm.intent_disarming_fault(CP, events, state, **context)
