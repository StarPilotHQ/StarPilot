"""Inventory consumers use coherent producer health and preserve axis guards.

Synthetic IPC exercises actual SelfdriveD, Controls and Card callers. Physical
USB and production main enumeration/reconnect remain separate qualifications.
"""
from dataclasses import replace
from unittest import mock

import pytest

from openpilot.cereal import custom, log, messaging
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.tests.test_hyundai_aol import candidate
from openpilot.starpilot.aol.runtime import current_native, native_inventory_matches_cp
from openpilot.starpilot.aol.transport_pause import healthy_transport
from openpilot.starpilot.aol.vehicle import native_matches_cp, policy_for
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, decode_safety, encode_safety
from openpilot.starpilot.aol.tests import test_runtime as runtime_tests
from openpilot.starpilot.aol.tests.test_runtime import car_state

NOW = 10_000_000_000
BOOT_OFFSET = 9_000_000_000


def configured_cp():
  _, cp = candidate(True)
  cp.safetyConfigs[-1].safetyParam |= 0x0800
  policy = policy_for(cp)
  cp.safetyConfigs[-1].safetyParam |= policy.safety_param_addition
  cp.alternativeExperience |= policy.alternative_experience_addition
  assert native_matches_cp(cp, cp.safetyConfigs[-1].safetyModel.raw, cp.safetyConfigs[-1].safetyParam)
  return cp


def bound_state(cp, *, now=NOW, serial='authority-panda', session='authority-session'):
  slots = tuple(PandaSlot(i, serial if i == len(cp.safetyConfigs) - 1 else 'auxiliary-panda',
                         int(config.safetyModel.raw), int(config.safetyParam), int(cp.alternativeExperience),
                         str(config.safetyModel) not in ('silent', 'noOutput'))
                for i, config in enumerate(cp.safetyConfigs))
  return SafetyState(1, True, now, now + 200_000_000,
                     slots[-1].safetyModel, slots[-1].safetyParam, True, True, True, True,
                     serial, session, now + BOOT_OFFSET, slots)


def sm_for(cp, state, *, stamp=None, panda_stamp=None):
  services = ['aolSafetyWire', 'pandaStates']
  sm = messaging.SubMaster(services, ignore_avg_freq=services)
  native = messaging.new_message('aolSafetyWire', 0, valid=True,
                                 logMonoTime=state.observedMonoTime if stamp is None else stamp)
  native.aolSafetyWire = encode_safety(state)
  pandas = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True,
                                 logMonoTime=state.sourcePandaStatesMonoTime if panda_stamp is None else panda_stamp)
  for panda, config in zip(pandas.pandaStates, cp.safetyConfigs, strict=True):
    panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
    panda.alternativeExperience = cp.alternativeExperience
    panda.controlsAllowed = str(config.safetyModel) not in ('silent', 'noOutput')
  sm.update_msgs((NOW - 1_000_000) / 1e9, [native.as_reader(), pandas.as_reader()])
  return sm


@pytest.mark.parametrize('mutation', ['serial', 'slot', 'model', 'param', 'ae', 'empty_serial',
                                     'rx_invalid', 'heartbeat', 'fault'])
def test_present_inventory_rejects_borrowed_or_malformed_owner(mutation):
  cp = configured_cp()
  state = bound_state(cp)
  assert current_native(sm_for(cp, state), cp, now_ns=NOW, axis_session_id=state.axisSessionId) == state
  changes = {'slot': {'slotIndex': 1}, 'model': {'safetyModel': 19}, 'param': {'safetyParam': 0},
             'ae': {'alternativeExperience': cp.alternativeExperience ^ 1}, 'empty_serial': {'hardwareSerial': ''},
             'rx_invalid': {'safetyRxChecksInvalid': True}, 'heartbeat': {'heartbeatLost': True}, 'fault': {'faults': 1}}
  bad = replace(state, pandaSerial='borrowed-same-profile') if mutation == 'serial' else replace(
    state, pandaInventory=(replace(state.pandaInventory[0], **changes[mutation]),))
  assert current_native(sm_for(cp, bad), cp, now_ns=NOW, axis_session_id=state.axisSessionId) is None


