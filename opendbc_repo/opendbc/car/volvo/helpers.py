from opendbc.car.volvo.values import CarControllerParams

def checksum_1_lca_2_message(b0: int, b5: int) -> int:
  """LCA_2 (0x69) byte6 checksum over bytes 0 and 5"""
  if b0 == 0 and b5 == 128:
    return 0
  m0 = (0x08, 0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00)
  m5 = (0x83, 0x86, 0xCF, 0xCD, 0x09, 0x02, 0x44, 0x89)

  def parity8(x: int) -> int:
    return bin(x & 0xFF).count("1") & 1

  return sum((parity8(b0 & m0[bit]) ^ parity8(b5 & m5[bit])) << bit for bit in range(8))


def checksum_2_lca_2_message(b0: int, b1: int, b3: int = 0, b4: int = 0) -> int:
  b0 &= 0xFF
  b1 &= 0xFF
  b3 &= 0xFF
  b4 &= 0xFF

  def bit(byte, pos):
    return (byte >> pos) & 1

  c = 0
  # Bit 0
  c |= (bit(b0, 0) ^ bit(b1, 2) ^ bit(b1, 3) ^ bit(b1, 4) ^ bit(b1, 5) ^ bit(b4, 0) ^ bit(b4, 1)) << 0
  # Bit 1
  c |= (bit(b0, 1) ^ bit(b0, 3) ^ bit(b1, 0) ^ bit(b1, 3) ^ bit(b1, 5) ^ bit(b1, 6) ^
        bit(b3, 1) ^ bit(b4, 0) ^ bit(b4, 1) ^ bit(b4, 2)) << 1
  # Bit 2
  c |= (bit(b0, 0) ^ bit(b0, 3) ^ bit(b1, 1) ^ bit(b1, 2) ^ bit(b1, 3) ^ bit(b1, 4) ^ bit(b1, 5) ^ bit(b1, 6) ^
        bit(b3, 0) ^ bit(b3, 1) ^ bit(b4, 0) ^ bit(b4, 2) ^ bit(b4, 3)) << 2
  # Bit 3
  c |= (bit(b0, 0) ^ bit(b0, 1) ^ bit(b1, 0) ^ bit(b1, 4) ^ bit(b1, 6) ^
        bit(b3, 0) ^ bit(b3, 1) ^ bit(b4, 0) ^ bit(b4, 3) ^ bit(b4, 4)) << 3
  # Bit 4
  c |= (bit(b0, 0) ^ bit(b0, 1) ^ bit(b0, 3) ^ bit(b1, 1) ^ bit(b1, 2) ^ bit(b1, 3) ^ bit(b1, 4) ^
        bit(b4, 4) ^ bit(b4, 5)) << 4
  # Bit 5
  c |= (bit(b0, 1) ^ bit(b1, 0) ^ bit(b1, 2) ^ bit(b1, 3) ^ bit(b1, 5) ^
        bit(b3, 1) ^ bit(b4, 5) ^ bit(b4, 6)) << 5
  # Bit 6
  c |= (bit(b0, 3) ^ bit(b1, 0) ^ bit(b1, 1) ^ bit(b1, 3) ^ bit(b1, 6) ^
        bit(b3, 0) ^ bit(b4, 6) ^ bit(b4, 7)) << 6
  # Bit 7
  c |= (bit(b0, 3) ^ bit(b1, 1) ^ bit(b1, 2) ^ bit(b1, 4) ^ bit(b4, 0) ^ bit(b4, 7)) << 7
  return c & 0xFF

def checksum_1_pscm_related_message(b1, b2):
  # Masks per checksum bit (bit 0..7) for b1 and b2
  M1 = [0x41, 0x82, 0x55, 0xF3, 0xA7, 0x46, 0x94, 0x20]
  M2 = [0x00, 0x00, 0x80, 0x00, 0x80, 0x00, 0x80, 0x80]

  def parity8(x: int) -> int:
    # 1 if x has an odd number of bits set, else 0
    x ^= x >> 4
    x ^= x >> 2
    x ^= x >> 1
    return x & 1

  b1 &= 0xFF
  b2 &= 0xFF

  c = 0
  for bit in range(8):
    p = 0
    if M1[bit]:
      p ^= parity8(b1 & M1[bit])
    if M2[bit]:
      p ^= parity8(b2 & M2[bit])
    c |= (p << bit)

  return c & 0xFF


def checksum_2_pscm_related_message(b2):
  lut = {
    0x80: 0xBF,
    0x81: 0xF3,
    0x82: 0x27,
    0x83: 0x6B,
    0x84: 0x92,
    0x85: 0xDE,
    0x86: 0x0A,
    0x87: 0x46,
    0x88: 0xE5,
    0x89: 0xA9,
    0x8A: 0x7D,
    0x8B: 0x31,
    0x8C: 0xC8,
    0x8D: 0x84,
    0x8E: 0x50,
  }
  return lut.get(b2, 0)


