"""Toyota physical PCM +/- edges must reach the SLC intent ledger."""
from unittest import mock
from types import SimpleNamespace
from collections import deque
from typing import cast

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, DBC
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.selfdrive.car.card import Car
from openpilot.cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.selfdrive.controls.plannerd import queue_cruise_event, current_cruise_event
from openpilot.starpilot.speed_limits.runtime import Action, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay_fixture


def params():
  rack = structs.CarParams.CarFw.new_message(ecu=structs.CarParams.Ecu.eps,
                                             fwVersion=b'8965B47070' + bytes(6))
  return CarInterface.get_params(CAR.TOYOTA_PRIUS, {0: {0x2FF: 8}, 1: {}, 2: {}}, [rack], False, False, False)


class ParsedCruise:
  def __init__(self):
    self.cp = params()
    self.ci = CarInterface(self.cp)
    self.packer = CANPacker(DBC[self.cp.carFingerprint][Bus.pt])
    self.helper = VCruiseHelper(self.cp)
    self.now = 2_000_000_000

  def step(self, speed, status=8, *, active=True, enabled=True, standstill=False, healthy=True):
    self.now += 10_000_000
    # Synthetic CAN acquisition, actual parser/state/button decoding. The CI
    # checks all lazy subscriptions: warm independent inputs, never set CS.
    frames = [self.packer.make_can_msg(name, 0, {}) for name in
              ('VSC1S07', 'BODY_CONTROL_STATE', 'BODY_CONTROL_STATE_2', 'ESP_CONTROL',
               'GEAR_PACKET', 'PCM_CRUISE_SM', 'BLINKERS_STATE', 'PRE_COLLISION', 'LIGHT_STALK', 'BRAKE_MODULE')]
    frames += [self.packer.make_can_msg('WHEEL_SPEEDS', 0, {f'WHEEL_SPEED_{w}': 0. if standstill else 72. for w in ('FL', 'FR', 'RL', 'RR')}),
               self.packer.make_can_msg('PCM_CRUISE', 0, {'CRUISE_ACTIVE': int(active), 'GAS_RELEASED': 1, 'CRUISE_STATE': status}),
               self.packer.make_can_msg('PCM_CRUISE_2', 0, {'SET_SPEED': speed, 'MAIN_ON': 1}),
               self.packer.make_can_msg('STEER_ANGLE_SENSOR', 0, {}),
               self.packer.make_can_msg('STEER_TORQUE_SENSOR', 0, {}),
               self.packer.make_can_msg('EPS_STATUS', 0, {'LKA_STATE': 1}),
               self.packer.make_can_msg('SDSU', 0, {'STATE': 0}),
               self.packer.make_can_msg('LKAS_HUD', 2, {}), self.packer.make_can_msg('RSA1', 2, {})]
    state = self.ci.update([(self.now, frames if healthy else [])])
    with mock.patch('openpilot.selfdrive.car.cruise.monotonic_ns', return_value=self.now):
      self.helper.update_v_cruise(state, enabled, True)
    return state, self.helper.slc_cruise_change

  def warm(self):
    for _ in range(12):
      self.step(90)


def publish_change(parsed, state):
  """Actual Card publication with transport/radar collaborators replaced."""
  host = Car.__new__(Car)
  host.CP = parsed.cp
  host.car_params_published = True
  host.sm = cast(messaging.SubMaster, SimpleNamespace(frame=1, all_checks=lambda services: True))
  host.last_actuators_output = structs.CarControl.Actuators()
  host.rk = cast(Ratekeeper, SimpleNamespace(remaining=0.))
  host.can_rcv_cum_timeout_counter = 0
  host.aol_replay = host.curve_replay = False
  host.slc_replay = True
  host.slc_producer_session = 'pcm-card'
  host.slc_cruise_event_id = 0
  host.v_cruise_helper = parsed.helper
  host.slc_receipts = []
  host.pm = mock.Mock()
  host.car_gps_publisher = mock.Mock()
  host.CI = cast(CarInterfaceBase, SimpleNamespace(CS=SimpleNamespace()))
  host.radar_publish = mock.Mock()
  # This is the same selected-speed copy normally made by Card.state_update.
  state.vCruise = parsed.helper.v_cruise_kph
  state.vCruiseCluster = parsed.helper.v_cruise_cluster_kph
  with mock.patch('openpilot.selfdrive.car.card.REPLAY', True):
    host.can_log_mono_time = parsed.now
    host.state_publish(state, None)
  sent = {call.args[0]: call.args[1] for call in host.pm.send.call_args_list}
  assert 'slcCruiseEvent' in sent
  # Match the actual wire, not an in-memory double-precision CruiseEvent.
  wire = messaging.log_from_bytes(sent['slcCruiseEvent'].to_bytes())
  queued, curve = deque(), deque()
  queue_cruise_event(wire, queued, curve)
  return current_cruise_event(queued, parsed.now), sent['carState']


