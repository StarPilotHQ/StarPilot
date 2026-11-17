"""Legacy Toyota pre-warning is distinct from dirty radar and CAN failure."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.radar_interface import RadarInterface
from opendbc.car.toyota.values import CAR, DBC
from openpilot.cereal import messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.tests import test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_toyota_moving_pcm_off_deadline import cp_for


LEGACY = (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT, CAR.TOYOTA_RAV4H, CAR.LEXUS_RX)


def params(candidate, alpha):
  if candidate == CAR.TOYOTA_PRIUS_RETROFIT:
    return cp_for('prius_long' if alpha else 'prius_stock')
  return CarInterface.get_params(candidate, gen_empty_fingerprint(), [], alpha, False, False)


def batch(radar, packer, tick, *, status=1, pre=3, counter=1358, target=False, omit=None):
  frames = []
  for address in radar.RADAR_A_MSGS + radar.RADAR_B_MSGS:
    if address == omit:
      continue
    first = address == radar.RADAR_A_MSGS[0]
    fields = ({'LONG_DIST': 30. if target and first else 255., 'VALID': int(target and first),
               'LAT_DIST': 0., 'REL_SPEED': -1.} if address in radar.RADAR_A_MSGS else {'SCORE': 100})
    frames.append(packer.make_can_msg(address, 1, fields))
  if omit != 'status':
    frames.append(packer.make_can_msg('STATUS_MSG', 1, {
      'RADAR_STATUS': status, 'RADAR_PRE_FAULT': pre, 'INVALID_COUNTER': counter}))
  return [(1_000_000_000 + tick * 50_000_000, frames)]


def feed(radar, packer, **kwargs):
  result = None
  for tick in range(30):
    result = radar.update(batch(radar, packer, tick, **kwargs))
  assert result is not None
  return result


@pytest.mark.parametrize('candidate', LEGACY)
@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('counter', (1358, 1872, 2643))
@pytest.mark.parametrize('target', (False, True))
def test_legacy_ready_radar_pre_warning_keeps_live_measurements(candidate, alpha, counter, target):
  cp = params(candidate, alpha)
  radar = RadarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.radar])
  result = feed(radar, packer, counter=counter, target=target)
  assert radar.rcp.can_valid
  assert not result.errors.radarUnavailableTemporary, 'LEGACY_RADAR_PRE_WARNING'
  assert not result.errors.canError
  assert len(result.points) == int(target)
  if target:
    assert result.points[0].dRel == 30. and result.points[0].vRel == -1.


@pytest.mark.parametrize('profile', ('prius_long', 'camera_long'))
@pytest.mark.parametrize('condition', ('initializing', 'fault', 'unknown_status', 'unknown_pre', 'dirty_threshold',
                                       'saturated_counter', 'missing_status', 'missing_track'))
def test_real_radar_faults_and_receive_checks_remain(profile, condition):
  cp = cp_for(profile)
  radar = RadarInterface(cp)
  packer = CANPacker(DBC[cp.carFingerprint][Bus.radar])
  options = {'target': True}
  if condition == 'initializing':
    options['status'] = 0
  elif condition == 'fault':
    options['status'] = 3
  elif condition == 'unknown_status':
    options['status'] = 2
  elif condition == 'unknown_pre':
    options['pre'] = 1
  elif condition == 'dirty_threshold':
    options['counter'] = 2659
  elif condition == 'saturated_counter':
    options['counter'] = 4095
  elif condition == 'missing_status':
    options['omit'] = 'status'
  else:
    options['omit'] = radar.RADAR_A_MSGS[0]
  result = feed(radar, packer, **options)
  assert result.errors.canError or result.errors.radarUnavailableTemporary
  if condition.startswith('missing'):
    assert result.errors.canError and not radar.rcp.can_valid
  else:
    assert result.errors.radarUnavailableTemporary


def test_tss2_pre_fault_behavior_is_unchanged():
  cp = cp_for('camera_long')
  radar = RadarInterface(cp)
  result = feed(radar, CANPacker(DBC[cp.carFingerprint][Bus.radar]))
  assert result.errors.radarUnavailableTemporary


@pytest.mark.parametrize('profile', ('prius_long', 'prius_stock'))
def test_actual_radar_to_selfdrived_keeps_pre_warning_distinct_from_fault(profile):
  with ExitStack() as cleanup:
    cleanup.enter_context(OpenpilotPrefix())
    cleanup.enter_context(patch.dict('os.environ', {'SIMULATION': '0', 'AOL_REPLAY_RUNTIME': '1'}))
    cp = cp_for(profile)
    case = fixture.IoniqAvailabilityTests(methodName='runTest')
    cleanup.callback(case.doCleanups)
    sd, _, drive = case._authority_callers(True, cp=cp, genuine_events=True)
    radar = RadarInterface(cp)
    packer = CANPacker(DBC[cp.carFingerprint][Bus.radar])
    observed = fixture.car_state()
    observed.cruiseState.available = observed.cruiseState.enabled = True
    original = sd.update_events
    errors = [feed(radar, packer).errors]

    def events(cs):
      message = messaging.new_message('radarState', valid=True)
      message.radarState.radarErrors = errors[0].as_reader()
      sd.sm.data['radarState'] = message.radarState.as_reader()
      return original(cs)

    with patch.object(sd, 'update_events', side_effect=events):
      for tick in range(5):
        axis, host, command = drive(tick, observed_state=observed)
        assert host.enabled and axis.lateralActive and command.latActive
        assert not sd.sm['radarState'].radarErrors.radarUnavailableTemporary, 'LEGACY_RADAR_PRE_WARNING'
        if cp.openpilotLongitudinalControl:
          assert axis.longitudinalActive and command.longActive
      errors[0] = feed(radar, packer, status=3, counter=2659).errors
      axis, host, command = drive(5, observed_state=observed, native_request_long=False, long_allowed=False)
      if cp.openpilotLongitudinalControl:
        assert not host.enabled and not axis.longitudinalActive and not command.longActive
      assert axis.lateralActive and command.latActive