def pscm_status_supported(msg: dict, dat: bytes) -> bool:
  """Whether a PSCM_RELATED (0x17) frame reports a status the PSCM accepts LCA steering in"""
  counter, replica = int(msg['RACK_COUNTER']), int(msg['RACK_COUNTER_REPLICA'])
  return (len(dat) == 8 and
          msg['PSCM_STATUS'] == 8 and
          counter < 15 and replica < 15 and (counter - replica) % 15 in (0, 1) and
          not int(msg['LCA_ENABLED_ECHO']) & 0x8 and
          msg['CHECKSUM_1'] == checksum_1_pscm_related_message(dat[1], dat[2]) and
          msg['CHECKSUM_2'] == checksum_2_pscm_related_message(dat[2]))


def checksum_lca_5_message(byte0: int, byte1: int, byte3: int, byte4: int, byte5: int) -> int:
  def bit(byte_val, pos):
    return (byte_val >> pos) & 1

  checksum = 0

  # Bit 0: XOR of 13 bits
  checksum |= (
    bit(byte0, 2) ^ bit(byte0, 3) ^ bit(byte0, 5) ^ bit(byte0, 7) ^
    bit(byte1, 2) ^
    bit(byte3, 4) ^ bit(byte3, 6) ^
    bit(byte4, 0) ^ bit(byte4, 1) ^ bit(byte4, 5) ^ bit(byte4, 6) ^
    bit(byte5, 3) ^ bit(byte5, 6)
  ) << 0

  # Bit 1: XOR of 12 bits
  checksum |= (
    bit(byte0, 0) ^ bit(byte0, 3) ^ bit(byte0, 4) ^ bit(byte0, 7) ^
    bit(byte1, 0) ^ bit(byte1, 3) ^
    bit(byte3, 5) ^ bit(byte3, 7) ^
    bit(byte4, 2) ^ bit(byte4, 6) ^
    bit(byte5, 4) ^ bit(byte5, 7)
  ) << 1

  # Bit 2: XOR of 17 bits
  checksum |= (
    bit(byte0, 1) ^ bit(byte0, 2) ^ bit(byte0, 3) ^ bit(byte0, 4) ^
    bit(byte1, 0) ^ bit(byte1, 1) ^ bit(byte1, 2) ^ bit(byte1, 4) ^
    bit(byte3, 4) ^
    bit(byte4, 1) ^ bit(byte4, 3) ^ bit(byte4, 5) ^ bit(byte4, 6) ^
    bit(byte5, 1) ^ bit(byte5, 3) ^ bit(byte5, 5) ^ bit(byte5, 6)
  ) << 2

  # Bit 3: XOR of 19 bits
  checksum |= (
    bit(byte0, 0) ^ bit(byte0, 4) ^ bit(byte0, 7) ^
    bit(byte1, 1) ^ bit(byte1, 3) ^ bit(byte1, 5) ^
    bit(byte3, 4) ^ bit(byte3, 5) ^ bit(byte3, 6) ^
    bit(byte4, 0) ^ bit(byte4, 1) ^ bit(byte4, 2) ^ bit(byte4, 4) ^ bit(byte4, 5) ^
    bit(byte5, 1) ^ bit(byte5, 2) ^ bit(byte5, 3) ^ bit(byte5, 4) ^ bit(byte5, 7)
  ) << 3

  # Bit 4: XOR of 16 bits
  checksum |= (
    bit(byte0, 1) ^ bit(byte0, 2) ^ bit(byte0, 3) ^ bit(byte0, 7) ^
    bit(byte1, 4) ^ bit(byte1, 6) ^
    bit(byte3, 4) ^ bit(byte3, 5) ^ bit(byte3, 7) ^
    bit(byte4, 1) ^ bit(byte4, 2) ^ bit(byte4, 3) ^
    bit(byte5, 2) ^ bit(byte5, 4) ^ bit(byte5, 5) ^ bit(byte5, 6)
  ) << 4

  # Bit 5: XOR of 15 bits
  checksum |= (
    bit(byte0, 0) ^ bit(byte0, 2) ^ bit(byte0, 3) ^ bit(byte0, 4) ^
    bit(byte1, 5) ^ bit(byte1, 7) ^
    bit(byte3, 5) ^ bit(byte3, 6) ^
    bit(byte4, 2) ^ bit(byte4, 3) ^ bit(byte4, 4) ^
    bit(byte5, 3) ^ bit(byte5, 5) ^ bit(byte5, 6) ^ bit(byte5, 7)
  ) << 5

  # Bit 6: XOR of 19 bits
  checksum |= (
    bit(byte0, 0) ^ bit(byte0, 1) ^ bit(byte0, 3) ^ bit(byte0, 4) ^ bit(byte0, 5) ^ bit(byte0, 7) ^
    bit(byte1, 0) ^ bit(byte1, 6) ^
    bit(byte3, 4) ^ bit(byte3, 6) ^ bit(byte3, 7) ^
    bit(byte4, 0) ^ bit(byte4, 3) ^ bit(byte4, 4) ^ bit(byte4, 5) ^
    bit(byte5, 1) ^ bit(byte5, 4) ^ bit(byte5, 6) ^ bit(byte5, 7)
  ) << 6

  # Bit 7: XOR of 15 bits
  checksum |= (
    bit(byte0, 1) ^ bit(byte0, 2) ^ bit(byte0, 4) ^ bit(byte0, 5) ^
    bit(byte1, 1) ^ bit(byte1, 7) ^
    bit(byte3, 5) ^ bit(byte3, 7) ^
    bit(byte4, 0) ^ bit(byte4, 4) ^ bit(byte4, 5) ^ bit(byte4, 6) ^
    bit(byte5, 2) ^ bit(byte5, 5) ^ bit(byte5, 7)
  ) << 7

  return checksum


