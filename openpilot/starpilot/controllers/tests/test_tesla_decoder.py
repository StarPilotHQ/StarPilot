"""Tesla Model 3/Y wheel and stalk frames decoded into controls: one event per gesture, on release."""
import unittest

from openpilot.starpilot.common.car_control_config import INPUTS
from openpilot.starpilot.controllers.tesla_controls import TeslaInputDecoder


def wheel(**values):
  bits = 1
  shifts = {'left_wheel_left': 14, 'left_wheel_right': 3, 'left_wheel_press': 5,
            'right_wheel_left': 8, 'right_wheel_right': 10, 'right_wheel_press': 12}
  for name, shift in shifts.items():
    bits |= values.get(name, 1) << shift
  for name, shift in [('left_scroll', 16), ('right_scroll', 24)]:
    bits |= (values.get(name, 0) & 63) << shift
  return bits.to_bytes(8, 'little')


class DecoderTests(unittest.TestCase):
  def setUp(self):
    self.d = TeslaInputDecoder(bus=1)
    self.t = 1.0

  def feed(self, data=None, address=0x3c2, bus=1, dt=.1):
    self.t += dt
    return self.d.feed(address, data or wheel(), bus, self.t)

  def test_defaults_and_all_wheel_entries(self):
    # The right wheel's two nudges, their holds and its scroll: the only wheel controls StarPilot names.
    self.assertEqual(sorted(k for k in INPUTS if '_wheel_' in k),
                     ['right_wheel_down', 'right_wheel_left', 'right_wheel_left_hold', 'right_wheel_right',
                      'right_wheel_right_hold', 'right_wheel_up'])
    self.assertFalse([k for k in INPUTS if k.startswith(('left_', 'indicator_', 'beam_', 'wiper_'))])
    self.assertEqual(self.feed(wheel(right_wheel_right=2)), [])
    self.assertEqual(self.feed(), [])
    self.assertEqual(self.feed(wheel(right_wheel_right=2)), [])
    self.assertEqual(self.feed(wheel(right_wheel_right=2)), [])
    self.assertEqual(self.feed(), ['right_wheel_right'])
    self.assertEqual(self.feed(), [])

  def test_scroll_signed_magnitude_and_direction_change(self):
    self.feed()
    self.feed(wheel(right_scroll=-2))
    self.assertEqual(self.feed(), ['right_wheel_down'])
    self.feed(wheel(right_scroll=1))
    self.feed(wheel(right_scroll=-1))
    self.assertEqual(self.feed(), [])
    self.feed(wheel(left_scroll=1))
    self.assertEqual(self.feed(), ['left_wheel_up'])

  def test_mux_bus_length_fault_and_simultaneous(self):
    self.feed()
    for data, bus in [(wheel(right_wheel_right=2), 0), (bytes(8), 1), (bytes(7), 1)]:
      self.assertEqual(self.feed(data, bus=bus), [])
    self.feed()
    self.feed(wheel(right_wheel_press=3))
    self.feed(wheel(right_wheel_right=2))
    self.assertEqual(self.feed(), [])
    self.feed(wheel(left_wheel_left=2, right_wheel_right=2))
    self.assertEqual(self.feed(), [])

  def test_gap_reset_and_explicit_reset(self):
    self.feed()
    self.feed(wheel(right_wheel_right=2))
    self.assertEqual(self.feed(dt=1), [])
    self.feed(wheel(right_wheel_right=2))
    self.d.reset()
    self.assertEqual(self.feed(), [])

  def test_invalid_mux_discards_pending_but_body_mux_does_not(self):
    for mux in (2, 3):
      self.feed()
      self.feed(wheel(right_wheel_press=2))
      self.feed(bytes([mux]) + bytes(7))
      self.assertEqual(self.feed(), [])
    self.feed(wheel(right_wheel_press=2))
    self.feed(bytes(8))
    self.assertEqual(self.feed(), ['right_wheel_press'])

  def stalk(self, value, counter, address=0x229, length=3, shift=12):
    return self.feed(((counter << 8) | (value << shift)).to_bytes(length, 'little'), address)

  def engage(self, counter):
    """Cruise comes on, then the stalk rests in neutral for 0.6 s. The frames repeat `counter` (the last one sent), so
    the counter sequence of the test carries on unchanged."""
    self.d.cruise_engaged = True
    for _ in range(6):
      self.assertEqual(self.stalk(0, counter), [])

  def test_right_stalk_stage_once_and_counter_gap(self):
    self.stalk(0, 0)
    self.stalk(1, 1)
    self.stalk(2, 2)
    self.assertEqual(self.stalk(0, 3), ['right_stalk_up_full'])
    self.stalk(3, 4)
    self.stalk(4, 6)
    self.assertEqual(self.stalk(0, 7), [])

  def test_right_stalk_hold_replaces_the_down_press(self):
    self.d = TeslaInputDecoder(bus=1, stalk_hold=True)
    self.stalk(0, 0)
    held = [name for counter in range(1, 10) for name in self.stalk(3, counter)]
    self.assertEqual(held, ['right_stalk_down_hold'], 'once, while still held (0.8 s)')
    self.assertEqual(self.stalk(0, 10), [], 'the release is not also a down press')
    self.stalk(3, 11)
    self.assertEqual(self.stalk(0, 12), ['right_stalk_down_half'], 'a tap is still a press')
    self.stalk(3, 13)
    self.stalk(4, 14)
    self.assertEqual(self.stalk(0, 15), ['right_stalk_down_full'])

  def test_right_stalk_hold_needs_neutral_first_and_an_assignment(self):
    self.d = TeslaInputDecoder(bus=1, stalk_hold=True)
    self.assertEqual([n for c in range(10) for n in self.stalk(3, c)], [], 'already down when the decoder started')
    self.d = TeslaInputDecoder(bus=1)
    self.stalk(0, 0)
    self.assertEqual([n for c in range(1, 12) for n in self.stalk(3, c)], [])
    self.assertEqual(self.stalk(0, 12), ['right_stalk_down_half'], 'without a hold assignment a long press stays a press')

  def test_right_stalk_tap_while_engaged(self):
    self.d = TeslaInputDecoder(bus=1, stalk_tap=True)
    self.stalk(0, 0)
    self.stalk(4, 1)
    self.assertEqual(self.stalk(0, 2), ['right_stalk_down_full'], 'the press that engages cruise stays a press')
    self.engage(2)
    self.stalk(3, 3)
    self.stalk(3, 4)
    self.assertEqual(self.stalk(0, 5), ['right_stalk_down_tap_engaged'])
    self.stalk(3, 6)
    self.stalk(4, 7)
    self.assertEqual(self.stalk(0, 8), ['right_stalk_down_full'], 'a full press while engaged is its own input')
    for counter in range(9, 18):
      self.stalk(3, counter % 16)
    self.assertEqual(self.stalk(0, 2), ['right_stalk_down_half'], 'a long press is not a tap')
    self.stalk(1, 3)
    self.assertEqual(self.stalk(0, 4), ['right_stalk_up_half'], 'up is untouched')

  def test_full_press_while_engaged(self):
    self.d = TeslaInputDecoder(bus=1, stalk_tap=True, stalk_tap_full=True)
    self.engage(0)
    self.stalk(0, 0)
    self.stalk(3, 1)
    self.stalk(4, 2)
    self.assertEqual(self.stalk(0, 3), ['right_stalk_down_full_engaged'])
    self.stalk(3, 4)
    self.assertEqual(self.stalk(0, 5), ['right_stalk_down_tap_engaged'])
    self.d = TeslaInputDecoder(bus=1, stalk_tap_full=True)
    self.engage(0)
    self.stalk(0, 0)
    self.stalk(3, 1)
    self.assertEqual(self.stalk(0, 2), ['right_stalk_down_half'], 'half press unassigned: a plain press')

  def test_tap_and_hold_together(self):
    self.d = TeslaInputDecoder(bus=1, stalk_hold=True, stalk_tap=True)
    self.engage(0)
    self.stalk(0, 0)
    self.assertEqual([n for c in range(1, 10) for n in self.stalk(3, c)], ['right_stalk_down_hold'])
    self.assertEqual(self.stalk(0, 10), [], 'a hold is not also a tap')
    self.stalk(3, 11)
    self.assertEqual(self.stalk(0, 12), ['right_stalk_down_tap_engaged'])
    self.d = TeslaInputDecoder(bus=1, stalk_hold=True)
    self.engage(0)
    self.stalk(0, 0)
    self.stalk(3, 1)
    self.assertEqual(self.stalk(0, 2), ['right_stalk_down_half'], 'tap unassigned: a plain press')

  def test_engaging_press_is_not_a_press_while_engaged(self):
    # The car turns cruise on during the press that engages it, so the press's first frames already see cruise on.
    for value in (3, 4):
      self.d = TeslaInputDecoder(bus=1, stalk_tap=True, stalk_tap_full=True)
      self.stalk(0, 0)
      self.d.cruise_engaged = True
      self.stalk(3, 1)
      self.stalk(value, 2)
      self.assertEqual(self.stalk(0, 3), ['right_stalk_down_half' if value == 3 else 'right_stalk_down_full'])
    # Cruise came on a moment before the press started (under 0.5 s): still the engaging press.
    self.d = TeslaInputDecoder(bus=1, stalk_tap=True)
    self.stalk(0, 0)
    self.d.cruise_engaged = True
    self.stalk(0, 1)
    self.stalk(0, 2)
    self.stalk(3, 3)
    self.assertEqual(self.stalk(0, 4), ['right_stalk_down_half'])

  def test_tap_one_second_after_engaging(self):
    self.d = TeslaInputDecoder(bus=1, stalk_tap=True)
    self.stalk(0, 0)
    self.d.cruise_engaged = True
    for c in range(1, 11):
      self.stalk(0, c)
    self.stalk(3, 11)
    self.assertEqual(self.stalk(0, 12), ['right_stalk_down_tap_engaged'])

  def test_press_held_across_engagement_never_counts_as_engaged(self):
    for hold in (False, True):
      self.d = TeslaInputDecoder(bus=1, stalk_tap=True, stalk_tap_full=True, stalk_hold=hold)
      self.stalk(0, 0)
      self.stalk(3, 1)
      self.d.cruise_engaged = True
      out = [n for c in range(2, 12) for n in self.stalk(3 if c < 6 else 4, c)] + self.stalk(0, 12)
      self.assertEqual(out, ['right_stalk_down_hold'] if hold else ['right_stalk_down_full'])
    # A short press that started before cruise came on stays a plain press too.
    self.d = TeslaInputDecoder(bus=1, stalk_tap=True)
    self.stalk(0, 0)
    self.stalk(3, 1)
    self.d.cruise_engaged = True
    for c in range(2, 4):
      self.stalk(3, c)
    self.assertEqual(self.stalk(0, 4), ['right_stalk_down_half'])

  def test_cruise_off_restarts_the_engaged_time(self):
    self.d = TeslaInputDecoder(bus=1, stalk_tap=True)
    self.engage(0)
    self.d.cruise_engaged = False
    self.stalk(0, 1)
    self.d.cruise_engaged = True
    self.stalk(3, 2)
    self.assertEqual(self.stalk(0, 3), ['right_stalk_down_half'], 'cruise only just came back on')

  def test_left_stalk_is_not_decoded(self):
    # Indicators, beams and wipers (0x249) are the car's own: the decoder holds no state for them.
    self.assertEqual(sorted(self.d.groups), [0x229, 0x3c2, 0x3df])
    for length, half, full in [(3, 3, 4), (4, 6, 8)]:
      out = [self.stalk(value, counter, 0x249, length, 16) for counter, value in enumerate((0, half, full, 0))]
      self.assertEqual(out, [[], [], [], []])
    self.assertNotIn(0x249, self.d.last)

  def test_the_stalk_reader_takes_the_frame_bits_alone(self):
    read = TeslaInputDecoder._stalk
    self.assertEqual(read(0), [])
    self.assertEqual([read(value << 12) for value in (1, 2, 3, 4)],
                     [[('right_stalk_up_half', 'right_stalk_up', 1)], [('right_stalk_up_full', 'right_stalk_up', 2)],
                      [('right_stalk_down_half', 'right_stalk_down', 1)], [('right_stalk_down_full', 'right_stalk_down', 2)]])
    self.assertEqual(read(1 << 16), [('park_press', 'park_press', 1)])
    self.assertEqual(read((3 << 12) | (1 << 16)), [('right_stalk_down_half', 'right_stalk_down', 1), ('park_press', 'park_press', 1)])
    for unreadable in (5 << 12, 7 << 12, 2 << 16, 3 << 16):
      self.assertIsNone(read(unreadable))

  def test_right_stalk_frames_of_another_length_are_dropped(self):
    self.stalk(0, 0)
    self.stalk(1, 1)
    self.assertEqual(self.stalk(0, 2, length=4), [])
    self.assertEqual(self.stalk(0, 3), [], 'the press before the odd frame is forgotten')

  def test_captured_park_event_reuses_periodic_counter(self):
    self.feed(bytes.fromhex('8c0b00'), 0x229)
    self.feed(bytes.fromhex('490c00'), 0x229)
    self.feed(bytes.fromhex('c60d01'), 0x229, dt=0)
    self.assertEqual(self.feed(bytes.fromhex('2f0d00'), 0x229), ['park_press'])

  def test_every_wheel_button_and_captured_frame(self):
    # Every wheel switch is still read, so that doing two things at once can be told apart; only the right wheel's
    # nudges are controls.
    names = ['left_wheel_left', 'left_wheel_right', 'left_wheel_press', 'right_wheel_left', 'right_wheel_right', 'right_wheel_press']
    self.assertEqual([n for n in names if n in INPUTS], ['right_wheel_left', 'right_wheel_right'])
    for name in names:
      self.feed()
      self.feed(wheel(**{name: 2}))
      self.assertEqual(self.feed(), [name])
    self.feed(bytes.fromhex('2959000000000040'))
    self.assertEqual(self.feed(bytes.fromhex('2955000000000040')), ['right_wheel_right'])

  def test_nudge_release_click_ends_the_nudge(self):
    # A right-wheel nudge left can spring back with one scroll-up click in the release frame. The nudge's own action
    # runs; the click is not a scroll.
    self.feed()
    self.feed(wheel(right_wheel_left=2))
    self.feed(wheel(right_wheel_left=2))
    self.assertEqual(self.feed(wheel(right_scroll=1)), ['right_wheel_left'])
    self.assertEqual(self.feed(), [])
    self.feed(wheel(right_scroll=1))
    self.assertEqual(self.feed(), ['right_wheel_up'], 'the next gesture works as normal')
    self.feed()
    self.feed(wheel(right_wheel_press=2))
    self.assertEqual(self.feed(wheel(right_scroll=-1)), ['right_wheel_press'])

  def test_nudge_followed_by_a_real_scroll_is_still_dropped(self):
    self.feed()
    self.feed(wheel(right_wheel_left=2))
    self.assertEqual(self.feed(wheel(right_scroll=2)), [], 'two clicks is a scroll, not a spring-back')
    self.assertEqual(self.feed(), [])
    self.feed(wheel(right_wheel_left=2))
    self.assertEqual(self.feed(wheel(left_scroll=1)), [], "the other wheel's click does not end this nudge")
    self.assertEqual(self.feed(), [])

  def test_fast_scroll_is_two_or_more_clicks_in_one_frame_or_at_its_peak(self):
    for frames, fast in (([1], False), ([2], True), ([1, 1], False), ([1, 3, 1], True), ([-1], False), ([-4, -1], True)):
      self.d = TeslaInputDecoder(bus=1)
      self.t += 5
      self.d.feed(0x3c2, wheel(), 1, self.t, details=True)
      for ticks in frames:
        self.t += .1
        self.assertEqual(self.d.feed(0x3c2, wheel(right_scroll=ticks), 1, self.t, details=True), [])
      self.t += .1
      name = 'right_wheel_up' if frames[0] > 0 else 'right_wheel_down'
      self.assertEqual(self.d.feed(0x3c2, wheel(), 1, self.t, details=True), [(name, fast)], frames)
