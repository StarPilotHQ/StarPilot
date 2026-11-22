"""Read-only estimator references for steering settings, never tuning authority."""
import json
import math
import time

import capnp
from opendbc.car import structs as car
from openpilot.cereal import log
from openpilot.starpilot.lateral.delay_preferences import scope
from openpilot.starpilot.schema_cache import inspect_cache

ERRORS = (AttributeError, KeyError, OSError, TypeError, ValueError, OverflowError, capnp.KjException)


def _basis(cp):
  return (*scope(cp), float(cp.steerRatio), json.dumps(cp.lateralTuning.to_dict(), sort_keys=True), str(cp.carVin), int(cp.flags))


def _fresh(sm, service, now, age=2_000_000_000):
  try:
    stamp = int(sm.logMonoTime[service])
    received = int(sm.recv_time[service] * 1e9)
    if (sm.seen[service] and sm.alive[service] and sm.valid[service] and
        getattr(sm, 'after_mono_ns', 0) < stamp <= now and now - stamp <= age and 0 <= now - received <= age):
      return sm[service]
  except ERRORS:
    pass
  return None


def _values(message, service, cp):
  if service == 'lateralDelay':
    value = float(message.lateralDelayEstimate)
    if (message.version == 1 and str(message.status) == 'estimated' and message.validBlocks >= 5 and
        math.isfinite(message.lateralDelayEstimateStd) and 0 <= message.lateralDelayEstimateStd <= .1 and .15 <= value <= .65):
      return {'steering_delay': f'{value:.3f} s'}
  elif service == 'lateralTorqueParameters':
    factor, friction = float(message.latAccelFactorFiltered), float(message.frictionCoefficientFiltered)
    if (cp.lateralTuning.which() == 'torque' and message.version == 1 and message.valid and message.totalBucketPoints > 0 and
        math.isfinite(factor) and math.isfinite(friction) and factor > 0 and friction >= 0):
      return {'torque:factor:value': f'{factor:.3f}', 'torque:friction:value': f'{friction:.3f}'}
  elif service == 'vehicleParameters':
    ratio = float(message.steerRatio)
    if (message.valid and message.sensorValid and message.posenetValid and message.steerRatioValid and
        .5 * cp.steerRatio <= ratio <= 2 * cp.steerRatio and abs(ratio - cp.steerRatio) > 1e-5):
      return {'torque:ratio:value': f'{ratio:.2f}'}
  return {}


def _progress(message, service, cp):
  if message is None or service not in ('lateralDelay', 'lateralTorqueParameters') or message.version != 1:
    return {}
  percent = message.calPerc
  if type(percent) is not int or not 0 <= percent < 100:
    return {}
  if service == 'lateralTorqueParameters':
    if cp.lateralTuning.which() != 'torque' or not message.useParams:
      return {}
    return dict.fromkeys(('torque:factor:value', 'torque:friction:value'), percent)
  if str(message.status) not in ('unestimated', 'estimated'):
    return {}
  return {'steering_delay': percent}


def _cached(owner, key):
  raw, readable = owner._read_saved(key, 2_097_152)
  return inspect_cache(key, raw).payload if readable else None


def learned_references(owner, *, now_ns=None):
  """Prefer fresh estimates; retained caches are explicitly labeled as historical."""
  cp, result = owner.vehicle_params(), {}
  now = time.monotonic_ns() if now_ns is None else now_ns
  try:
    basis = _basis(cp)
  except ERRORS:
    return result
  try:
    sm = owner.learning_messages()
  except ERRORS:
    sm = None
  live_cp = _fresh(sm, 'carParams', now, 100_000_000_000)
  device = _fresh(sm, 'deviceState', now, 10_000_000_000)
  live, cached = False, False
  try:
    live = live_cp is not None and _basis(live_cp) == basis and device is not None and device.started
    previous = _cached(owner, 'CarParamsPrevRoute')
    if previous is not None:
      with car.CarParams.from_bytes(previous) as prior:
        cached = _basis(prior) == basis
  except ERRORS:
    pass
  from openpilot.starpilot.lateral.gm_geometry_runtime import read_geometry
  geometry = read_geometry(owner.params, cp)
  manual_ratio = geometry.force_off or (geometry.profile is not None and
                 geometry.profile.ratio.mode == 'custom' and not geometry.force_auto)
  for key, service in (('LiveDelay', 'lateralDelay'), ('LiveTorqueParameters', 'lateralTorqueParameters'),
                       ('LiveParametersV2', 'vehicleParameters')):
    try:
      message = _fresh(sm, service, now) if live and not (manual_ratio and service == 'vehicleParameters') else None
      values = _values(message, service, cp) if message is not None else {}
      if values:
        result.update({field: 'Live learned · ' + value for field, value in values.items()})
      elif cached:
        payload = _cached(owner, key)
        if payload is not None:
          with log.Event.from_bytes(payload) as event:
            if event.valid:
              values = _values(getattr(event, service), service, cp)
              result.update({field: 'Last learned · ' + value for field, value in values.items()})
      for field, percent in _progress(message, service, cp).items():
        result[field] = result[field] + f' · {percent}%' if field in result else f'Learning · {percent}%'
    except ERRORS:
      continue
  return result
