import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.hyundai.carstate import CarState
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC


def state_for(model, topology, longitudinal=False):
  fingerprint = gen_empty_fingerprint()
  if topology == 'lka':
    fingerprint[2][0x50] = 16
  elif topology == 'lka_alt':
    fingerprint[2][0x110] = 32
  ecan = 0 if topology == 'camera' else 1
  fingerprint[ecan].update({0x1cf: 8, 0x1ba: 24})
  cp = CarInterface.get_params(model, fingerprint, [], False, False, False)
  cp.openpilotLongitudinalControl = longitudinal
  cp.pcmCruise = not longitudinal
  return CarState(cp), CANPacker(DBC[model][Bus.pt]), CanBus(cp).ECAN


def update(state, parsers, now, frames):
  for parser in parsers.values():
    parser.update((now, frames))
  state.out = state.update(parsers)


class TestCanfdBlindspotReserved(unittest.TestCase):
  def test_rear_indication_values_across_canfd_profiles(self):
    for model in (CAR.HYUNDAI_IONIQ_6, CAR.HYUNDAI_IONIQ_5, CAR.KIA_EV6):
      for topology in ('camera', 'lka', 'lka_alt'):
        for longitudinal in (False, True):
          state, packer, bus = state_for(model, topology, longitudinal)
          parsers = state.get_can_parsers(state.CP)
          for left in range(4):
            for right in range(4):
              with self.subTest(model=model, topology=topology, longitudinal=longitudinal, left=left, right=right):
                frame = packer.make_can_msg('ADAS_CMD_50_50ms', bus, {'BCW_LtIndSta': left, 'BCW_RtIndSta': right})
                update(state, parsers, 1_000_000_000 + (left * 4 + right) * 50_000_000, [frame])
                self.assertEqual(state.out.leftBlindspot, left in (1, 2))
                self.assertEqual(state.out.rightBlindspot, right in (1, 2))

  def test_reserved_rear_does_not_hide_physical_corner_detection(self):
    for topology in ('camera', 'lka', 'lka_alt'):
      state, packer, bus = state_for(CAR.HYUNDAI_IONIQ_6, topology)
      parsers = state.get_can_parsers(state.CP)
      for index, (corner, expected) in enumerate(((0x02, (False, False)), (0x12, (True, False)),
                                                (0x0a, (False, True)), (0x1a, (True, True)))):
        with self.subTest(topology=topology, corner=corner):
          frames = [packer.make_can_msg('ADAS_CMD_50_50ms', bus, {'BCW_LtIndSta': 3, 'BCW_RtIndSta': 3}),
                    packer.make_can_msg('BLINDSPOTS_FRONT_CORNER_2', bus, {'SIDE_DETECT_STATE': corner})]
          update(state, parsers, 1_000_000_000 + index * 50_000_000, frames)
          self.assertEqual((state.out.leftBlindspot, state.out.rightBlindspot), expected)

  def test_corrupt_and_stale_sources_do_not_extend_detection(self):
    state, packer, bus = state_for(CAR.HYUNDAI_IONIQ_6, 'lka_alt')
    parsers = state.get_can_parsers(state.CP)
    rear = packer.make_can_msg('ADAS_CMD_50_50ms', bus, {'BCW_LtIndSta': 1})
    update(state, parsers, 1_000_000_000, [rear])
    self.assertTrue(state.out.leftBlindspot)
    data = bytearray(rear[1])
    data[0] ^= 1
    update(state, parsers, 1_100_000_000, [(rear[0], bytes(data), rear[2])])
    update(state, parsers, 1_201_000_000, [packer.make_can_msg('ACCELERATOR', bus, {})])
    self.assertFalse(state.out.leftBlindspot)


if __name__ == '__main__':
  unittest.main()
