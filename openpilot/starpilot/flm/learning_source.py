"""Identity-bound automatic fit consumption; never changes steering authority."""

from dataclasses import dataclass
import hashlib
import json
import math

from opendbc.car.car_helpers import interfaces
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car import structs
from openpilot.starpilot.flm.live import gm_capability
from openpilot.starpilot.lateral.controller_selection import ControllerMode, read_selection
from openpilot.starpilot.lateral.learning_preferences import read_preference
from openpilot.starpilot.lateral.torque_runtime import factor_edit_supported
from openpilot.starpilot.lateral.torque_settings import (
  DOCUMENT_KEY, LEGACY_KEYS, MAX_DOCUMENT_BYTES, interpret_legacy, parse_document, serialize_document,
)
from openpilot.starpilot.lateral.torque_tuning import TorqueSource, TorqueTuning
from openpilot.starpilot.saved_source import read_saved

SERVICE = 'autoSteeringLearning'
VERSION = 1
MAX_AGE_NS = 500_000_000
RESET_KEY = 'AutoSteeringLearningReset'
REFRESH_NS = 1_000_000_000


def learning_payload(envelope):
  """The appended learner child; historical CarParams defaults cannot become a fit."""
  return envelope.learning


def _digest(value):
  return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def automatic_capability(CP, controller):
  """Separate scalar-fit scope; existing manual and native guards are unchanged."""
  mode = ControllerMode(controller)
  if (str(CP.carFingerprint) not in interfaces or CP.notCar or CP.passive or CP.dashcamOnly or
      CP.steerControlType != structs.CarParams.SteerControlType.torque or CP.lateralTuning.which() != 'torque'):
    return None
  tune = CP.lateralTuning.torque
  basis = (float(tune.latAccelFactor), float(tune.latAccelOffset), float(tune.friction))
  if not all(math.isfinite(v) for v in basis) or basis[0] <= 0 or basis[2] < 0:
    return None
  if str(CP.brand) == 'gm':
    capability = gm_capability(CP, controller)
    return capability if capability is not None and factor_edit_supported(CP, mode) else None
  if mode != ControllerMode.STANDARD:
    return None
  return {'fingerprint': str(CP.carFingerprint), 'controller': str(mode), 'policy': 'native_standard',
          'basis': basis, 'profile': 'torque_linear'}


def conversion_identity(CP, CI, controller):
  """Require the actual registered interface's linear forward/inverse pair."""
  try:
    if automatic_capability(CP, controller) is None:
      return None
    interface = interfaces[str(CP.carFingerprint)]
    if type(CI) is not interface or str(CP.brand) != interface.__module__.split('.')[-2]:
      return None
    forward, inverse = CI.torque_from_lateral_accel(), CI.lateral_accel_from_torque()
    if (getattr(forward, '__func__', None) is not CarInterfaceBase.torque_from_lateral_accel_linear or
        getattr(inverse, '__func__', None) is not CarInterfaceBase.lateral_accel_from_torque_linear):
      return None
    return 'opendbc.linear_inverse_v1'
  except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RuntimeError):
    return None