@pytest.mark.parametrize('boundary', ['at_200ms', 'past_200ms', 'future', 'wrong_session', 'wrong_event', 'extended_lease'])
def test_inventory_uses_original_monotonic_lease(boundary):
  cp = configured_cp()
  state = bound_state(cp)
  now = NOW + (200_000_000 if boundary == 'at_200ms' else 200_000_001 if boundary == 'past_200ms' else 0)
  if boundary == 'future':
    state = bound_state(cp, now=NOW + 1)
  if boundary == 'extended_lease':
    state = replace(state, validUntilMonoTime=NOW + 200_000_001)
  sm = sm_for(cp, state, stamp=NOW - 1 if boundary == 'wrong_event' else None)
  native = current_native(sm, cp, now_ns=now,
                          axis_session_id='foreign-session' if boundary == 'wrong_session' else state.axisSessionId)
  assert (native is not None) == (boundary == 'at_200ms')


@pytest.mark.parametrize('arrival', ['older', 'newer', 'boot_offset', 'latest_fault', 'latest_invalid'])
def test_coherent_health_avoids_independently_conflated_stamp_race(arrival):
  cp = configured_cp()
  state = bound_state(cp)
  stamp = NOW - 100_000_000 if arrival == 'older' else NOW + 1 if arrival == 'newer' else NOW + BOOT_OFFSET
  sm = sm_for(cp, state, panda_stamp=stamp)
  if arrival == 'latest_fault':
    message = messaging.new_message('pandaStates', 1, valid=True, logMonoTime=stamp)
    message.pandaStates[0] = sm['pandaStates'][0]
    message.pandaStates[0].heartbeatLost = True
    sm.update_msgs(NOW / 1e9, [message.as_reader()])
  elif arrival == 'latest_invalid':
    sm.valid['pandaStates'] = False
  healthy = healthy_transport(sm, cp, car_state(), state.axisSessionId, NOW)
  assert healthy == (arrival not in ('latest_fault', 'latest_invalid'))


def test_legacy_and_strict_version_rollout():
  cp = configured_cp()
  state = bound_state(cp)
  legacy = replace(state, sourcePandaStatesMonoTime=0, pandaInventory=())
  assert decode_safety(encode_safety(legacy)) == legacy
  assert current_native(sm_for(cp, legacy, panda_stamp=NOW), cp, now_ns=NOW) == legacy
  assert not legacy.pandaInventory  # Historical compatibility is not inventory qualification.
  for version in (1, 2):
    wire = custom.AolAxisState.SafetyWire.new_message(kind=1, version=version)
    if version == 1:
      wire.sourcePandaStatesMonoTime = NOW
    assert decode_safety(wire.to_bytes()) is None
  with pytest.raises(ValueError):
    encode_safety(replace(state, sourcePandaStatesMonoTime=0))


def test_ioniq6_two_slot_identity_keeps_exact_owner_and_live_inventory_checks():
  cp = configured_cp()
  original = cp.safetyConfigs[0].to_dict()
  cp.safetyConfigs = [{'safetyModel': 'noOutput', 'safetyParam': 0}, original]
  state = bound_state(cp)
  assert native_inventory_matches_cp(state, cp)
  assert native_matches_cp(cp, state.safetyModel, state.safetyParam)
  assert current_native(sm_for(cp, state), cp, now_ns=NOW) is not None
  for slots in ((state.pandaInventory[1], state.pandaInventory[0]),
                (replace(state.pandaInventory[0], hardwareSerial=state.pandaSerial), state.pandaInventory[1]),
                (replace(state.pandaInventory[0], controlsAllowed=True), state.pandaInventory[1]),
                (state.pandaInventory[1],)):
    assert not native_inventory_matches_cp(replace(state, pandaInventory=slots), cp)


