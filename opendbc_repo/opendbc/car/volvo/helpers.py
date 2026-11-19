def checksum_lca_2_message(b0: int, b5: int) -> int:
  """
  Compute checksum for VCU1 CAN ID 0x69 from bytes b0 and b5.

  b0: first data byte (MSB) of the frame (usually 0x18 in your logs)
  b5: sixth data byte of the frame (what you called Byte5)

  Returns: checksum byte (0..255) that goes into byte index 6.
  """
  if b0 == 0 and b5 == 128: # Hotfix openpilot test (don't know where this alleged test message comes from)
    return 0

  # Masks per checksum bit (bit 0..7) for b0 and b5
  M0 = [0x08, 0x00, 0x00, 0x00, 0x08, 0x00, 0x00, 0x00]
  M5 = [0x83, 0x86, 0xCF, 0xCD, 0x09, 0x02, 0x44, 0x89]

  def parity8(x: int) -> int:
    # 1 if x has an odd number of bits set, else 0
    x ^= x >> 4
    x ^= x >> 2
    x ^= x >> 1
    return x & 1

  b0 &= 0xFF
  b5 &= 0xFF

  c = 0
  for bit in range(8):
    p = 0
    if M0[bit]:
      p ^= parity8(b0 & M0[bit])
    if M5[bit]:
      p ^= parity8(b5 & M5[bit])
    c |= (p << bit)

  return c & 0xFF

def checksum_2_0x69_message(b0: int, b1: int, b3: int = 0, b4: int = 0) -> int:
  """
  Compute checksum byte (b2) for CAN ID 0x69 (LCA_2 message).

  The checksum depends on bytes 0, 1, 3, and 4. During normal driving (BYTE_1_MSBS_3=0),
  bytes 3-4 (NEW_SIGNAL_2) are always 0, so only b0 and b1 matter. During stability
  control events (aquaplaning, etc.), BYTE_1_MSBS_3 becomes non-zero and bytes 3-4
  contain non-zero values that affect the checksum.

  Args:
      b0: Byte 0 (usually 0x18)
      b1: Byte 1 ([7:5] BYTE_1_MSBS_3 | [4] PILOT_ASSIST_ENGAGED | [3:0] COUNTER_1)
      b3: Byte 3 (NEW_SIGNAL_2 high byte, default 0)
      b4: Byte 4 (NEW_SIGNAL_2 low byte, default 0)

  Returns:
      Checksum byte (0-255) for position 2
  """
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
  """
  Computes checksum #1 (goes in byte[0]) for PSCM-related 0x17 message.
  Depends only on (byte[1], byte[2]).

  b1 = SIG1 counter (high nibble) | LCA_ENABLED_ECHO (low nibble)
  b2 = 0x80 | SIG1 counter replica (low nibble)

  Linear over GF(2), same shape as checksum_lca_2_message: each output bit is
  the parity of a fixed mask over b1 and b2. Solved from 137,965 logged PSCM
  frames (60 distinct (b1,b2) keys, leave-one-out cross-validated 60/60).

  This replaces a 45-entry lookup table that covered only LCA_ENABLED_ECHO in
  {0, 1, 4} and returned 0 on a miss. During an ESC intervention the rack
  reports ECHO=6, so openpilot transmitted 128 consecutive frames with an
  invalid checksum (0x00) before this fix.

  Note: bit 3 of b1 (LCA_ENABLED_ECHO >= 8) has never been observed on the bus,
  so its contribution is unconstrained by the data and is taken to be zero.
  """
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
  """
  Computes checksum #2 (goes in byte[3]) for PSCM-related 0x17 message.
  Depends only on byte[2].
  """

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


def pscm_status_validation_class(data):
  # Keep pending stock status in the same validation class as the native cache.
  checksum_valid = (data[0] == checksum_1_pscm_related_message(data[1], data[2]) and
                    data[3] == checksum_2_pscm_related_message(data[2]))
  quality_valid = ((data[1] & 8) == 0 and 0x80 <= data[2] <= 0x8E and
                   data[1] >> 4 == data[2] & 15)
  return data[2] >> 4, checksum_valid, quality_valid


