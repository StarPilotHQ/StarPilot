"""Registered controller/CAN encoder checks with explicit synthetic car state.

These fixtures exercise command encoding, not road authority or plant maturity.
"""

import math

import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.chrysler.values import CAR as CHRYSLER
from opendbc.car.gm.values import CAR as GM, CanBus as GMCanBus
from opendbc.car.gm.radar_interface import RADAR_HEADER_MSG
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.mazda.values import CAR as MAZDA
from opendbc.car.mg.values import CAR as MG
from opendbc.car.rivian.values import CAR as RIVIAN
from opendbc.car.subaru.values import CAR as SUBARU
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.volkswagen.values import CAR as VOLKSWAGEN
from opendbc.car.steering_projection import (
  inspect_projection,
  install_observer,
  normalized_applied_command,
  project_candidate,
  project_request,
  projection_from_dict,
  projection_to_dict,
)

PLATFORMS = (
  HYUNDAI.HYUNDAI_SONATA,
  HYUNDAI.HYUNDAI_IONIQ_6,
  HYUNDAI.KIA_EV6,
  GM.GMC_ACADIA,
  HONDA.HONDA_CIVIC,
  TOYOTA.TOYOTA_COROLLA_TSS2,
  VOLKSWAGEN.VOLKSWAGEN_GOLF_MK7,
  VOLKSWAGEN.VOLKSWAGEN_PASSAT_NMS,
  VOLKSWAGEN.AUDI_Q5_MK1,
  SUBARU.SUBARU_IMPREZA,
  MAZDA.MAZDA_CX5_2022,
  CHRYSLER.CHRYSLER_PACIFICA_2018,
  RIVIAN.RIVIAN_R1,
)
BASE_NS = 1_000_000_000


def make_interface(platform, firmware=()):
  factory = interfaces[platform]
  fingerprint = gen_empty_fingerprint()
  if platform == GM.GMC_ACADIA:
    # Explicit registered ASCM radar layout; state below remains a codec fixture.
    fingerprint[GMCanBus.OBSTACLE][RADAR_HEADER_MSG] = 8
  cp = factory.get_params(platform, fingerprint, list(firmware), False, False, False)
  ci = factory(cp)
  ci.update([])
  assert type(ci) is factory and type(ci.CC) is factory.CarController
  assert cp.carFingerprint == platform
  set_state(ci)
  return ci


def set_state(ci, *, speed=18.0, driver=0.0, pressed=False):
  # Only the command codec's input state is synthetic. No native grant is made.
  state = structs.CarState(
    vEgo=speed, vEgoRaw=speed, canValid=True, steeringTorque=driver, steeringPressed=pressed, gearShifter=structs.CarState.GearShifter.drive
  )
  state.cruiseState.available = True
  state.cruiseState.enabled = True
  ci.CS.out = state.as_reader()


def control(torque, *, active=True):
  cc = structs.CarControl(enabled=active, latActive=active)
  cc.actuators.torque = torque
  return cc.as_reader()


def apply(ci, cc):
  stamp = BASE_NS + ci.CC.frame * 10_000_000
  output, frames = ci.apply(cc, stamp)
  return output, frames, ci.CC.learning_projection


def settle(ci, cc, ticks=260):
  records = []
  for tick in range(ticks + 16):
    output, frames, witness = apply(ci, cc)
    if witness is not None:
      records.append((output.as_reader(), tuple(frames), witness))
      if tick >= ticks - 1:
        break
  assert records
  return records


def decoded(ci, witness):
  dbc = ci.can_parsers[Bus.pt].dbc_name
  parser = CANParser(dbc, [(witness.can_address, 0)], witness.can_bus)
  packet = (witness.can_address, witness.can_data, witness.can_bus)
  updated = parser.update([BASE_NS + witness.frame * 10_000_000, [packet]])
  assert witness.can_address in updated
  return parser.vl[witness.can_address]