@pytest.mark.parametrize('failure', ['none', 'serial', 'ae', 'heartbeat', 'critical', 'stale_intent', 'transport_pause'])
def test_actual_sd_controls_card_inventory_callers(failure):
  case = runtime_tests.IpcAxisContractTests(methodName='runTest')
  try:
    with OpenpilotPrefix():
      sd, controls, step, card, feedback = case._transport_callers()
      real_encode = encode_safety
      corrupt = [False]

      def production_shaped_wire(value):
        state = bound_state(sd.CP, now=value.observedMonoTime, serial=value.pandaSerial, session=value.axisSessionId)
        state = replace(state, lateralAllowed=value.lateralAllowed, longitudinalAllowed=value.longitudinalAllowed,
                        requestedLateral=value.requestedLateral, requestedLongitudinal=value.requestedLongitudinal)
        if corrupt[0] and failure == 'serial':
          state = replace(state, pandaSerial='borrowed-same-profile')
        if corrupt[0] and failure in ('ae', 'heartbeat'):
          slot = replace(state.pandaInventory[0], **({'alternativeExperience': sd.CP.alternativeExperience ^ 1}
                                                    if failure == 'ae' else {'heartbeatLost': True}))
          state = replace(state, pandaInventory=(slot,))
        return real_encode(state)

      with mock.patch('openpilot.starpilot.aol.tests.test_ioniq_availability.encode_safety', production_shaped_wire):
        axis, _, command = step(0)
        assert axis.lateralActive and axis.longitudinalActive and command.latActive and command.longActive
        assert not feedback(NOW, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        assert card.aol_card_intent.allowed_latch
        corrupt[0] = True
        axis, driver_state, command = step(8 if failure == 'transport_pause' else 4 if failure == 'stale_intent' else 1,
                                old_intent=failure == 'stale_intent', timeout=failure == 'transport_pause',
                                critical_event=log.OnroadEvent.EventName.canError if failure == 'critical' else None)
        now = int(sd.pm.messages['aolAxisState'].logMonoTime)
        fault = feedback(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        if failure == 'none':
          assert axis.lateralActive and axis.longitudinalActive and command.latActive and command.longActive
          assert not fault and card.aol_card_intent.allowed_latch
        elif failure == 'transport_pause':
          assert not axis.lateralActive and not axis.longitudinalActive and not command.latActive and not command.longActive
          assert not fault and card.aol_card_intent.allowed_latch
          # Coherent inventory qualifies ordinary host-loss soft disable;
          # active commands stay OFF and only exact armed intent is retained.
          assert str(axis.faultReason) == 'availabilityPause'
          assert axis.sourceCarStateMonoTime == 0 and not sd.conditional_car_state_valid
          assert driver_state.enabled and driver_state.state == log.SelfdriveState.OpenpilotState.softDisabling
          assert log.OnroadEvent.EventName.commIssue in sd.events.names
          assert not sd.aol_authority_lost and not getattr(sd, 'aol_availability_long_inhibit', False)
        else:
          assert not axis.lateralActive and not axis.longitudinalActive and not command.latActive and not command.longActive
          assert fault and not card.aol_card_intent.allowed_latch
          assert str(axis.faultReason) == 'critical'
  finally:
    case.doCleanups()


class _CancelObservationReady(Exception):
  pass


class _Ev6CancelCaller:
  """Real parser/Card receipt and physical intent, with synthetic native IPC.

  Halt state_update after its real CAN observation; SLC/radar are unrelated to
  this regression. The joined production actor separately proves native TX.
  """
  def __init__(self):
    import time
    from types import SimpleNamespace
    from opendbc.car.hyundai.interface import CarInterface
    from opendbc.can.packer import CANPacker
    from openpilot.selfdrive.car.card import Car
    from openpilot.starpilot.aol.intent import AolSettings, AolProcessFaultContext, AOL_TOGGLE
    from openpilot.starpilot.aol.tests.test_ev6_physical_authorization import ev6_params
    from openpilot.starpilot.aol.transport_pause import TransportPauseFeedback
    from openpilot.starpilot.car.hyundai.ev6_intent import Ev6CardIntent

    self.clock = time
    self.cp = ev6_params()
    assert self.cp.safetyConfigs[-1].safetyParam == 0x15
    self.cp.safetyConfigs[-1].safetyParam = 0x815
    self.cp.alternativeExperience = 32
    self.card = Car.__new__(Car)
    self.card.CP, self.card.CI = self.cp, CarInterface(self.cp)
    self.card.CI.update([])
    self.packers = {bus: CANPacker(parser.dbc.name) for bus, parser in self.card.CI.can_parsers.items()}
    self.counters = {}
    self.drive = next(value for value, name in self.card.CI.CS.shifter_values.items() if name == 'D')
    self._observation_stubs = {
      'RI': SimpleNamespace(update=lambda _: None),
      'timing_mark': lambda _: None,
      'observe_ioniq6_long_authority': lambda *_: None,
      'update_vehicle_state_context': lambda _: None,
      'refresh_slc_configuration': self._stop_after_observation,
    }
    self.card.can_sock = object()
    self.card.can_rcv_cum_timeout_counter = 0
    services = ['aolAxisState', 'aolSafetyWire', 'pandaStates', 'onroadEvents']
    self.card.sm = messaging.SubMaster(services, ignore_avg_freq=services)
    self.card.aol_transport_feedback = TransportPauseFeedback(self.cp)
    self.card.aol_process_fault_context = AolProcessFaultContext()
    self.card.aol_settings = AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
    self.card.aol_card_intent = Ev6CardIntent(self.card.aol_settings)
    self.sequence = 0
    self.last_now = 0

  @staticmethod
  def _stop_after_observation(_):
    raise _CancelObservationReady

  def parse(self, button=0, *, stamp_delta=0, lkas=0):
    common = {'GEAR': self.drive, 'DRIVER_SEATBELT': 1, 'DriverBraking': 0, 'ACCEnable': 0, 'ACC_REQ': 0,
              'WHL_SpdFLVal': 45., 'WHL_SpdFRVal': 45., 'WHL_SpdRLVal': 45., 'WHL_SpdRRVal': 45.,
              'ACCMode': 0, 'MainMode_ACC': 1, 'VSetDis': 60, 'DISTANCE_UNIT': 0,
              'MDPS_StrTqSnsrVal': 0, 'MDPS_OutTqVal': 0, 'MDPS_LkaFailSta': 0}
    frames = []
    for bus, parser in self.card.CI.can_parsers.items():
      for address in sorted(parser.addresses):
        message = parser.dbc.addr_to_msg[address]
        values = {name: value for name, value in common.items() if name in message.sigs}
        if message.name == 'CRUISE_BUTTONS':
          values.update(ADAPTIVE_CRUISE_MAIN_BTN=0, LDA_BTN=lkas, CRUISE_BUTTONS=button)
        key = (parser.bus, message.name)
        counter = self.counters.get(key, 0)
        self.counters[key] = counter + 1
        if 'COUNTER' in message.sigs:
          values['COUNTER'] = counter % (16 if message.name == 'CRUISE_BUTTONS' else 256)
        frames.append(self.packers[bus].make_can_msg(message.name, parser.bus, values))
    can = messaging.new_message('can', len(frames), valid=True,
      logMonoTime=self.clock.clock_gettime_ns(self.clock.CLOCK_BOOTTIME) + stamp_delta)
    for target, (address, data, bus) in zip(can.can, frames, strict=True):
      target.address, target.dat, target.src = address, data, bus
    with (
      mock.patch('openpilot.selfdrive.car.card.messaging.drain_sock_raw', return_value=[can.to_bytes()]),
      mock.patch.multiple(self.card, create=True,
        RI=self._observation_stubs['RI'],
        timing_mark=self._observation_stubs['timing_mark'],
        observe_ioniq6_long_authority=self._observation_stubs['observe_ioniq6_long_authority'],
        update_vehicle_state_context=self._observation_stubs['update_vehicle_state_context'],
        refresh_slc_configuration=self._observation_stubs['refresh_slc_configuration']),
    ):
      with pytest.raises(_CancelObservationReady):
        self.card.state_update()
    self.cs = self.card._aol_can_observation[0]
    self.last_now = self.clock.monotonic_ns()
    assert self.cs.canValid and not self.cs.canTimeout
    return self.cs

  def intent(self, *, fault=False, active=False, now=None):
    now = self.last_now if now is None else now
    owner = self.card.aol_card_intent
    owner.observe_physical_samples(self.card.CI.CS, now_ns=now)
    owner.update(self.cs, now_ns=now, fault_active=fault, standard_enabled=active)
    owner.observe_full_engagement(self.cs, enabled=active, active=active, source_ns=now,
                                 observation_valid=True, now_ns=now, fault_active=fault)
    return owner

  def arm(self):
    from opendbc.car.hyundai.values import Buttons
    self.parse(Buttons.NONE)
    assert not self.intent().allowed_latch
    self.parse(Buttons.SET_DECEL)
    assert not self.intent().allowed_latch
    self.parse(Buttons.NONE)
    assert not self.intent().allowed_latch
    assert self.intent(active=True, now=self.last_now + 1).allowed_latch
    assert self.card.aol_card_intent.physical.authorized

  def evidence(self, *, partial=False, lat_only=False, now=None, mutation=None, event=None):
    now = self.clock.monotonic_ns() if now is None else now
    self.last_now = now
    self.sequence += 1
    state = bound_state(self.cp, now=now, session='ev6-cancel-session')
    state = replace(state, requestedLongitudinal=not lat_only, longitudinalAllowed=not partial and not lat_only,
                    sourcePandaStatesMonoTime=self.clock.clock_gettime_ns(self.clock.CLOCK_BOOTTIME),
                    pandaInventory=tuple(replace(p, controlsAllowed=not partial and not lat_only) for p in state.pandaInventory))
    if mutation == 'owner':
      state = replace(state, pandaSerial='replacement',
                      pandaInventory=tuple(replace(p, hardwareSerial='replacement') for p in state.pandaInventory))
    if mutation in ('ae', 'rx', 'heartbeat', 'native_fault'):
      changes = {'ae': {'alternativeExperience': 0}, 'rx': {'safetyRxChecksInvalid': True},
                 'heartbeat': {'heartbeatLost': True}, 'native_fault': {'faults': 1}}
      state = replace(state, pandaInventory=tuple(replace(p, **changes[mutation]) for p in state.pandaInventory))
    if mutation == 'native_stale':
      state = replace(state, observedMonoTime=now - 200_000_001, validUntilMonoTime=now - 1)
    if mutation == 'lat_denied':
      state = replace(state, lateralAllowed=False)
    native = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=state.observedMonoTime)
    native.aolSafetyWire = encode_safety(state)
    pandas = messaging.new_message('pandaStates', len(self.cp.safetyConfigs), valid=True,
                                  logMonoTime=state.sourcePandaStatesMonoTime)
    for panda, config in zip(pandas.pandaStates, self.cp.safetyConfigs, strict=True):
      panda.safetyModel, panda.safetyParam = config.safetyModel, config.safetyParam
      panda.alternativeExperience = self.cp.alternativeExperience
      panda.controlsAllowed = not partial and not lat_only
    if mutation == 'latest_fault':
      pandas.pandaStates[-1].heartbeatLost = True
    axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=now)
    axis.aolAxisState = {'sessionId': state.axisSessionId, 'sequence': self.sequence,
      'sourceCarStateMonoTime': now, 'observedMonoTime': now, 'validUntilMonoTime': now + 30_000_000,
      'mode': 'lateralOnly' if lat_only else 'combined', 'lateralActive': True, 'longitudinalActive': not lat_only,
      'qualified': True, 'desiredLateral': True, 'desiredLongitudinal': not lat_only,
      'nativeAcknowledged': True, 'faultReason': 'none', 'faultSessionId': state.axisSessionId}
    if mutation == 'session':
      axis.aolAxisState.sessionId = axis.aolAxisState.faultSessionId = 'new-session'
    if mutation == 'sequence':
      axis.aolAxisState.sequence = 1
    if mutation == 'axis_stale':
      axis.logMonoTime = axis.aolAxisState.observedMonoTime = now - 30_000_001
      axis.aolAxisState.validUntilMonoTime = now - 1
    events = messaging.new_message('onroadEvents', int(event is not None), valid=True, logMonoTime=now)
    if event is not None:
      events.onroadEvents[0].name = event
      events.onroadEvents[0].immediateDisable = True
    self.card.sm.update_msgs(now / 1e9, [native.as_reader(), pandas.as_reader(), axis.as_reader(), events.as_reader()])
    self.native = state
    return now

  def fault(self, *, now=None):
    now = self.last_now if now is None else now
    result = self.card.aol_disarming_fault(self.cs, int(self.card.sm.logMonoTime['onroadEvents']), now)
    self.intent(fault=result, active=True, now=now)
    return result

  def baseline(self):
    self.arm()
    self.parse()
    self.evidence()
    assert not self.fault()
    feedback = self.card.aol_transport_feedback
    assert feedback.session and feedback.lateral_baseline_ns and feedback.lateral_inventory
    assert self.card.aol_card_intent.allowed_latch

  def assert_no_exemption(self):
    feedback = self.card.aol_transport_feedback
    assert not feedback.qualified and not feedback.awaiting and not feedback.paused


