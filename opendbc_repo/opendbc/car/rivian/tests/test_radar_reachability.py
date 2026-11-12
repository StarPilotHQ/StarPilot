from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.rivian.interface import CarInterface
from opendbc.car.rivian.radar_interface import RADAR_MSG_COUNT, RADAR_START_ADDR
from opendbc.car.rivian.values import CAR, DBC, RivianFlags
from openpilot.cereal import messaging
from openpilot.selfdrive.car.card import Car


def fingerprint(case='healthy'):
  fp = gen_empty_fingerprint()
  if case != 'gen2':
    fp[0][0x321] = 7
  if case != 'missing_marker':
    fp[0 if case == 'wrong_marker_bus' else 1][0x131A] = 8
  if case == 'angle_receiver':
    fp[1][0x1310] = 8
  if case != 'missing_bank':
    bus = 0 if case == 'wrong_radar_bus' else 1
    fp[bus].update(dict.fromkeys(range(RADAR_START_ADDR, RADAR_START_ADDR + RADAR_MSG_COUNT), 8))
    if case == 'partial_bank':
      del fp[bus][RADAR_START_ADDR + RADAR_MSG_COUNT - 1]
    if case == 'wrong_length':
      fp[bus][RADAR_START_ADDR] = 7
  return fp


def params(fp, alpha=False, release=False):
  return CarInterface.get_params(CAR.RIVIAN_R1, fp, [], alpha, release, False)


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('release', (False, True))
def test_factory_restores_observed_radar_without_changing_control_parameters(alpha, release):
  absent = params(fingerprint('missing_bank'), alpha, release)
  observed = params(fingerprint(), alpha, release)
  assert absent.radarUnavailable
  assert not observed.radarUnavailable, 'OBSERVED_LEGACY_RIVIAN_RADAR_UNREACHABLE'
  before, after = absent.to_dict(), observed.to_dict()
  assert observed.flags == absent.flags == 0
  before.pop('radarUnavailable')
  after.pop('radarUnavailable')
  assert before == after


@pytest.mark.parametrize('case', ('missing_marker', 'wrong_marker_bus', 'missing_bank', 'wrong_radar_bus',
                                  'partial_bank', 'wrong_length', 'gen2', 'angle_receiver'))
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('release', (False, True))
def test_other_hardware_and_incomplete_radar_sources_stay_unavailable(case, alpha, release):
  assert params(fingerprint(case), alpha, release).radarUnavailable


def bank(*, mode=2, state=3, bus=1):
  packer = CANPacker(DBC[CAR.RIVIAN_R1][Bus.radar])
  return [packer.make_can_msg(f'RADAR_TRACK_{address:x}', bus, {
    'STATE': state if address == RADAR_START_ADDR + RADAR_MSG_COUNT - 1 else 0,
    'MODE': mode,
    'LONG_DIST': 42.5,
    'AZIMUTH': 0.,
    'REL_SPEED': -2.,
  }) for address in range(RADAR_START_ADDR, RADAR_START_ADDR + RADAR_MSG_COUNT)]


@pytest.mark.parametrize('alpha', (False, True))
def test_factory_parser_and_card_publish_the_last_track_and_retire_it(alpha):
  cp = params(fingerprint(), alpha)
  assert not cp.radarUnavailable, 'RIVIAN_FACTORY_CANNOT_REACH_RADAR_PUBLICATION'
  radar = CarInterface.RadarInterface(cp)
  assert radar.update([(1_000_000_000, bank(bus=0))]) is None
  result = radar.update([(1_050_000_000, bank())])
  assert result is not None and not result.errors.canError
  assert len(result.points) == 1
  point = result.points[0]
  assert (point.dRel, point.yRel, point.vRel) == pytest.approx((42.5, 0., -2.))
  published = []
  caller = SimpleNamespace(pm=SimpleNamespace(send=lambda service, event: published.append((service, event.to_bytes()))))
  Car.radar_publish(caller, result)
  assert len(published) == 1 and published[0][0] == 'radarTracks'
  event = messaging.log_from_bytes(published[0][1])
  assert event.valid and len(event.radarTracks.points) == 1
  wire = event.radarTracks.points[0]
  assert wire.trackId == point.trackId
  assert (wire.dRel, wire.yRel, wire.vRel) == pytest.approx((42.5, 0., -2.))
  repeated = radar.update([(1_100_000_000, bank())])
  assert repeated is not None and repeated.points[0].trackId == point.trackId
  removed = radar.update([(1_150_000_000, bank(state=0))])
  assert removed is not None and not removed.points


@pytest.mark.parametrize('mode', (0, 1, 2, 3))
def test_existing_short_range_phantom_braking_filter_is_preserved(mode):
  cp = params(fingerprint())
  assert not cp.radarUnavailable, 'RIVIAN_FACTORY_CANNOT_REACH_EXISTING_MODE_FILTER'
  result = CarInterface.RadarInterface(cp).update([(1_000_000_000, bank(mode=mode))])
  assert result is not None and not result.errors.canError
  assert len(result.points) == (1 if mode in (2, 3) else 0)


@pytest.mark.parametrize('mutation', ('gen2', 'legacy_bridge_flag',
                                      'unknown_flag', 'passive', 'fixed', 'wrong_native_word', 'multi_panda'))
