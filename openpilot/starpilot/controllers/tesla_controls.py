"""Optional, receive-only Model 3/Y inputs. Never participates in CAN validity."""
from dataclasses import dataclass

from openpilot.starpilot.common.car_control_config import STALK_HOLD, STALK_TAP, STALK_TAP_FULL

# Raw scroll count in one ~100 ms packet; retain the peak until neutral.
FAST_SCROLL_TICKS = 2
# Wheel tilts and presses, which can end with one stray scroll click as the wheel springs back.
NUDGES = frozenset(f'{side}_wheel_{action}' for side in ('left', 'right') for action in ('left', 'right', 'press'))
# Holding the right stalk down sets the Tesla's own set speed to its detected limit after about 1 s (measured 1.02 to
# 1.07 s into a hold); taps to engage last under 0.4 s. The hold fires earlier, so the comma's own change lands first
# and the Tesla's reply is not adopted as a driver change. A down press shorter than this, started once cruise had been
# on for ENGAGED_BEFORE_PRESS_SECONDS, is a tap while engaged.
HOLD_SECONDS = .6
# A down press counts as "while engaged" only if cruise had already been on for this long, in frame time, when the press
# started. The car engages on the same press that engages cruise, so cruise is already on by that press's first frames.
ENGAGED_BEFORE_PRESS_SECONDS = .5


@dataclass
class Gesture:
  armed: bool = False
  key: str | None = None
  family: str | None = None
  rank: int = 0

  def clear(self):
    self.armed, self.key, self.family, self.rank = False, None, None, 0

  def update(self, values, details=False):
    if values is None or len(values) > 1:
      self.clear()
      return []
    if not values:
      result = [(self.key, self.rank) if details else self.key] if self.armed and self.key else []
      self.clear()
      self.armed = True
      return result
    if not self.armed:
      return []
    key, family, rank = values[0]
    if self.family is not None and family != self.family:
      if (self.key in NUDGES and family in (f"{self.key.split('_')[0]}_wheel_up", f"{self.key.split('_')[0]}_wheel_down")
          and rank == 1):
        # A wheel tilted or pressed often clicks once as it springs back: that click ends the nudge (or press) and is
        # not a scroll. Emit the nudge; the next neutral frame arms the next gesture.
        result = [(self.key, self.rank) if details else self.key]
        self.clear()
        return result
      self.clear()
    elif rank >= self.rank:
      self.key, self.family, self.rank = key, family, rank
    return []


class ScreenGesture:
  """Asynchronous count-only touch input; emit once on complete release."""
  MAX_DURATION = 1.2

  def __init__(self):
    self.clear()

  def clear(self):
    self.armed = False
    self.started = None
    self.peak = 0
    self.last = None

  def feed(self, data, timestamp, details):
    if len(data) != 8 or (self.last is not None and timestamp < self.last):
      self.clear()
      return []
    self.last = timestamp
    count = data[3]
    if count == 0:
      emit = self.armed and self.peak == 3 and self.started is not None and 0 < timestamp - self.started <= self.MAX_DURATION
      self.clear()
      self.armed, self.last = True, timestamp
      return [('screen_three_finger_tap', False) if details else 'screen_three_finger_tap'] if emit else []
    if not self.armed:
      return []
    if self.started is None:
      self.started = timestamp
    self.peak = max(self.peak, count)
    if self.peak > 3 or timestamp - self.started > self.MAX_DURATION:
      self.armed = False
    return []