@pytest.mark.parametrize('order', ['wire_before_press', 'press_before_wire'])
def test_ev6_cancel_real_card_async_orders_retain_only_existing_intent(order):
  from opendbc.car.hyundai.values import Buttons
  from openpilot.starpilot.aol.runtime import ordinary_axis_acknowledged
  with OpenpilotPrefix():
    caller = _Ev6CancelCaller()
    caller.baseline()
    if order == 'wire_before_press':
      caller.evidence(partial=True)
      caller.parse(Buttons.CANCEL)
    else:
      caller.parse(Buttons.CANCEL)
      caller.evidence()
    assert caller.card.aol_cancel_source(caller.cs, caller.last_now) > 0
    assert not caller.fault()
    first = caller.card.aol_transport_feedback.longitudinal_cancel_ns
    assert first > 0
    caller.assert_no_exemption()
    caller.parse(Buttons.NONE)
    caller.evidence(partial=True)
    assert not caller.fault()
    assert caller.card.aol_transport_feedback.longitudinal_cancel_ns == first
    assert caller.card.aol_card_intent.allowed_latch and caller.card.aol_card_intent.output(caller.cs)[0]
    assert not ordinary_axis_acknowledged(caller.card.sm, caller.cp, now_ns=caller.last_now)
    caller.parse()
    caller.evidence(lat_only=True)
    assert not caller.fault()
    assert ordinary_axis_acknowledged(caller.card.sm, caller.cp, now_ns=caller.last_now)
    assert not caller.native.requestedLongitudinal and not caller.native.longitudinalAllowed
    assert caller.card.aol_transport_feedback.longitudinal_cancel_ns == 0
    caller.assert_no_exemption()
    assert caller.card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('mutation', ['cp', 'owner', 'ae', 'session', 'sequence'])
