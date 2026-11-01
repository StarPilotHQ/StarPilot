import time
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

from openpilot.cereal import log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from opendbc.car.structs import car
from openpilot.selfdrive.car.cruise import CRUISE_LONG_PRESS
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.starpilot.aol.intent import AolCardIntent, AolSettings, disarming_fault, read_settings
from openpilot.starpilot.aol.runtime import AxisDecision, current_intent, current_native, decide_axes
from openpilot.starpilot.aol.tests import test_ioniq_availability as availability_fixture
from openpilot.starpilot.aol.tests.test_ioniq_availability import car_state
from openpilot.starpilot.aol.wire import IntentState, SafetyState, encode_intent, encode_safety


def empty_carstate_queue(sock):
  original_receive = messaging.recv_one_or_none

  def receive(received_sock):
    return None if received_sock is sock else original_receive(received_sock)

  return mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=receive)


def sample_car_state(sd, state, stamp):
  event = messaging.new_message('carState', valid=bool(state.canValid), logMonoTime=stamp)
  event.carState = state
  sock = object()
  with mock.patch.object(sd, 'car_state_sock', sock, create=True), \
       empty_carstate_queue(sock), \
       mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=event.as_reader()), \
       mock.patch.object(sd.sm, 'update', create=True):
    return SelfdriveD.data_sample(sd)


