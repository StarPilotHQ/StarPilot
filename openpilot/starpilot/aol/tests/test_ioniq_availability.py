import os
import unittest
from unittest import mock

from openpilot.cereal import log, messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from opendbc.car.structs import car
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.starpilot.aol.runtime import AxisDecision, current_native
from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState, encode_intent, encode_safety


def car_state(*, brake=False):
  return car.CarState(canValid=True, gearShifter=car.CarState.GearShifter.drive,
                      vEgo=20.0, brakePressed=brake)


def assert_ordinary_source_pause(sd, *, publication_only=False):
  """Host loss withdraws commands while the existing state machine owns life."""
  assert sd.enabled and sd.active
  if publication_only:
    assert sd.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
    assert sd.state_machine.soft_disable_timer == 0
  else:
    assert sd.state_machine.state == log.SelfdriveState.OpenpilotState.softDisabling
    assert 0 < sd.state_machine.soft_disable_timer <= 300
  assert log.OnroadEvent.EventName.commIssue in sd.events.names
  assert log.OnroadEvent.EventName.controlsMismatch not in sd.events.names
  assert not sd.events.contains(ET.ENABLE)
  assert not sd.aol_authority_lost and not getattr(sd, 'aol_availability_long_inhibit', False)
  assert sd.aol_fault_reason == 'availabilityPause'
  assert sd.aol_car_state_log_ns == 0 and not sd.conditional_car_state_valid
  assert not sd.aol_axis_decision.lateral_active and not sd.aol_axis_decision.longitudinal_active


def assert_fresh_ordinary_recovery(sd, axis, command, source):
  """Only a fresh exact input plus its current native grant resumes commands."""
  assert sd.enabled and sd.active
  assert sd.state_machine.state == log.SelfdriveState.OpenpilotState.enabled
  assert axis.sourceCarStateMonoTime == source
  assert 0 <= axis.observedMonoTime - source <= 30_000_000
  assert axis.validUntilMonoTime == axis.observedMonoTime + 30_000_000
  assert axis.longitudinalActive and command.longActive
  native = current_native(sd.sm, sd.CP, now_ns=axis.observedMonoTime, axis_session_id=sd.aol_session_id)
  assert native is not None and native.requestedLongitudinal and native.longitudinalAllowed
  assert not sd.events.contains(ET.ENABLE)
  assert not sd.aol_authority_lost and not getattr(sd, 'aol_availability_long_inhibit', False)