def test_ev6_cancel_rejects_changed_identity_and_continuity(mutation):
  from opendbc.car.hyundai.values import Buttons
  with OpenpilotPrefix():
    caller = _Ev6CancelCaller()
    caller.baseline()
    caller.parse(Buttons.CANCEL)
    if mutation == 'cp':
      caller.cp.safetyConfigs[-1].safetyParam = 0x15
    caller.evidence(partial=True, mutation=mutation)
    assert caller.fault()
    caller.assert_no_exemption()
    assert not caller.card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('source', ['queued_old', 'future_batch', 'old_local', 'wrong_cs', 'native_stale', 'axis_stale'])
def test_ev6_cancel_requires_current_parser_batch_and_two_clock_receipts(source):
  from opendbc.car.hyundai.values import Buttons
  with OpenpilotPrefix():
    caller = _Ev6CancelCaller()
    caller.baseline()
    caller.parse(Buttons.CANCEL, stamp_delta=-40_000_000 if source == 'queued_old' else 1_000_000_000 if source == 'future_batch' else 0)
    if source == 'wrong_cs':
      caller.cs = caller.cs.as_reader().as_builder()
    now = caller.clock.monotonic_ns() + (30_000_001 if source == 'old_local' else 0)
    caller.evidence(partial=True, now=now, mutation=source)
    if source in ('queued_old', 'future_batch', 'old_local', 'wrong_cs'):
      assert caller.card.aol_cancel_source(caller.cs, now) == 0
    assert caller.fault()
    caller.assert_no_exemption()
    assert not caller.card.aol_card_intent.allowed_latch