class LcaAuthorityEnvelope:
  """Replicates stock Pilot Assist's easy override and bounce-free release"""
  def __init__(self):
    self.reset()

  def reset(self):
    self.pos = 0.0
    self.neg = 0.0
    self._drv_prev = 0.0
    self._light_frames = 0
    self._real_off_frames = 1000
    self._override_active = False
    self._drv_mag_filt = 0.0

  def update(self, steering_torque: float) -> tuple[float, float]:
    """Advance one frame given driver torque (openpilot convention: > 0 is pushing right)."""
    P = CarControllerParams
    DT = 0.01  # 100 Hz
    drv_mag = abs(steering_torque)
    drv_rate = drv_mag - self._drv_prev
    self._drv_prev = drv_mag
    self._drv_mag_filt = ((1.0 - P.LCA_AUTH_YIELD_LP_ALPHA) * self._drv_mag_filt
                          + P.LCA_AUTH_YIELD_LP_ALPHA * drv_mag)

    if not self._override_active and drv_mag > P.LCA_AUTH_OVERRIDE_ENTER:
      self._override_active = True
    elif self._override_active and drv_mag < P.LCA_AUTH_OVERRIDE_EXIT:
      self._override_active = False

    real_override = self._override_active
    if real_override:
      self._real_off_frames = 0
    else:
      self._real_off_frames += 1

    if (drv_mag > P.LCA_AUTH_LIGHT_THRESH and
        drv_rate > P.LCA_AUTH_LIGHT_RISE_DELTA and
        self._light_frames == 0 and
        self._real_off_frames > P.LCA_AUTH_LIGHT_COOLDOWN_FRAMES):
      self._light_frames = P.LCA_AUTH_LIGHT_HOLD_FRAMES
    else:
      self._light_frames = max(0, self._light_frames - 1)
    light_collapse = self._light_frames > 0
    overriding = real_override or light_collapse

    collapse_rate = P.LCA_AUTH_COLLAPSE_RATE * max(1.0, drv_mag / float(P.LCA_AUTH_OVERRIDE_ENTER))
    step = collapse_rate * DT
    if overriding:
      if self.pos > P.LCA_AUTH_SPLIT or -self.neg > P.LCA_AUTH_SPLIT:
        # Symmetric collapse phase
        self.pos = max(float(P.LCA_AUTH_SPLIT), self.pos - step)
        self.neg = min(-float(P.LCA_AUTH_SPLIT), self.neg + step)
      else:
        # Asymmetric plateau phase
        excess = max(0.0, self._drv_mag_filt - float(P.LCA_AUTH_OVERRIDE_ENTER))
        yield_signed = float(P.LCA_AUTH_YIELD_BASE) - P.LCA_AUTH_YIELD_SLOPE * excess
        yield_signed = max(float(P.LCA_AUTH_YIELD_MIN), min(yield_signed, float(P.LCA_AUTH_YIELD_BASE)))
        if steering_torque > 0:  # driver pushing right
          target_pos = yield_signed                              # yield arm (+ side)
          target_neg = -float(P.LCA_AUTH_PLATEAU_COUNTER)        # counter arm
        else:  # driver pushing left (or zero — default to symmetric collapse direction)
          target_pos = float(P.LCA_AUTH_PLATEAU_COUNTER)         # counter arm
          target_neg = -yield_signed                             # yield arm (− side)
        # Drive each arm toward its plateau target at COLLAPSE_RATE
        self.pos = max(target_pos, self.pos - step) \
                   if self.pos > target_pos \
                   else min(target_pos, self.pos + step)
        self.neg = min(target_neg, self.neg + step) \
                   if self.neg < target_neg \
                   else max(target_neg, self.neg - step)
    else:
      # No override → rebuild both arms toward saturation
      rebuild_step = P.LCA_AUTH_REBUILD_RATE * DT
      self.pos = min(float(P.LCA_AUTH_MAX), self.pos + rebuild_step)
      self.neg = max(-float(P.LCA_AUTH_MAX), self.neg - rebuild_step)

    return self.pos, self.neg
