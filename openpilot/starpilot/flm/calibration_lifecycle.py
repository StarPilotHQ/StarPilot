"""Calibration estimates share a vehicle frame until the producer resets it."""
import math
import uuid


def calibrated_basis(message):
  try:
    basis_values, spread_values = message.rpyCalib, message.rpyCalibSpread
    if len(basis_values) != 3 or len(spread_values) != 3:
      return None
    basis = tuple(float(v) for v in basis_values)
    blocks = message.validBlocks
    if type(blocks) is not int:
      return None
    spread = tuple(float(v) for v in spread_values)
    if (str(message.calStatus) != 'calibrated' or blocks < 5 or blocks > 50 or
        len(basis) != 3 or not all(math.isfinite(v) for v in basis) or
        len(spread) != 3 or not all(math.isfinite(v) and v >= 0 for v in spread) or
        spread[1] > math.radians(4) or spread[2] > math.radians(2)):
      return None
    return basis, blocks
  except (AttributeError, TypeError, ValueError, OverflowError):
    return None


def generation_valid(value):
  return type(value) is str and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


class ProducerCalibrationLifecycle:
  def __init__(self, token_factory=lambda: uuid.uuid4().hex):
    self.token_factory = token_factory
    self.token = ''
    self.blocks = None
    self.issued = set()

  def observe(self, message, *, current):
    value = calibrated_basis(message)
    if not current and value is not None and (self.blocks is None or value[1] >= self.blocks):
      return None
    if value is None:
      self.token = ''
      self.blocks = None
      return None
    basis, blocks = value
    if self.blocks is not None and blocks < self.blocks:
      self.token = ''
    self.blocks = blocks
    if not current:
      return None
    if not self.token:
      if len(self.issued) >= 32:
        return None  # Bounded reset history; never recycle an old generation.
      try:
        token = self.token_factory()
      except Exception:
        return None
      if not generation_valid(token) or token in self.issued:
        return None
      self.token = token
      self.issued.add(token)
    self.blocks = blocks
    return self.token, basis


class ConsumerCalibrationLifecycle:
  def __init__(self):
    self.generation = ''
    self.blocks = None
    self.rejected = set()
    self.exhausted = False
    self.current = False
    self.last_invalid_ns = 0

  def retire(self, generation):
    if generation and generation not in self.rejected:
      if len(self.rejected) >= 32:
        self.exhausted = True
      else:
        self.rejected.add(generation)

  def capture(self, generation):
    if not generation_valid(generation):
      return False
    if self.generation and generation != self.generation:
      self.retire(self.generation)
    self.generation = generation
    return not self.exhausted and generation not in self.rejected

  def reject(self, *, now_ns=0):
    self.retire(self.generation)
    if type(now_ns) is int and now_ns > self.last_invalid_ns:
      self.last_invalid_ns = now_ns
    self.blocks = None
    self.current = False

  def unavailable(self, *, now_ns=0):
    if type(now_ns) is int and now_ns > self.last_invalid_ns:
      self.last_invalid_ns = now_ns
    self.current = False

  def observe(self, message, *, current, now_ns=0):
    value = calibrated_basis(message)
    if not current and value is not None and (self.blocks is None or value[1] >= self.blocks):
      self.unavailable(now_ns=now_ns)
      return False
    if value is None:
      self.reject(now_ns=now_ns)
      return False
    _, blocks = value
    if self.blocks is not None and blocks < self.blocks:
      self.reject(now_ns=now_ns)
    self.blocks = blocks
    self.current = bool(current)
    return self.current

  def admit(self, generation, observed_ns):
    if (not self.current or self.exhausted or not generation_valid(generation) or
        type(observed_ns) is not int or observed_ns <= self.last_invalid_ns or
        generation in self.rejected):
      return False
    return self.capture(generation)


def configuration_calibration(sm, calibration_lifecycle, now):
  # Calibration describes the vehicle frame, not a per-message command lease.
  # Like torqued's PoseCalibrator, retain a valid calibrated transform between
  # deliveries. Sample freshness and current native authority are checked
  # independently when admitting physical observations and applying offers.
  stamp = int(sm.logMonoTime['extrinsicsCalibration'])
  receipt = int(round(sm.recv_time['extrinsicsCalibration'] * 1e9))
  if not (sm.seen['extrinsicsCalibration'] and sm.valid['extrinsicsCalibration'] and
          0 < stamp <= now and 0 < receipt <= now):
    return calibration_lifecycle.observe(None, current=False)
  return calibration_lifecycle.observe(sm['extrinsicsCalibration'], current=True)