def test_ev6_cancel_deadline_cannot_be_renewed_by_duplicate_held_or_release():
  from opendbc.car.hyundai.values import Buttons
  with OpenpilotPrefix():
    caller = _Ev6CancelCaller()
    caller.baseline()
    caller.parse(Buttons.CANCEL)
    caller.evidence(partial=True)
    assert not caller.fault()
    first = caller.card.aol_transport_feedback.longitudinal_cancel_ns
    assert not caller.fault()  # Identical Card observation is not a new occurrence.
    assert caller.card.aol_transport_feedback.longitudinal_cancel_ns == first
    for button in (Buttons.CANCEL, Buttons.NONE):
      caller.parse(button)
      caller.evidence(partial=True)
      assert not caller.fault()
      assert caller.card.aol_transport_feedback.longitudinal_cancel_ns == first
    # Fresh typed IPC keeps the independent 30ms baseline current. Explicit
    # input times exercise the deadline without a global clock override/sleep.
    for offset in range(20_000_000, 180_000_001, 20_000_000):
      caller.evidence(partial=True, now=first + offset)
      assert not caller.card.aol_disarming_fault(caller.cs, caller.last_now, caller.last_now)
      assert caller.card.aol_transport_feedback.longitudinal_cancel_ns == first
      caller.assert_no_exemption()
    assert first + 200_000_001 - caller.card.aol_transport_feedback.lateral_baseline_ns <= 30_000_000
    caller.evidence(partial=True, now=first + 200_000_001)
    assert caller.fault()
    caller.assert_no_exemption()
    assert not caller.card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('mutation', ['lat_denied', 'rx', 'heartbeat', 'native_fault', 'latest_fault', 'steer', 'acc'])
