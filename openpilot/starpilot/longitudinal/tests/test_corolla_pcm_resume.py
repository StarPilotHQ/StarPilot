from dataclasses import replace
from types import SimpleNamespace

import pytest

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.starpilot.longitudinal.extension import LongitudinalContext
from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs, ResumeFreshness
from openpilot.starpilot.longitudinal.toyota_output_policy import Lead


LONG_STATE = structs.CarControl.Actuators.LongControlState
MOVING_LEAD = Lead(True, 23.0, 0.4, 2.0, 0.0)
STOPPED_LEAD = Lead(True, 5.0, 0.2, 0.0, 0.0)


@pytest.mark.parametrize('blocker', (None, 'brake', 'gas', 'aeb', 'can', 'plan', 'hold', 'stale', 'no_lead', 'stopped_lead', 'auto_resume_off'))
def test_corolla_pcm_hold_requires_current_plan_and_moving_lead(blocker):
  cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, gen_empty_fingerprint(), [], False, False, False)
  assert cp.autoResumeSng and cp.safetyConfigs[0].safetyParam == 73
  if blocker == 'auto_resume_off':
    cp.autoResumeSng = False
  driver_state = structs.CarState(vEgo=0., standstill=True, canValid=True)
  driver_state.cruiseState.enabled = driver_state.cruiseState.standstill = True
  driver_state.brakePressed = blocker == 'brake'
  driver_state.gasPressed = blocker == 'gas'
  driver_state.stockAeb = blocker == 'aeb'
  driver_state.canTimeout = blocker == 'can'
  leads = () if blocker == 'no_lead' else (MOVING_LEAD, STOPPED_LEAD) if blocker == 'stopped_lead' else (MOVING_LEAD,)
  context = LongitudinalContext(leads=leads, toyota_resume_fresh=blocker != 'stale', force_stop_braking=blocker == 'hold')
  owner = LongControl(cp)
  for _ in range(20):
    assert owner.update(True, driver_state, -0.3, True, (-3.5, 2.0), context=context) < 0
  target, should_stop = (0.8, blocker == 'plan')
  outputs = [owner.update(True, driver_state, target, should_stop, (-3.5, 2.0), context=context) for _ in range(60)]
  if blocker is None:
    assert owner.long_control_state == LONG_STATE.pid
    assert outputs[-1] > 0
    assert owner.update(True, driver_state, target, False, (-3.5, 2.0),
                        context=replace(context, toyota_resume_fresh=False)) < 0
    assert owner.long_control_state == LONG_STATE.stopping
  else:
    assert owner.long_control_state == LONG_STATE.stopping
    assert max(outputs) <= 0


def test_corolla_resume_evidence_requires_current_car_and_plan(monkeypatch):
  names = ('carState', 'longitudinalPlan', 'deviceState')
  now = [1_050_000_000]
  car_state = structs.CarState()
  car_state.cruiseState.standstill = True
  class Messages(SimpleNamespace):
    def __getitem__(self, name):
      return {'carState': car_state, 'deviceState': SimpleNamespace(started=True, startedMonoTime=900_000_000)}[name]
  sm = Messages(seen={'deviceState': True}, alive={'deviceState': True}, valid={'deviceState': True},
                all_checks=lambda requested: bool(requested) and requested[0] in names)
  def publish():
    sm.logMonoTime = dict.fromkeys(names, now[0] - 5_000_000)
    sm.recv_time = dict.fromkeys(names, (now[0] - 2_000_000) / 1e9)

  monkeypatch.setattr('openpilot.starpilot.longitudinal.inputs.clock_pair_ns', lambda: (now[0], now[0] + 500_000_000))
  inputs = LongitudinalInputs.__new__(LongitudinalInputs)
  inputs.messages = lambda: sm
  inputs.resume_freshness = ResumeFreshness()
  inputs.ev9_long_enabled = inputs.toyota_sienna_replay = inputs.toyota_sienna_production = False
  inputs.toyota_corolla_stop = True
  monkeypatch.setattr(inputs, "_toyota_leads", lambda: (MOVING_LEAD,))
  publish()
  assert not inputs._vehicle_context(True).toyota_resume_fresh
  now[0] += 20_000_000
  publish()
  assert inputs._vehicle_context(True).toyota_resume_fresh
  now[0] += 170_000_000
  publish()
  sm.logMonoTime['longitudinalPlan'] = now[0] - 160_000_000
  assert not inputs._vehicle_context(True).toyota_resume_fresh
  assert not inputs._vehicle_context(False).toyota_resume_fresh