def identity_for(CP, controller, params, conversion_version):
  """Return exact current baseline identity and explicit manual precedence."""
  capability = automatic_capability(CP, controller)
  preference = read_preference(params, CP)
  selection = read_selection(params, CP)
  if (capability is None or not conversion_version or not preference.valid or
      selection.source == 'invalid' or str(selection.mode) != str(controller)):
    return None, False, False
  reset, readable = read_saved(params, RESET_KEY, 64)
  if not readable or reset is not None and (not reset or any(c not in b'0123456789abcdef' for c in reset)):
    return None, False, False
  raw, readable = read_saved(params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
  if not readable:
    return None, False, False
  basis = capability['basis']
  manual = False
  if raw is None:
    legacy = {}
    for key in LEGACY_KEYS:
      value, readable = read_saved(params, key, MAX_DOCUMENT_BYTES)
      if not readable:
        return None, False, False
      legacy[key] = value
    choice = interpret_legacy(legacy, basis, fingerprint=str(CP.carFingerprint))
    manual = choice.factor is not None or choice.friction is not None
    baseline = {key: None if value is None else value.hex() for key, value in legacy.items()}
  else:
    profiles = parse_document(raw)
    profile = profiles.get(str(CP.carFingerprint))
    if profile is not None and profile.basis != basis:
      return None, False, False
    if profile is not None and profile.geometry is not None and profile.geometry.learning == 'force_off':
      return None, False, False
    manual = profile is not None and (profile.factor.mode == 'custom' or profile.friction.mode == 'custom' or
                                     profile.proportional_gain.mode == 'custom' or profile.flm is not None or
                                     profile.manual_coordinates is not None and any(v for _, v in profile.manual_coordinates.values) or
                                     profile.geometry is not None and (profile.geometry.ratio.mode == 'custom' or
                                                                      profile.geometry.full_delay.mode == 'custom'))
    baseline = serialize_document({str(CP.carFingerprint): profile}).hex() if profile is not None else None
  firmware = sorted((str(fw.ecu), int(fw.address), int(fw.subAddress), bytes(fw.fwVersion).hex()) for fw in CP.carFw)
  identity = {'vehicle': str(CP.carFingerprint), 'brand': str(CP.brand), 'controller': str(controller),
              'policy': capability['policy'], 'profile': capability['profile'], 'conversionVersion': conversion_version,
              'controllerRevision': 'linear-controller-v1',
              'firmwareSha256': _digest(firmware), 'basis': list(basis), 'baselineSha256': _digest(baseline),
              'preferenceSha256': _digest((preference.source, preference.enabled)),
              'resetGeneration': '0' if reset is None else reset.decode('ascii')}
  return identity, preference.enabled, manual


@dataclass(frozen=True)
class AdmittedPolicy:
  """Qualified algorithm policy shared with the producer; not road calibration."""
  max_step_fraction: float

  def __post_init__(self):
    if not math.isfinite(self.max_step_fraction) or not 0 < self.max_step_fraction <= .3:
      raise ValueError('Invalid admitted policy step')


class AutomaticLearningConsumer:
  def __init__(self, CP, CI, controller, params, *, policies=None):
    self.CP, self.controller, self.params = CP, str(controller), params
    self.conversion = conversion_identity(CP, CI, self.controller)
    if policies is None and self.conversion is not None:
      from openpilot.starpilot.flm.automatic import linear_policy
      policy = linear_policy()
      policies = {policy.identifier: AdmittedPolicy(policy.max_step_fraction)}
    self.policies = dict(policies or {})
    self.identity = None
    self.enabled = self.manual = False
    self.refresh_ns = self.last_ns = None
    self.session = ''
    self.sequence = -1
    self.result = None
    self.status = 'unavailable'

  @property
  def supported(self):
    return self.conversion is not None

  def sample(self, sm, *, now_ns, lat_active):
    if not self.supported:
      return None
    from openpilot.starpilot.flm.automatic import admit_linear_factor
    if type(now_ns) is not int or now_ns <= 0 or self.last_ns is not None and now_ns < self.last_ns:
      self.identity = self.result = None
      self.refresh_ns = None
      self.last_ns = now_ns
      self.status = 'unavailable'
      return None
    self.last_ns = now_ns
    if self.refresh_ns is None or now_ns - self.refresh_ns >= REFRESH_NS:
      self.refresh_ns = now_ns
      try:
        identity, self.enabled, self.manual = identity_for(self.CP, self.controller, self.params, self.conversion)
      except (AttributeError, OSError, TypeError, ValueError, OverflowError, UnicodeError):
        identity, self.enabled, self.manual = None, False, False
      if identity != self.identity:
        self.result = None
        self.session, self.sequence = '', -1
      self.identity = identity
    if self.identity is None or not self.enabled or self.manual or not lat_active:
      self.status = 'manualOverride' if self.manual else 'unavailable'
      return None
    try:
      if not (sm.seen[SERVICE] and sm.alive[SERVICE] and sm.valid[SERVICE] and sm.freq_ok[SERVICE]):
        return None
      received_ns = int(round(sm.recv_time[SERVICE] * 1e9))
      if not 0 <= now_ns - received_ns <= MAX_AGE_NS:
        return None
      state = learning_payload(sm[SERVICE])
      observed, until = int(state.observedMonoTime), int(state.validUntilMonoTime)
      if not (int(state.version) == VERSION and 0 < observed <= now_ns <= until <= observed + MAX_AGE_NS):
        return None
      wire_identity = state.identity.to_dict()
      if wire_identity != self.identity:
        return None
      session, sequence = str(state.session), int(state.sequence)
      if not session or len(session) > 64 or sequence < 0:
        return None
      payload = state.to_dict()
      if session == self.session and (sequence < self.sequence or sequence == self.sequence and payload != self.result):
        return None
      self.session, self.sequence, self.result = session, sequence, payload
      self.status = str(state.status)
      if self.status != 'qualifiedFit' or str(state.family) != 'linearFactor':
        return None
      if not (0 < int(state.sourceWindowStartMonoTime) < int(state.sourceWindowEndMonoTime) <= observed):
        return None
      policy = self.policies.get(str(state.acceptancePolicyId))
      if not isinstance(policy, AdmittedPolicy):
        return None
      losses = (float(state.trainBaselineLoss), float(state.trainCandidateLoss),
                float(state.heldoutBaselineLoss), float(state.heldoutCandidateLoss))
      if (int(state.trainWindows) < 8 or int(state.heldoutWindows) < 8 or
          any(not math.isfinite(v) or v < 0 for v in losses) or
          not losses[1] < losses[0] or not losses[3] < losses[2]):
        return None
      factor = float(state.latAccelFactor)
      basis = self.identity['basis']
      if not admit_linear_factor(factor, basis[0], basis[0], policy.max_step_fraction):
        return None
      return TorqueTuning(TorqueSource.LEARNED, self.identity['vehicle'], factor, basis[1], basis[2])
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
      return None