class TeslaInputDecoder:
  # >3x measured maximum intervals: mux1 .2031s, right .2032s, left .1008s.
  TIMEOUTS = {0x3c2: .75, 0x229: .75, 0x249: .35, 0x3df: None}

  def __init__(self, bus, *, stalk_hold=False, stalk_tap=False, stalk_tap_full=False):
    self.bus = bus
    # Only when assigned: a hold, or a half / full press while engaged, then replaces the down press it would end as.
    self.stalk_hold = stalk_hold
    self.stalk_tap = stalk_tap
    self.stalk_tap_full = stalk_tap_full
    # The worker sets this from carState before feeding each batch of frames.
    self.cruise_engaged = False
    self.reset()

  def reset(self):
    previous_screen = getattr(self, 'groups', {}).get(0x3df)
    released = bool(previous_screen and previous_screen.armed and previous_screen.started is None)
    self.groups = {address: Gesture() for address in self.TIMEOUTS}
    self.groups[0x3df] = ScreenGesture()
    # Keep a positively observed released baseline, never an in-progress touch.
    self.groups[0x3df].armed = released
    self.last = {}
    self.counters = {}
    self.states = {}
    self.lengths = {}
    self.down_start = None
    self.down_engaged = False
    # Frame time of the first frame seen with cruise on; None while cruise is off.
    self.engaged_since = None

  @staticmethod
  def _wheel(bits):
    values = []
    for name, shift in [('left_wheel_left',14),('left_wheel_right',3),('left_wheel_press',5),
                        ('right_wheel_left',8),('right_wheel_right',10),('right_wheel_press',12)]:
      value = (bits >> shift) & 3
      if value not in (1, 2):
        return None
      if value == 2:
        values.append((name, name, 1))
    for side, shift in [('left',16),('right',24)]:
      value = (bits >> shift) & 63
      value = value - 64 if value >= 32 else value
      if value:
        key = f'{side}_wheel_{"up" if value > 0 else "down"}'
        values.append((key, key, abs(value)))
    return values

  @staticmethod
  def _stalk(address, bits, length):
    values = []
    def add(value, mapping):
      if value == 0:
        return True
      entry = mapping.get(value)
      if entry is None:
        return False
      values.append(entry)
      return True
    if address == 0x229:
      good = add((bits >> 12) & 7, {
        1: ('right_stalk_up_half','right_stalk_up',1), 2: ('right_stalk_up_full','right_stalk_up',2),
        3: ('right_stalk_down_half','right_stalk_down',1), 4: ('right_stalk_down_full','right_stalk_down',2),
      }) and add((bits >> 16) & 3, {1: ('park_press','park_press',1)})
    else:
      # Four-byte variant adds intermediate positions; only stable detents emit.
      turn = (bits >> 16) & (15 if length == 4 else 7)
      mapping = {1: ('indicator_right_half','indicator_right',1), 2: ('indicator_right_full','indicator_right',2),
                 3: ('indicator_left_half','indicator_left',1), 4: ('indicator_left_full','indicator_left',2)}
      if length == 4:
        mapping = {2: ('indicator_right_half','indicator_right',1), 4: ('indicator_right_full','indicator_right',2),
                   6: ('indicator_left_half','indicator_left',1), 8: ('indicator_left_full','indicator_left',2),
                   1: (None,'indicator_right',0), 3: (None,'indicator_right',0),
                   5: (None,'indicator_left',0), 7: (None,'indicator_left',0)}
      good = (add(turn, mapping) and
              add((bits >> 12) & 3, {1: ('beam_pull','beam_pull',1), 2: ('beam_push','beam_push',1)}) and
              add((bits >> 14) & 3, {1: ('wiper_half','wiper',1), 2: ('wiper_full','wiper',2)}))
    return values if good else None

  def _stalk_down(self, group, values, timestamp, released):
    """The right stalk down from neutral: a hold (fires once while still held; the release then emits nothing) or a tap
    while engaged (a release before HOLD_SECONDS of a press that started with cruise already on for
    ENGAGED_BEFORE_PRESS_SECONDS). None keeps the plain press."""
    if group.armed and values and len(values) == 1 and values[0][1] == 'right_stalk_down':
      if self.down_start is None:
        self.down_start = timestamp
        self.down_engaged = (self.engaged_since is not None and
                             timestamp - self.engaged_since >= ENGAGED_BEFORE_PRESS_SECONDS)
      elif self.stalk_hold and timestamp - self.down_start >= HOLD_SECONDS:
        group.clear()
        self.down_start = None
        return STALK_HOLD
      return None
    start, engaged = self.down_start, self.down_engaged
    self.down_start, self.down_engaged = None, False
    if (engaged and start is not None and len(released) == 1 and timestamp - start < HOLD_SECONDS and
        released[0] in ('right_stalk_down_half', 'right_stalk_down_full')):
      if released[0] == 'right_stalk_down_half' and self.stalk_tap:
        return STALK_TAP
      if released[0] == 'right_stalk_down_full' and self.stalk_tap_full:
        return STALK_TAP_FULL
    return None

  def feed(self, address, data, bus, timestamp, *, details=False):
    if bus != self.bus or address not in self.groups:
      return []
    if not self.cruise_engaged:
      self.engaged_since = None
    elif self.engaged_since is None:
      self.engaged_since = timestamp
    group = self.groups[address]
    if address == 0x3df:
      return group.feed(data, timestamp, details)
    lengths = (8,) if address == 0x3c2 else (3,) if address == 0x229 else (3,4)
    if len(data) not in lengths:
      group.clear()
      return []
    bits = int.from_bytes(data, 'little')
    if address == 0x3c2 and bits & 3 != 1:
      if bits & 3 in (2,3):
        group.clear()
      return []
    previous = self.last.get(address)
    if previous is not None and timestamp < previous:
      group.clear()
      return []
    if previous is None or timestamp - previous > self.TIMEOUTS[address]:
      group.clear()
      self.counters.pop(address, None)
    self.last[address] = timestamp
    if self.lengths.get(address, len(data)) != len(data):
      group.clear()
      self.counters.pop(address, None)
    self.lengths[address] = len(data)
    if address != 0x3c2:
      counter = (bits >> 8) & 15
      previous_counter = self.counters.get(address)
      previous_state = self.states.get(address)
      self.states[address] = bits >> 12
      self.counters[address] = counter
      if previous_counter is not None:
        delta = (counter - previous_counter) % 16
        if delta == 0:
          # Captured Park event followed by its periodic release reuses the
          # counter. Permit that exact state-bit transition, never a stalk move.
          park_only_change = address == 0x229 and previous_state is not None and (previous_state ^ (bits >> 12)) == 16
          if not park_only_change:
            if previous_state != bits >> 12:
              group.clear()
            return []
        if delta not in (0, 1):
          group.clear()
    values = self._wheel(bits) if address == 0x3c2 else self._stalk(address, bits, len(data))
    result = group.update(values, details=details)
    if address == 0x229 and (self.stalk_hold or self.stalk_tap or self.stalk_tap_full):
      released = [item[0] if details else item for item in result]
      special = self._stalk_down(group, values, timestamp, released)
      if special:
        return [(special, False)] if details else [special]
    if details:
      return [(key, address == 0x3c2 and key.endswith(('_wheel_up', '_wheel_down')) and rank >= FAST_SCROLL_TICKS)
              for key, rank in result]
    return result
