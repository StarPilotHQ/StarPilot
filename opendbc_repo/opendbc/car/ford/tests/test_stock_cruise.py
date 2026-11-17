"""Original switch context, state event and reached controller regressions."""
import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs, gen_empty_fingerprint
from opendbc.car.ford.interface import CarInterface
from opendbc.car.ford.stock_cruise import FordStockCruiseButton, qualified
from opendbc.car.ford.carstate import CarState
from opendbc.car.ford.carcontroller import CarController
from opendbc.car.ford.values import CAR, DBC
from opendbc.car.ford.tests.test_three_ports import params


def test_original_press_context_is_held_until_release_and_master_off_denies():
  owner = FordStockCruiseButton()
  assert owner.update(True, True, True) == (True, False)
  assert owner.update(True, True, False) == (True, False)
  assert owner.update(False, True, False) == (False, False)
  assert owner.update(True, True, False) == (False, True)
  assert owner.update(True, True, True) == (False, True)
  assert owner.update(False, True, True) == (False, False)
  assert owner.update(True, False, False) == (False, False)
  assert owner.update(True, True, True) == (False, False)


def test_actual_parser_cancel_edges_are_op_long_only_and_contextual():
  for op_long in (False, True):
    cp = params(CAR.FORD_MUSTANG_MACH_E_MK1, alpha=op_long)
    state = CarState(cp)
    parsers = state.get_can_parsers(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    for i, (pressed, enabled, expected) in enumerate(((0, True, []), (1, True, [True]),
                                                    (1, False, []), (0, False, [False]),
                                                    (1, False, []), (1, True, []))):
      rx = [packer.make_can_msg('Steering_Data_FD1', 0, {'CcAslButtnCnclResPress': pressed}),
            packer.make_can_msg('EngBrakeData', 0, {'CcStat_D_Actl': 5 if enabled else 3}),
            packer.make_can_msg('Cluster_Info1_FD1', 0, {'AccEnbl_B_RqDrv': 1})]
      parsers[Bus.pt].update([(1_000_000_000+i*10_000_000, rx)])
      out = state.update(parsers)
      cancel = [event.pressed for event in out.buttonEvents if event.type == structs.CarState.ButtonEvent.Type.cancel]
      assert cancel == (expected if op_long else [])


@pytest.mark.parametrize('vehicle', [CAR.FORD_ESCAPE_MK4, CAR.FORD_MUSTANG_MACH_E_MK1])
@pytest.mark.parametrize('op_long', [False, True])
@pytest.mark.parametrize('signal,button', [
  ('CcAslButtnSetIncPress', 'accelCruise'), ('CcAslButtnResIncPress', 'accelCruise'),
  ('CcAslButtnIndxIncPress', 'accelCruise'), ('CcAslButtnSetDecPress', 'decelCruise'),
  ('CcAslButtnResDecPress', 'decelCruise'), ('CcAslButtnIndxDecPress', 'decelCruise'),
])
def test_directional_cruise_edges_reach_software_cruise_only_with_op_long(vehicle, op_long, signal, button):
  cp = params(vehicle, alpha=op_long)
  state = CarState(cp)
  parsers = state.get_can_parsers(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
  for i, (pressed, expected) in enumerate(((0, []), (1, [True]), (1, []), (0, [False]))):
    parsers[Bus.pt].update([(1_000_000_000 + i * 10_000_000, [
      packer.make_can_msg('Steering_Data_FD1', parsers[Bus.pt].bus, {signal: pressed}),
    ])])
    out = state.update(parsers)
    edges = [event.pressed for event in out.buttonEvents if event.type == getattr(structs.CarState.ButtonEvent.Type, button)]
    assert edges == (expected if op_long else [])


def test_reached_controller_preserves_precedence_cadence_and_stock_bits():
  cp = params(CAR.FORD_MUSTANG_MACH_E_MK1, alpha=False)
  controller = CarController(DBC[cp.carFingerprint], cp)
  state = CarState(cp)
  state.update(state.get_can_parsers(cp))
  out = structs.CarState(vEgoRaw=10.)
  out.cruiseState.available = True
  state.out = out.as_reader()
  state.buttons_stock_values['CcAslButtnCnclResPress'] = 1
  state.buttons_stock_values['TurnLghtSwtch_D_Stat'] = 2
  state.acc_tja_status_stock_values['Tja_D_Stat'] = 0
  command = structs.CarControl()
  for frame in range(11):
    controller.frame = frame
    _, packets = controller.update(command.as_reader(), state, frame*10_000_000)
    buttons = [(bus, data) for address, data, bus in packets if address == 0x83]
    assert len(buttons) == (2 if frame % 5 == 0 else 0)
    if buttons:
      assert [bus for bus, _ in buttons] == [controller.CAN.camera, controller.CAN.main]
      assert all(data[3] & 2 for _, data in buttons)
  command.cruiseControl.cancel = True
  controller.frame = 11
  _, packets = controller.update(command.as_reader(), state, 110_000_000)
  buttons = [data for address, data, _ in packets if address == 0x83]
  assert len(buttons) == 2 and all(data[1] & 1 for data in buttons)
  assert not any(data[3] & 2 for data in buttons)


def test_unknown_ae_longitudinal_and_unpaired_profiles_deny_translation():
  # Actual factory results, not fabricated safety words.
  fingerprint = gen_empty_fingerprint()
  fingerprint[0][0x5a] = 8
  fingerprint[2][0x3d6] = fingerprint[2][0x186] = 8
  matrix = ((CAR.FORD_F_150_MK14, 2), (CAR.FORD_EDGE_MK2, 8),
            (CAR.FORD_MONDEO_MK5, 10), (CAR.FORD_TRANSIT_MK5, 12),
            (CAR.FORD_MUSTANG_MACH_E_MK1, 2), (CAR.FORD_BRONCO_SPORT_MK1, 0))
  for vehicle, word in matrix:
    actual = CarInterface.get_params(vehicle, fingerprint, [], False, False, False)
    assert actual.safetyConfigs[-1].safetyParam == word, vehicle
    assert not actual.openpilotLongitudinalControl and qualified(actual), vehicle
  unknown = params(CAR.FORD_MUSTANG_MACH_E_MK1, alpha=False)
  unknown.carFingerprint = "unrecognized Ford"
  assert not qualified(unknown)
  cp = params(CAR.FORD_MUSTANG_MACH_E_MK1, alpha=False)
  assert qualified(cp)
  cp.alternativeExperience = 32
  assert qualified(cp)
  cp.safetyConfigs = [cp.safetyConfigs[0], cp.safetyConfigs[0]]
  assert not qualified(cp)
  cp.safetyConfigs = [cp.safetyConfigs[0]]
  for experience in (1, 64, 96):
    cp.alternativeExperience = experience
    assert not qualified(cp)
  cp.alternativeExperience = 0
  cp.openpilotLongitudinalControl = True
  assert not qualified(cp)
  cp.openpilotLongitudinalControl = False
  cp.safetyConfigs[-1].safetyParam = 3
  assert not qualified(cp)
  for word in (0, 4, 34):
    cp.safetyConfigs[-1].safetyParam = word
    assert not qualified(cp)