def test_observed_radar_identity_preserves_existing_aol_refusals(mutation):
  from opendbc.car import structs
  from opendbc.car.rivian.stock_aol import qualified

  cp = params(fingerprint())
  assert not cp.radarUnavailable, 'OBSERVED_RADAR_IDENTITY_UNAVAILABLE'
  assert qualified(cp), 'OBSERVED_RADAR_CANNOT_USE_EXISTING_GEN1_AOL'
  if mutation == 'gen2':
    cp.flags |= RivianFlags.GEN2.value
  elif mutation == 'legacy_bridge_flag':
    cp.flags = 2
  elif mutation == 'unknown_flag':
    cp.flags |= 1 << 17
  elif mutation == 'passive':
    cp.passive = True
  elif mutation == 'fixed':
    cp.fingerprintSource = structs.CarParams.FingerprintSource.fixed
  elif mutation == 'wrong_native_word':
    cp.safetyConfigs[0].safetyParam = 4
  else:
    cp.safetyConfigs = [cp.safetyConfigs[0], cp.safetyConfigs[0]]
  assert not qualified(cp)


@pytest.mark.parametrize('visibility', (None, 'false', 0, 1))
def test_unknown_radar_visibility_does_not_qualify_a_configuration(visibility):
  from opendbc.car.rivian.stock_aol import qualified

  cp = params(fingerprint())
  fields = ('brand', 'carFingerprint', 'flags', 'passive', 'notCar', 'dashcamOnly', 'fingerprintSource',
            'safetyConfigs', 'openpilotLongitudinalControl', 'alternativeExperience')
  view = SimpleNamespace(**{name: getattr(cp, name) for name in fields}, radarUnavailable=visibility)
  assert not qualified(view)


@pytest.mark.parametrize('alpha', (False, True), ids=('stock', 'alpha'))
def test_observed_radar_actual_aol_callers_keep_brake_reverse_and_fault_recovery(alpha, monkeypatch, tmp_path):
  from opendbc.car import structs
  from opendbc.car.rivian.stock_aol import qualified
  from opendbc.safety.tests.libsafety import libsafety_py
  from openpilot.starpilot.car.rivian.tests.test_aol_lifecycle import test_actual_rivian_aol_engage_brake_reverse_fault_recovery

  cp = params(fingerprint(), alpha)
  assert not cp.radarUnavailable, 'OBSERVED_RADAR_IDENTITY_UNAVAILABLE'
  assert cp.flags == 0 and not cp.radarUnavailable
  assert qualified(cp), 'OBSERVED_RADAR_CANNOT_USE_EXISTING_GEN1_AOL'
  native = libsafety_py.libsafety
  native.init_tests()
  debug = native.set_safety_hooks(structs.CarParams.SafetyModel.allOutput, 0) == 0
  if alpha and not debug:
    from openpilot.starpilot.car.rivian.tests.test_aol_native import test_native_unqualified_aol_profile_has_no_transmit_catalog
    test_native_unqualified_aol_profile_has_no_transmit_catalog(1)
    native.init_tests()
    native.set_alternative_experience(0)
    assert native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0) == 0
    return
  test_actual_rivian_aol_engage_brake_reverse_fault_recovery(alpha, monkeypatch, tmp_path, radar_available=True)


@pytest.mark.parametrize('alpha', (False, True))
def test_sensor_presence_does_not_change_controller_packets(alpha, monkeypatch):
  assert not params(fingerprint(), alpha).radarUnavailable, 'OBSERVED_RADAR_IDENTITY_UNAVAILABLE'
  from opendbc.car.rivian.tests import test_base_driving

  base = test_base_driving.ControllerRig(alpha=alpha)
  with monkeypatch.context() as observed:
    observed.setattr(test_base_driving, 'gen_empty_fingerprint', fingerprint)
    radar = test_base_driving.ControllerRig(alpha=alpha)
  assert base.cp.flags == 0
  assert radar.cp.flags == 0
  output_torque = []
  for _ in range(6):
    base.feed(ack=True)
    radar.feed(ack=True)
    before, _, old_frames = base.apply(base.request())
    after, _, new_frames = radar.apply(radar.request())
    assert before.to_dict() == after.to_dict()
    assert old_frames == new_frames
    assert new_frames
    output_torque.append(after.torqueOutputCan)
  assert any(output_torque)


def test_missing_radar_sources_still_publish_invalid_data():
  cp = params(fingerprint())
  assert not cp.radarUnavailable, 'OBSERVED_RADAR_IDENTITY_UNAVAILABLE'
  radar = CarInterface.RadarInterface(cp)
  assert not radar.update([(1_000_000_000, bank())]).errors.canError
  for tick in range(20):
    result = radar.update([(3_000_000_000 + tick * 50_000_000, bank()[-1:])])
  assert result is not None and result.errors.canError
  published = []
  caller = SimpleNamespace(pm=SimpleNamespace(send=lambda service, event: published.append(event.to_bytes())))
  Car.radar_publish(caller, result)
  event = messaging.log_from_bytes(published[-1])
  assert not event.valid and event.radarTracks.errors.canError


@pytest.mark.parametrize('error', ('radarFault', 'radarUnavailableTemporary', 'canError'))
def test_radar_faults_preserve_independent_lateral_without_hiding_the_alert(error, monkeypatch):
  from openpilot.starpilot.aol.tests import test_radar_lateral_isolation

  cp = params(fingerprint())
  assert not cp.radarUnavailable, 'OBSERVED_RADAR_IDENTITY_UNAVAILABLE'
  with monkeypatch.context() as observed:
    observed.setattr(test_radar_lateral_isolation, 'params', lambda family: cp)
    test_radar_lateral_isolation.test_radar_only_fault_keeps_lateral_for_every_port('rivian', error)