def test_ev6_cancel_lateral_and_hard_fault_vetoes_remain_closed(mutation):
  from opendbc.car.hyundai.values import Buttons
  with OpenpilotPrefix():
    caller = _Ev6CancelCaller()
    caller.baseline()
    caller.parse(Buttons.CANCEL)
    if mutation == 'steer':
      caller.cs.steerFaultPermanent = True
    if mutation == 'acc':
      caller.cs.accFaulted = True
    caller.evidence(partial=True, mutation=mutation)
    assert caller.fault()
    caller.assert_no_exemption()
    assert not caller.card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('case', ['no_baseline', 'non_cancel'])
def test_ev6_cancel_needs_physical_occurrence_and_accepted_lateral_baseline(case):
  from opendbc.car.hyundai.values import Buttons
  with OpenpilotPrefix():
    caller = _Ev6CancelCaller()
    if case == 'no_baseline':
      caller.arm()
    else:
      caller.baseline()
    caller.parse(Buttons.CANCEL if case == 'no_baseline' else Buttons.NONE)
    caller.evidence(partial=True)
    fault = caller.fault()
    assert caller.card.aol_transport_feedback.longitudinal_cancel_ns == 0
    caller.assert_no_exemption()
    if case == 'non_cancel':
      assert fault and not caller.card.aol_card_intent.allowed_latch
    assert not caller.native.longitudinalAllowed


