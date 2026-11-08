"""Conventional CC's longitudinal speed minimum cannot inhibit independent LAT."""
from dataclasses import replace
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.gm import gmcan
from opendbc.car.gm.tests.test_ascm_intercept import params
from opendbc.car.gm.tests.test_bolt_cc import native
from opendbc.car.gm.tests.test_ordinary_cc import qualified_frames
from opendbc.car.gm.tests.test_sierra_cc_layout import params as sierra_params
from opendbc.car.gm.values import CAR, DBC, ORDINARY_CC_CAR, CruiseButtons, GMFlags
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import log
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.aol.intent import read_settings
from openpilot.starpilot.aol.tests import test_gm_parked_startup as parked, test_ioniq_availability as fixture
from openpilot.starpilot.aol.tests.test_gm_cc_lateral_minimum import setup
from openpilot.starpilot.car.gm.aol import independent_lateral_below_engage


def cc_params(candidate):
  cp = sierra_params() if candidate == CAR.CHEVROLET_SILVERADO_CC else params(candidate)
  cp.alternativeExperience = 32
  return cp


@pytest.mark.parametrize('candidate', sorted(ORDINARY_CC_CAR | {CAR.CHEVROLET_SILVERADO_CC}))
def test_all_exact_ordinary_cc_owners_keep_independent_steering_minimum(candidate):
  cp = cc_params(candidate)
  _, intent = setup()
  assert independent_lateral_below_engage(cp, intent, enabled=False, active=False)
  for enabled, active in ((True, True), (True, False), (False, True)):
    assert not independent_lateral_below_engage(cp, intent, enabled=enabled, active=active)
  for key in ('settingsQualified', 'allowedLatch', 'lateralArmed'):
    assert not independent_lateral_below_engage(cp, replace(intent, **{key: False}), enabled=False, active=False)
  assert not independent_lateral_below_engage(cp, replace(intent, pauseLateral=True), enabled=False, active=False)
  for flag in (GMFlags.PEDAL_LONG, GMFlags.NO_CAMERA, 1 << 30):
    denied = cp.as_reader().as_builder()
    denied.flags |= int(flag)
    assert not independent_lateral_below_engage(denied, intent, enabled=False, active=False)


@pytest.mark.parametrize('main_action', [0, 9])
def test_sierra_parser_card_selfdrived_controls_and_native_steer_below_cruise_minimum(main_action):
  cp = cc_params(CAR.CHEVROLET_SILVERADO_CC)
  original = parked.parsed_packets

  def packets(*args):
    packer = args[1]
    return original(*args) + [packer.make_can_msg('BCMBlindSpotMonitor', 0, {})]

  with patch.object(parked, 'parsed_packets', side_effect=packets):
    parked._exercise_parked_main(3, main_action, below_engage=True, cp=cp)


@pytest.mark.parametrize('candidate', [CAR.CHEVROLET_SILVERADO_CC, CAR.CHEVROLET_MALIBU_CC])
@pytest.mark.parametrize('speed_mph,allowed', [(20., False), (30., True)])
def test_native_longitudinal_direction_buttons_keep_existing_24mph_floor(candidate, speed_mph, allowed):
  cp = cc_params(candidate)
  assert cp.minEnableSpeed == pytest.approx(24 * .44704)
  safety = libsafety_py.libsafety
  safety.init_tests()
  safety.set_alternative_experience(cp.alternativeExperience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  packer = CANPacker(DBC[candidate][Bus.pt])
  for tick in range(5):
    now = 1_000_000_000 + tick * 10_000_000
    frames = [f for f in qualified_frames(packer, tick % 4) if f[0] not in (0x34A, 0x3D1)]
    frames += [packer.make_can_msg('EBCMWheelSpdRear', 0, {'RLWheelSpd': speed_mph * 1.609344,
               'RRWheelSpd': speed_mph * 1.609344, 'RLWheelDir': 1, 'RRWheelDir': 1}),
               packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': 1, 'CruiseSetSpeed': 50})]
    for frame in frames:
      assert native('rx', frame, now // 1000)
    safety.safety_tick()
  assert safety.safety_config_valid() and safety.get_controls_allowed()
  safety.set_aol_test_heartbeat(True)
  safety.aol_set_host_request(3)
  if allowed:
    assert safety.aol_get_permission_mask() & 2
  command = gmcan.create_buttons(packer, 0, (tick + 1) % 4, CruiseButtons.RES_ACCEL)
  assert bool(native('tx', command, now // 1000)) == allowed


@pytest.mark.parametrize('braking', [False, True])
def test_sierra_actual_lateral_callers_keep_main_intent_when_crossing_24mph(braking):
  cp = cc_params(CAR.CHEVROLET_SILVERADO_CC)
  case = fixture.IoniqAvailabilityTests(methodName='runTest')
  with OpenpilotPrefix(), ExitStack() as cleanup:
    cleanup.callback(case.doCleanups)
    sd, _, step = case._authority_callers(False, cp=cp, genuine_events=True)
    sd.aol_settings = read_settings(sd.params)
    for tick, mph in enumerate((30., 25., 23.85, 20., 10., 7.5, 6.5, 10.)):
      cs = fixture.car_state(brake=braking)
      cs.vEgo = cs.vEgoRaw = mph * .44704
      axis, state, command = step(tick, observed_state=cs, native_request_long=False, panda_controls_allowed=False)
      assert cs.cruiseState.available
      assert not state.enabled and not command.enabled
      assert not axis.longitudinalActive and not command.longActive
      assert axis.desiredLateral and axis.lateralActive, (mph, axis.to_dict(), sd.events.names)
      assert bool(command.latActive) == (mph > 7.)
      assert (log.OnroadEvent.EventName.belowEngageSpeed in sd.events.names) == (mph < 24.)
      assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
