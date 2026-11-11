"""GEN1 Torque Interceptor (TI1) behaviour the car controller and car state work around.

Ported from ladelisandwich/openpilot `mazda-long-ti1-testing` (CX-9 2023 with a TI1), where it was measured
on the road. None of this applies to the GEN2/GEN3 TI2, which has its own fault model (values.TI2_HEALTH).

Violation codes: TI_FEEDBACK VIOL carries the TI's green-LED code as two hex digits (MoreTorque, "TI General
Documentation", Part 2). Class 2 and 3 violations clear by themselves about 30 s after the condition is gone
(RUN returns 2 s later); class 1 needs a power cycle. At power-up the TI reports 0x12 (CAN_TMO) for one frame.

Ceiling: the TI faults with 0x11 LKAS_STUK when a nonzero command is held unchanged (the documentation: "LKAS
request input does not change when not 0 or +/-600"). On the CX-9 an 800 command held for 1.84-1.99 s tripped
it 22 times out of 22, and -1 held for 2 s at a creep tripped it once. Above about 650 counts the EPS torque
sensor stops answering, so the command is clamped at CarControllerParams.TI_CEILING (700), and TiCeilingGuard
briefly lowers any command that is held near the ceiling, or held unchanged, before the TI's timer runs out.
"""

# VIOL byte -> (mnemonic, class, what trips it)
VIOLATIONS: dict[int, tuple[str, int, str]] = {
  0x11: ("LKAS_STUK", 2, "the LKAS request did not change while not 0 or +/-600"),
  0x12: ("CAN_TMO", 3, "CAN bus off-line for more than 100 ms"),
  0x13: ("LKAS_ERR", 2, "LKAS torque request out of the window"),
  0x14: ("LOW_VOLT", 3, "CPU voltage too low"),
  0x15: ("TMR_ERR", 1, "the periodic timer interrupt was not detected"),
  0x16: ("MAIN_ERR", 1, "main loop not running within its time limits"),
  0x17: ("TORQUE_H", 2, "computed torque output above the high limit window"),
  0x18: ("TORQUE_L", 2, "computed torque output below the low limit window"),
  0x19: ("WDT_ERR", 1, "internal watchdog triggered"),
  0x21: ("CPU_REG", 1, "CPU pin or peripheral configuration error"),
  0x22: ("HIGH_TMP", 3, "CPU die above 65 C"),
  0x23: ("DATA_MEM", 1, "EEPROM memory CRC error"),
  0x25: ("TK_TRNSF", 2, "calculated torque output differs from the measured output beyond the window"),
  0x26: ("ADC_STUK", 1, "ADC channel failed"),
  0x31: ("HIN_ERR", 2, "EPS hall sensor input out of the window (normal while the car powers up or down)"),
  0x32: ("TK_NLOAD", 2, "feedback torque output out of the window under no load"),
  0x33: ("TK_WLOAD", 2, "feedback torque output out of the window under load"),
  0x34: ("CAN_ERR", 3, "a CAN bus error other than CAN_TMO"),
  0x35: ("STCK_ERR", 1, "stack over- or underflow"),
  0x36: ("PANIC_MON", 1, "the panic monitor output is high"),
  0x37: ("PANIC", 1, "panic test injection"),
  0x41: ("LOGIC", 1, "code logic error (see the ERROR byte)"),
  0x42: ("DBG_STOP", 1, "debug console switched the TI off"),
  0x43: ("CLK_STOP", 1, "CPU main oscillator stopped"),
}

# Class 1 violations stay until the TI is power cycled.
PERMANENT_CLASS = 1

# Panda refuses nonzero 0x249 once TI_FEEDBACK is 200 ms old (MAZDA_TI_FEEDBACK_TIMEOUT_US). The host judges
# freshness when it builds the frame, which panda checks a little later, so it stops sooner.
TI1_FEEDBACK_FRESH_NS = 150_000_000


def violation_name(code: int) -> str:
  """'0x33 TK_WLOAD (class 2)' for a known code, '0x5a (unknown)' otherwise, '' for none."""
  if not code:
    return ""
  name = VIOLATIONS.get(code)
  return f"0x{code:02x} {name[0]} (class {name[1]})" if name else f"0x{code:02x} (unknown)"


def violation_is_permanent(code: int) -> bool:
  """True only for a known class 1 code. Unknown codes are treated as recoverable: lateral is withheld
  either way (car state and panda both require VIOL == 0), and only the alert differs."""
  entry = VIOLATIONS.get(code)
  return entry is not None and entry[1] == PERMANENT_CLASS


def ti1_follow(stock: int, last: int, rate_up: int) -> int:
  """The TI1 command with the camera present: the stock command's sign, at most its magnitude, and at most
  `rate_up` above the last command the TI was sent in that direction (from zero after a reversal).
  Mirrors the 0x249 rule in safety/modes/mazda.h."""
  if stock == 0:
    return 0
  base = abs(last) if last != 0 and (last > 0) == (stock > 0) else 0
  magnitude = min(abs(stock), base + rate_up)
  return magnitude if stock > 0 else -magnitude


class TiCeilingGuard:
  def __init__(self, dip: int, hold_frames: int, dip_frames: int, step: int = 20):
    self.dip = dip                    # magnitude of a dipped command, and the level above which the count runs
    self.hold_frames = hold_frames    # frames above `dip`, or of one unchanged nonzero command, before a dip
    self.dip_frames = dip_frames
    self.step = step                  # how far an unchanged command is lowered toward zero
    self.held = 0                     # consecutive frames above `dip`
    self.same = 0                     # consecutive frames of one unchanged nonzero command
    self.prev = 0
    self.dipping = 0                  # frames left in the current dip
    self.cap = dip                    # the magnitude the current dip allows

  def update(self, torque: int) -> int | None:
    """Fed every frame with the command about to go to the TI (0 while it isn't steering).
    Returns the magnitude the TI command may have this frame, or None for no limit."""
    if self.dipping == 0:
      self.held = self.held + 1 if abs(torque) > self.dip else 0
      self.same = self.same + 1 if torque != 0 and torque == self.prev else 0
      if self.held >= self.hold_frames:
        self.dipping, self.cap = self.dip_frames, self.dip
      elif self.same >= self.hold_frames:
        self.dipping, self.cap = self.dip_frames, max(abs(torque) - self.step, 0)
    self.prev = torque
    if self.dipping > 0:
      self.dipping -= 1
      self.held = self.same = 0
      return self.cap
    return None