class CardIntentTests(unittest.TestCase):
  def test_optional_cancel_preserves_only_current_owned_lateral(self):
    from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
    from opendbc.car.gm.values import CAR as GM
    from openpilot.starpilot.aol.vehicle import policy_for
    from openpilot.starpilot.nostalgia import aol_no_entry
    from openpilot.starpilot.tests.test_ev6_startup import params

    cp = params(alpha=True, radar=True)
    policy = policy_for(cp)
    cp.safetyConfigs[0].safetyParam |= policy.safety_param_addition
    cp.alternativeExperience |= policy.alternative_experience_addition
    self.assertEqual((cp.safetyConfigs[0].safetyParam, cp.alternativeExperience), (0x815, 32))
    gm = ordinary_params(GM.CHEVROLET_VOLT_ASCM, alpha=True, sascm=True, radar=True)
    now = 4_000_000_000
    state = car_state()
    state.cruiseState.available = True
    button = car.CarState.ButtonEvent.Type.cancel
    cancel = log.OnroadEvent.EventName.buttonCancel

    class SM(dict):
      model_ready = True
      def all_checks(self, services=None):
        return self.model_ready or services is None or 'modelV2' not in services

    sm = SM(pandaStates=[], driverMonitoringState=SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False),
            extrinsicsCalibration=SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated))
    services = ('aolSafetyWire', 'aolIntentWire')
    sm.valid = dict.fromkeys(services, True)
    sm.alive = dict.fromkeys(services, True)
    sm.seen = dict.fromkeys(services, True)
    sm.logMonoTime = dict.fromkeys(services, now)
    sd = SelfdriveD.__new__(SelfdriveD)
    sd.CP = cp.as_reader()
    sd.initialized, sd.enabled, sd.active = True, True, True
    sd.aol_replay, sd.axis_transport_required = True, True
    sd.aol_car_state_log_ns, sd.aol_session_id = now, 'cancel-session'
    sd.aol_axis_decision = AxisDecision()
    sd.aol_dm_lateral_inhibit, sd.nostalgia_paddle_cancel = False, False
    sd.aol_authority_lost = False
    sd.aol_settings = None
    sd.events, sd.state_machine = Events(), StateMachine()
    sd.state_machine.state = log.SelfdriveState.OpenpilotState.enabled

    def step(*, pressed=True, case=None, extra=None):
      sd.aol_dm_lateral_inhibit = False
      state.buttonEvents = [] if pressed is None else [car.CarState.ButtonEvent(type=button, pressed=pressed)]
      sd.events.clear()
      if pressed is not None:
        sd.events.add(cancel)
      if extra is not None:
        sd.events.add(extra)
      sm.seen['aolIntentWire'] = case != 'missing'
      sm.seen['aolSafetyWire'] = case != 'native_missing'
      sm.model_ready = case != 'model'
      sm['driverMonitoringState'].lockout = case == 'dm'
      state.canValid = case != 'invalid_rx'
      sm['aolIntentWire'] = encode_intent(IntentState('card', 1, now,
        now - 30_000_001 if case == 'stale' else now, now + 200_000_000,
        case != 'false_latch', False, False, case != 'unqualified', True, case != 'optional_off'))
      if case == 'malformed':
        sm['aolIntentWire'] = b'malformed'
      sm['aolSafetyWire'] = encode_safety(SafetyState(1, True, now, now + 200_000_000,
        int(sd.CP.safetyConfigs[0].safetyModel.raw), int(sd.CP.safetyConfigs[-1].safetyParam),
        case != 'native_denied', False, case != 'native_mismatch', False, 'panda', 'cancel-session'))
      sd.step()
      return sd.aol_axis_decision

    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now), \
         mock.patch.object(sd, 'sm', sm, create=True), mock.patch.object(sd, 'data_sample', side_effect=lambda: sample_car_state(sd, state, now)), \
         mock.patch.object(sd, 'update_events'), mock.patch.object(sd, 'update_alerts'), \
         mock.patch.object(sd, 'update_conditional_mode'), mock.patch.object(sd, 'publish_selfdriveState'), mock.patch.object(sd, 'publish_axis_proof'), \
         mock.patch('openpilot.selfdrive.selfdrived.selfdrived.aol_no_entry', wraps=aol_no_entry) as veto:
      for pressed in (True, False, None):
        retained = step(pressed=pressed)
        self.assertTrue(retained.desired_lateral and retained.lateral_active)
        self.assertFalse(sd.enabled or sd.active or retained.desired_longitudinal or retained.longitudinal_active)
        self.assertTrue(veto.call_args.kwargs['allow_lateral_cancel'])
        if pressed is not None:
          self.assertIn(cancel, sd.events.names)
      self.assertTrue(aol_no_entry([cancel], state, paddle_only_cancel=False))
      for case in ('missing', 'false_latch', 'optional_off', 'stale', 'unqualified', 'malformed',
                   'invalid_rx', 'model', 'dm', 'native_missing', 'native_denied', 'native_mismatch'):
        with self.subTest(case=case):
          sd.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
          blocked = step(case=case)
          self.assertFalse(blocked.lateral_active or blocked.longitudinal_active or sd.enabled or sd.active)
          if case in ('missing', 'false_latch', 'optional_off', 'stale', 'unqualified', 'malformed'):
            self.assertFalse(veto.call_args.kwargs['allow_lateral_cancel'])
      for extra in (log.OnroadEvent.EventName.doorOpen, log.OnroadEvent.EventName.calibrationInvalid,
                    log.OnroadEvent.EventName.canError):
        with self.subTest(extra=extra):
          self.assertEqual(step(extra=extra).lateral_active, extra == log.OnroadEvent.EventName.doorOpen)
      for optional in (False, True):
        with self.subTest(gm_optional=optional):
          sd.CP = gm.as_reader()
          sd.state_machine.state = log.SelfdriveState.OpenpilotState.enabled
          self.assertFalse(step(case=None if optional else 'optional_off').lateral_active)
          self.assertFalse(veto.call_args.kwargs['allow_lateral_cancel'])

  def test_volt_ascm_low_speed_continuation_and_engagement_gates(self):
    # Route 14 crosses 5 kph while enabled; the stock no-entry event must
    # still block a new longitudinal engagement, without dropping active axes.
    from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
    from opendbc.car.gm.values import CAR

    import os
    import struct

    from openpilot.starpilot.aol.tests.test_gm import TestGmAol

    with OpenpilotPrefix(), mock.patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      for key in ('AlwaysOnLateral', 'AlphaLongitudinalEnabled', 'VoltOnePedalMode', 'VoltSNG', 'GMAutoHold'):
        settings.put_bool(key, True, block=True)
      selected = TestGmAol.card(ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=True,
                                              sascm=True, radar=True), settings)
      try:
        cp = selected.CP.as_reader()
        self.assertEqual((cp.safetyConfigs[0].safetyParam, cp.alternativeExperience), (0xD114, 32))
      finally:
        selected.vehicle_startup.close()
    self.assertEqual(cp.minEnableSpeed, struct.unpack('f', struct.pack('f', 5. / 3.6))[0])
    from openpilot.starpilot.nostalgia import aol_no_entry
    low_speed = [log.OnroadEvent.EventName.belowEngageSpeed]
    self.assertTrue(aol_no_entry(low_speed, car_state(), paddle_only_cancel=False))
    self.assertFalse(aol_no_entry(low_speed, car_state(), paddle_only_cancel=False, allow_below_engage_speed=True))
    now = 4_000_000_000
    state = car_state()
    state.cruiseState.available = True

    class SM(dict):
      def all_checks(self, _services=None):
        return True

    sm = SM(pandaStates=[], driverMonitoringState=SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False),
            extrinsicsCalibration=SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated))
    services = ('aolSafetyWire', 'aolIntentWire')
    sm.valid = sm.alive = sm.seen = dict.fromkeys(services, True)
    sm.logMonoTime = dict.fromkeys(services, now)
    sd = SelfdriveD.__new__(SelfdriveD)
    sd.CP = cp
    sd.initialized, sd.enabled, sd.active = True, False, False
    sd.aol_replay, sd.axis_transport_required = True, True
    sd.aol_car_state_log_ns, sd.aol_session_id = now, 'volt-session'
    sd.aol_axis_decision = AxisDecision()
    sd.aol_dm_lateral_inhibit, sd.nostalgia_paddle_cancel = False, False
    sd.aol_authority_lost = False
    sd.aol_settings = None
    sd.events, sd.state_machine = Events(), StateMachine()

    def step(speed, *, armed, long_requested, event=None):
      state.vEgo = speed
      sd.events.clear()
      if speed < cp.minEnableSpeed:
        sd.events.add(log.OnroadEvent.EventName.belowEngageSpeed)
      if event is not None:
        sd.events.add(event)
      sm['aolIntentWire'] = encode_intent(IntentState('card', 1, now, now, now + 200_000_000,
                                                     armed, False, False, True, armed))
      sm['aolSafetyWire'] = encode_safety(SafetyState(1, True, now, now + 200_000_000,
        int(cp.safetyConfigs[0].safetyModel.raw), 0xD114, True, long_requested,
        True, long_requested, 'panda', 'volt-session'))
      sd.step()
      return sd.aol_axis_decision

    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now), \
         mock.patch.object(sd, 'sm', sm, create=True), \
         mock.patch.object(sd, 'data_sample', side_effect=lambda: sample_car_state(sd, state, now)), \
         mock.patch.object(sd, 'update_events'), mock.patch.object(sd, 'update_alerts'), \
         mock.patch.object(sd, 'update_conditional_mode'), mock.patch.object(sd, 'publish_selfdriveState'), mock.patch.object(sd, 'publish_axis_proof'):
      combined = step(1.40, armed=False, long_requested=True,
                      event=log.OnroadEvent.EventName.buttonEnable)
      self.assertEqual(combined.mode, 'combined')
      for speed in (1.385523796, 0., 1.395637870):
        self.assertEqual(step(speed, armed=False, long_requested=True).mode, 'combined')
        self.assertTrue(sd.enabled)
      step(1.40, armed=True, long_requested=False, event=log.OnroadEvent.EventName.buttonCancel)
      blocked = step(1.385523796, armed=True, long_requested=False,
                     event=log.OnroadEvent.EventName.buttonEnable)
      self.assertTrue(sd.events.contains(ET.NO_ENTRY))
      self.assertFalse(sd.enabled or blocked.desired_longitudinal)
      self.assertEqual(blocked.mode, 'lateralOnly')
      self.assertEqual(step(1.395637870, armed=True, long_requested=False).mode, 'lateralOnly')
      self.assertFalse(sd.enabled)  # Crossing the threshold is not an enable gesture.
      for event in (log.OnroadEvent.EventName.canError, log.OnroadEvent.EventName.accFaulted,
                    log.OnroadEvent.EventName.calibrationInvalid):
        with self.subTest(event=event):
          blocked = step(1.385523796, armed=True, long_requested=False, event=event)
          self.assertFalse(blocked.desired_lateral or blocked.desired_longitudinal)
      state.steerFaultTemporary = True
      self.assertFalse(step(1.385523796, armed=True, long_requested=False).lateral_active)
      state.steerFaultTemporary = False
      sm.seen['aolSafetyWire'] = False
      self.assertEqual(step(1.385523796, armed=True, long_requested=False).mode, 'off')

  def test_seatbelt_blocks_standard_longitudinal_without_disarming_aol(self):
    from openpilot.starpilot.nostalgia import aol_no_entry

    owner = AolCardIntent(AolSettings(True, 0, 9, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True)
    state = car_state()
    state.cruiseState.available = True
    state.seatbeltUnlatched = True
    events = Events()
    events.add(log.OnroadEvent.EventName.seatbeltNotLatched)
    self.assertTrue(events.contains(ET.NO_ENTRY))
    self.assertTrue(events.contains(ET.SOFT_DISABLE))
    owner.update(state, fault_active=disarming_fault(events.to_msg(), state))
    self.assertFalse(owner.allowed_latch)
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state, fault_active=disarming_fault(events.to_msg(), state))
    self.assertTrue(owner.allowed_latch)
    state.buttonEvents = []
    standard = StateMachine()
    events.add(log.OnroadEvent.EventName.buttonEnable)
    self.assertEqual(standard.update(events), (False, False))
    native = SimpleNamespace(requestedLateral=True, requestedLongitudinal=False,
                             lateralAllowed=True, longitudinalAllowed=True)
    for unlatched in (True, True, False):
      state.seatbeltUnlatched = unlatched
      events.clear()
      if unlatched:
        events.add(log.OnroadEvent.EventName.seatbeltNotLatched)
      owner.update(state, fault_active=disarming_fault(events.to_msg(), state))
      enabled, active = standard.update(events)
      self.assertFalse(enabled or active)  # Buckling alone is not an engagement gesture.
      allowed, pause_lat, pause_long = owner.output(state)
      decision = decide_axes(standard_lateral=active, standard_longitudinal=enabled,
        intent=SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long),
        native=native, car_state=state, initialized=True, model_ready=True,
        no_entry=aol_no_entry(events.names, state, paddle_only_cancel=False),
        immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
      self.assertTrue(decision.lateral_active)
      self.assertFalse(decision.desired_longitudinal or decision.longitudinal_active)
    state.seatbeltUnlatched = True
    direct = decide_axes(standard_lateral=True, standard_longitudinal=True,
      intent=SimpleNamespace(allowedLatch=True, pauseLateral=False, pauseLongitudinal=False),
      native=native, car_state=state, initialized=True, model_ready=True, no_entry=False,
      immediate_disable=False, dm_lockout=False, pause_brake_mps=0.)
    self.assertTrue(direct.lateral_active)
    self.assertFalse(direct.desired_longitudinal)
    events.add(log.OnroadEvent.EventName.doorOpen)
    self.assertFalse(disarming_fault(events.to_msg(), state))
    self.assertFalse(aol_no_entry(events.names, state, paddle_only_cancel=False))

  def test_temporary_eps_and_known_gears_preserve_latch_with_zero_steering(self):
    owner = AolCardIntent(AolSettings(True, 0, 0, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True)
    state = car_state()
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=False)]
    owner.update(state)
    state.buttonEvents = []
    for gear, temporary, name in (
        (car.CarState.GearShifter.drive, True, log.OnroadEvent.EventName.steerTempUnavailable),
        (car.CarState.GearShifter.neutral, False, log.OnroadEvent.EventName.wrongGear),
        (car.CarState.GearShifter.reverse, False, log.OnroadEvent.EventName.wrongGear),
        (car.CarState.GearShifter.park, False, log.OnroadEvent.EventName.wrongGear)):
      with self.subTest(gear=gear, temporary=temporary):
        state.gearShifter = gear
        state.steerFaultTemporary = temporary
        events = Events()
        events.add(name)
        fault = disarming_fault(events.to_msg(), state)
        self.assertFalse(fault)
        for _ in range(100):
          owner.update(state, fault_active=fault)
          self.assertTrue(owner.allowed_latch)
          allowed, pause_lat, pause_long = owner.output(state)
          decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
            intent=SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long),
            native=SimpleNamespace(requestedLateral=False, requestedLongitudinal=False, lateralAllowed=False, longitudinalAllowed=False),
            car_state=state, initialized=True, model_ready=True, no_entry=True, immediate_disable=False,
            dm_lockout=False, pause_brake_mps=0)
          self.assertFalse(decision.desired_lateral)
          self.assertFalse(decision.lateral_active)
        state.gearShifter = car.CarState.GearShifter.drive
        state.steerFaultTemporary = False
        owner.update(state, fault_active=False)
        self.assertTrue(owner.output(state)[0])
        decision = decide_axes(standard_lateral=False, standard_longitudinal=False,
          intent=SimpleNamespace(allowedLatch=owner.output(state)[0], pauseLateral=False, pauseLongitudinal=False),
          native=SimpleNamespace(requestedLateral=True, requestedLongitudinal=False, lateralAllowed=True, longitudinalAllowed=False),
          car_state=state, initialized=True, model_ready=True, no_entry=False, immediate_disable=False,
          dm_lockout=False, pause_brake_mps=0)
        self.assertTrue(decision.lateral_active)

  def test_calibration_pause_retains_intent_and_mapping_change_cannot_rearm(self):
    from dataclasses import replace
    from openpilot.starpilot.aol.intent import AOL_TOGGLE
    owner = AolCardIntent(AolSettings(True, 0., 0, 0, (0, 0, 0), (0, 0, 0)))
    state = car_state()
    state.cruiseState.available = True
    owner.update(state)
    owner.observe_calibration(state, events=[])
    self.assertTrue(owner.allowed_latch)
    invalid = Events()
    invalid.add(log.OnroadEvent.EventName.calibrationInvalid)
    owner.observe_calibration(state, events=invalid.to_msg())
    self.assertTrue(owner.allowed_latch)
    self.assertEqual(owner.output(state)[1:], (True, True))
    owner.settings = replace(owner.settings, lkas_action=AOL_TOGGLE)
    owner.update(state)
    owner.observe_calibration(state, events=[])
    self.assertTrue(owner.allowed_latch)
    self.assertEqual(owner.output(state)[1:], (False, False))
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state)
    self.assertFalse(owner.allowed_latch)
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=False)]
    owner.update(state)
    state.buttonEvents = []
    owner.settings = replace(owner.settings, main_action=AOL_TOGGLE)
    owner.update(state)
    owner.settings = replace(owner.settings, main_action=0)
    owner.update(state)
    self.assertFalse(owner.allowed_latch)  # Changing another mapping is not an arming gesture.
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state)
    self.assertTrue(owner.allowed_latch)

  def test_low_speed_brake_suppresses_output_without_destroying_explicit_latch(self):
    owner = AolCardIntent(AolSettings(True, 5.0, 0, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True)
    state = car_state()
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state)
    state.buttonEvents = []
    events = Events()
    events.add(log.OnroadEvent.EventName.pedalPressed)
    for speed, brake, expected in ((2., True, False), (2., False, True), (6., True, True)):
      state.vEgo, state.brakePressed = speed, brake
      owner.update(state, fault_active=disarming_fault(events.to_msg(), state))
      self.assertTrue(owner.allowed_latch)
      allowed, pause_lat, pause_long = owner.output(state)
      axes = decide_axes(standard_lateral=False, standard_longitudinal=False,
        intent=SimpleNamespace(allowedLatch=allowed, pauseLateral=pause_lat, pauseLongitudinal=pause_long),
        native=SimpleNamespace(requestedLateral=expected, requestedLongitudinal=False,
                               lateralAllowed=expected, longitudinalAllowed=False),
        car_state=state, initialized=True, model_ready=True, no_entry=False, immediate_disable=False,
        dm_lockout=False, pause_brake_mps=5.0)
      self.assertEqual(axes.lateral_active, expected)
      self.assertFalse(axes.longitudinal_active)

  def test_permanent_and_unrelated_faults_still_disarm(self):
    state = car_state()
    for name in (log.OnroadEvent.EventName.steerUnavailable, log.OnroadEvent.EventName.controlsMismatch,
                 log.OnroadEvent.EventName.overheat):
      events = Events()
      events.add(name)
      self.assertEqual(disarming_fault(events.to_msg(), state),
                       any(event.immediateDisable for event in events.to_msg()))
    events = Events()
    events.add(log.OnroadEvent.EventName.wrongGear)
    self.assertFalse(disarming_fault(events.to_msg(), state))  # Events may lag the return to Drive.
    state.gearShifter = car.CarState.GearShifter.unknown
    self.assertTrue(disarming_fault(events.to_msg(), state))
    state.steerFaultPermanent = True
    self.assertTrue(disarming_fault([], state))
    owner = AolCardIntent(AolSettings(True, 0, 0, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True)
    owner.allowed_latch = True
    owner.update(state)
    self.assertFalse(owner.allowed_latch)

  def test_native_rejection_rearms_with_one_new_press_without_replaying_old_receipt(self):
    owner = AolCardIntent(AolSettings(True, 0, 0, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True)
    state = car_state()
    lkas = car.CarState.ButtonEvent.Type.lkas
    owner.update(state, now_ns=100)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=True)]
    owner.update(state, now_ns=200)
    self.assertTrue(owner.allowed_latch)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=False)]
    owner.update(state, now_ns=300)
    state.buttonEvents = []
    owner.update(state, now_ns=400, native_rejection_ns=350)
    self.assertFalse(owner.allowed_latch)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=True)]
    owner.update(state, now_ns=500, native_rejection_ns=350)
    self.assertTrue(owner.allowed_latch)
    state.buttonEvents = []
    owner.update(state, now_ns=600, native_rejection_ns=450)
    self.assertTrue(owner.allowed_latch)  # Delayed denial predates the new press.
    owner.update(state, now_ns=700, native_rejection_ns=650)
    self.assertFalse(owner.allowed_latch)
    owner.update(state, now_ns=800)
    self.assertFalse(owner.allowed_latch)  # The held physical button cannot rearm.
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=False)]
    owner.update(state, now_ns=900)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=True)]
    owner.update(state, now_ns=1000)
    self.assertTrue(owner.allowed_latch)

  def test_native_rejection_and_fresh_press_same_frame_preserve_fault_inhibit(self):
    for fault in (False, True):
      owner = AolCardIntent(AolSettings(True, 0, 0, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True)
      state = car_state()
      owner.update(state, now_ns=100)
      owner.allowed_latch = True
      state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
      owner.update(state, now_ns=200, native_rejection_ns=150, fault_active=fault)
      self.assertEqual(owner.allowed_latch, not fault)

  def test_explicit_latch_requires_new_gesture_after_fault(self):
    owner = AolCardIntent(AolSettings(True, 0, 0, 0, (0, 0, 0), (0, 0, 0)), explicit_latch=True)
    state = car_state()
    lkas = car.CarState.ButtonEvent.Type.lkas
    owner.update(state, fault_active=False)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=True)]
    owner.update(state)
    self.assertTrue(owner.allowed_latch)

    state.buttonEvents = []
    owner.update(state, fault_active=True)
    self.assertFalse(owner.allowed_latch)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=False)]
    owner.update(state, fault_active=True)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=True)]
    owner.update(state, fault_active=True)
    self.assertFalse(owner.allowed_latch)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=False)]
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=True)]
    owner.update(state)  # No fresh clear event yet.
    self.assertFalse(owner.allowed_latch)

    # A held button at recovery is not a new request. The driver must release it first.
    owner.update(state, fault_active=False)
    self.assertFalse(owner.allowed_latch)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=False)]
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=lkas, pressed=True)]
    owner.update(state)
    self.assertTrue(owner.allowed_latch)

  def test_persisted_preferences_and_fresh_defaults(self):
    with OpenpilotPrefix():
      params = Params()
      fresh = read_settings(params)
      self.assertFalse(fresh.enabled)
      self.assertEqual(fresh.pause_brake_mps, 0.0)
      self.assertEqual(fresh.distance_actions, (0, 0, 0))
      params.put_bool('AlwaysOnLateral', True, block=True)
      params.put('PauseAOLOnBrake', 10.0, block=True)
      params.put('DistanceButtonControl', 3, block=True)
      saved = read_settings(params)
      self.assertTrue(saved.enabled)
      self.assertEqual(saved.pause_brake_mps, 10.0)
      self.assertEqual(saved.distance_actions, (3, 0, 0))

  def test_brake_pause_units_preserve_legacy_until_explicit_si_override(self):
    with OpenpilotPrefix():
      params = Params()
      params.put_bool('AlwaysOnLateral', True, block=True)
      params.put('PauseAOLOnBrake', 10.0, block=True)
      for metric in (False, True):
        params.put_bool('IsMetric', metric, block=True)
        self.assertEqual(read_settings(params).pause_brake_mps, 10.0)
      params.put('AolBrakePauseSpeedMps', 4.4704, block=True)
      self.assertAlmostEqual(read_settings(params).pause_brake_mps, 4.4704)
      self.assertTrue(read_settings(params).enabled)
      params.put('AolBrakePauseSpeedMps', 0.0, block=True)
      self.assertEqual(read_settings(params).pause_brake_mps, 0.0)
      params.put('AolBrakePauseSpeedMps', -1.0, block=True)
      self.assertFalse(read_settings(params).enabled)
      self.assertTrue(params.get_bool('AlwaysOnLateral'))
      params.remove('AolBrakePauseSpeedMps')
      self.assertEqual(read_settings(params).pause_brake_mps, 10.0)

  def test_invalid_threshold_never_falls_back_to_permissive_value(self):
    with OpenpilotPrefix():
      params = Params()
      params.put_bool('AlwaysOnLateral', True, block=True)
      params.put('PauseAOLOnBrake', 10.0, block=True)
      for key in ('AolBrakePauseSpeedMps', 'PauseAOLOnBrake'):
        path = Path(params.get_param_path(key))
        for value in (b'', b'bad', b'\xff', b'nan', b'inf', b'-1', b'100.1'):
          with self.subTest(key=key, value=value):
            path.write_bytes(value)
            self.assertFalse(read_settings(params).enabled)
            self.assertTrue(params.get_bool('AlwaysOnLateral'))
        path.unlink()
      self.assertTrue(read_settings(params).enabled)
      self.assertEqual(read_settings(params).pause_brake_mps, 0.0)

  def test_saved_threshold_controls_actual_brake_boundary(self):
    with OpenpilotPrefix():
      params = Params()
      params.put_bool('AlwaysOnLateral', True, block=True)
      params.put('PauseAOLOnBrake', 10.0, block=True)
      state = car_state(brake=True)
      native = SimpleNamespace(requestedLateral=True, requestedLongitudinal=False,
                               lateralAllowed=True, longitudinalAllowed=False)
      intent = SimpleNamespace(allowedLatch=True, lateralArmed=True, pauseLateral=False, pauseLongitudinal=False)
      for metric in (False, True):
        params.put_bool('IsMetric', metric, block=True)
        for speed, mode in ((9.9, 'off'), (10.0, 'lateralOnly')):
          state.vEgo = speed
          result = decide_axes(standard_lateral=False, standard_longitudinal=False, intent=intent,
                               native=native, car_state=state, initialized=True, model_ready=True,
                               no_entry=False, immediate_disable=False, dm_lockout=False,
                               pause_brake_mps=read_settings(params).pause_brake_mps)
          self.assertEqual(result.mode, mode)

  def test_honda_main_lkas_pause_and_cancel_ownership(self):
    settings = AolSettings(True, 5.0, 9, 0, (3, 4, 0), (3, 0, 0))
    owner = AolCardIntent(settings)
    state = car_state()
    state.cruiseState.available = True
    owner.update(state)
    self.assertFalse(owner.output(state)[0])  # LKAS button owns the latch

    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state)
    self.assertTrue(owner.output(state)[0])

    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
    owner.update(state)
    self.assertFalse(owner.pause_lateral)  # Honda cancel never doubles as pause

    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.gapAdjustCruise, pressed=True)]
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.gapAdjustCruise, pressed=False)]
    owner.update(state)
    self.assertTrue(owner.pause_lateral)
    self.assertEqual(owner.output(state), (True, True, False))

  def test_invalid_can_and_settings_disable_intent(self):
    owner = AolCardIntent(AolSettings(True, 0, 0, 0, (0, 0, 0), (0, 0, 0)))
    state = car_state()
    state.cruiseState.available = True
    owner.update(state)
    self.assertTrue(owner.output(state)[0])
    state.canValid = False
    owner.update(state)
    self.assertFalse(owner.output(state)[0])

  def test_interrupted_distance_hold_does_not_replay_and_keeps_deliberate_pause(self):
    settings = AolSettings(True, 0, 0, 0, (3, 4, 0), (0, 0, 0))
    owner = AolCardIntent(settings)
    state = car_state()
    state.cruiseState.available = True
    button = car.CarState.ButtonEvent.Type.gapAdjustCruise
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=True)]
    owner.update(state)
    state.buttonEvents = []
    for _ in range(5):
      owner.update(state)
    state.canValid = False
    owner.update(state)
    self.assertFalse(owner._held[int(button)])
    self.assertEqual(owner._timers[int(button)], 0)
    state.canValid = True
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
    owner.update(state)
    self.assertFalse(owner.pause_lateral or owner.pause_longitudinal)

    # A complete user pause remains latched through CAN loss.
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=True)]
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
    owner.update(state)
    self.assertTrue(owner.pause_lateral)
    state.canValid = False
    owner.update(state)
    self.assertTrue(owner.pause_lateral)

    # If another owner consumed a release, it cannot complete the held gesture.
    state.canValid = True
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=True)]
    owner.update(state)
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
    owner.update(state, consumed_buttons=frozenset((int(button),)))
    self.assertFalse(owner._held[int(button)])
    self.assertTrue(owner.pause_lateral)

    # CAN loss just before the long-press boundary cannot finish that gesture.
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=True)]
    owner.update(state)
    state.buttonEvents = []
    for _ in range(CRUISE_LONG_PRESS - 2):
      owner.update(state)
    self.assertFalse(owner.pause_longitudinal)
    state.canValid = False
    owner.update(state)
    state.canValid = True
    for _ in range(3):
      owner.update(state)
    self.assertFalse(owner.pause_longitudinal)
    self.assertTrue(owner.pause_lateral)

    # An owner can reserve the button on a tick with no new button event.
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=True)]
    owner.update(state)
    state.buttonEvents = []
    for _ in range(CRUISE_LONG_PRESS - 2):
      owner.update(state)
    owner.update(state, consumed_buttons=frozenset((int(button),)))
    state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
    owner.update(state)
    self.assertFalse(owner.pause_longitudinal)
    self.assertTrue(owner.pause_lateral)

  def test_held_at_start_distance_waits_for_neutral_before_all_press_lengths(self):
    button = car.CarState.ButtonEvent.Type.gapAdjustCruise
    for held_ticks, actions in ((1, (3, 0, 0)), (CRUISE_LONG_PRESS, (0, 4, 0)),
                                (CRUISE_LONG_PRESS * 5, (0, 0, 3))):
      with self.subTest(held_ticks=held_ticks):
        settings = AolSettings(True, 0, 0, 0, actions, (0, 0, 0))
        owner = AolCardIntent(settings, explicit_latch=True)
        state = car_state()
        state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=True)]
        owner.update(state)
        state.buttonEvents = []
        for _ in range(held_ticks):
          owner.update(state)
        state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
        owner.update(state)
        self.assertFalse(owner.pause_lateral or owner.pause_longitudinal)
        state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=True)]
        owner.update(state)
        state.buttonEvents = []
        for _ in range(held_ticks - 1):
          owner.update(state)
        state.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=False)]
        owner.update(state)
        self.assertEqual((owner.pause_lateral, owner.pause_longitudinal),
                         (held_ticks != CRUISE_LONG_PRESS, held_ticks == CRUISE_LONG_PRESS))


