"""A close stopped lead must finish an admitted MQB rolling stop before creep resumes."""
from unittest.mock import patch
from types import SimpleNamespace

import pytest
from opendbc.car import structs
from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
from openpilot.starpilot.car.volkswagen.stopped_lead import owner_for
from openpilot.starpilot.tests.test_ford_release_long_preferences import StopApproachFrame, stopped_sample


def params():
  cp = structs.CarParams(brand='volkswagen', carFingerprint='SEAT_ATECA_MK1', flags=5, alternativeExperience=32,
    safetyConfigs=[structs.CarParams.SafetyConfig(safetyModel=structs.CarParams.SafetyModel.volkswagen, safetyParam=9)],
    pcmCruise=False, openpilotLongitudinalControl=True, alphaLongitudinalAvailable=True,
    transmissionType=structs.CarParams.TransmissionType.automatic, networkLocation=structs.CarParams.NetworkLocation.gateway,
    steerControlType=structs.CarParams.SteerControlType.torque, stopAccel=-.55)
  cp.longitudinalTuning.kiBP = [0.]
  cp.longitudinalTuning.kiV = [0.]
  return cp


@pytest.mark.parametrize('direct,quiet', [(False, False), (False, True), (True, False), (True, True)])
def test_admitted_automatic_and_direct_native_owner(direct, quiet):
  cp = params()
  if direct:
    cp.transmissionType = structs.CarParams.TransmissionType.direct
    cp.safetyConfigs[0].safetyParam = 41
  if quiet:
    owner = structs.CarParams.SafetyConfig(**cp.safetyConfigs[0].to_dict())
    cp.safetyConfigs = [structs.CarParams.SafetyConfig(safetyModel=structs.CarParams.SafetyModel.noOutput), owner]
  assert owner_for(cp, 5.5) is not None


@pytest.mark.parametrize('field,value', [
  ('brand', 'ford'), ('carFingerprint', 'VOLKSWAGEN_ID4_MK1'), ('flags', 1 << 30), ('passive', True),
  ('dashcamOnly', True), ('notCar', True), ('openpilotLongitudinalControl', False), ('pcmCruise', True),
  ('alphaLongitudinalAvailable', False), ('alternativeExperience', 0),
  ('networkLocation', structs.CarParams.NetworkLocation.fwdCamera),
])
def test_other_configurations_keep_their_existing_owner(field, value):
  cp = params()
  setattr(cp, field, value)
  assert owner_for(cp, 5.5) is None


def test_manual_stock_acc_and_second_active_panda_stay_unchanged():
  cp = params()
  cp.transmissionType = structs.CarParams.TransmissionType.manual
  cp.safetyConfigs[0].safetyParam = 25
  assert owner_for(cp, 5.5) is None
  cp = params()
  cp.safetyConfigs[0].safetyParam = 8
  assert owner_for(cp, 5.5) is None
  cp = params()
  owner = structs.CarParams.SafetyConfig(**cp.safetyConfigs[0].to_dict())
  cp.safetyConfigs = [owner, owner]
  assert owner_for(cp, 5.5) is None


def test_close_stationary_lead_enters_actual_longcontrol_stopping_before_creep():
  cp, sm = params(), StopApproachFrame()
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  with patch('openpilot.selfdrive.controls.lib.longitudinal_planner.LongitudinalMpc'):
    owner = LongitudinalPlanner(cp).ford_stop_approach
  assert owner is not None
  assert stopped_sample(owner, sm) == (-.05, False)
  sm['carState'].vEgo = .449
  sm['radarState'].leadOne.dRel = 3.19
  sm['radarState'].leadOne.vLead = .066
  sm['radarState'].leadOne.radar = False
  target, stopping = stopped_sample(owner, sm, target=-.347)
  assert stopping and target == -.347
  control = LongControl(cp)
  control.long_control_state = LongCtrlState.pid
  control.last_output_accel = -.347
  sm['carState'].cruiseState = SimpleNamespace(standstill=False)
  sm['carState'].aEgo = -.328
  acceleration = control.update(True, sm['carState'], target, stopping, (-3.5, 2.))
  assert control.long_control_state == LongCtrlState.stopping
  assert acceleration < -.347
  assert stopped_sample(owner, sm, target=-3.5) == (-3.5, True)
  sm['carState'].standstill, sm['carState'].vEgo = True, 0.
  assert stopped_sample(owner, sm, target=.3) == (.3, False)
  sm['carState'].standstill, sm['carState'].vEgo = False, .2
  sm['radarState'].leadOne.vLead = 1.
  assert stopped_sample(owner, sm, target=.3) == (.3, False)

