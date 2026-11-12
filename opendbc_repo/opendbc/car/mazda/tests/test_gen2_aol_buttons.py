"""GEN2 physical-button event path for the AOL intent owner.

GEN2 CarState had no button events at all, so a mapped Cancel could never reach
the host intent layer. The path is gated on admission, so a non-admitted car keeps
reporting no cancel event.
"""
from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, structs
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.values import CAR, DBC, MazdaSafetyFlags

CANCEL = structs.CarState.ButtonEvent.Type.cancel


def make_cp(experience=0):
  cp = structs.CarParams(carFingerprint=CAR.MAZDA_3_2019, brand="mazda", flags=int(MazdaSafetyFlags.GEN2),
                         pcmCruise=True, openpilotLongitudinalControl=True,
                         transmissionType=structs.CarParams.TransmissionType.automatic,
                         fingerprintSource=structs.CarParams.FingerprintSource.can)
  cp.alternativeExperience = experience
  cp.init("safetyConfigs", 1)
  cp.safetyConfigs[0].safetyModel = structs.CarParams.SafetyModel.mazda
  cp.safetyConfigs[0].safetyParam = int(MazdaSafetyFlags.GEN2)
  return cp


def update_with_buttons(cp, pressed):
  state = CarState(cp)
  dbc_name = DBC[CAR.MAZDA_3_2019][Bus.pt]
  parsers = {
    Bus.pt: CANParser(dbc_name, [("CRZ_BTNS", 50)], 0),
    Bus.body: CANParser(dbc_name, [], 1),
    Bus.cam: CANParser(dbc_name, [], 2),
  }
  msg = CANPacker(dbc_name).make_can_msg("CRZ_BTNS", 0, {"CAN": int(pressed)})
  parsers[Bus.pt].update([0, [msg]])
  return state.update(parsers)


def test_admitted_gen2_produces_a_cancel_button_event():
  cp = make_cp(experience=32)
  ret = update_with_buttons(cp, True)
  assert any(event.type == CANCEL for event in ret.buttonEvents), ret.buttonEvents


def test_unadmitted_gen2_produces_no_button_event():
  cp = make_cp(experience=0)
  ret = update_with_buttons(cp, True)
  assert len(ret.buttonEvents) == 0


def test_no_press_produces_no_edge_even_when_admitted():
  cp = make_cp(experience=32)
  ret = update_with_buttons(cp, False)
  assert len(ret.buttonEvents) == 0