class IpcAxisContractTests(unittest.TestCase):
  _authority_callers = availability_fixture.IoniqAvailabilityTests._authority_callers

  _ioniq_availability_callers = availability_fixture.IoniqAvailabilityTests._ioniq_availability_callers

  def test_fresh_ioniq_input_gets_full_publication_lease(self):
    from openpilot.starpilot.aol.runtime import current_axis

    with OpenpilotPrefix():
      sd, controls, step, _, _ = self._ioniq_availability_callers()
      step(2)
      source = sd.aol_car_state_log_ns
      published = source + 29_960_000
      with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=published):
        sd.publish_axis_proof(sd.CS_prev)
      proof = sd.pm.messages['aolAxisState']
      self.assertEqual(proof.aolAxisState.sourceCarStateMonoTime, source)
      self.assertEqual(proof.aolAxisState.observedMonoTime, published)
      self.assertEqual(proof.aolAxisState.validUntilMonoTime, published + 30_000_000)
      self.assertTrue(proof.aolAxisState.lateralActive and proof.aolAxisState.longitudinalActive)
      controls.sm.update_msgs(published / 1e9, [proof])
      # Source age has crossed 30 ms; this already-admitted output is still fresh.
      for now in (source + 33_507_000, published + 30_000_000):
        self.assertIsNotNone(current_axis(controls.sm, now_ns=now))
        with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
          command, _ = controls.state_control()
        self.assertTrue(command.latActive and command.longActive)
      now = published + 30_000_001
      self.assertIsNone(current_axis(controls.sm, now_ns=now))
      with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=now):
        command, _ = controls.state_control()
      self.assertFalse(command.latActive or command.longActive)

  def test_expired_ioniq_input_has_inactive_publication_lease(self):
    from openpilot.starpilot.aol.runtime import current_axis

    for age in (30_008_000, 30_055_000):
      with self.subTest(source_age_ns=age), OpenpilotPrefix():
        sd, controls, step, _, _ = self._ioniq_availability_callers()
        step(2)
        published = sd.aol_car_state_log_ns + age
        with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=published):
          sd.publish_axis_proof(sd.CS_prev)
        proof = sd.pm.messages['aolAxisState']
        axis = proof.aolAxisState
        self.assertEqual(axis.validUntilMonoTime, published + 30_000_000)
        self.assertEqual(str(axis.faultReason), 'availabilityPause')
        self.assertEqual(axis.sourceCarStateMonoTime, 0)
        self.assertFalse(axis.lateralActive or axis.longitudinalActive or axis.desiredLongitudinal)
        availability_fixture.assert_ordinary_source_pause(sd, publication_only=True)
        controls.sm.update_msgs(published / 1e9, [proof])
        # A fresh output lease does not make stale input eligible for actuation.
        self.assertIsNotNone(current_axis(controls.sm, now_ns=published))
        with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=published):
          command, _ = controls.state_control()
        self.assertFalse(command.latActive or command.longActive)
        self.assertIsNone(current_axis(controls.sm, now_ns=published + 30_000_001))
        recovered, _, recovered_command = step(6)
        availability_fixture.assert_fresh_ordinary_recovery(sd, recovered, recovered_command, 10_060_000_000)
        self.assertEqual(axis.validUntilMonoTime, published + 30_000_000)

  def test_publication_lease_preserves_ioniq_fault_denial(self):
    for fault in ('native_missing', 'native_denied', 'can_invalid', 'permanent_eps'):
      with self.subTest(fault=fault), OpenpilotPrefix():
        sd, controls, step, _, _ = self._ioniq_availability_callers()
        step(2)
        observed = car_state()
        kwargs = {}
        if fault == 'native_missing':
          kwargs['native_valid'] = False
        elif fault == 'native_denied':
          kwargs['allowed'] = False
        elif fault == 'can_invalid':
          observed.canValid = False
        else:
          observed.steerFaultPermanent = True
        step(3, observed_state=observed, **kwargs)
        published = 10_040_000_000
        with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=published):
          sd.publish_axis_proof(sd.CS_prev)
        proof = sd.pm.messages['aolAxisState']
        self.assertEqual(proof.aolAxisState.validUntilMonoTime, published + 30_000_000)
        self.assertFalse(proof.aolAxisState.lateralActive or proof.aolAxisState.longitudinalActive)
        controls.sm.update_msgs(published / 1e9, [proof])
        with mock.patch('openpilot.selfdrive.controls.controlsd.time.monotonic_ns', return_value=published):
          command, _ = controls.state_control()
        self.assertFalse(command.latActive or command.longActive)

  def test_ioniq6_fresh_backlog_companion_keeps_axes_and_stale_loss_warns(self):
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.hyundai.interface import CarInterface
    from opendbc.car.hyundai.ioniq6_handoff import build_ioniq6_hda2_long_candidate
    from opendbc.car.hyundai.values import CAR
    from openpilot.starpilot.aol.intent_companion import IntentCompanion

    for full_op in (False, True):
      with self.subTest(full_op=full_op), OpenpilotPrefix():
        fp = gen_empty_fingerprint()
        fp[2].update({0x110: 32, 0x362: 32})
        fp[0][0x3A5] = 24
        fp[1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24,
                      0x1BA: 24, 0x1E5: 16, 0x36A: 16})
        stock = CarInterface.get_params(CAR.HYUNDAI_IONIQ_6, fp, [], False, False, False)
        cp = build_ioniq6_hda2_long_candidate(stock, fp)
        self.assertIsNotNone(cp)
        # Mirror Card's saved-AOL marker before publishing the LONG candidate.
        cp.safetyConfigs[-1].safetyParam |= 0x0800
        pm = messaging.PubMaster(['aolIntentWire'])
        sd, controls, step = self._authority_callers(full_op, cp=cp)
        self.assertIsInstance(sd.aol_intent_companion, IntentCompanion)
        self.assertEqual(sd.CP.safetyConfigs[-1].safetyParam, 0x8895)
        self.assertEqual(sd.CP.alternativeExperience, 0)
        axis, _, command = step(0, offset_ns=50_000_000)
        self.assertTrue(axis.lateralActive and command.latActive)
        self.assertEqual(command.longActive, full_op)
        base = 10_000_000_000

        def intent(stamp, sequence):
          message = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=stamp)
          message.aolIntentWire = encode_intent(IntentState('authority-card', sequence, stamp, stamp,
                                                           stamp + 200_000_000, True, False, False, True, True))
          return message

        source = base + 66_000_000
        first = intent(source, 2)
        newer = intent(base + 77_000_000, 3)
        pm.send('aolIntentWire', first)
        pm.send('aolIntentWire', newer)
        axis, state, command = step(8, state_stamp=source, intent_message=newer)
        self.assertEqual(axis.sourceCarStateMonoTime, source)
        self.assertTrue(axis.nativeAcknowledged and axis.lateralActive and command.latActive)
        self.assertEqual(axis.longitudinalActive, full_op)
        self.assertEqual(command.longActive, full_op)
        self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
        self.assertNotEqual(state.alertText1, 'TAKE CONTROL IMMEDIATELY')
        self.assertTrue(step(9)[0].lateralActive)
        axis, state, command = step(17, timeout=True, offset_ns=877_165)
        self.assertEqual(axis.sourceCarStateMonoTime, 0)
        self.assertFalse(sd.enabled or sd.active or axis.lateralActive or axis.longitudinalActive)
        self.assertFalse(command.latActive or command.longActive)
        self.assertIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
        self.assertEqual(state.alertText1, 'TAKE CONTROL IMMEDIATELY')
        self._assert_withdrawal_alert(controls, sd.pm.messages['selfdriveState'])
        self.assertEqual(str(axis.faultReason), 'transportPause')
        self.assertFalse(sd.aol_authority_lost)
        recovered, _, command = step(18, native_request_long=False)
        self.assertTrue(recovered.lateralActive and command.latActive)
        self.assertFalse(sd.enabled or sd.active or recovered.longitudinalActive or command.longActive)

  def _transport_callers(self):
    from openpilot.selfdrive.car.card import Car
    from openpilot.selfdrive.car.tests.test_hyundai_aol import candidate
    from openpilot.starpilot.aol.intent import AolProcessFaultContext
    from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
    from openpilot.starpilot.car.hyundai.aol import create_intent

    _, cp = candidate(True)
    cp.safetyConfigs[-1].safetyParam |= 0x0800
    sd, controls, step = self._authority_callers(True, cp=cp)
    card = Car.__new__(Car)
    card.CP = cp.as_reader()
    # Match the actual Card producer behind this fixture's raw intent wire.
    card.slc_producer_session = 'authority-card'
    services = ['aolAxisState', 'aolSafetyWire', 'pandaStates', 'onroadEvents']
    self.enterContext(mock.patch.object(card, 'sm', messaging.SubMaster(services, ignore_avg_freq=services), create=True))
    card.aol_process_fault_context = AolProcessFaultContext()
    card.aol_transport_feedback = TransportPauseFeedback(card.CP)
    card.aol_settings = AolSettings(True, 0., 9, 0, (0, 0, 0), (0, 0, 0))
    card.aol_card_intent = create_intent(cp, card.aol_settings)
    cs = car_state()
    cs.cruiseState.available = True
    card.aol_card_intent.update(cs, now_ns=100)
    cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    card.aol_card_intent.update(cs, now_ns=200)
    cs.buttonEvents = []
    self.assertTrue(card.aol_card_intent.allowed_latch)

    def feedback(now, *, axis=None, event=None, state=None):
      frames = []
      for service in ('pandaStates', 'aolSafetyWire'):
        message = messaging.new_message(service, len(sd.sm[service]), valid=sd.sm.valid[service],
                                        logMonoTime=sd.sm.logMonoTime[service])
        setattr(message, service, sd.sm[service])
        frames.append(message.as_reader())
      frames += [m for m in (axis, event) if m is not None]
      card.sm.update_msgs(now / 1e9, frames)
      fault = card.aol_disarming_fault(cs if state is None else state,
        int(card.sm.logMonoTime['onroadEvents']), now)
      card.aol_card_intent.update(cs if state is None else state, fault_active=fault, now_ns=now,
                                 standard_enabled=sd.enabled)
      return fault

    return sd, controls, step, card, feedback

  def test_actual_hkg_transport_pause_preserves_intent_and_recovers_only_lateral(self):
    from openpilot.starpilot.aol.intent_companion import IntentCompanion
    with OpenpilotPrefix():
      sd, controls, step, card, feedback = self._transport_callers()
      self.assertIsInstance(sd.aol_intent_companion, IntentCompanion)
      axis, _, command = step(0)
      self.assertTrue(axis.lateralActive and axis.longitudinalActive and command.longActive)
      feedback(10_000_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      axis, _, command = step(8, timeout=True, offset_ns=877_165)
      now = int(sd.pm.messages['aolAxisState'].logMonoTime)
      self.assertEqual(str(axis.faultReason), 'transportPause')
      self.assertTrue(sd.pm.messages['aolAxisState'].valid)
      self.assertFalse(sd.enabled or sd.active or command.latActive or command.longActive)
      self._assert_withdrawal_alert(controls, sd.pm.messages['selfdriveState'])
      self.assertFalse(feedback(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents']))
      self.assertTrue(card.aol_card_intent.allowed_latch)
      axis, _, command = step(9, native_request_lat=False, native_request_long=False)
      self.assertTrue(axis.desiredLateral)
      self.assertFalse(axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
      self.assertEqual(str(axis.faultReason), 'transportPause')
      now = int(sd.pm.messages['aolAxisState'].logMonoTime)
      self.assertFalse(feedback(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents']))
      self.assertTrue(card.aol_card_intent.allowed_latch)
      axis, _, command = step(10, native_request_long=False)
      self.assertEqual(str(axis.faultReason), 'none')
      self.assertTrue(axis.lateralActive and command.latActive)
      self.assertFalse(sd.enabled or sd.active or axis.longitudinalActive or command.longActive)
      now = int(sd.pm.messages['aolAxisState'].logMonoTime)
      feedback(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      self.assertTrue(card.aol_card_intent.allowed_latch)
      self.assertFalse(step(11, latched=False, native_request_long=False)[0].lateralActive)
      cs = car_state()
      from dataclasses import replace
      card.aol_card_intent.settings = replace(card.aol_card_intent.settings, cancel_actions=(3, 0, 0))
      cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
      card.aol_card_intent.update(cs, now_ns=now + 10_000_000)
      self.assertFalse(card.aol_card_intent.allowed_latch)

  def test_transport_pause_hard_upgrades_never_recover_without_withdrawal(self):
    cases = ('native_missing', 'native_deny', 'panda_rx', 'calibration', 'permanent_eps',
             'can_timeout', 'critical', 'producer_restart', 'invalid_then_fresh', 'cp_change', 'session_change')
    for case in cases:
      with self.subTest(case=case), OpenpilotPrefix():
        sd, _, step, _, _ = self._transport_callers()
        self.assertTrue(step(0)[0].longitudinalActive)
        self.assertEqual(str(step(8, timeout=True)[0].faultReason), 'transportPause')
        kwargs = {'native_request_long': False}
        if case == 'native_missing':
          kwargs['native_valid'] = False
        elif case == 'native_deny':
          kwargs['allowed'] = False
        elif case == 'panda_rx':
          kwargs['panda_fault'] = True
        elif case == 'calibration':
          kwargs['calibrated'] = False
        elif case in ('permanent_eps', 'can_timeout'):
          state = car_state()
          if case == 'permanent_eps':
            state.steerFaultPermanent = True
          else:
            state.canTimeout = True
          kwargs['observed_state'] = state
        elif case == 'critical':
          kwargs['critical_event'] = log.OnroadEvent.EventName.controlsMismatch
        elif case == 'producer_restart':
          now = 10_090_000_000
          message = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=now)
          message.aolIntentWire = encode_intent(IntentState('restarted-card', 1, now, now,
            now + 200_000_000, True, False, False, True, True))
          kwargs['intent_message'] = message
        elif case == 'invalid_then_fresh':
          raw = sd.sm['aolIntentWire']
          sd.aol_intent_companion._observe(sd.sm.logMonoTime['aolIntentWire'], False, raw)
        elif case == 'cp_change':
          cp = sd.CP.as_builder()
          cp.safetyConfigs[-1].safetyParam ^= 1
          sd.CP = cp.as_reader()
        else:
          sd.aol_session_id = 'restarted-selfdrived'
        axis, _, command = step(9, **kwargs)
        self.assertEqual(str(axis.faultReason), 'critical')
        self.assertTrue(sd.aol_authority_lost)
        self.assertFalse(axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
        self.assertFalse(step(10, native_request_long=False)[0].lateralActive)

  def test_actual_card_feedback_pairing_both_orders_and_fixed_deadline(self):
    for order in ('axis_first', 'event_first', 'together', 'missing_event', 'missing_axis',
                  'renew_event', 'unknown', 'critical', 'wrong_session', 'restart', 'card_restart',
                  'mixed_fault', 'disabled', 'can_timeout'):
      with self.subTest(order=order), OpenpilotPrefix():
        sd, _, step, card, feedback = self._transport_callers()
        step(0)
        feedback(10_000_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        step(2)
        feedback(10_020_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        step(4, timeout=True)
        feedback(10_040_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        step(6, timeout=True)
        now = 10_060_000_000
        proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
        # The actual last retained-source publication is 20 ms old. Deliver
        # these real publisher envelopes separately in either socket order.
        if order == 'axis_first':
          self.assertFalse(feedback(now, axis=proof))
          self.assertFalse(card.aol_transport_feedback.qualified)
          self.assertFalse(feedback(now, event=event))
        elif order == 'event_first':
          self.assertFalse(feedback(now, event=event))
          self.assertTrue(card.aol_transport_feedback.awaiting)
          self.assertFalse(feedback(now, axis=proof))
        elif order == 'together':
          self.assertFalse(feedback(now, axis=proof, event=event))
        elif order in ('missing_event', 'missing_axis', 'renew_event'):
          self.assertFalse(feedback(now, axis=proof if order == 'missing_event' else None,
                                    event=event if order != 'missing_event' else None))
          step(9, native_request_lat=False, native_request_long=False)
          renewed = sd.pm.messages['aolAxisState']
          next_event = event
          if order == 'renew_event':
            next_event = event.as_builder()
            next_event.logMonoTime = now + 10_000_000
            next_event = next_event.as_reader()
          # Event occurrences and fresh axis streams cannot renew a pending
          # exact pair beyond the first occurrence's original 30 ms deadline.
          self.assertTrue(feedback(now + 31_000_000, axis=renewed if order == 'missing_event' else None,
                                   event=next_event if order == 'renew_event' else None))
          self.assertFalse(card.aol_card_intent.allowed_latch)
          self.assertTrue(feedback(now + 32_000_000, axis=proof, event=event))
          continue
        else:
          if order == 'can_timeout':
            cs = car_state()
            cs.canTimeout = True
            self.assertTrue(feedback(now, axis=proof, event=event, state=cs))
          elif order == 'card_restart':
            from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
            card.aol_transport_feedback = TransportPauseFeedback(card.CP)
            self.assertTrue(feedback(now, axis=proof, event=event))
          elif order == 'mixed_fault':
            faults = Events()
            faults.add(log.OnroadEvent.EventName.controlsMismatch)
            faults.add(log.OnroadEvent.EventName.relayMalfunction)
            mixed = messaging.new_message('onroadEvents', len(faults), valid=True, logMonoTime=now)
            mixed.onroadEvents = faults.to_msg()
            self.assertTrue(feedback(now, axis=proof, event=mixed.as_reader()))
          else:
            altered = proof.as_builder()
            if order in ('unknown', 'critical'):
              altered.aolAxisState.faultReason = order
            elif order in ('wrong_session', 'restart'):
              altered.aolAxisState.faultSessionId = 'wrong'
              if order == 'restart':
                altered.aolAxisState.sessionId = 'wrong'
            else:
              card.aol_card_intent.settings = AolSettings(False, 0., 9, 0, (0, 0, 0), (0, 0, 0))
            fault = feedback(now, axis=altered.as_reader(), event=event)
            if order != 'disabled':
              self.assertTrue(fault)
          self.assertFalse(card.aol_card_intent.allowed_latch)
          continue
        self.assertTrue(card.aol_card_intent.allowed_latch)
        self.assertTrue(card.aol_transport_feedback.qualified)

  def test_actual_card_unknown_and_missing_native_after_healthy_baseline_disarm(self):
    for failure in ('unknown', 'source_zero', 'ack_false', 'native_missing', 'native_deny', 'panda_rx'):
      with self.subTest(failure=failure), OpenpilotPrefix():
        sd, _, step, card, feedback = self._transport_callers()
        step(0)
        feedback(10_000_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        kwargs = {'native_valid': False} if failure == 'native_missing' else {'allowed': False} if failure == 'native_deny' else {
          'panda_fault': True} if failure == 'panda_rx' else {}
        step(1, **kwargs)
        proof = sd.pm.messages['aolAxisState']
        if failure in ('unknown', 'source_zero', 'ack_false'):
          proof = proof.as_builder()
          if failure == 'unknown':
            proof.aolAxisState.faultReason = 'unknown'
          elif failure == 'source_zero':
            proof.aolAxisState.sourceCarStateMonoTime = 0
          else:
            proof.aolAxisState.nativeAcknowledged = False
          proof = proof.as_reader()
        self.assertTrue(feedback(10_010_000_000, axis=proof, event=sd.pm.messages['onroadEvents']))
        self.assertFalse(card.aol_card_intent.allowed_latch)

  def test_transport_pause_user_intent_off_is_honored_without_resuming_long(self):
    with OpenpilotPrefix():
      sd, _, step, card, feedback = self._transport_callers()
      step(0)
      feedback(10_000_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      step(8, timeout=True)
      feedback(10_080_000_000, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      axis, _, command = step(9, latched=False, native_request_long=False)
      self.assertFalse(sd.aol_transport_pause.active or sd.enabled or sd.active)
      self.assertFalse(axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
      self.assertFalse(sd.aol_authority_lost)
      cs = car_state()
      from dataclasses import replace
      card.aol_card_intent.settings = replace(card.aol_card_intent.settings, cancel_actions=(3, 0, 0))
      cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
      card.aol_card_intent.update(cs, now_ns=10_090_000_000)
      self.assertFalse(card.aol_card_intent.allowed_latch)

  def _assert_withdrawal_alert(self, controls, message):
    from openpilot.starpilot.audio.axis_alerts import AxisAlerts
    from openpilot.starpilot.ui.runtime_snapshot import current_alert
    now = int(message.logMonoTime)
    alert = current_alert(controls.sm, now, after_frame=0)
    self.assertTrue(alert.critical)
    self.assertEqual(alert.size, 'full')
    self.assertEqual(alert.text1, 'TAKE CONTROL IMMEDIATELY')
    self.assertEqual(AxisAlerts().update(controls.sm, int(message.selfdriveState.alertSound.raw), now),
                     log.SelfdriveState.AudibleAlert.warningImmediate)

  def test_actual_callers_companion_loss_disables_and_requires_intent_withdrawal(self):
    for full_op in (False, True):
      for loss in ('car_state', 'intent', 'authority_companion', 'wrong_stamp', 'wrong_source', 'invalid'):
        with self.subTest(full_op=full_op, loss=loss), OpenpilotPrefix():
          sd, controls, step = self._authority_callers(full_op)
          axis, _, command = step(0)
          self.assertTrue(axis.lateralActive and axis.nativeAcknowledged and command.latActive)
          self.assertAlmostEqual(command.actuators.torque, 0.)
          retained, _, command = step(2, timeout=True)
          self.assertTrue(retained.lateralActive and command.latActive)
          self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
          self.assertEqual(retained.sourceCarStateMonoTime, 10_000_000_000)
          axis, state, command = step(7, timeout=loss == 'car_state', old_intent=loss == 'intent', companion=False if loss == 'authority_companion' else loss)
          self.assertFalse(sd.enabled or sd.active or axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
          self.assertIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
          self.assertEqual(state.alertText1, 'TAKE CONTROL IMMEDIATELY')
          self.assertEqual(state.alertSound, log.SelfdriveState.AudibleAlert.warningImmediate)
          self.assertFalse(controls.pm.messages['carControl'].carControl.latActive)
          self._assert_withdrawal_alert(controls, sd.pm.messages['selfdriveState'])
          self.assertEqual(axis.sourceCarStateMonoTime, 0 if loss == 'car_state' else 10_070_000_000)
          self.assertFalse(sd.pm.messages['aolAxisState'].valid if loss == 'car_state' else axis.lateralActive)
          self.assertFalse(step(8)[0].lateralActive)  # Fresh input alone cannot restart the latched axis.
          self.assertFalse(step(9, latched=False)[0].lateralActive)
          self.assertTrue(step(10)[0].lateralActive)  # Intent owner supplies its fresh rearm after withdrawal.

  def test_actual_callers_fresh_native_denial_preserves_deliberate_pauses(self):
    for full_op in (False, True):
      for reason in ('denial', 'pause', 'brake', 'steering', 'gas', 'user_disable', 'startup',
                     *(('long_denial', 'long_pause') if full_op else ())):
        with self.subTest(full_op=full_op, reason=reason), OpenpilotPrefix():
          sd, controls, step = self._authority_callers(full_op)
          if reason != 'startup':
            self.assertTrue(step(0)[0].lateralActive)
          axis, state, command = step(1, allowed=reason in ('long_denial', 'long_pause'), pause=reason == 'pause',
                                     long_allowed=reason not in ('long_denial', 'long_pause'), pause_long=reason == 'long_pause',
                                     brake=reason == 'brake', steering=reason == 'steering',
                                     gas=reason == 'gas', user_disable=reason == 'user_disable')
          self.assertEqual(bool(axis.lateralActive), reason == 'long_pause')
          self.assertEqual(bool(command.latActive), reason == 'long_pause')
          if reason in ('denial', 'long_denial'):
            self.assertFalse(sd.enabled or sd.active)
            self.assertEqual(state.alertText2, 'Controls Mismatch')
            self.assertEqual(state.alertSound, log.SelfdriveState.AudibleAlert.warningImmediate)
            self.assertTrue(sd.aol_authority_lost)
            self._assert_withdrawal_alert(controls, sd.pm.messages['selfdriveState'])
          else:
            self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
            self.assertFalse(sd.aol_authority_lost)

  def test_actual_eps_monitor_fault_reaches_both_axis_callers(self):
    from opendbc.can import CANPacker
    from opendbc.car import Bus
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.tests.test_ascm_intercept import TestAscmIntercept
    from opendbc.car.gm.values import DBC
    for full_op in (False, True):
      with self.subTest(full_op=full_op), OpenpilotPrefix():
        sd, _, step = self._authority_callers(full_op)
        ci = CarInterface(sd.CP)
        packer = CANPacker(DBC[sd.CP.carFingerprint][Bus.pt])
        producer = TestAscmIntercept()
        for tick in range(20):
          producer.authority_frame(ci, packer, tick, torque=0.)
        healthy = producer.authority_frame(ci, packer, 20, torque=0.)
        self.assertTrue(healthy.canValid and not healthy.canTimeout)
        self.assertTrue(ci.CS.steering_authority.seen_active)
        self.assertFalse(healthy.steerFaultTemporary)
        self.assertTrue(step(0, observed_state=healthy)[0].lateralActive)
        for tick in range(21, 30):
          producer.authority_frame(ci, packer, tick, torque=0.)
        loss = producer.authority_frame(ci, packer, 30, status=0, torque=0.)
        self.assertTrue(loss.canValid and not loss.canTimeout)
        self.assertTrue(loss.steerFaultTemporary)
        axis, state, command = step(1, observed_state=loss, authority_unavailable=ci.CS.steering_authority.latched)
        self.assertFalse(axis.lateralActive or command.latActive)
        self.assertEqual(state.alertText2, 'Controls Mismatch')
        self.assertEqual(state.alertSound, log.SelfdriveState.AudibleAlert.warningImmediate)
        self.assertFalse(sd.enabled or sd.active)
        self.assertIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
        for tick in range(31, 40):
          producer.authority_frame(ci, packer, tick, status=0, torque=0.)
        recovered_status = producer.authority_frame(ci, packer, 40, torque=0.)
        self.assertTrue(recovered_status.canValid and not recovered_status.canTimeout)
        self.assertTrue(recovered_status.steerFaultTemporary)
        self.assertFalse(step(2, observed_state=recovered_status, authority_unavailable=ci.CS.steering_authority.latched)[0].lateralActive)
        # A fresh physical status2 is ordinary temporary EPS limitation, not the monitor's latched loss.
        sd, _, step = self._authority_callers(full_op)
        ci = CarInterface(sd.CP)
        for tick in range(20):
          producer.authority_frame(ci, packer, tick, torque=0.)
        healthy = producer.authority_frame(ci, packer, 20, torque=0.)
        self.assertTrue(healthy.canValid and not healthy.canTimeout)
        self.assertTrue(step(0, observed_state=healthy)[0].lateralActive)
        for tick in range(21, 30):
          producer.authority_frame(ci, packer, tick, torque=0.)
        temporary = producer.authority_frame(ci, packer, 30, status=2, torque=0.)
        self.assertTrue(temporary.canValid and not temporary.canTimeout)
        self.assertFalse(ci.CS.steering_authority.latched)
        axis, state, command = step(1, observed_state=temporary)
        self.assertFalse(axis.lateralActive or command.latActive)
        self.assertEqual(state.alertSound, log.SelfdriveState.AudibleAlert.prompt)
        self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)

  def test_car_state_timeout_retains_only_original_fresh_valid_sample(self):
    sd = SelfdriveD.__new__(SelfdriveD)
    sd.car_state_sock = object()
    sd.CS_prev = car_state()
    sd.aol_car_state_log_ns = 0
    sd.conditional_car_state_valid = False
    sd.initialized = True
    sd.enabled = False
    self.enterContext(mock.patch.object(sd, 'sm', SimpleNamespace(update=mock.Mock()), create=True))
    stamp = 1_000_000_000
    message = messaging.new_message('carState')
    message.logMonoTime = stamp
    message.valid = True
    message.carState = sd.CS_prev
    with (mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=message) as recv,
          mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=stamp) as now):
      sd.CS_prev = sd.data_sample()
      self.assertEqual(sd.aol_car_state_log_ns, stamp)
      recv.return_value = None  # 20 ms socket timeout, still inside the existing 30 ms lease.
      now.return_value = stamp + 23_000_000
      self.assertIs(sd.data_sample(), sd.CS_prev)
      self.assertEqual(sd.aol_car_state_log_ns, stamp)
      self.assertTrue(sd.conditional_car_state_valid)
      now.return_value = stamp + 30_000_001
      sd.data_sample()
      self.assertEqual(sd.aol_car_state_log_ns, 0)
      self.assertFalse(sd.conditional_car_state_valid)

      # A next-drive/new process has no retained source, even if CS_prev exists.
      sd.data_sample()
      self.assertEqual(sd.aol_car_state_log_ns, 0)
      message.logMonoTime = stamp + 24_000_000
      recv.return_value = message
      sd.CS_prev = sd.data_sample()
      self.assertEqual(sd.aol_car_state_log_ns, stamp + 24_000_000)
      for invalid_kind in ('valid', 'canValid', 'canTimeout', 'clock'):
        with self.subTest(invalid_kind=invalid_kind):
          message.valid = invalid_kind != 'valid'
          message.carState.canValid = invalid_kind != 'canValid'
          message.carState.canTimeout = invalid_kind == 'canTimeout'
          recv.return_value = message
          sd.CS_prev = sd.data_sample()
          recv.return_value = None
          now.return_value = message.logMonoTime + (-1 if invalid_kind == 'clock' else 20_000_000)
          sd.data_sample()
          self.assertEqual(sd.aol_car_state_log_ns, 0)
          self.assertFalse(sd.conditional_car_state_valid)

  def test_lateral_only_receipt_loss_keeps_take_control_alert(self):
    class SM:
      seen = {'aolIntentWire': False}
      frame = 0

      def __getitem__(self, service):
        if service == 'deviceState':
          return SimpleNamespace(started=True, startedMonoTime=1)
        if service == 'driverMonitoringState':
          return SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False)
        if service == 'extrinsicsCalibration':
          return SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated)
        raise KeyError(service)

      def all_checks(self, _services):
        return True

    sd = SelfdriveD.__new__(SelfdriveD)
    sd.CP = car.CarParams.new_message(passive=False, openpilotLongitudinalControl=True)
    self.enterContext(mock.patch.object(sd, 'sm', SM(), create=True))
    sd.events = Events()
    sd.state_machine = StateMachine()
    sd.AM = AlertManager()
    from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown
    sd.switchback_capable = False
    sd.switchback_cooldown = SwitchbackCooldown()
    sd.switchback_setting_ns = time.monotonic_ns()
    sd.switchback_cooldown_ns = 300_000_000_000
    sd.personality = 1
    sd.is_metric = False
    sd.enabled = sd.active = False
    sd.initialized = True
    sd.aol_replay = True
    sd.axis_transport_required = sd.aol_replay
    sd.aol_car_state_log_ns = 0
    sd.conditional_car_state_valid = True
    sd.aol_session_id = 'drive-session'
    sd.aol_axis_decision = AxisDecision()
    sd.aol_dm_lateral_inhibit = False
    sd.aol_authority_lost = False
    sd.aol_settings = None
    sd.nostalgia_paddle_cancel = False
    self.enterContext(mock.patch.object(sd, 'data_sample', car_state))
    self.enterContext(mock.patch.object(sd, 'update_events', lambda _cs: sd.events.clear()))
    sd.update_conditional_mode = mock.Mock()
    sd.publish_selfdriveState = mock.Mock()
    sd.publish_axis_proof = mock.Mock()

    native = SimpleNamespace(requestedLateral=True, requestedLongitudinal=False,
                             lateralAllowed=True, longitudinalAllowed=False)
    intent_ns = time.monotonic_ns()
    intent = IntentState('alert-card', 1, intent_ns, intent_ns, intent_ns + 200_000_000,
                         True, False, False, True, True)
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.current_intent', return_value=intent), \
         mock.patch('openpilot.selfdrive.selfdrived.selfdrived.current_native', side_effect=(None, native, None, None)):
      sd.sm.frame = 1
      sd.step()
      self.assertEqual(sd.aol_axis_decision.mode, 'off')
      self.assertFalse(sd.enabled)
      self.assertNotIn(ET.IMMEDIATE_DISABLE, sd.state_machine.current_alert_types)
      self.assertNotEqual(sd.AM.current_alert.alert_text_1, 'TAKE CONTROL IMMEDIATELY')

      sd.sm.frame = 2
      sd.step()
      self.assertEqual(sd.aol_axis_decision.mode, 'lateralOnly')
      self.assertFalse(sd.enabled)

      sd.sm.frame = 3
      sd.step()
      self.assertEqual(sd.aol_axis_decision.mode, 'off')
      self.assertFalse(sd.enabled)
      self.assertIn(ET.IMMEDIATE_DISABLE, sd.state_machine.current_alert_types)
      self.assertEqual(sd.AM.current_alert.alert_text_1, 'TAKE CONTROL IMMEDIATELY')
      self.assertEqual(sd.AM.current_alert.alert_text_2, 'Controls Mismatch')
      alert_end_frame = sd.AM.alerts['controlsMismatch/immediateDisable'].end_frame
      self.assertGreater(alert_end_frame, sd.sm.frame)

      sd.sm.frame = 4
      sd.step()
      self.assertNotIn(ET.IMMEDIATE_DISABLE, sd.state_machine.current_alert_types)
      self.assertEqual(sd.AM.current_alert.alert_text_1, 'TAKE CONTROL IMMEDIATELY')
      self.assertEqual(sd.AM.alerts['controlsMismatch/immediateDisable'].end_frame, alert_end_frame)

  def test_lateral_only_temporary_fault_uses_existing_orange_warning(self):
    class SM:
      seen = {'aolIntentWire': False}
      frame = 1

      def __getitem__(self, service):
        if service == 'deviceState':
          return SimpleNamespace(started=True, startedMonoTime=1)
        if service == 'driverMonitoringState':
          return SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False)
        if service == 'extrinsicsCalibration':
          return SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated)
        raise KeyError(service)

      def all_checks(self, _services):
        return True

    sd = SelfdriveD.__new__(SelfdriveD)
    sd.CP = car.CarParams.new_message(passive=False, openpilotLongitudinalControl=True)
    self.enterContext(mock.patch.object(sd, 'sm', SM(), create=True))
    sd.events, sd.state_machine, sd.AM = Events(), StateMachine(), AlertManager()
    from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown
    sd.switchback_capable = False
    sd.switchback_cooldown = SwitchbackCooldown()
    sd.switchback_setting_ns = time.monotonic_ns()
    sd.switchback_cooldown_ns = 300_000_000_000
    sd.personality, sd.is_metric = 1, False
    sd.enabled = sd.active = False
    sd.initialized = sd.aol_replay = True
    sd.axis_transport_required = sd.aol_replay
    sd.aol_car_state_log_ns = 0
    sd.conditional_car_state_valid = True
    sd.aol_session_id = 'drive-session'
    sd.aol_axis_decision = AxisDecision()
    sd.aol_dm_lateral_inhibit = False
    sd.aol_authority_lost = False
    sd.aol_settings = None
    sd.nostalgia_paddle_cancel = False
    state = car_state()
    state.steerFaultTemporary = True
    self.enterContext(mock.patch.object(sd, 'data_sample', return_value=state))
    self.enterContext(mock.patch.object(sd, 'update_events', lambda _cs: sd.events.clear()))
    sd.update_conditional_mode = mock.Mock()
    sd.publish_selfdriveState = mock.Mock()
    sd.publish_axis_proof = mock.Mock()
    native = SimpleNamespace(requestedLateral=False, requestedLongitudinal=False,
                             lateralAllowed=False, longitudinalAllowed=False)
    intent_ns = time.monotonic_ns()
    intent = IntentState('alert-card', 1, intent_ns, intent_ns, intent_ns + 200_000_000,
                         True, False, False, True, True)
    with (mock.patch('openpilot.selfdrive.selfdrived.selfdrived.current_intent', return_value=intent),
          mock.patch('openpilot.selfdrive.selfdrived.selfdrived.current_native', return_value=native)):
      sd.step()
      self.assertFalse(sd.enabled)
      self.assertFalse(sd.aol_axis_decision.desired_lateral)
      self.assertFalse(sd.aol_axis_decision.lateral_active)
      self.assertEqual(sd.AM.current_alert.alert_type, 'steerTempUnavailableSilent/warning')
      self.assertEqual(sd.AM.current_alert.alert_status, log.SelfdriveState.AlertStatus.userPrompt)
      self.assertEqual(sd.AM.current_alert.alert_text_1, 'Steering Assist Temporarily Unavailable')
      # A hard driver override puts Honda EPS in NO_TORQUE_ALERT_1; the driver
      # is already steering, so the warning waits until they let go.
      state.steeringPressed = True
      sd.sm.frame += 1
      sd.step()
      self.assertNotIn(log.OnroadEvent.EventName.steerTempUnavailableSilent, sd.events.names)
      state.steeringPressed = False
      sd.sm.frame += 1
      sd.step()
      self.assertIn(log.OnroadEvent.EventName.steerTempUnavailableSilent, sd.events.names)
      state.steerFaultTemporary = False
      native.requestedLateral = native.lateralAllowed = True
      sd.sm.frame += 1
      sd.step()
      self.assertTrue(sd.aol_axis_decision.lateral_active)
      self.assertNotIn(log.OnroadEvent.EventName.steerTempUnavailableSilent, sd.events.names)

  def test_native_bootstrap_scope_preserves_finalized_bolt_admission(self):
    from opendbc.car.gm.aol import native_bootstrap_supported
    from opendbc.car.gm.tests.test_bolt_cc import params as bolt_params
    from opendbc.car.gm.values import CAR
    from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

    for identity in (CAR.CHEVROLET_BOLT_CC_2017, CAR.CHEVROLET_BOLT_CC_2018_2021,
                     CAR.CHEVROLET_BOLT_CC_2022_2023, CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL):
      with self.subTest(identity=identity):
        cp = bolt_params(identity, present=True)
        self.assertTrue(native_bootstrap_supported(cp))
        removed = bolt_params(identity, present=True, removed=True)
        self.assertTrue(native_bootstrap_supported(removed))
        disabled = cp.as_reader().as_builder()
        VehicleStartupPreferences(disable_bolt_long=True).prepare(disabled)
        self.assertEqual(native_bootstrap_supported(disabled), identity != CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL)
        for field, value in (('passive', True), ('notCar', True), ('dashcamOnly', True),
                             ('carFingerprint', CAR.CHEVROLET_VOLT_CC)):
          denied = cp.as_reader().as_builder()
          setattr(denied, field, value)
          self.assertFalse(native_bootstrap_supported(denied))

  def _deferred_step(self, card, receive):
    batch = []
    calls = [0]

    def state_update(*, defer_radar=False):
      self.assertTrue(defer_radar)
      calls[0] += 1
      card._deferred_radar_can = batch
      return receive()

    def radar_update(value):
      self.assertIs(value, batch)
      return None

    radar = self.enterContext(mock.patch.object(card.RI, 'update', side_effect=radar_update))
    self.addCleanup(lambda: self.assertEqual(radar.call_count, calls[0]))
    return mock.patch.object(card, 'state_update', side_effect=state_update)

  def test_gm_startup_has_no_prepublication_transaction_owner(self):
    from opendbc.car.gm.interface import CarInterface
    from openpilot.starpilot.aol.tests.test_gm import _configurations
    from openpilot.starpilot.vehicle_startup import VehicleStartupOwner

    # GM's real factory has no HKG-style prepublication transaction. Injecting
    # an arbitrary owner into an already constructed GM Card is not this path.
    self.assertIsNone(getattr(CarInterface, 'startup_owner', None))
    for cp in _configurations():
      with self.subTest(identity=cp.carFingerprint, safety=cp.safetyConfigs[0].safetyParam):
        holder = VehicleStartupOwner()
        recv, send, admission = mock.Mock(), mock.Mock(), mock.Mock()
        result = holder.prepare(cp, CarInterface, (recv, send), requested=True,
                                admission=admission, defer_until_controls=True)
        self.assertIs(result, cp)
        self.assertIsNone(holder.owner)
        recv.assert_not_called()
        send.assert_not_called()
        admission.assert_not_called()

  def test_initialization_waits_for_current_matching_native_receipt(self):
    from dataclasses import replace
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR

    from openpilot.starpilot.aol.tests.test_gm import TestGmAol

    with OpenpilotPrefix(), mock.patch.dict('os.environ', {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      fingerprint = gen_empty_fingerprint()
      fingerprint[0].update({0x201: 6, 0x142: 8})
      fingerprint[2].update({0x320: 8, 0x180: 4})
      factory_cp = CarInterface.get_params(CAR.CHEVROLET_BOLT_CC_2018_2021, fingerprint, [], True, False, False)
      selected = TestGmAol.card(factory_cp, settings)
      cp = selected.CP.as_reader()
      self.assertFalse(settings.get_bool('ControlsReady'))
      self.assertTrue(settings.get_bool('FirmwareQueryDone'))
      now = 4_000_000_000
      cc = car.CarControl.new_message()
      cc.actuators.curvature = 0.01  # Real inactive Controls telemetry need not be zero.
      boot_events = Events()
      boot_events.add(log.OnroadEvent.EventName.selfdriveInitializing)

      class BootstrapSM(dict):
        seen = {'onroadEvents': True, 'carControl': True, 'pandaStates': False}
        valid = {'carControl': True, 'pandaStates': False}
        alive = {'carControl': True, 'pandaStates': False}
        logMonoTime = {'carControl': now, 'pandaStates': 0}
        recv_time = {'carControl': now / 1e9, 'pandaStates': 0.0}

        def all_alive(self, _services):
          return self.alive['carControl']

      boot_sm = BootstrapSM(onroadEvents=boot_events.to_msg(), carControl=cc, pandaStates=[])
      physical = car_state()
      with (mock.patch.object(selected, 'sm', boot_sm),
            self._deferred_step(selected, lambda: (physical, None)),
            mock.patch.object(selected, 'state_publish'),
            mock.patch.object(selected.CI, 'init', wraps=selected.CI.init) as initialize,
            mock.patch.object(selected.CI, 'apply') as apply,
            mock.patch.object(selected, 'publish_sendcan') as sendcan,
            mock.patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now)):
        for invalid in ('unseen', 'invalid', 'dead', 'old_source', 'future_source', 'old_receipt', 'future_receipt',
                        'enabled', 'lateral', 'longitudinal', 'torque', 'accel', 'nan_torque', 'bad_can', 'can_timeout'):
          with self.subTest(bootstrap_invalid=invalid):
            boot_sm.seen['carControl'] = invalid != 'unseen'
            boot_sm.valid['carControl'] = invalid != 'invalid'
            boot_sm.alive['carControl'] = invalid != 'dead'
            boot_sm.logMonoTime['carControl'] = now - 150_000_001 if invalid == 'old_source' else now + 1 if invalid == 'future_source' else now
            boot_sm.recv_time['carControl'] = (now - 150_000_001 if invalid == 'old_receipt' else now + 1000 if invalid == 'future_receipt' else now) / 1e9
            cc.enabled, cc.latActive, cc.longActive = invalid == 'enabled', invalid == 'lateral', invalid == 'longitudinal'
            cc.actuators.torque = float('nan') if invalid == 'nan_torque' else 0.1 if invalid == 'torque' else 0.0
            cc.actuators.accel = 0.1 if invalid == 'accel' else 0.0
            physical.canValid, physical.canTimeout = invalid != 'bad_can', invalid == 'can_timeout'
            selected.step()
            self.assertFalse(settings.get_bool('ControlsReady'))
            self.assertFalse(selected.ci_initialized)
            initialize.assert_not_called()
            apply.assert_not_called()
            sendcan.assert_not_called()
        physical.canValid, physical.canTimeout = True, False
        cc.enabled = cc.latActive = cc.longActive = False
        cc.actuators.torque = cc.actuators.accel = 0.0
        self.assertIsNone(selected.vehicle_startup.owner)
        for excluded in ('passive', 'volt_transport'):
          with (self.subTest(bootstrap_excluded=excluded),
                mock.patch.object(selected, 'startup_panda_configured', return_value=False),
                mock.patch('openpilot.selfdrive.car.card.clock_pair_ns', return_value=None)):
            selected.CP.passive = excluded == 'passive'
            selected.volt_cc_selected = excluded == 'volt_transport'
            selected.step()
            self.assertFalse(settings.get_bool('ControlsReady'))
            self.assertFalse(selected.ci_initialized)
            initialize.assert_not_called()
            apply.assert_not_called()
            if excluded == 'volt_transport':
              sendcan.assert_called_once_with([], valid=False)
              sendcan.reset_mock()
            else:
              sendcan.assert_not_called()
        selected.CP.passive = False
        selected.vehicle_startup.owner = None
        selected.volt_cc_selected = False
        selected.step()
        self.assertTrue(selected.ci_initialized)
        self._assert_controls_ready(settings)
        initialize.assert_called_once()
        apply.assert_not_called()
        sendcan.assert_not_called()
        selected.step()
        initialize.assert_called_once()
        apply.assert_not_called()
        sendcan.assert_not_called()
      # Actual Card wrote both prerequisites consumed by PandaSafety::fetchCarParams.
      with car.CarParams.from_bytes(settings.get('CarParams')) as configured_cp:
        self.assertEqual(configured_cp.safetyConfigs[0].safetyParam, cp.safetyConfigs[0].safetyParam)
        self.assertEqual(configured_cp.alternativeExperience, cp.alternativeExperience)
    self.assertEqual((cp.safetyConfigs[0].safetyParam, cp.alternativeExperience, cp.flags), (0x9D, 32, 17))
    self.assertTrue(cp.openpilotLongitudinalControl)
    self.assertFalse(cp.pcmCruise)
    now = 4_000_000_000
    native = SafetyState(1, True, now, now + 200_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
                         int(cp.safetyConfigs[0].safetyParam), False, False, False, False, 'panda', 'drive-session')

    class SM(dict):
      frame = 1
      valid = {'aolSafetyWire': True, 'aolIntentWire': False}
      alive = {'aolSafetyWire': True, 'aolIntentWire': False}
      seen = {'aolSafetyWire': False, 'aolIntentWire': False}
      logMonoTime = {'aolSafetyWire': now}
      freq_ok = {'aolSafetyWire': True}

      def update(self, *args, **kwargs):
        pass

      def all_checks(self, _services=None):
        return True

    for aol in (True, False):
      with self.subTest(aol=aol):
        sd = SelfdriveD.__new__(SelfdriveD)
        sd.CP = cp
        sm = SM(aolSafetyWire=b'', pandaStates=[],
                driverMonitoringState=SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False),
                extrinsicsCalibration=SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated))
        sm.ignore_alive, sm.ignore_valid = set(), set()
        sm.seen = dict(SM.seen)
        self.enterContext(mock.patch.object(sd, 'sm', sm, create=True))
        sd.car_state_sock = object()
        sd.CS_prev = car_state()
        sd.aol_car_state_log_ns = 0
        sd.conditional_car_state_valid = False
        sd.initialized = sd.enabled = sd.active = False
        sd.aol_replay, sd.ordinary_axis_ack_required = aol, not aol
        sd.axis_transport_required = True
        sd.aol_session_id = 'drive-session'
        sd.aol_axis_decision = AxisDecision()
        sd.aol_dm_lateral_inhibit = False
        sd.aol_authority_lost = False
        sd.aol_settings = None
        sd.nostalgia_paddle_cancel = False
        sd.events, sd.state_machine = Events(), StateMachine()
        sd.update_alerts = mock.Mock()
        sd.update_conditional_mode = mock.Mock()
        sd.publish_selfdriveState = mock.Mock()
        sd.publish_axis_proof = mock.Mock()
        self.enterContext(mock.patch.object(sd, 'update_events', lambda _cs, sd=sd: sd.events.clear()))
        message = messaging.new_message('carState')
        message.valid, message.logMonoTime, message.carState = True, now, sd.CS_prev
        with (mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=message),
              empty_carstate_queue(sd.car_state_sock),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=now),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.SIMULATION', False),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.VisionIpcClient',
                         SimpleNamespace(available_streams=lambda *_args, **_kwargs: [])),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.cloudlog.event')):
          for receipt in (None, replace(native, observedMonoTime=now - 250_000_000, validUntilMonoTime=now - 1),
                          replace(native, axisSessionId='old-session'), replace(native, safetyParam=native.safetyParam + 1)):
            sm.seen['aolSafetyWire'] = receipt is not None
            sm['aolSafetyWire'] = encode_safety(receipt) if receipt else b''
            sd.step()  # Actual data_sample and strict current_native decoding remain in this path.
            self.assertFalse(sd.initialized)
            self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
            self.assertEqual(sd.aol_axis_decision.mode, 'off')
          sm['aolSafetyWire'] = encode_safety(native)
          sd.step()
          self.assertTrue(sd.initialized)
          self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
          self.assertEqual(sd.aol_axis_decision.mode, 'off')  # Zero-axis native acknowledgment grants no actuation.
          sm.seen['aolSafetyWire'] = False
          sd.step()
          self.assertIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
          self.assertTrue(sd.events.contains(ET.IMMEDIATE_DISABLE))
          sd.initialized = False
          sm.frame = 601
          sd.step()
          self.assertTrue(sd.initialized)  # Existing six-second timeout still exposes a genuine missing receipt.
          self.assertIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)

  def test_selected_volt_camera_bootstrap_waits_for_native(self):
    self._assert_sibling_bootstrap(transport=False)

  def test_selected_ordinary_cc_bootstrap_preserves_physical_clock_floor(self):
    self._assert_sibling_bootstrap(transport=True)

  def _assert_controls_ready(self, settings):
    deadline = time.monotonic() + 1.
    while not settings.get_bool('ControlsReady') and time.monotonic() < deadline:
      time.sleep(.001)
    self.assertTrue(settings.get_bool('ControlsReady'))

  def _assert_sibling_bootstrap(self, *, transport):
    from dataclasses import replace
    from opendbc.can import CANPacker
    from opendbc.car import Bus
    from opendbc.car.gm.aol import qualified_gm
    from opendbc.car.gm.ordinary_cc import control_transport_required
    from opendbc.car.gm.tests.test_cc_gateway_stock import params as cc_params
    from opendbc.car.gm.tests.test_ordinary_cc import qualified_frames
    from opendbc.car.gm.tests.test_volt_camera_control import camera_params, feed_camera
    from opendbc.car.gm.values import CAR, DBC
    from openpilot.starpilot.aol.tests.test_gm import TestGmAol

    factory_cp = cc_params(CAR.CADILLAC_CT6_CC) if transport else camera_params(alpha=False)
    self.assertEqual(factory_cp.safetyConfigs[0].safetyParam, 0xC160 if transport else 5)
    self.assertEqual(control_transport_required(factory_cp), transport)
    self.assertTrue(qualified_gm(factory_cp))
    now = [4_000_000_000]
    offset = 4_000_000_000
    pair_missing, hold_main = [False], [False]
    packer = CANPacker(DBC[factory_cp.carFingerprint][Bus.pt])
    cc = car.CarControl.new_message()
    cc.actuators.curvature = .01
    boot_events = Events()
    boot_events.add(log.OnroadEvent.EventName.selfdriveInitializing)
    device = messaging.new_message('deviceState').deviceState
    device.started, device.startedMonoTime = True, 1_000_000_000

    class BootstrapSM(dict):
      def all_alive(self, services):
        return all(self.alive[s] for s in services)

    sm = BootstrapSM(onroadEvents=boot_events.to_msg(), carControl=cc, deviceState=device, pandaStates=[])
    sm.seen = {'onroadEvents': True, 'carControl': True, 'deviceState': True, 'pandaStates': False}
    sm.valid = {'carControl': True, 'deviceState': True, 'pandaStates': False}
    sm.alive = dict(sm.valid)
    sm.logMonoTime, sm.recv_time = {'pandaStates': 0}, {'pandaStates': 0.0}

    def refresh():
      # IPC receipt follows production by 1us; float seconds need not round-trip ns.
      sm.logMonoTime.update(carControl=now[0] - 1_000, deviceState=now[0] - 1_000)
      sm.recv_time.update(carControl=now[0] / 1e9, deviceState=now[0] / 1e9)
      for service in ('carControl', 'deviceState'):
        self.assertLessEqual(sm.logMonoTime[service], int(sm.recv_time[service] * 1e9))
        self.assertLessEqual(int(sm.recv_time[service] * 1e9), now[0])

    def parse_state(selected):
      stamp = now[0] + offset
      if transport:
        esp = packer.make_can_msg('ESPStatus', 0, {'TractionControlOn': 1})
        frames = [f for f in qualified_frames(packer, (stamp // 10_000_000) % 4) if f[0] not in (esp[0], 0x3D1)]
        frames.append(esp)
        frames.append(packer.make_can_msg('ECMCruiseControl', 0, {'CruiseActive': 0, 'CruiseSetSpeed': 80}))
        if hold_main[0]:
          frames = [f for f in frames if f[0] != 0xC9]
        selected.CI.update([(stamp - 1_000_000, frames)])
        physical = selected.CI.update([(stamp, frames)])
      else:
        physical, _ = feed_camera(selected.CI, packer, stamp, active=False)
      self.assertTrue(physical.canValid)
      self.assertFalse(physical.canTimeout)
      self.assertFalse(physical.espDisabled)
      self.assertFalse(physical.brakePressed)
      self.assertEqual(physical.gearShifter, car.CarState.GearShifter.drive)
      return physical, None

    with OpenpilotPrefix(), mock.patch.dict('os.environ', {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', False, block=True)
      normal = TestGmAol.card(factory_cp, settings)
      refresh()
      with (mock.patch.object(normal, 'sm', sm),
            self._deferred_step(normal, lambda: parse_state(normal)),
            mock.patch.object(normal, 'state_publish'),
            mock.patch.object(normal.CI, 'init') as initialize,
            mock.patch.object(normal.CI, 'apply') as apply):
        normal.step()
        self.assertFalse(normal.aol_replay)
        self.assertFalse(normal.ci_initialized)
        self.assertFalse(settings.get_bool('ControlsReady'))
        initialize.assert_not_called()
        apply.assert_not_called()

    with OpenpilotPrefix(), mock.patch.dict('os.environ', {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0'}):
      settings = Params()
      settings.put_bool('AlwaysOnLateral', True, block=True)
      selected = TestGmAol.card(factory_cp, settings)
      self.assertTrue(selected.aol_replay)
      self.assertEqual(selected.volt_cc_selected, transport)
      self.assertIsNone(selected.vehicle_startup.owner)
      with (mock.patch.object(selected, 'sm', sm),
            self._deferred_step(selected, lambda: parse_state(selected)),
            mock.patch.object(selected, 'state_publish'),
            mock.patch.object(selected.CI, 'init', wraps=selected.CI.init) as initialize,
            mock.patch.object(selected.CI, 'apply') as apply,
            mock.patch.object(selected, 'publish_sendcan') as sendcan,
            mock.patch('openpilot.selfdrive.car.card.time.monotonic_ns', side_effect=lambda: now[0]),
            mock.patch('openpilot.selfdrive.car.card.clock_pair_ns',
                       side_effect=lambda: None if pair_missing[0] else (now[0], now[0] + offset))):
        for invalid in ('enabled', 'torque', 'stale_control'):
          refresh()
          cc.enabled = invalid == 'enabled'
          cc.actuators.torque = .1 if invalid == 'torque' else 0.
          if invalid == 'stale_control':
            sm.logMonoTime['carControl'] = now[0] - 150_000_001
          selected.step()
          self.assertFalse(settings.get_bool('ControlsReady'))
          initialize.assert_not_called()
          apply.assert_not_called()
        cc.enabled, cc.actuators.torque = False, 0.
        refresh()
        if transport:
          selected.step()  # Actual paired-clock acquisition establishes the source floor.
          self.assertEqual(selected.volt_cc_source_floor_ns, now[0])
          self.assertFalse(settings.get_bool('ControlsReady'))
          now[0] += 10_000_000
          refresh()
          hold_main[0] = True
          selected.step()  # C9 still at the real floor; all other physical sources are refreshed.
          self.assertFalse(settings.get_bool('ControlsReady'))
          self.assertLessEqual(selected.CI.CS.volt_cc_physical.source_ns[2] - offset, selected.volt_cc_source_floor_ns)
          hold_main[0] = False
          device.started = False
          selected.step()
          self.assertFalse(settings.get_bool('ControlsReady'))
          device.started = True
          sm.logMonoTime['deviceState'] = now[0] - 2_000_000_001
          selected.step()
          self.assertFalse(settings.get_bool('ControlsReady'))
          refresh()
          pair_missing[0] = True
          selected.step()
          self.assertFalse(settings.get_bool('ControlsReady'))
          pair_missing[0] = False
          selected.step()  # Recovery acquires a new floor; cannot admit same-frame sources.
          self.assertFalse(settings.get_bool('ControlsReady'))
          now[0] += 10_000_000
          refresh()
          initialize.assert_not_called()
          self.assertTrue(sendcan.call_args_list)
          self.assertTrue(all(c.args == ([],) and c.kwargs == {'valid': False} for c in sendcan.call_args_list))
        selected.step()
        self.assertTrue(selected.ci_initialized)
        self._assert_controls_ready(settings)
        initialize.assert_called_once()
        apply.assert_not_called()
        self.assertTrue(all(c.args == ([],) and c.kwargs == {'valid': False} for c in sendcan.call_args_list))
        selected.step()
        initialize.assert_called_once()
        apply.assert_not_called()
      cp = selected.CP.as_reader()
      with car.CarParams.from_bytes(settings.get('CarParams')) as configured:
        self.assertEqual(configured.safetyConfigs[0].safetyParam, cp.safetyConfigs[0].safetyParam)
        self.assertEqual(configured.alternativeExperience, cp.alternativeExperience)
      physical = selected.CS_prev

    receipt_now = now[0]
    # Synthetic zero-axis Panda configuration receipt is withheld until actual Card wrote ControlsReady.
    native = SafetyState(1, True, receipt_now, receipt_now + 200_000_000, int(cp.safetyConfigs[0].safetyModel.raw),
                         int(cp.safetyConfigs[0].safetyParam), False, False, False, False, 'panda', 'drive-session')

    class SM(dict):
      frame = 1
      valid = {'aolSafetyWire': True, 'aolIntentWire': False}
      alive = {'aolSafetyWire': True, 'aolIntentWire': False}
      seen = {'aolSafetyWire': False, 'aolIntentWire': False}
      logMonoTime = {'aolSafetyWire': receipt_now}
      freq_ok = {'aolSafetyWire': True}

      def update(self, *args, **kwargs):
        pass

      def all_checks(self, _services=None):
        return True

    for aol in (True,):
      with self.subTest(aol=aol):
        sd = SelfdriveD.__new__(SelfdriveD)
        sd.CP = cp
        sm = SM(aolSafetyWire=b'', pandaStates=[],
                driverMonitoringState=SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False),
                extrinsicsCalibration=SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated))
        sm.ignore_alive, sm.ignore_valid = set(), set()
        sm.seen = dict(SM.seen)
        self.enterContext(mock.patch.object(sd, 'sm', sm, create=True))
        sd.car_state_sock = object()
        sd.CS_prev = physical
        sd.aol_car_state_log_ns = 0
        sd.conditional_car_state_valid = False
        sd.initialized = sd.enabled = sd.active = False
        sd.aol_replay, sd.ordinary_axis_ack_required = aol, not aol
        sd.axis_transport_required = True
        sd.aol_session_id = 'drive-session'
        sd.aol_axis_decision = AxisDecision()
        sd.aol_dm_lateral_inhibit = False
        sd.aol_authority_lost = False
        sd.aol_settings = None
        sd.nostalgia_paddle_cancel = False
        sd.events, sd.state_machine = Events(), StateMachine()
        sd.update_alerts = mock.Mock()
        sd.update_conditional_mode = mock.Mock()
        sd.publish_selfdriveState = mock.Mock()
        sd.publish_axis_proof = mock.Mock()
        self.enterContext(mock.patch.object(sd, 'update_events', lambda _cs, sd=sd: sd.events.clear()))
        message = messaging.new_message('carState')
        message.valid, message.logMonoTime, message.carState = True, receipt_now, sd.CS_prev
        with (mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one', return_value=message),
              empty_carstate_queue(sd.car_state_sock),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=receipt_now),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.SIMULATION', False),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.VisionIpcClient',
                         SimpleNamespace(available_streams=lambda *_args, **_kwargs: [])),
              mock.patch('openpilot.selfdrive.selfdrived.selfdrived.cloudlog.event')):
          for receipt in (None, replace(native, observedMonoTime=receipt_now - 250_000_000, validUntilMonoTime=receipt_now - 1),
                          replace(native, axisSessionId='old-session'), replace(native, safetyParam=native.safetyParam + 1)):
            sm.seen['aolSafetyWire'] = receipt is not None
            sm['aolSafetyWire'] = encode_safety(receipt) if receipt else b''
            sd.step()  # Actual data_sample and strict current_native decoding remain in this path.
            self.assertFalse(sd.initialized)
            self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
            self.assertEqual(sd.aol_axis_decision.mode, 'off')
          sm['aolSafetyWire'] = encode_safety(native)
          sd.step()
          self.assertTrue(sd.initialized)
          self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
          self.assertEqual(sd.aol_axis_decision.mode, 'off')  # Zero-axis native acknowledgment grants no actuation.
          sm.seen['aolSafetyWire'] = False
          sd.step()
          self.assertIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
          self.assertTrue(sd.events.contains(ET.IMMEDIATE_DISABLE))
          sd.initialized = False
          sm.frame = 601
          sd.step()
          self.assertTrue(sd.initialized)  # Existing six-second timeout still exposes a genuine missing receipt.
          self.assertIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)

  def test_native_receipt_gates_engagement_and_disables_on_loss(self):
    class SM:
      seen = {'aolIntentWire': False}
      def __getitem__(self, service):
        if service == 'deviceState':
          return SimpleNamespace(started=True, startedMonoTime=1)
        if service == 'driverMonitoringState':
          return SimpleNamespace(alertLevel=0, lockout=False, alwaysOnLockout=False)
        if service == 'extrinsicsCalibration':
          return SimpleNamespace(calStatus=log.ExtrinsicsCalibration.Status.calibrated)
        raise KeyError(service)

      def all_checks(self, _services):
        return True

    def driver(aol_enabled):
      sd = SelfdriveD.__new__(SelfdriveD)
      sd.CP = car.CarParams.new_message(passive=False, openpilotLongitudinalControl=True)
      self.enterContext(mock.patch.object(sd, 'sm', SM(), create=True))
      sd.events = Events()
      sd.state_machine = StateMachine()
      sd.enabled = sd.active = False
      sd.initialized = True
      sd.aol_replay = aol_enabled
      sd.axis_transport_required = sd.aol_replay
      sd.aol_car_state_log_ns = 0
      sd.conditional_car_state_valid = True
      sd.aol_session_id = 'drive-session'
      sd.aol_axis_decision = AxisDecision()
      sd.aol_dm_lateral_inhibit = False
      sd.aol_authority_lost = False
      sd.aol_settings = None
      sd.nostalgia_paddle_cancel = False
      self.enterContext(mock.patch.object(sd, 'data_sample', car_state))
      sd.update_alerts = mock.Mock()
      sd.update_conditional_mode = mock.Mock()
      sd.publish_selfdriveState = mock.Mock()
      sd.publish_axis_proof = mock.Mock()
      self.enterContext(mock.patch.object(sd, 'update_events',
                                          lambda _cs: (sd.events.clear(), sd.events.add(log.OnroadEvent.EventName.buttonEnable))))
      return sd

    native = SimpleNamespace(requestedLateral=True, requestedLongitudinal=True,
                             lateralAllowed=True, longitudinalAllowed=True)
    intent_ns = time.monotonic_ns()
    intent = IntentState('alert-card', 1, intent_ns, intent_ns, intent_ns + 200_000_000,
                         True, False, False, True, True)
    sd = driver(True)
    sd.initialized = False
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.current_intent', return_value=intent), \
         mock.patch('openpilot.selfdrive.selfdrived.selfdrived.current_native', side_effect=(None, None, native, None)):
      sd.step()
      self.assertFalse(sd.enabled or sd.active)
      self.assertEqual(sd.aol_axis_decision.mode, 'off')
      self.assertNotIn(log.OnroadEvent.EventName.controlsMismatch, sd.events.names)
      self.assertFalse(disarming_fault(sd.events.to_msg(), car_state()))
      sd.publish_selfdriveState.assert_called_once()
      sd.publish_selfdriveState.reset_mock()
      sd.initialized = True
      sd.step()
      self.assertFalse(sd.enabled)
      self.assertTrue(sd.events.contains(ET.NO_ENTRY))
      self.assertEqual(sd.aol_axis_decision.mode, 'off')
      sd.publish_selfdriveState.assert_called_once()  # disabled bootstrap still publishes the zero-axis request
      sd.step()
      self.assertTrue(sd.enabled and sd.active)
      self.assertEqual(sd.aol_axis_decision.mode, 'combined')
      sd.step()
      self.assertFalse(sd.enabled or sd.active)
      self.assertTrue(sd.events.contains(ET.IMMEDIATE_DISABLE))
      alerts = sd.events.create_alerts(sd.state_machine.current_alert_types)
      self.assertTrue(any(alert.alert_text_1 == 'TAKE CONTROL IMMEDIATELY' and
                          alert.alert_text_2 == 'Controls Mismatch' for alert in alerts))

    stock = driver(False)
    with mock.patch('openpilot.selfdrive.selfdrived.selfdrived.current_native') as read_native:
      stock.step()
    read_native.assert_not_called()
    self.assertTrue(stock.enabled and stock.active)

  def test_independent_ack_keeps_unchanged_axis(self):
    state = car_state()
    old_combined = SimpleNamespace(requestedLateral=True, requestedLongitudinal=True,
                                   lateralAllowed=True, longitudinalAllowed=True)
    intent = SimpleNamespace(allowedLatch=True, lateralArmed=True, pauseLateral=False, pauseLongitudinal=False)
    def decide(standard_long, native):
      return decide_axes(standard_lateral=False, standard_longitudinal=standard_long,
                         intent=intent, native=native, car_state=state, initialized=True,
                         model_ready=True, no_entry=False, immediate_disable=False,
                         dm_lockout=False, pause_brake_mps=5.0)
    self.assertEqual(decide(True, old_combined).mode, 'combined')
    self.assertEqual(decide(False, old_combined).mode, 'lateralOnly')
    old_lateral = SimpleNamespace(requestedLateral=True, requestedLongitudinal=False,
                                  lateralAllowed=True, longitudinalAllowed=False)
    self.assertEqual(decide(True, old_lateral).mode, 'lateralOnly')
    intent.pauseLateral = True
    self.assertEqual(decide(True, old_combined).mode, 'longitudinalOnly')
    intent.pauseLateral = False
    intent.pauseLongitudinal = True
    self.assertEqual(decide(True, old_combined).mode, 'lateralOnly')
    intent.pauseLongitudinal = False
    old_long = SimpleNamespace(requestedLateral=False, requestedLongitudinal=True,
                               lateralAllowed=False, longitudinalAllowed=True)
    self.assertEqual(decide(True, old_long).mode, 'longitudinalOnly')

  def test_card_selfdrive_panda_cadence_and_four_modes(self):
    with OpenpilotPrefix():
      messaging.reset_context()
      pm = messaging.PubMaster(['carState', 'aolIntentWire', 'aolAxisState', 'aolSafetyWire'])
      sm = messaging.SubMaster(['carState', 'aolIntentWire', 'aolAxisState', 'aolSafetyWire'])
      base = time.monotonic_ns() - 50_000_000
      from opendbc.car import gen_empty_fingerprint
      from opendbc.car.honda.interface import CarInterface
      from opendbc.car.honda.values import CAR
      from openpilot.starpilot.aol.vehicle import policy_for
      CP = CarInterface.get_params(CAR.HONDA_ACCORD, gen_empty_fingerprint(), [], True, False, False)
      policy = policy_for(CP)
      CP.alternativeExperience |= policy.alternative_experience_addition
      CP.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
      self.assertEqual(CP.safetyConfigs[-1].safetyParam, 34)

      def publish_intent(stamp, *, pause_lat=False, pause_long=False):
        message = messaging.new_message('aolIntentWire', 0)
        message.logMonoTime = stamp
        message.valid = True
        message.aolIntentWire = encode_intent(IntentState(
          'card-session', stamp - base + 1, stamp, stamp, stamp + 100_000_000,
          True, pause_lat, pause_long, True))
        pm.send('aolIntentWire', message)

      def publish_native(stamp, lat, long, session='drive-session'):
        message = messaging.new_message('aolSafetyWire', 0)
        message.logMonoTime = stamp
        message.valid = True
        message.aolSafetyWire = encode_safety(SafetyState(
          1, True, stamp, stamp + 200_000_000, int(car.CarParams.SafetyModel.hondaBosch),
          34, lat, long, lat, long, 'panda', session))
        pm.send('aolSafetyWire', message)

      for index, (standard_long, pause_lat, pause_long, expected) in enumerate((
        (False, False, False, 'lateralOnly'),
        (True, False, False, 'combined'),
        (True, True, False, 'longitudinalOnly'),
        (False, True, False, 'off'),
      )):
        stamp = base + index * 10_000_000
        state_msg = messaging.new_message('carState')
        state_msg.logMonoTime = stamp
        state_msg.valid = True
        pm.send('carState', state_msg)
        publish_intent(stamp, pause_lat=pause_lat, pause_long=pause_long)
        desired_lat = not pause_lat
        desired_long = standard_long and not pause_long
        publish_native(stamp, desired_lat, desired_long)
        sm.update(100)
        intent = current_intent(sm, car_state_ns=stamp, now_ns=stamp + 1_000_000)
        native = current_native(sm, CP, now_ns=stamp + 1_000_000, axis_session_id='drive-session')
        decision = decide_axes(standard_lateral=False, standard_longitudinal=standard_long,
                               intent=intent, native=native, car_state=car_state(),
                               initialized=True, model_ready=True, no_entry=False,
                               immediate_disable=False, dm_lockout=False, pause_brake_mps=5.0)
        self.assertEqual(decision.mode, expected)

      self.assertIsNone(current_native(sm, CP, now_ns=base + 500_000_000, axis_session_id='drive-session'))
      self.assertIsNone(current_native(sm, CP, now_ns=base + 31_000_000, axis_session_id='new-session'))

  def test_no_native_ack_still_requests_but_does_not_actuate(self):
    intent = SimpleNamespace(pauseLateral=False, pauseLongitudinal=False, allowedLatch=True)
    decision = decide_axes(standard_lateral=False, standard_longitudinal=True,
                           intent=intent, native=None, car_state=car_state(), initialized=True,
                           model_ready=True, no_entry=False, immediate_disable=False,
                           dm_lockout=False, pause_brake_mps=5.0)
    self.assertTrue(decision.desired_lateral)
    self.assertTrue(decision.desired_longitudinal)
    self.assertEqual(decision.mode, 'off')
    self.assertFalse(decision.native_acknowledged)


class UiProcessIntentTests(unittest.TestCase):
  def make_card(self):
    from openpilot.selfdrive.car.card import Car
    from openpilot.starpilot.aol.intent import AolProcessFaultContext
    from openpilot.starpilot.aol.tests.test_ioniq6_stock_repair import stock

    card = Car.__new__(Car)
    card.CP = stock(True)
    card.aol_settings = None
    card.aol_card_intent = None  # Real Card initializes this when no optional owner is selected.
    card.aol_process_fault_context = AolProcessFaultContext()
    class SM:
      seen = {'managerState': True}
      valid = {'managerState': True}
      alive = {'managerState': True}
      logMonoTime = {'managerState': 1_000_000_000}
      events = Events()
      processes = []

      def __getitem__(self, key):
        if key == 'managerState':
          return SimpleNamespace(processes=self.processes)
        if key == 'onroadEvents':
          return self.events.to_msg()
        raise KeyError(key)
    self.enterContext(mock.patch.object(card, 'sm', SM(), create=True))
    return card

  def observe(self, card, stamp, failed):
    card.sm.logMonoTime['managerState'] = stamp
    card.sm.processes = [SimpleNamespace(name=name, running=name not in failed, shouldBeRunning=True)
                         for name in ('ui', 'modeld', 'controlsd', 'card', 'pandad', 'unknown_service')]
    card.aol_process_fault_context.observe(card.sm, stamp + 1)

  def test_actual_card_ui_fault_pause_recovery_and_ordinary_cancel_handoff(self):
    from openpilot.starpilot.aol.tests.test_ioniq6_stock_repair import stock
    from openpilot.starpilot.car.hyundai.aol import create_intent, native_latch_rejected

    card = self.make_card()
    cp = stock(True)
    cp.openpilotLongitudinalControl, cp.pcmCruise = True, False
    cp.safetyConfigs[0].safetyParam = 0x8895
    card.CP = cp
    owner = create_intent(cp, AolSettings(True, 0., 9, 0, (0, 0, 0), (0, 0, 0)))
    state = car_state()
    state.cruiseState.available = True
    owner.update(state, now_ns=100)
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
    owner.update(state, now_ns=200)
    state.buttonEvents = []
    self.assertTrue(owner.allowed_latch)
    self.observe(card, 1_000_000_000, {'ui'})
    card.sm.events.add(log.OnroadEvent.EventName.processNotRunning)
    event_ns = 1_020_000_000
    self.assertTrue(card.sm.events.contains(ET.NO_ENTRY))
    self.assertTrue(card.sm.events.contains(ET.SOFT_DISABLE))
    fault = card.aol_disarming_fault(state, event_ns, event_ns + 1)
    self.assertFalse(fault)
    owner.update(state, fault_active=fault)
    self.assertTrue(owner.allowed_latch)
    native = SimpleNamespace(requestedLateral=False, requestedLongitudinal=False,
                             lateralAllowed=False, longitudinalAllowed=False)
    self.assertFalse(native_latch_rejected(cp, native))
    for standard in (True, False):
      paused = decide_axes(standard_lateral=standard, standard_longitudinal=standard, intent=SimpleNamespace(
        allowedLatch=owner.allowed_latch, pauseLateral=False, pauseLongitudinal=False), native=native,
        car_state=state, initialized=True, model_ready=True, no_entry=True, immediate_disable=False,
        dm_lockout=False, pause_brake_mps=0)
      self.assertTrue(owner.allowed_latch)
      self.assertEqual(paused.mode, 'off')
      self.assertFalse(paused.desired_lateral or paused.desired_longitudinal)
    # Manager recovery precedes clearing the old fault event, as in the route.
    self.observe(card, 1_400_000_000, set())
    self.assertFalse(card.aol_disarming_fault(state, event_ns, 1_410_000_000))
    card.sm.events.clear()
    owner.update(state, fault_active=card.aol_disarming_fault(state, 1_420_000_000, 1_420_000_001))
    native.requestedLateral = native.lateralAllowed = True
    recovered = decide_axes(standard_lateral=False, standard_longitudinal=False,
      intent=SimpleNamespace(allowedLatch=owner.allowed_latch, pauseLateral=False, pauseLongitudinal=False),
      native=native, car_state=state, initialized=True, model_ready=True, no_entry=False,
      immediate_disable=False, dm_lockout=False, pause_brake_mps=0)
    self.assertEqual(recovered.mode, 'lateralOnly')  # No normal engagement or fresh button required.
    self.assertTrue(recovered.desired_lateral)
    self.assertFalse(recovered.desired_longitudinal)
    native.requestedLongitudinal = native.longitudinalAllowed = True
    for standard, expected in ((True, 'combined'), (False, 'lateralOnly')):
      state.buttonEvents = ([] if standard else
        [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.altButton2, pressed=True)])
      owner.update(state, standard_enabled=standard)
      self.assertTrue(owner.allowed_latch)
      native.requestedLongitudinal = native.longitudinalAllowed = standard
      decision = decide_axes(standard_lateral=standard, standard_longitudinal=standard,
        intent=SimpleNamespace(allowedLatch=owner.allowed_latch, pauseLateral=False, pauseLongitudinal=False),
        native=native, car_state=state, initialized=True, model_ready=True, no_entry=False,
        immediate_disable=False, dm_lockout=False, pause_brake_mps=0)
      self.assertEqual(decision.mode, expected)
    # Real requested-lateral revocation and critical faults keep their authority.
    native.lateralAllowed = False
    self.assertTrue(native_latch_rejected(cp, native))
    owner.update(state, now_ns=2_000_000_000, native_rejection_ns=1_990_000_000)
    self.assertFalse(owner.allowed_latch)
    owner.allowed_latch = True
    from dataclasses import replace
    owner.settings = replace(owner.settings, cancel_actions=(3, 0, 0))
    state.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.cancel, pressed=True)]
    owner.update(state)
    self.assertFalse(owner.allowed_latch)  # Physical CANCEL remains intentional withdrawal.

  def test_soft_process_context_guards_remain_distinct_from_immediate_faults(self):
    card = self.make_card()
    state = car_state()
    card.sm.events.add(log.OnroadEvent.EventName.processNotRunning)
    event_ns = 1_020_000_000
    self.assertFalse(card.aol_disarming_fault(state, event_ns, event_ns + 1))
    self.assertFalse(card.aol_process_fault_context.temporary_ui_failure(event_ns, event_ns + 1))
    for failures in ({'controlsd'}, {'unknown_service'}, {'ui', 'modeld'}, set()):
      with self.subTest(failures=failures):
        card.aol_process_fault_context.history.clear()
        self.observe(card, 1_000_000_000, failures)
        self.assertFalse(card.aol_disarming_fault(state, event_ns, event_ns + 1))
        self.assertFalse(card.aol_process_fault_context.temporary_ui_failure(event_ns, event_ns + 1))
    self.observe(card, 1_100_000_000, {'ui'})
    self.assertFalse(card.aol_disarming_fault(state, 1_120_000_000, 2_600_000_001))
    self.assertFalse(card.aol_process_fault_context.temporary_ui_failure(1_120_000_000, 2_600_000_001))
    self.observe(card, 1_150_000_000, {'ui', 'modeld'})
    self.assertFalse(card.aol_disarming_fault(state, 1_120_000_000, 1_160_000_000))
    self.assertFalse(card.aol_process_fault_context.temporary_ui_failure(1_120_000_000, 1_160_000_000))
    card.aol_process_fault_context.history.clear()
    self.observe(card, 1_100_000_000, {'ui'})
    card.sm.processes[0].shouldBeRunning = False
    card.aol_process_fault_context.observe(card.sm, 1_120_000_000)
    self.assertFalse(card.aol_disarming_fault(state, 1_120_000_000, 1_120_000_001))
    self.assertFalse(card.aol_process_fault_context.temporary_ui_failure(1_120_000_000, 1_120_000_001))
    self.observe(card, 1_100_000_000, {'ui'})
    for source_flag in ('seen', 'valid', 'alive'):
      with self.subTest(source_flag=source_flag):
        getattr(card.sm, source_flag)['managerState'] = False
        card.aol_process_fault_context.observe(card.sm, 1_120_000_000)
        self.assertFalse(card.aol_disarming_fault(state, 1_120_000_000, 1_120_000_001))
        self.assertFalse(card.aol_process_fault_context.temporary_ui_failure(1_120_000_000, 1_120_000_001))
        getattr(card.sm, source_flag)['managerState'] = True
        self.observe(card, 1_100_000_000, {'ui'})
    for name in (log.OnroadEvent.EventName.controlsMismatch, log.OnroadEvent.EventName.steerUnavailable):
      with self.subTest(name=name):
        card.sm.events.add(name)
        self.assertTrue(card.aol_disarming_fault(state, 1_120_000_000, 1_120_000_001))
        card.sm.events.clear()
        card.sm.events.add(log.OnroadEvent.EventName.processNotRunning)
    state.steerFaultPermanent = True
    self.assertTrue(card.aol_disarming_fault(state, 1_120_000_000, 1_120_000_001))


if __name__ == '__main__':
  unittest.main()