def test_pcm_resume_edge_and_release_frame_set_speed_feed_override():
  parsed = ParsedCruise()
  parsed.warm()
  state, change = parsed.step(90, 9)
  assert state.canValid and state.buttonEvents[0].type == structs.CarState.ButtonEvent.Type.accelCruise
  assert change is None
  state, change = parsed.step(92, 8)  # Real route: update can coincide with release.
  assert state.buttonEvents[0].pressed is False
  assert change[:2] == pytest.approx((25., 92. / 3.6))
  assert change[2:] == ('accel', False)

  # The existing runtime receives the same classified Card event; stock selected
  # speed still belongs to PCM and the SLC ceiling follows qualified driver intent.
  fixture = replay_fixture.RuntimeReplayTests()
  fixture.setUp()
  sm = fixture.sm
  sm['carState'].vCruise = sm['carState'].vCruiseCluster = 90.
  runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1}),
                    session_id='pcm-drive')
  pending = runtime.step(sm, parsed.cp, now_ns=2_000_000_000)
  assert pending.message.slcState.hasPending
  sm.advance(2_010_000_000)
  first = runtime.step(sm, parsed.cp, now_ns=2_010_000_000,
                       request=Action(runtime.session_id, 1, pending.message.slcState.decisionId,
                                      pending.message.slcState.presentationId, 'accept'))
  assert first.result.acceptance is not None and first.result.acceptance.basis == 'driver_accept'
  assert first.result.ceiling is not None
  receipt, published = publish_change(parsed, state)
  sm.advance(parsed.now)
  sm.data['carState'] = published.carState
  result = runtime.step(sm, parsed.cp, now_ns=parsed.now,
                        cruise_event=receipt)
  assert not result.message.slcState.driverOverrideActive
  assert result.result.ceiling is not None
  assert parsed.helper.v_cruise_kph == pytest.approx(92.)


@pytest.mark.parametrize('mode', ('no_button', 'wrong_direction', 'disengage', 'stop_resume', 'invalid', 'gap', 'reengage'))
def test_pcm_speed_changes_without_current_matching_driver_edge_do_not_arm(mode):
  parsed = ParsedCruise()
  parsed.warm()
  if mode == 'gap':
    parsed.step(90, 9)
    parsed.now += 1_000_000_000
  elif mode == 'reengage':
    parsed.step(90, 0, active=False, enabled=False)
  elif mode == 'invalid':
    for _ in range(60):
      parsed.step(90, 9, healthy=False)
  status = 10 if mode == 'wrong_direction' else 9 if mode in ('stop_resume', 'disengage', 'reengage') else 8
  _, change = parsed.step(92, status, active=mode != 'disengage', enabled=mode != 'disengage', standstill=mode == 'stop_resume')
  assert change is None


def test_pcm_held_plus_and_minus_keep_selected_owner_and_do_not_duplicate():
  parsed = ParsedCruise()
  parsed.warm()
  _, change = parsed.step(92, 9)
  assert change[:2] == pytest.approx((25., 92. / 3.6))
  assert change[2:] == ('accel', False)
  assert parsed.step(92, 9)[1] is None
  assert parsed.step(94, 9)[1][:2] == pytest.approx((92. / 3.6, 94. / 3.6))
  parsed.step(94, 8)
  change = parsed.step(92, 10)[1]
  assert change[:2] == pytest.approx((94. / 3.6, 92. / 3.6))
  assert change[2:] == ('decel', False)