@pytest.mark.parametrize('event', [log.OnroadEvent.EventName.controlsMismatch, log.OnroadEvent.EventName.canError],
                         ids=['controlsMismatch', 'canError'])
def test_ev6_cancel_never_exempts_controls_mismatch_or_critical_events(event):
  from opendbc.car.hyundai.values import Buttons
  with OpenpilotPrefix():
    caller = _Ev6CancelCaller()
    caller.baseline()
    caller.parse(Buttons.CANCEL)
    caller.evidence(partial=True, event=event)
    assert caller.fault()
    caller.assert_no_exemption()
    assert not caller.card.aol_card_intent.allowed_latch


@pytest.mark.parametrize('change', ['latest_fault', 'cp_change', 'native_expiry', 'borrowed_owner'])
def test_feedback_native_snapshot_is_revalidated_each_observation(change):
  from openpilot.starpilot.aol import transport_pause

  cp = configured_cp()
  state = bound_state(cp)
  services = ['aolAxisState', 'aolSafetyWire', 'pandaStates', 'onroadEvents']
  sm = messaging.SubMaster(services, ignore_avg_freq=services)
  initial = sm_for(cp, state)
  native = messaging.new_message('aolSafetyWire', 0, valid=True, logMonoTime=NOW)
  native.aolSafetyWire = encode_safety(state)
  pandas = messaging.new_message('pandaStates', len(cp.safetyConfigs), valid=True, logMonoTime=NOW + BOOT_OFFSET)
  pandas.pandaStates = initial['pandaStates']
  axis = messaging.new_message('aolAxisState', valid=True, logMonoTime=NOW)
  axis.aolAxisState = {'sessionId': state.axisSessionId, 'sequence': 1,
    'sourceCarStateMonoTime': NOW, 'observedMonoTime': NOW, 'validUntilMonoTime': NOW + 30_000_000,
    'qualified': True, 'desiredLateral': True, 'lateralActive': True, 'nativeAcknowledged': True,
    'faultReason': 'none', 'faultSessionId': state.axisSessionId}
  events = messaging.new_message('onroadEvents', 0, valid=True, logMonoTime=NOW)
  sm.update_msgs(NOW / 1e9, [native.as_reader(), pandas.as_reader(), axis.as_reader(), events.as_reader()])
  feedback = transport_pause.TransportPauseFeedback(cp)
  with mock.patch.object(transport_pause, 'current_native', wraps=current_native) as validate:
    assert not feedback.observe(sm, cp, car_state(), NOW)
    assert validate.call_count == 1
    assert feedback.lateral_baseline_ns == NOW and feedback.lateral_inventory
    later = NOW + 1_000_000
    if change == 'latest_fault':
      pandas.pandaStates[-1].heartbeatLost = True
      sm.update_msgs(later / 1e9, [pandas.as_reader()])
    elif change == 'cp_change':
      cp.safetyConfigs[-1].safetyParam ^= 1
    else:
      bad = (replace(state, validUntilMonoTime=NOW + 500_000) if change == 'native_expiry' else
             replace(state, pandaSerial='borrowed-owner'))
      native.aolSafetyWire = encode_safety(bad)
      sm.update_msgs(later / 1e9, [native.as_reader()])
    assert feedback.observe(sm, cp, car_state(), later)
    assert validate.call_count == 2
    assert feedback.failed and not feedback.qualified and not feedback.awaiting