class IoniqAvailabilityTests(unittest.TestCase):
  def _authority_callers(self, full_op, *, cp=None, intent_companion=None, genuine_events=False, initial_panda_config=None):
    self.enterContext(mock.patch('openpilot.selfdrive.selfdrived.selfdrived.REPLAY', False))
    from opendbc.car.gm.tests.test_bolt_volt_configurations import ordinary_params
    from opendbc.car.gm.values import CAR
    from openpilot.selfdrive.controls.controlsd import Controls
    from openpilot.starpilot.aol.vehicle import policy_for
    from openpilot.starpilot.controllers.mode_actions import SwitchbackCooldown
    from openpilot.starpilot.lateral.tests.test_lane_runtime import feed

    params = Params()
    params.put_bool('OpenpilotEnabledToggle', True, block=True)
    params.put_bool('SafeMode', False, block=True)
    params.put_bool('AlwaysOnLateral', True, block=True)
    supplied_cp = cp is not None
    if cp is None:
      cp = ordinary_params(CAR.CHEVROLET_VOLT_ASCM, alpha=True, sascm=True, radar=True)
    policy = policy_for(cp)
    cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
    cp.alternativeExperience |= policy.alternative_experience_addition
    params.put('CarParams', cp.to_bytes(), block=True)
    controls = Controls()
    sd = SelfdriveD(CP=cp.as_reader()) if supplied_cp else SelfdriveD.__new__(SelfdriveD)
    sd.params = params
    sd.CP = cp.as_reader()
    services = ['deviceState', 'pandaStates', 'driverMonitoringState', 'extrinsicsCalibration', 'modelV2',
                'carState', 'carControl', 'aolIntentWire', 'aolSafetyWire', 'starpilotCarState']
    if not genuine_events:
      self.enterContext(mock.patch.object(sd, 'sm', messaging.SubMaster(services, ignore_avg_freq=services), create=True))
    sd.sm.update = mock.Mock()
    # Preserve the real constructor's CP-qualified family event rules.
    if not (supplied_cp and genuine_events):
      sd.events = Events()
    sd.state_machine, sd.AM = StateMachine(), AlertManager()
    sd.state_machine.state = log.SelfdriveState.OpenpilotState.enabled if full_op else log.SelfdriveState.OpenpilotState.disabled
    sd.enabled = sd.active = full_op
    sd.mismatch_counter = 0
    sd.events_prev = []
    sd.experimental_mode = sd.requested_experimental_mode = False
    sd.initialized = sd.aol_replay = True
    sd.axis_transport_required = sd.aol_replay or policy.ordinary_axis_ack_required
    sd.aol_authority_lost = sd.aol_dm_lateral_inhibit = False
    sd.aol_axis_decision = AxisDecision()
    sd.aol_settings = None
    sd.aol_car_state_log_ns = 0
    sd.conditional_car_state_valid = False
    sd.aol_last_intent = None
    if intent_companion is not None or not supplied_cp:
      sd.aol_intent_companion = intent_companion
    sd.aol_session_id, sd.aol_sequence = 'authority-session', 0
    from openpilot.starpilot.aol.transport_pause import TransportPause
    sd.aol_transport_pause = TransportPause(sd.CP, sd.aol_session_id)
    sd.conditional_replay = False
    sd.nostalgia_paddle_cancel = False
    sd.switchback_capable = False
    sd.switchback_cooldown = SwitchbackCooldown()
    sd.switchback_setting_ns, sd.switchback_cooldown_ns = 0, 300_000_000_000
    sd.personality, sd.is_metric = 1, False
    sd.CS_prev = car_state()
    sd.car_state_sock = object()
    original_optional_receive = messaging.recv_one_or_none
    steering_receipts = []

    def optional_receive(sock):
      if getattr(sd, 'steering_companion', None) is not None and sock is sd.steering_companion.sock:
        return steering_receipts.pop(0) if steering_receipts else None
      return None if sock is sd.car_state_sock else original_optional_receive(sock)

    self.enterContext(mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one_or_none', side_effect=optional_receive))
    sd.update_conditional_mode = mock.Mock()
    if not genuine_events:
      self.enterContext(mock.patch.object(sd, 'update_events', lambda cs: (sd.events.clear(), sd._update_steering_authority(cs))))
    else:
      self.assertTrue(supplied_cp)
      self.assertFalse(sd.sm.simulation)
      sd.startup_event = None
      # Retain the real 100 Hz Ratekeeper created by SelfdriveD(CP=...).

    class Publisher:
      def __init__(self):
        self.messages = {}
      def send(self, service, message):
        self.messages[service] = messaging.log_from_bytes(message.to_bytes())
    self.enterContext(mock.patch.object(sd, 'pm', Publisher(), create=True))
    self.enterContext(mock.patch.object(controls, 'pm', Publisher()))
    clock = self.enterContext(mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic_ns', return_value=10_000_000_000))
    recv = self.enterContext(mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.recv_one'))
    base = 10_000_000_000
    seconds = (self.enterContext(mock.patch('openpilot.selfdrive.selfdrived.selfdrived.time.monotonic',
                                            return_value=base / 1e9)) if genuine_events else None)
    original_message = messaging.new_message
    def clocked_message(*args, **kwargs):
      message = original_message(*args, **kwargs)
      if 'logMonoTime' not in kwargs:
        message.logMonoTime = clock.return_value
      return message
    self.enterContext(mock.patch('openpilot.selfdrive.selfdrived.selfdrived.messaging.new_message', clocked_message))


    # The opt-in path keeps the constructor's production subscriptions and
    # frequency/validity checks. Only receive transport and the clock are synthetic.
    previous_control = [car.CarControl(enabled=full_op, latActive=full_op, longActive=full_op).as_reader()]
    # Production subscribes before the first Controls packet arrives. Preserve
    # that unseen state, then feed only the preceding actual published command.
    previous_control_message = [None]
    due = {}
    controls_panda_due = [base]
    def feed_event_sources(stamp, companions=(), *, car_output_valid=True):
      from openpilot.cereal.services import SERVICE_LIST
      names = ['deviceState', 'pandaStates', 'peripheralState', 'modelV2', 'extrinsicsCalibration',
               'carOutput', 'driverMonitoringState', 'longitudinalPlan', 'deviceMotion', 'lateralDelay',
               'managerState', 'vehicleParameters', 'radarState', 'lateralTorqueParameters',
               'controlsState', 'carControl', 'driverAssistance', *sd.camera_packets, *sd.sensor_packets]
      supplied = {sample.which(): sample for sample in companions}
      samples = []
      for name in dict.fromkeys([*names, *supplied]):
        frequency = SERVICE_LIST[name].frequency
        # Ignored authority wires remain fresh on every 100 Hz source tick.
        period = round(1e9 / min(frequency, 100)) if frequency > 0 else 0
        if (stamp < due.get(name, 0) and name not in ('aolIntentWire', 'aolSafetyWire', 'starpilotCarState') and
            not (name == 'pandaStates' and initial_panda_config is None and name in supplied and
                 any(p.safetyRxChecksInvalid or p.faults for p in supplied[name].pandaStates))):
          continue
        due[name] = stamp + period
        sample = supplied.get(name)
        if sample is None:
          sample = messaging.new_message(name, len(cp.safetyConfigs) if name == 'pandaStates' else None,
                                         valid=name != 'carOutput' or car_output_valid, logMonoTime=stamp)
          value = getattr(sample, name)
          if name == 'deviceState':
            value.started, value.startedMonoTime, value.freeSpacePercent = True, base - 1_000_000_000, 50.
          elif name == 'pandaStates':
            for panda, config in zip(value, cp.safetyConfigs, strict=True):
              if initial_panda_config is None:
                panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
                panda.alternativeExperience, panda.controlsAllowed = cp.alternativeExperience, full_op
              else:
                model, word, experience, controls_allowed, rx_invalid = initial_panda_config
                panda.safetyModel, panda.safetyParam, panda.alternativeExperience = model, word, experience
                panda.controlsAllowed, panda.safetyRxChecksInvalid = controls_allowed, rx_invalid
          elif name == 'extrinsicsCalibration':
            value.calStatus, value.rpyCalib = 'calibrated', [0., 0., 0.]
          elif name == 'deviceMotion':
            value.inputsOK = value.posenetOK = True
          elif name == 'vehicleParameters':
            value.valid = True
          elif name == 'controlsState':
            value.lateralControlState.init('torqueState')
          elif name == 'carControl':
            sample.carControl = previous_control[0]
          sample = sample.as_reader()
        samples.append(sample)
      sd.sm.update_msgs(stamp / 1e9, samples)

    if genuine_events:
      # Prime actual cadence trackers before the initialized onroad baseline;
      # this advances fixture time only and neither sleeps nor skips a check.
      for prime in range(-110, 0):
        feed_event_sources(base + prime * 10_000_000)
      self.assertTrue(sd.sm.all_checks())

    def step(tick, *, timeout=False, old_intent=False, allowed=True, pause=False, latched=True,
             brake=False, steering=False, gas=False, user_disable=False, observed_state=None, authority_unavailable=False, companion=True,
             long_allowed=True, pause_long=False, state_stamp=None, intent_message=None, offset_ns=0,
             native_request_lat=True, native_request_long=None, native_valid=True, panda_fault=False, panda_controls_allowed=None,
             critical_event=None, calibrated=True, car_output_valid=True, relay_fault=False,
             car_state_message=None, car_output_message=None, authority_message=None, panda_state_message=None, safety_message=None):
      now = base + tick * 10_000_000 + offset_ns
      clock.return_value = now
      if seconds is not None:
        seconds.return_value = now / 1e9
      if car_state_message is None:
        state = car_state(brake=brake) if observed_state is None else observed_state
        state.cruiseState.available = True
        state.steeringPressed, state.gasPressed = steering, gas
      else:
        # Opt-in producer-envelope path: do not reconstruct/modify raw state.
        state = car_state_message.carState
      if panda_controls_allowed is None:
        panda_controls_allowed = full_op if native_request_long is None else native_request_long
      if car_state_message is None:
        state_event = messaging.new_message('carState', valid=True, logMonoTime=now if state_stamp is None else state_stamp)
        state_event.carState = state
      else:
        state_event = car_state_message
      recv.return_value = None if timeout else state_event.as_reader()
      frames = []
      for service in ('deviceState', 'driverMonitoringState', 'extrinsicsCalibration', 'modelV2'):
        message = messaging.new_message(service, valid=True, logMonoTime=now)
        if service == 'deviceState':
          message.deviceState.started, message.deviceState.startedMonoTime = True, base - 1_000_000_000
          if genuine_events:
            message.deviceState.freeSpacePercent = 50.
        elif service == 'extrinsicsCalibration':
          message.extrinsicsCalibration.calStatus = 'calibrated' if calibrated else 'uncalibrated'
          if genuine_events:
            message.extrinsicsCalibration.rpyCalib = [0., 0., 0.]
        frames.append(message.as_reader())
      pandas = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True, logMonoTime=now)
      for panda, config in zip(pandas.pandaStates, cp.safetyConfigs, strict=True):
        panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
        panda.alternativeExperience = cp.alternativeExperience
        panda.controlsAllowed = panda_controls_allowed
        panda.safetyRxChecksInvalid = panda_fault
        if relay_fault:
          panda.faults = ['relayMalfunction']
      if panda_state_message is not None:
        pandas = panda_state_message
      frames.append(pandas.as_reader())
      if car_output_message is not None:
        frames.append(car_output_message.as_reader())
      intent_ns = base if old_intent else now
      intent = messaging.new_message('aolIntentWire', 0, valid=True, logMonoTime=intent_ns)
      intent.aolIntentWire = encode_intent(IntentState('authority-card', tick + 1, intent_ns, intent_ns,
                                                     intent_ns + 200_000_000, latched, pause, pause_long, True, latched))
      if intent_message is not None:
        intent = intent_message
      native = messaging.new_message('aolSafetyWire', 0, valid=native_valid, logMonoTime=now)
      native.aolSafetyWire = encode_safety(SafetyState(1, True, now, now + 200_000_000,
        int(cp.safetyConfigs[-1].safetyModel.raw), cp.safetyConfigs[-1].safetyParam,
        allowed, (full_op or native_request_long is True) and long_allowed, native_request_lat,
        full_op if native_request_long is None else native_request_long, 'authority-panda', sd.aol_session_id,
        now if genuine_events else 0, tuple(
          PandaSlot(index, 'authority-panda' if index == len(cp.safetyConfigs) - 1 else f'silent-panda-{index}',
                    int(config.safetyModel.raw), config.safetyParam,
                    cp.alternativeExperience, panda_controls_allowed, panda_fault, False,
                    1 if relay_fault else 0)
          for index, config in enumerate(cp.safetyConfigs)) if genuine_events else ()))
      if safety_message is not None:
        native = safety_message
      if authority_message is not None:
        frames.append(authority_message.as_reader())
      elif companion:
        authority = messaging.new_message('starpilotCarState', valid=companion != 'invalid',
                                          logMonoTime=now - 1 if companion == 'wrong_stamp' else now)
        authority.starpilotCarState.sourceCarStateMonoTime = now - 1 if companion == 'wrong_source' else now
        authority.starpilotCarState.lateralAuthorityUnavailable = authority_unavailable
        frames.append(authority.as_reader())
        # Mirror this producer envelope on the real nonconflating receipt,
        # as Card does. The conflating SubMaster view alone proves no receipt.
        if getattr(sd, 'steering_companion', None) is not None:
          steering_receipts.append(authority.as_reader())
      if genuine_events:
        feed_event_sources(now, frames + [intent.as_reader(), native.as_reader()], car_output_valid=car_output_valid)
      else:
        if previous_control_message[0] is not None:
          frames.append(previous_control_message[0])
        sd.sm.update_msgs(now / 1e9, frames + [intent.as_reader(), native.as_reader()])
      if user_disable:
        self.enterContext(mock.patch.object(sd, 'update_events', lambda cs: (
          sd.events.clear(), sd._update_steering_authority(cs), sd.events.add(log.OnroadEvent.EventName.pedalPressed))))
      if critical_event is not None:
        self.enterContext(mock.patch.object(sd, 'update_events', lambda cs: (
          sd.events.clear(), sd._update_steering_authority(cs), sd.events.add(critical_event))))
      sd.step()
      control_frames = []
      with mock.patch.object(controls.sm, 'update_msgs', side_effect=lambda _timestamp, messages: control_frames.extend(messages)):
        feed(controls, now, tick * 5, active=False, enabled=False)
      model = messaging.new_message('modelV2', valid=True, logMonoTime=now)
      model.modelV2 = next(message.modelV2 for message in control_frames if message.which() == 'modelV2')
      model.modelV2.action.desiredCurvature = 0.
      if car_state_message is None:
        control_state = messaging.new_message('carState', valid=sd.conditional_car_state_valid,
                                              logMonoTime=sd.aol_car_state_log_ns)
        control_state.carState = sd.CS_prev
      else:
        control_state = car_state_message
      if car_output_message is not None:
        control_frames = [sample for sample in control_frames if sample.which() != 'carOutput']
        control_frames.append(car_output_message.as_reader())
      if panda_state_message is not None and 'pandaStates' in controls.sm.services:
        control_frames = [sample for sample in control_frames if sample.which() != 'pandaStates']
        if now >= controls_panda_due[0]:
          control_frames.append(panda_state_message.as_reader())
          controls_panda_due[0] = now + 100_000_000
      replaced = ('carState', 'aolSafetyWire', 'modelV2', 'aolAxisState', 'selfdriveState')
      controls.sm.update_msgs(now / 1e9, [message for message in control_frames if message.which() not in replaced] +
                             [control_state.as_reader(), native.as_reader(), model.as_reader(),
                              sd.pm.messages['aolAxisState'], sd.pm.messages['selfdriveState']])
      command, lac_log = controls.state_control()
      controls.publish(command, lac_log)
      previous_control_message[0] = controls.pm.messages['carControl']
      if genuine_events:
        previous_control[0] = command.as_reader()
      return sd.pm.messages['aolAxisState'].aolAxisState, sd.pm.messages['selfdriveState'].selfdriveState, command
    return sd, controls, step

  def _ioniq_availability_callers(self, full_op=True, *, initially_armed=True, stock_angle=False, cp=None, cold_start=False, startup_owner=None):
    from opendbc.car.hyundai.interface import CarInterface
    from opendbc.car.hyundai.radar_interface import RadarInterface
    from openpilot.selfdrive.car.card import Car
    from openpilot.selfdrive.car.tests.test_hyundai_aol import candidate

    previous_simulation = os.environ.get('SIMULATION')

    def restore_simulation():
      if previous_simulation is None:
        os.environ.pop('SIMULATION', None)
      else:
        os.environ['SIMULATION'] = previous_simulation

    self.addCleanup(restore_simulation)
    os.environ['SIMULATION'] = '0'
    supplied_profile = cp is not None
    if stock_angle and not supplied_profile:
      from opendbc.car.hyundai.tests.test_stock_angle_aol import source_profile
      from opendbc.car.hyundai.values import CAR
      cp = source_profile(CAR.HYUNDAI_IONIQ_5_PE if stock_angle is True else CAR(stock_angle))
    elif not supplied_profile:
      _, cp = candidate(True)
      cp.safetyConfigs[-1].safetyParam |= 0x0800
    sd, controls, step = self._authority_callers(full_op, cp=cp, genuine_events=True)
    card_cp = cp.as_reader().as_builder() if startup_owner is None else startup_owner.owner.cp
    card = Car(CI=CarInterface(card_cp), RI=RadarInterface(card_cp), startup_owner=startup_owner)
    self.assertEqual(card.CP.to_dict(), sd.CP.to_dict())
    self.assertEqual(card.CP.safetyConfigs[-1].safetyParam, cp.safetyConfigs[-1].safetyParam if stock_angle or supplied_profile else 0x8895)
    self.assertTrue(card.aol_card_intent.explicit_latch)
    card.slc_producer_session = 'authority-card'
    cs = car_state()
    cs.cruiseState.available = True
    card.aol_card_intent.update(cs, now_ns=9_980_000_000)
    if initially_armed:
      cs.buttonEvents = [car.CarState.ButtonEvent(type=car.CarState.ButtonEvent.Type.lkas, pressed=True)]
      card.aol_card_intent.update(cs, now_ns=9_990_000_000)
      cs.buttonEvents = []
    self.assertEqual(card.aol_card_intent.allowed_latch, initially_armed)

    def receive(now, *, axis=None, event=None, state=None, preserve_cruise_state=False, can_packets=()):
      frames = []
      for service in ('pandaStates', 'aolSafetyWire', 'deviceState', 'carControl'):
        message = messaging.new_message(service, len(sd.sm[service]) if service == 'pandaStates' else
                                        len(sd.sm[service]) if service == 'aolSafetyWire' else None,
                                        valid=sd.sm.valid[service], logMonoTime=sd.sm.logMonoTime[service])
        setattr(message, service, sd.sm[service])
        # Card receives the exact envelope bytes, including validity and stamps.
        frames.append(messaging.log_from_bytes(message.to_bytes()))
      card.sm.update_msgs(now / 1e9, frames + [m for m in (axis, event, sd.pm.messages.get('selfdriveState')) if m is not None])
      observed = state if state is not None else car_state()
      if stock_angle and state is None:
        observed.vEgoRaw = observed.vEgo
      if not preserve_cruise_state:
        observed.cruiseState.available = True
      card.CC_prev = card.sm['carControl']
      with mock.patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[b'fixture']), \
           mock.patch('openpilot.selfdrive.car.card.can_capnp_to_list', return_value=can_packets), \
           mock.patch('openpilot.selfdrive.car.card.time.monotonic_ns', return_value=now), \
           mock.patch('openpilot.selfdrive.car.card.time.clock_gettime_ns', return_value=now), \
           mock.patch('openpilot.selfdrive.car.card.REPLAY', False), \
           mock.patch.object(card.CI, 'update', return_value=observed), \
           mock.patch.object(card.RI, 'update', return_value=None), mock.patch.object(card.sm, 'update'):
        actual, _ = card.state_update()
      self.assertIs(actual, observed)
      return card.aol_card_intent.allowed_latch

    def drive(tick, **kwargs):
      kwargs.setdefault('latched', card.aol_card_intent.allowed_latch)
      if stock_angle and 'observed_state' not in kwargs:
        state = car_state(brake=kwargs.get('brake', False))
        state.vEgoRaw = state.vEgo
        kwargs['observed_state'] = state
      return step(tick, **kwargs)

    for tick in (() if cold_start else (0, 1)):
      axis, _, command = drive(tick)
      if full_op:
        self.assertTrue(axis.lateralActive and axis.longitudinalActive and command.latActive and command.longActive)
      else:
        self.assertEqual(bool(axis.lateralActive and command.latActive), initially_armed)
        self.assertFalse(sd.enabled or sd.active or axis.longitudinalActive or command.longActive)
      self.assertEqual(receive(10_000_000_000 + tick * 10_000_000,
                              axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents']), initially_armed)
    return sd, controls, drive, card, receive

  def test_full_set_engagement_arms_aol_and_brake_keeps_lateral_without_lkas(self):
    from opendbc.car.hyundai.values import Buttons

    with OpenpilotPrefix():
      sd, _, step, card, receive = self._ioniq_availability_callers(full_op=False, initially_armed=False)
      self.assertFalse(card.aol_card_intent.allowed_latch)
      for tick, previous, current in ((2, Buttons.NONE, Buttons.SET_DECEL),
                                      (3, Buttons.SET_DECEL, Buttons.NONE)):
        cs = car_state()
        cs.buttonEvents = card.CI.CS.create_cruise_button_events(current, previous)
        cs.buttonEnable = card.CI.CS.update_button_enable(cs.buttonEvents)
        step(tick, observed_state=cs, native_request_long=False)
        receive(10_000_000_000 + tick * 10_000_000, state=cs,
                axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      self.assertIn(log.OnroadEvent.EventName.buttonEnable, sd.events.names)
      self.assertTrue(sd.enabled and sd.active)
      # PandaStates updates at 10 Hz. Grant LONG only once its actual fresh
      # health observation agrees, retaining the production latest-Panda veto.
      for tick in range(4, 13):
        granted = tick >= 10
        axis, _, command = step(tick, native_request_long=granted)
        self.assertTrue(axis.lateralActive and command.latActive)
        self.assertEqual(bool(axis.longitudinalActive and command.longActive), granted)
        receive(10_000_000_000 + tick * 10_000_000,
                axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
      self.assertTrue(card.aol_card_intent.allowed_latch)
      for tick in (13, 14, 15):
        axis, _, command = step(tick, brake=tick == 13, native_request_long=False)
        self.assertTrue(axis.lateralActive and command.latActive)
        self.assertFalse(sd.enabled or sd.active or axis.longitudinalActive or command.longActive)
        self.assertTrue(receive(10_000_000_000 + tick * 10_000_000, state=car_state(brake=tick == 13),
                                axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents']))
      self.assertFalse(any(e.type == car.CarState.ButtonEvent.Type.lkas for e in card.CI.CS.out.buttonEvents))

  def test_reverse_keeps_actual_card_armed_without_output_and_drive_requires_ack(self):
    from openpilot.starpilot.aol.wire import decode_intent

    with OpenpilotPrefix():
      sd, _, step, card, receive = self._ioniq_availability_callers(full_op=False)
      card.pm = type(sd.pm)()
      card.aol_sequence = 2
      for tick in range(2, 45):
        reverse = tick < 42
        cs = car_state(brake=reverse)
        cs.gearShifter = car.CarState.GearShifter.reverse if reverse else car.CarState.GearShifter.drive
        now = 10_000_000_000 + tick * 10_000_000
        original_message = messaging.new_message
        def current_message(*args, _original=original_message, _now=now, **kwargs):
          message = _original(*args, **kwargs)
          message.logMonoTime = _now
          return message
        with mock.patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=current_message):
          card.state_publish(cs, None)
        intent_message = card.pm.messages['aolIntentWire'].as_builder()
        intent = decode_intent(bytes(intent_message.aolIntentWire))
        self.assertTrue(intent.lateralArmed)
        self.assertEqual(intent.allowedLatch, not reverse)
        requested = tick == 2 or tick >= 43
        axis, state, command = step(tick, observed_state=cs, intent_message=intent_message,
                                    native_request_lat=requested, allowed=requested, native_request_long=False)
        self.assertEqual(bool(axis.lateralActive), tick >= 43)
        self.assertEqual(bool(command.latActive), tick >= 43)
        self.assertFalse(axis.longitudinalActive or command.longActive or sd.enabled or sd.active)
        self.assertTrue(axis.nativeAcknowledged, (tick, sd.events.names,
          [(str(sample.gearShifter), sample.canValid, sample.canTimeout) for sample, _ in sd.car_state_samples], axis.to_dict()))
        self.assertEqual(str(axis.faultReason), 'none')
        if reverse:
          self.assertIn(log.OnroadEvent.EventName.reverseGear, sd.events.names)
          self.assertIn(log.OnroadEvent.EventName.wrongGear, sd.events.names)
          self.assertFalse(axis.desiredLateral or axis.desiredLongitudinal)
        self.assertTrue(receive(now, state=cs, axis=sd.pm.messages['aolAxisState'],
                                event=sd.pm.messages['onroadEvents']))

  def test_ioniq_caroutput_communication_fault_joined_delivery_and_recovery(self):
    for order in ('axis_first', 'event_first', 'together'):
      with self.subTest(order=order), OpenpilotPrefix():
        sd, _, step, card, receive = self._ioniq_availability_callers()
        axis, state, command = step(2, car_output_valid=False)
        self.assertFalse(sd.sm.valid['carOutput'])
        self.assertTrue(sd.sm.all_checks(['carControl']))
        self.assertTrue(sd.CS_prev.canValid and not sd.CS_prev.canTimeout)
        self.assertIn(log.OnroadEvent.EventName.commIssue, sd.events.names)
        self.assertTrue(sd.events.contains(ET.SOFT_DISABLE))
        self.assertFalse(axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
        self.assertEqual(state.alertType, 'commIssue/softDisable')
        self.assertEqual(state.alertText2, 'Communication Issue Between Processes')
        # The shared SOFT owner retains armed intent while both output axes
        # are withdrawn; source/event joins still have their original deadline.
        proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
        self.assertEqual(proof.aolAxisState.faultEventMonoTime, event.logMonoTime)
        now = 10_020_000_000
        if order == 'axis_first':
          self.assertTrue(receive(now, axis=proof))
          self.assertTrue(receive(now + 1_000_000, event=event))
        elif order == 'event_first':
          self.assertTrue(receive(now, event=event))
          self.assertTrue(receive(now + 1_000_000, axis=proof))
        else:
          self.assertTrue(receive(now, axis=proof, event=event))
        self.assertEqual(str(axis.faultReason), 'availabilityPause')
        self.assertFalse(sd.aol_authority_lost)
        self.assertTrue(card.aol_transport_feedback.ordinary_warning_qualified)
        self.assertFalse(card.aol_transport_feedback.retention_qualified)
        self.assertFalse(card.aol_transport_feedback.qualified)
        self.assertEqual(proof.aolAxisState.intentProducerSessionId, card.slc_producer_session)
        self.assertEqual(proof.aolAxisState.intentIntegrityEpoch, sd.aol_intent_companion.integrity_epoch)
        recovered, _, command = step(3, native_request_long=False)
        self.assertTrue(recovered.lateralActive and command.latActive)
        self.assertFalse(recovered.desiredLongitudinal or recovered.longitudinalActive or command.longActive)
        self.assertTrue(receive(10_030_000_000, axis=sd.pm.messages['aolAxisState'],
                                event=sd.pm.messages['onroadEvents']))

  def test_ioniq_availability_reengagement_requires_real_set_release_and_fresh_long_ack(self):
    from opendbc.car.hyundai.values import Buttons

    with OpenpilotPrefix():
      sd, _, step, card, receive = self._ioniq_availability_callers()
      occurrence = 0
      # Qualified availability keeps published ordinary state disabled;
      # retained lateral intent does not itself reengage LONG.
      for tick in range(2, 313):
        axis, _, command = step(tick, car_output_valid=False)
        self.assertFalse(axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
        published = sd.pm.messages['selfdriveState'].selfdriveState
        self.assertFalse(sd.enabled or sd.active or published.enabled or published.active)
        event = sd.pm.messages['onroadEvents'] if axis.faultEventMonoTime != occurrence else None
        occurrence = axis.faultEventMonoTime
        self.assertTrue(receive(10_000_000_000 + tick * 10_000_000,
                                axis=sd.pm.messages['aolAxisState'], event=event))
      self.assertFalse(sd.enabled or sd.active)
      recovered, _, command = step(313, native_request_long=False)
      self.assertTrue(recovered.lateralActive and command.latActive)
      published = sd.pm.messages['selfdriveState'].selfdriveState
      self.assertFalse(sd.enabled or sd.active or published.enabled or published.active)
      self.assertFalse(recovered.desiredLongitudinal or recovered.longitudinalActive or command.longActive)
      self.assertTrue(receive(13_130_000_000, axis=sd.pm.messages['aolAxisState'],
                              event=sd.pm.messages['onroadEvents']))

      pressed = car_state()
      pressed.cruiseState.available = True
      pressed.buttonEvents = card.CI.CS.create_cruise_button_events(Buttons.SET_DECEL, Buttons.NONE)
      pressed.buttonEnable = card.CI.CS.update_button_enable(pressed.buttonEvents)
      self.assertFalse(pressed.buttonEnable)
      axis, _, command = step(314, observed_state=pressed, native_request_long=False)
      self.assertNotIn(log.OnroadEvent.EventName.buttonEnable, sd.events.names)
      self.assertFalse(sd.enabled or axis.longitudinalActive or command.longActive)
      self.assertTrue(receive(13_140_000_000, axis=sd.pm.messages['aolAxisState'],
                              event=sd.pm.messages['onroadEvents'], state=pressed))

      released = car_state()
      released.cruiseState.available = True
      released.buttonEvents = card.CI.CS.create_cruise_button_events(Buttons.NONE, Buttons.SET_DECEL)
      released.buttonEnable = card.CI.CS.update_button_enable(released.buttonEvents)
      self.assertTrue(released.buttonEnable)
      pending, _, command = step(315, observed_state=released, native_request_long=False)
      self.assertIn(log.OnroadEvent.EventName.buttonEnable, sd.events.names)
      self.assertTrue(sd.events.contains(ET.ENABLE))
      self.assertFalse(sd.events.contains(ET.NO_ENTRY))
      self.assertTrue(sd.enabled and sd.active and pending.desiredLongitudinal)
      self.assertTrue(pending.lateralActive and command.latActive)
      self.assertFalse(pending.longitudinalActive or command.longActive)
      self.assertTrue(receive(13_150_000_000, axis=sd.pm.messages['aolAxisState'],
                              event=sd.pm.messages['onroadEvents'], state=released))

      combined, _, command = step(316, native_request_long=True)
      native = current_native(sd.sm, sd.CP, now_ns=13_160_000_000, axis_session_id=sd.aol_session_id)
      self.assertIsNotNone(native)
      self.assertTrue(native.requestedLongitudinal and native.longitudinalAllowed)
      self.assertEqual(native.observedMonoTime, 13_160_000_000)
      self.assertTrue(combined.lateralActive and combined.longitudinalActive and command.latActive and command.longActive)
      self.assertTrue(receive(13_160_000_000, axis=sd.pm.messages['aolAxisState'],
                              event=sd.pm.messages['onroadEvents']))

  def test_ioniq_aol_only_availability_prompt_reaches_controls_and_clears_on_recovery(self):
    with OpenpilotPrefix():
      sd, controls, step, card, receive = self._ioniq_availability_callers(full_op=False)
      axis, state, command = step(2, car_output_valid=False)
      self.assertEqual(str(axis.faultReason), 'availabilityPause')
      self.assertEqual(state.alertType, 'commIssue/softDisable')
      self.assertEqual((state.alertText1, state.alertText2), ('TAKE CONTROL IMMEDIATELY', 'Communication Issue Between Processes'))
      self.assertEqual(str(state.alertSound), 'warningImmediate')
      delivered = controls.sm['selfdriveState']
      self.assertEqual(delivered.alertType, state.alertType)
      self.assertEqual(str(delivered.alertSound), 'warningImmediate')
      self.assertFalse(sd.enabled or sd.active or state.enabled or state.active or
                       axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
      self.assertTrue(receive(10_020_000_000, axis=sd.pm.messages['aolAxisState'],
                              event=sd.pm.messages['onroadEvents']))
      for tick in range(3, 45):
        axis, state, command = step(tick, car_output_valid=False)
        self.assertEqual(state.alertType, 'commIssue/softDisable')
        self.assertFalse(sd.enabled or sd.active or state.enabled or state.active)
        self.assertFalse(axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
        self.assertTrue(receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState']))
      # The normal takeover alert has its original four-second lifetime;
      # it outranks the optional 0.4-second availability prompt.
      self.assertEqual(str(state.alertSound), 'warningImmediate')
      self.assertEqual(str(controls.sm['selfdriveState'].alertSound), 'warningImmediate')
      for tick in range(45, 408):
        recovered, state, command = step(tick, native_request_long=False)
        self.assertTrue(recovered.lateralActive and command.latActive)
        self.assertFalse(sd.enabled or sd.active or state.enabled or state.active or recovered.desiredLongitudinal or
                         recovered.longitudinalActive or command.longActive)
        self.assertTrue(receive(10_000_000_000 + tick * 10_000_000, axis=sd.pm.messages['aolAxisState'],
                                event=sd.pm.messages['onroadEvents']))
      self.assertNotEqual(state.alertType, 'commIssue/softDisable')
      self.assertNotEqual(controls.sm['selfdriveState'].alertType, 'commIssue/softDisable')
      self.assertEqual(str(state.alertSound), 'none')
      self.assertEqual(str(controls.sm['selfdriveState'].alertSound), 'none')

  def test_ioniq_availability_sustained_advancing_proof_keeps_warning_and_no_long(self):
    with OpenpilotPrefix():
      sd, _, step, card, receive = self._ioniq_availability_callers()
      occurrences = set()
      for tick in range(2, 413):
        axis, state, command = step(tick, car_output_valid=False)
        now = 10_000_000_000 + tick * 10_000_000
        self.assertEqual(str(axis.faultReason), 'availabilityPause')
        self.assertTrue(axis.desiredLateral and axis.nativeAcknowledged)
        self.assertFalse(axis.lateralActive or axis.desiredLongitudinal or axis.longitudinalActive or
                         command.latActive or command.longActive)
        self.assertIn(log.OnroadEvent.EventName.commIssue, sd.events.names)
        self.assertTrue(state.alertText1 or state.alertText2)
        proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
        advanced = axis.faultEventMonoTime not in occurrences
        occurrences.add(axis.faultEventMonoTime)
        self.assertTrue(receive(now, axis=proof, event=event if advanced else None))
        self.assertTrue(card.aol_transport_feedback.ordinary_warning_qualified)
        self.assertFalse(card.aol_transport_feedback.retention_qualified)
      # Real periodic SD publications advance the matched occurrence beyond the
      # initial 30 ms pairing window; they do not extend an unmatched occurrence.
      self.assertGreaterEqual(len(occurrences), 4)
      self.assertFalse(sd.enabled or sd.active)
      self.assertEqual(state.alertType, 'aolAvailability/warning')
      self.assertEqual((state.alertText1, state.alertText2), ('Steering Paused', 'Communication Issue'))
      recovered, _, command = step(413, native_request_long=False)
      self.assertTrue(recovered.lateralActive and command.latActive)
      self.assertFalse(recovered.desiredLongitudinal or recovered.longitudinalActive or command.longActive)
      self.assertFalse(sd.enabled or sd.active)
      self.assertTrue(receive(14_130_000_000, axis=sd.pm.messages['aolAxisState'],
                              event=sd.pm.messages['onroadEvents']))

  def test_ioniq_availability_missing_pair_has_fixed_deadline(self):
    for missing in ('axis', 'event'):
      with self.subTest(missing=missing), OpenpilotPrefix():
        sd, _, step, card, receive = self._ioniq_availability_callers()
        step(2, car_output_valid=False)
        proof, event = sd.pm.messages['aolAxisState'], sd.pm.messages['onroadEvents']
        now = 10_020_000_000
        self.assertTrue(receive(now, axis=proof if missing == 'event' else None,
                                event=event if missing == 'axis' else None))
        step(4, car_output_valid=False, native_request_long=False)
        renewed = sd.pm.messages['aolAxisState']
        self.assertTrue(receive(now + 20_000_000, axis=renewed if missing == 'event' else None,
                                event=event if missing == 'axis' else None))
        self.assertFalse(receive(now + 31_000_000, axis=renewed if missing == 'event' else None))
        self.assertFalse(receive(now + 32_000_000, axis=proof, event=event))
        self.assertFalse(card.aol_card_intent.allowed_latch)

  def test_ioniq_availability_never_masks_native_can_or_integrity_fault(self):
    for fault in ('native_missing', 'native_denied', 'permanent_eps', 'can_invalid', 'relay',
                  'intent_invalid', 'producer_restart'):
      with self.subTest(fault=fault), OpenpilotPrefix():
        sd, _, step, card, receive = self._ioniq_availability_callers()
        step(2, car_output_valid=False)
        self.assertTrue(receive(10_020_000_000, axis=sd.pm.messages['aolAxisState'],
                                event=sd.pm.messages['onroadEvents']))
        kwargs = {'car_output_valid': False, 'native_request_long': False}
        observed = car_state()
        if fault == 'native_missing':
          kwargs['native_valid'] = False
        elif fault == 'native_denied':
          kwargs['allowed'] = False
        elif fault == 'permanent_eps':
          observed.steerFaultPermanent = True
        elif fault == 'can_invalid':
          observed.canValid = False
        elif fault == 'relay':
          kwargs['relay_fault'] = True
        else:
          stamp = 10_030_000_000
          intent = messaging.new_message('aolIntentWire', 0, valid=fault != 'intent_invalid', logMonoTime=stamp)
          intent.aolIntentWire = encode_intent(IntentState(
            'restarted-card' if fault == 'producer_restart' else card.slc_producer_session,
            1, stamp, stamp, stamp + 200_000_000, True, False, False, True, True))
          kwargs['intent_message'] = intent
        epoch_before = sd.aol_intent_companion.integrity_epoch
        axis, _, command = step(3, observed_state=observed, **kwargs)
        self.assertFalse(axis.lateralActive or axis.longitudinalActive or command.latActive or command.longActive)
        if fault == 'native_denied':
          native = current_native(sd.sm, sd.CP, now_ns=10_030_000_000, axis_session_id=sd.aol_session_id)
          self.assertIsNotNone(native)
          self.assertTrue(native.requestedLateral)
          self.assertFalse(native.lateralAllowed)
          self.assertEqual(str(axis.faultReason), 'availabilityPause')
          self.assertFalse(receive(10_030_000_000, axis=sd.pm.messages['aolAxisState'],
                                 event=sd.pm.messages['onroadEvents'], state=observed))
          self.assertFalse(card.aol_card_intent.allowed_latch)
        else:
          if fault in ('intent_invalid', 'producer_restart'):
            self.assertGreater(sd.aol_intent_companion.integrity_epoch, epoch_before)
            self.assertEqual(str(axis.faultReason), 'availabilityPause')
          else:
            self.assertEqual(str(axis.faultReason), 'critical')
          self.assertFalse(receive(10_030_000_000, axis=sd.pm.messages['aolAxisState'],
                                   event=sd.pm.messages['onroadEvents'], state=observed))
          self.assertFalse(card.aol_card_intent.allowed_latch)

  def test_neutral_reverse_neutral_drive_preserves_actual_card_latch(self):
    from openpilot.starpilot.aol.wire import decode_intent

    with OpenpilotPrefix():
      sd, _, step, card, receive = self._ioniq_availability_callers()
      card.pm = type(sd.pm)()
      card.aol_sequence = 2
      gears = ['neutral'] * 3 + ['reverse'] * 40 + ['neutral'] * 3 + ['drive'] * 4
      for tick, gear in enumerate(gears, 2):
        cs = car_state(brake=True)
        cs.gearShifter = gear
        now = 10_000_000_000 + tick * 10_000_000
        original_message = messaging.new_message
        def current_message(*args, _original=original_message, _now=now, **kwargs):
          message = _original(*args, **kwargs)
          message.logMonoTime = _now
          return message
        with mock.patch('openpilot.selfdrive.car.card.messaging.new_message', side_effect=current_message):
          card.state_publish(cs, None)
        intent_message = card.pm.messages['aolIntentWire'].as_builder()
        intent = decode_intent(bytes(intent_message.aolIntentWire))
        self.assertTrue(intent.lateralArmed)
        requested = gear == 'drive'
        axis, state, command = step(tick, observed_state=cs, intent_message=intent_message,
                                    native_request_lat=requested, allowed=requested, native_request_long=False)
        if gear != 'drive':
          self.assertFalse(axis.desiredLateral or axis.lateralActive or command.latActive)
        self.assertFalse(axis.longitudinalActive or command.longActive)
        self.assertEqual(str(axis.faultReason), 'none')
        self.assertTrue(receive(now, state=cs, axis=sd.pm.messages['aolAxisState'],
                                event=sd.pm.messages['onroadEvents']))
      self.assertTrue(axis.lateralActive and command.latActive)
      self.assertFalse(state.enabled or state.active)
