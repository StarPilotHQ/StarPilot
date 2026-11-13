"""Restore pre-rebase EV6 0x448 media controls without borrowing other models.

Legacy source: StarPilot c2e5e469, Hyundai carstate/get_can_parsers and
hyundai_canfd.dbc (MODE bit 22, CUSTOM bit 44, selected E-CAN).
"""
from types import SimpleNamespace
import unittest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.values import CAR, HyundaiFlags
from opendbc.car.hyundai.ioniq6_media import Ioniq6MediaButtons
from opendbc.car.hyundai.tests.test_ioniq6_media import packet
from openpilot.starpilot.car.hyundai.settings import configuration_media_supported, configuration_wheel_policy
from openpilot.starpilot.conditional_mode.manual import Button, ButtonTracker, Gesture, MEDIA_LONG_NS, Press, ioniq6_media_eligible
from opendbc.car.structs import car


def ev6(*, hda2, offset):
  fp = gen_empty_fingerprint()
  fp[offset + 2].update({0x50: 16, 0x2A4: 24} if hda2 else {0x12A: 16, 0x1A0: 32})
  fp[offset + int(hda2)].update({0x1CF: 8, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24})
  return CarInterface.get_params(CAR.KIA_EV6, fp, [], False, False, False)


class EV6MediaTests(unittest.TestCase):
  def test_hda1_hda2_and_offset_use_selected_bus_and_release_once(self):
    for hda2 in (False, True):
      for offset in (0, 4):
        with self.subTest(hda2=hda2, offset=offset):
          cp = ev6(hda2=hda2, offset=offset)
          self.assertTrue(ioniq6_media_eligible(cp))
          bus = CanBus(cp).ECAN
          self.assertEqual(bus, offset + int(hda2))
          receiver, tracker = Ioniq6MediaButtons(bus), ButtonTracker()
          state = car.CarState(canValid=True)
          def sample(stamp, custom=False, source_bus=bus, tracker=tracker, state=state, receiver=receiver):
            return tracker.observe(state, receiver.update([packet(stamp, custom=custom, bus=source_bus)]))
          self.assertEqual(sample(1_000_000_000), ())
          self.assertEqual(sample(1_100_000_000, True), ())
          self.assertEqual(sample(1_200_000_000), (Gesture(Button.CUSTOM, Press.SHORT),))
          self.assertEqual(sample(1_300_000_000), ())
          self.assertEqual(sample(1_400_000_000, True, (bus + 1) % 6), ())
          self.assertEqual(sample(1_500_000_000), ())
          self.assertTrue(configuration_media_supported(cp))
          # Media support must not copy Ioniq-specific AOL/cruise policy.
          self.assertIsNone(configuration_wheel_policy(cp))

  def test_ev6_hold_and_source_loss_preserve_gesture_guards(self):
    source, tracker = Ioniq6MediaButtons(0), ButtonTracker()
    state = car.CarState(canValid=True)
    def sample(stamp, custom=False):
      return tracker.observe(state, source.update([packet(stamp, custom=custom, bus=0)]))
    start = 1_000_000_000
    sample(start)
    sample(start + 100_000_000, True)
    gestures = []
    for elapsed in range(100_000_000, MEDIA_LONG_NS + 100_000_000, 100_000_000):
      gestures.extend(sample(start + 100_000_000 + elapsed, True))
    self.assertEqual(gestures, [Gesture(Button.CUSTOM, Press.LONG)])
    self.assertEqual(sample(start + MEDIA_LONG_NS + 200_000_000), ())
    # Missing source revokes held state, not a synthetic release command.
    sample(start + MEDIA_LONG_NS + 300_000_000, True)
    self.assertEqual(tracker.observe(state, source.update([])), ())
    self.assertEqual(sample(start + MEDIA_LONG_NS + 400_000_000), ())

  def test_other_models_and_malformed_topology_are_not_expanded(self):
    for model in (CAR.KIA_EV6_2025, CAR.KIA_EV9, CAR.HYUNDAI_IONIQ_5):
      cp = ev6(hda2=True, offset=0)
      cp.carFingerprint = model
      self.assertFalse(ioniq6_media_eligible(cp))
      self.assertFalse(configuration_media_supported(cp))
    for flag in (HyundaiFlags.CCNC, HyundaiFlags.CANFD_ANGLE_STEERING):
      cp = ev6(hda2=True, offset=0)
      cp.flags |= int(flag)
      self.assertFalse(ioniq6_media_eligible(cp))
    cp = ev6(hda2=True, offset=0)
    cp.safetyConfigs = []
    self.assertFalse(ioniq6_media_eligible(cp))

  def test_saved_ev6_media_requires_usable_vehicle(self):
    cp = SimpleNamespace(brand='hyundai', carFingerprint=CAR.KIA_EV6, notCar=False, passive=False, dashcamOnly=False)
    self.assertTrue(configuration_media_supported(cp))
    for key in ('notCar', 'passive', 'dashcamOnly'):
      setattr(cp, key, True)
      self.assertFalse(configuration_media_supported(cp))
      setattr(cp, key, False)