def check_packet(ci, frames, witness):
  assert (witness.can_address, witness.can_data, witness.can_bus) in frames
  values = decoded(ci, witness)
  family = witness.family
  if family == 'hyundai':
    if witness.codec_id in ('hyundai.LKAS11', 'hyundai.BLENDED_LKAS11'):
      torque, request = values['CR_Lkas_StrToqReq'], bool(values['CF_Lkas_ActToi'])
    elif witness.codec_id == 'hyundai.BLENDED_TORQUE':
      torque, request = values['TORQUE_REQUEST'], bool(values['STEER_REQ'])
    else:
      torque, request = values['StrTqReqVal'], bool(values['ActToiSta'])
  elif family == 'honda':
    torque, request = values['STEER_TORQUE'], bool(values['STEER_TORQUE_REQUEST'])
  elif family == 'toyota':
    torque, request = values['STEER_TORQUE_CMD'], bool(values['STEER_REQUEST'])
  elif family == 'gm':
    torque, request = values['LKASteeringCmd'], bool(values['LKASteeringCmdActive'])
  elif family == 'volkswagen':
    if 'HCA_01_LM_Offset' in values:
      torque = values['HCA_01_LM_Offset'] * (-1 if values['HCA_01_LM_OffSign'] else 1)
      request = bool(values['HCA_01_Sendestatus'])
    else:
      torque = values['LM_Offset'] * (-1 if values['LM_OffSign'] else 1)
      request = values['HCA_Status'] == 7
  elif family == 'subaru':
    if witness.codec_id == 'subaru.PREGLOBAL_ES_LKAS':
      torque, request = values['LKAS_Command'], bool(values['LKAS_Active'])
    else:
      torque, request = values['LKAS_Output'], bool(values['LKAS_Request'])
  elif family == 'mazda':
    torque, request = values['LKAS_REQUEST'], witness.steer_requested
    # GEN1 forwards camera status; the codec has no separate CC request bit.
    assert values['BIT_1'] == ci.CS.cam_lkas['BIT_1']
  elif family == 'chrysler':
    torque, request = values['STEERING_TORQUE'], bool(values['LKAS_CONTROL_BIT'])
  elif family == 'mg':
    torque, request = values['LKAReqToqHSC2'], bool(values['LKAReqToqStsHSC2'])
  else:
    assert family == 'rivian'
    torque, request = values['ACM_lkaStrToqReq'], bool(values['ACM_lkaActToi'])
  assert int(torque) == witness.final_raw
  assert request is witness.steer_requested


@pytest.mark.parametrize('platform', (GM.CADILLAC_ATS, SUBARU.SUBARU_FORESTER_PREGLOBAL, MAZDA.MAZDA_CX9, MG.MG_5_EV))
def test_dashcam_configuration_refuses_observer(platform):
  ci = make_interface(platform)
  assert ci.CP.dashcamOnly
  assert install_observer(ci) is False
  assert ci.CC.learning_projection_observer is None
  assert ci.CC.learning_projection is None


@pytest.mark.parametrize('platform', PLATFORMS)
@pytest.mark.parametrize('units', (-1.6, -1.5, -1.4, 1.4, 1.5, 1.6))
def test_actual_factory_quantization_and_packet(platform, units):
  ci = make_interface(platform)
  assert install_observer(ci)
  seed = settle(ci, control(0.01))[-1][2]
  cc = control(units / seed.effective_max)
  records = settle(ci, cc)
  # inspect_projection consumes only the exact latest owner object.
  output, frames, witness = records[-1]
  assert witness is ci.CC.learning_projection
  assert inspect_projection(ci, cc, output, witness) is True
  assert project_request(witness, cc.actuators.torque) == witness.final_raw
  assert project_candidate(witness, cc.actuators.torque) == witness.final_raw
  delivered = normalized_applied_command(witness)
  assert delivered is not None
  assert math.isfinite(delivered)
  assert projection_from_dict(projection_to_dict(witness)) == witness
  check_packet(ci, frames, witness)


@pytest.mark.parametrize('platform', PLATFORMS)
def test_observer_has_no_command_effect_and_inactive_veto(platform):
  observed, plain = make_interface(platform), make_interface(platform)
  assert plain.CC.learning_projection is None
  assert install_observer(observed)
  for tick in range(280):
    cc = control(0.04 if tick < 260 else -0.02, active=tick < 275)
    out_a, frames_a, witness = apply(observed, cc)
    out_b, frames_b, _ = apply(plain, cc)
    assert out_a.to_dict() == out_b.to_dict()
    assert frames_a == frames_b
    if tick >= 275 and witness is not None:
      assert inspect_projection(observed, cc, out_a, witness) is False


def test_one_integer_rate_cut_is_rejected_without_epsilon():
  ci = make_interface(TOYOTA.TOYOTA_COROLLA_TSS2)
  assert install_observer(ci)
  settle(ci, control(0.0))
  cc = control((ci.CC.params.STEER_DELTA_UP + 1) / ci.CC.params.STEER_MAX)
  output, frames, witness = apply(ci, cc)
  assert witness is not None
  assert witness.unlimited_raw - witness.final_raw == 1
  assert abs(output.torque - cc.actuators.torque) < 0.01
  assert inspect_projection(ci, cc, output, witness) is False
  assert project_candidate(witness, cc.actuators.torque) is None
  check_packet(ci, frames, witness)