class LCA3CounterSync:
  """
  Best-effort pattern synchronization for LCA_3 COUNTER_1.

  The counter follows a 20-element pattern that cycles based on transmission count.
  We track recent observed counter values and match them against the pattern to
  determine the current index. While not synchronized, we pass through stock values.
  Once synchronized, we permanently use the pattern.
  """

  PATTERN = [2, 2, 1, 2, 2, 2, 1, 2, 2, 3, 0, 2, 3, 2, 0, 2, 3, 2, 0, 3]
  PATTERN_LEN = 20
  WINDOW_SIZE = 5  # Track last 5 values for matching
  MIN_CONFIDENCE = 4  # Need 4 consecutive matches to sync

  def __init__(self):
    self.pattern_index = None  # Current index in pattern (None = not synced)
    self.observed_window = []  # Circular buffer of last N observed values
    self.confidence = 0  # Number of consecutive successful matches

  def update(self, observed_counter: int) -> tuple:
    """
    Update with newly observed counter value from stock message.

    Args:
        observed_counter: Counter value from CS.msg_lca_3['COUNTER_1']

    Returns:
        Tuple of (counter_to_send, is_synchronized)
    """
    # If already synchronized, ignore stock and use our pattern permanently
    if self.pattern_index is not None:
      counter_to_send = self.PATTERN[self.pattern_index]
      self.pattern_index = (self.pattern_index + 1) % self.PATTERN_LEN
      return counter_to_send, True

    # Not synchronized yet - try to find pattern index
    self.observed_window.append(observed_counter)
    if len(self.observed_window) > self.WINDOW_SIZE:
      self.observed_window.pop(0)

    # Attempt to sync if we have enough samples
    if len(self.observed_window) >= 3:
      self._attempt_sync()

    # While not synced, pass through stock counter
    return observed_counter, False

  def _attempt_sync(self):
    """Try to find current pattern index based on observed window."""
    # Try to match observation window against all positions in pattern
    best_match_idx = None
    best_match_len = 0

    for start_idx in range(self.PATTERN_LEN):
      match_len = self._count_match(start_idx)
      if match_len > best_match_len:
        best_match_len = match_len
        best_match_idx = start_idx

    # Require MIN_CONFIDENCE matching values to declare sync
    if best_match_len >= self.MIN_CONFIDENCE:
      # The match tells us where we WERE in the pattern
      # We need to set index to NEXT position for next transmission
      self.pattern_index = (best_match_idx + len(self.observed_window)) % self.PATTERN_LEN
      self.confidence = best_match_len

  def _count_match(self, pattern_start_idx: int) -> int:
    """
    Count how many values in observed_window match pattern starting at pattern_start_idx.

    Returns:
        Number of consecutive matching values from start
    """
    match_count = 0
    for i, observed in enumerate(self.observed_window):
      pattern_idx = (pattern_start_idx + i) % self.PATTERN_LEN
      if observed == self.PATTERN[pattern_idx]:
        match_count += 1
      else:
        break  # Stop at first mismatch
    return match_count

  def is_synchronized(self) -> bool:
    """Returns True if we have synchronized to the pattern."""
    return self.pattern_index is not None

def checksum_lca_5_message(byte0: int, byte1: int, byte3: int, byte4: int, byte5: int) -> int:
  """
  Calculate checksum for LCA_5 message 0x67 (byte 2)

  Args:
    byte0: Byte 0 (0-255)
    byte1: Byte 1 (0-255)
    byte3: Byte 3 (0-255)
    byte4: Byte 4 (0-255)
    byte5: Byte 5 (0-255)

  Returns:
    int: Checksum value (0-255) for byte 2

  Example:
    >>> checksum = checksum_lca_5_message(0x80, 0x00, 0x4F, 0x00, 0x00)
    >>> print(f"0x{checksum:02X}")
    0x32
  """

  # Helper function to extract a bit (LSB = bit 0)
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


# Test examples
