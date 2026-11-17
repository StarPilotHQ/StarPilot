"""Publishing stalls must be localized without changing published commands."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest
from opendbc.car import structs
from openpilot.cereal import log
from openpilot.selfdrive.controls import controlsd
from openpilot.selfdrive.controls.lib import cycle_timing


class Messages(dict):
  logMonoTime = {'modelV2': 10, 'carState': 11, 'longitudinalPlan': 12}
  valid = {'driverAssistance': False}

  def all_checks(self, services):
    return True


def run_publish(*, measured, blocked=''):
  owner = Mock(spec=controlsd.Controls)
  owner.CP = structs.CarParams(steerControlType='torque', openpilotLongitudinalControl=False, pcmCruise=True)
  owner.CP.lateralTuning.init('torque')
  cs = structs.CarState(canValid=True, vEgo=17., vCruiseCluster=72.)
  owner.sm = Messages(carState=cs, carOutput=controlsd.messaging.new_message('carOutput').carOutput,
                      selfdriveState=NS(personality=NS(raw=1), state=controlsd.State.disabled, alertHudVisual='none'),
                      longitudinalPlan=NS(shouldStop=False, hasLead=True),
                      driverMonitoringState=NS(noResponseForceDecel=False))
  owner.curvature = owner.desired_curvature = .001
  owner.calibrated_pose = None
  owner.LoC = NS(extension=None, long_control_state='off', pid=NS(p=0., i=0., f=0.))
  owner.longitudinal_inputs = NS(lead_visible=lambda value: value)
  owner.steer_limited_by_safety = False
  owner.last_lane_centering_result = None
  owner.lane_centering_applied = 0.
  clocks = {'wall': 0, 'cpu': 0}
  if measured:
    kwargs = {'phases': cycle_timing.PUBLISH_PHASES} if hasattr(cycle_timing, 'PUBLISH_PHASES') else {}
    owner.publish_cycle_timing = cycle_timing.ControlCycleTiming(
      wall_clock=lambda: clocks['wall'], cpu_clock=lambda: clocks['cpu'], **kwargs)
  output = []

  def send(service, event):
    output.append((service, event.to_dict()))
    if service == blocked:
      clocks['wall'] += 60_000_000
      clocks['cpu'] += 1_000_000

  owner.pm = NS(send=send)
  original = controlsd.messaging.new_message
  def message(service, **kwargs):
    return original(service, logMonoTime=100, **kwargs)
  command = structs.CarControl(latActive=True)
  command.actuators.torque = .2
  lateral_log = log.ControlsState.LateralTorqueState(active=True, output=.2, desiredLateralAccel=.1, actualLateralAccel=.1)
  with patch.object(controlsd.messaging, 'new_message', side_effect=message):
    controlsd.Controls.publish(owner, command, lateral_log)
  return owner, output


@pytest.mark.parametrize('service,phase', [('controlsState', 'controls_state_send'), ('carControl', 'car_control_send'),
                                          ('starpilotLateralState', 'feedback_send_and_diagnostic')])
def test_actual_publish_identifies_blocking_send_without_zeroing_torque(service, phase):
  owner, messages = run_publish(measured=True, blocked=service)
  report = owner.publish_cycle_timing.snapshot()
  assert phase in report, 'PUBLISH_STALL_NOT_LOCALIZED'
  assert owner.publish_cycle_timing.complete
  assert report[phase] == {'wall_ms': 60., 'cpu_ms': 1.}
  assert sum(row['wall_ms'] for row in report.values()) == 60.
  command = next(data['carControl'] for name, data in messages if name == 'carControl')
  assert command['latActive'] and command['actuators']['torque'] == pytest.approx(.2)


def test_timing_preserves_all_actual_published_fields_and_order():
  _, before = run_publish(measured=False)
  _, after = run_publish(measured=True)
  assert after == before
  assert [name for name, _ in after] == ['controlsState', 'carControl', 'starpilotLateralState']
