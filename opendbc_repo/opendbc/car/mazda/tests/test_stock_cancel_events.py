import unittest

from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.mazda.interface import CarInterface
from opendbc.car.mazda.values import CAR


class TestStockCancelEvents(unittest.TestCase):
  def test_actual_parser_cancel_is_scoped_to_marked_profile(self):
    for identity in (CAR.MAZDA_CX5, CAR.MAZDA_CX5_2022, CAR.MAZDA_CX9_2021):
      for experience in (0, 32):
        with self.subTest(identity=identity, experience=experience):
          cp = CarInterface.get_params(identity, gen_empty_fingerprint(), [], False, False, False)
          cp.alternativeExperience = experience
          ci = CarInterface(cp)
          packer = CANPacker('mazda_2017')
          events = []
          for tick, pressed in enumerate((0, 1, 0), 1):
            frame = packer.make_can_msg('CRZ_BTNS', 0, {'CAN_OFF': pressed})
            state = ci.update([(tick * 10_000_000, [frame])])
            events.extend(bool(event.pressed) for event in state.buttonEvents
                          if event.type == structs.CarState.ButtonEvent.Type.cancel)
          self.assertEqual(events, [True, False] if experience == 32 else [])