def test_driver_cut_remains_ineligible():
  ci = make_interface(MAZDA.MAZDA_CX5_2022)
  assert install_observer(ci)
  set_state(ci, driver=-1000.0, pressed=True)
  cc = control(0.5)
  output, frames, witness = apply(ci, cc)
  assert witness is not None and witness.final_raw != witness.unlimited_raw
  assert inspect_projection(ci, cc, output, witness) is False
  assert project_candidate(witness, cc.actuators.torque) is None
  check_packet(ci, frames, witness)


@pytest.mark.parametrize('platform', (GM.GMC_ACADIA, SUBARU.SUBARU_IMPREZA, VOLKSWAGEN.VOLKSWAGEN_GOLF_MK7, CHRYSLER.CHRYSLER_PACIFICA_2018))
def test_nontransmission_has_no_new_witness(platform):
  ci = make_interface(platform)
  assert install_observer(ci)
  settle(ci, control(0.01))
  original = None
  for _ in range(5):
    _, frames, witness = apply(ci, control(0.01))
    if witness is not None:
      original = witness
    elif original is not None:
      assert (original.can_address, original.can_data, original.can_bus) not in frames
      assert ci.CC.learning_projection is None
      assert original.frame < ci.CC.frame - 1
      return
  raise AssertionError('expected the actual controller transmission cadence to hold a frame')


@pytest.mark.parametrize('speed,expected', ((9.0, 350.0), (13.0, 300.0), (17.0, 250.0)))
def test_rivian_captures_actual_dynamic_max(speed, expected):
  ci = make_interface(RIVIAN.RIVIAN_R1)
  assert install_observer(ci)
  set_state(ci, speed=speed)
  cc = control(0.005)
  output, frames, witness = settle(ci, cc)[-1]
  assert witness.effective_max == expected
  assert project_request(witness, cc.actuators.torque) == output.torqueOutputCan
  check_packet(ci, frames, witness)


def test_modified_civic_has_actual_filter_and_lookup_context():
  firmware = structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, address=0x18DA30F1, subAddress=0, fwVersion=b'39990-TGG,A020\x00\x00', brand='honda')
  ci = make_interface(HONDA.HONDA_CIVIC_BOSCH, (firmware,))
  assert ci.CC.modified_civic_steering is not None
  assert install_observer(ci)
  cc = control(0.005)
  output, frames, witness = settle(ci, cc)[-1]
  assert len(witness.shape) == 7
  assert witness.mapping_kind == 'honda_lookup'
  assert project_request(witness, cc.actuators.torque) == witness.final_raw
  assert inspect_projection(ci, cc, output, witness) is True
  assert project_request(witness, 0.006) is None
  assert project_request(witness, 0.006, shape=witness.shape) is not None
  check_packet(ci, frames, witness)


def test_changed_configuration_and_fabricated_observation_refused():
  ci = make_interface(TOYOTA.TOYOTA_COROLLA_TSS2)
  assert install_observer(ci)
  cc = control(0.002)
  output, _, witness = settle(ci, cc)[-1]
  assert inspect_projection(ci, cc, output, witness) is True
  assert inspect_projection(ci, cc, output, witness._replace(final_raw=witness.final_raw + 1)) is None
  ci.CP.flags = int(ci.CP.flags) ^ 1
  assert inspect_projection(ci, cc, output, witness) is None


@pytest.mark.parametrize(
  'field,value',
  (
    ('codec_id', 'unknown.codec'),
    ('mapping_kind', 'unknown'),
    ('can_data', b'x' * 65),
    ('shape', [0.0] * 8),
    ('effective_max', math.nan),
    ('limits', [('STEER_DELTA_UP', -1.0)]),
    ('frame', True),
    ('lookup_x', [0.0] * 17),
  ),
)
def test_bounded_wire_decoder_refuses_unknown_or_malformed(field, value):
  ci = make_interface(TOYOTA.TOYOTA_COROLLA_TSS2)
  assert install_observer(ci)
  data = projection_to_dict(settle(ci, control(0.002))[-1][2])
  assert data is not None
  data[field] = value
  assert projection_from_dict(data) is None
