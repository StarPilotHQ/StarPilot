"""Factory Toyota CAN evidence reaches existing auxiliary action owners."""
import json
from types import SimpleNamespace as NS

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.toyota.carstate import CarState
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, DBC
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.car.toyota.aol import ToyotaCardIntent
from openpilot.starpilot.conditional_mode.card_input import conditional_manual_candidate
from openpilot.starpilot.conditional_mode.manual import ButtonTracker
from openpilot.starpilot.conditional_mode.policy import ModeChoice
from openpilot.starpilot.conditional_mode.preferences import SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.controllers.coast import CoastRuntime
from openpilot.starpilot.controllers.tests import test_native_wheel_callbacks as native_callbacks
from openpilot.starpilot.controllers.tests.test_wheel_actions import Publisher, services, state, candidate, DRIVE, NOW
from openpilot.starpilot.controllers.wheel_actions import WheelConsumer, WheelPublisher
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.wheel_feature import WheelFeature

native_session = native_callbacks.native_session

KEY = 'LKASButtonControl'
TYPE = structs.CarState.ButtonEvent.Type


def factory(candidate=CAR.TOYOTA_RAV4_TSS2):
  return CarInterface.get_params(candidate, {0: {}, 1: {}, 2: {}}, [], False, False, False)


class ToyotaButtons:
  def __init__(self, cp):
    self.state = CarState(cp)
    self.parsers = CarState.get_can_parsers(cp)
    self.packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    self.tick = 0

  def sample(self, lda=0):
    self.tick += 1
    frame = self.packer.make_can_msg('LKAS_HUD', 2, {'LDA_ON_MESSAGE': lda})
    self.parsers[Bus.cam].update([(self.tick * 10_000_000, [frame])])
    result = self.state.update(self.parsers)
    # Isolated edge bench: transport health is a synthetic premise here.
    # The full CarInterface tests below verify real CAN validity without overrides.
    result.canValid, result.canTimeout = True, False
    return result


def emit(params, cp, code):
  params.put(KEY, code, block=True)
  source, parser = WheelPublisher(params), ToyotaButtons(cp)
  source.observe(params, cp, parser.sample(), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  physical = parser.sample(1)
  assert [(e.type, e.pressed) for e in physical.buttonEvents] == [(TYPE.lkas, True), (TYPE.lkas, False)]
  commands = source.observe(params, cp, physical, now_ns=NOW, drive_id=DRIVE)
  assert commands == ((KEY, code),)
  assert not any(action for _, action in source.observe(params, cp, parser.sample(1), now_ns=NOW + 1, drive_id=DRIVE))
  sender = Publisher()
  source.publish(commands, cp, sender, now_ns=NOW, drive_id=DRIVE, source_car_ns=NOW, source_control_ns=NOW)
  return messaging.log_from_bytes(sender.events[-1][1])


def feature(params, cp):
  return WheelFeature(NS(params=params, vehicle_params=lambda: cp, configuration_vehicle=lambda: False,
                         configuration_longitudinal=lambda: cp.openpilotLongitudinalControl,
                         longitudinal_available=lambda: cp.openpilotLongitudinalControl, authority=lambda _: True))


def test_factory_settings_allow_auxiliary_lkas_hide_unreported_cancel_and_keep_ccmain_fixed(tmp_path):
  params, cp = Params(str(tmp_path)), factory()
  policy = policy_for(cp)
  assert policy.fixed_cruise_buttons and policy.auxiliary_lkas_supported
  wheel = feature(params, cp)
  rows = {row.key: row for row in wheel.rows()}
  row = rows['wheel:' + KEY]
  assert {'Off', 'Bookmark', 'Traffic mode', 'Cycle personality', 'Experimental override'} <= set(row.choices)
  assert not {'Pause steering', 'Pause longitudinal', 'Toggle AOL'} & set(row.choices)
  assert not any('Cancel' in key for key in rows)
  main = rows['wheel:MainCruiseButtonControl']
  assert main.value == 'Factory cruise / AOL' and not main.available and not main.choices
  assert not wheel.apply(FeatureSettingsRequest(main.key, main.source, 'Toggle AOL', capability=main.capability))
  assert wheel.apply(FeatureSettingsRequest(row.key, row.source, 'Bookmark', capability=row.capability))
  assert params.get(KEY) == 8
  assert not wheel.apply(FeatureSettingsRequest(row.key, b'8', 'Toggle AOL', capability=row.capability))
  assert not wheel.apply(FeatureSettingsRequest('wheel:CancelButtonControl', None, 'Bookmark', capability=row.capability))


@pytest.mark.parametrize('code', (1, 5, 6, 8))
def test_factory_can_parser_to_publisher_to_consumer(tmp_path, code):
  params, cp = Params(str(tmp_path)), factory()
  event = emit(params, cp, code)
  consumer = WheelConsumer()
  command = consumer.accept(event, params, cp, services(cp), now_ns=NOW)
  assert (command.key, command.action) == (KEY, code)
  assert consumer.accept(event, params, cp, services(cp), now_ns=NOW) is None


@pytest.mark.parametrize('code', (3, 4, 9))
def test_axis_assignments_cannot_take_native_lkas_or_ccmain_ownership(tmp_path, code):
  params, cp = Params(str(tmp_path)), factory()
  params.put(KEY, code, block=True)
  params.put('MainCruiseButtonControl', code, block=True)
  native = ToyotaCardIntent(AolSettings(True, 0., code, code, (0, 0, 0), (0, 0, 0)))
  assert native.settings.lkas_action == native.settings.main_action == 0
  source, parser = WheelPublisher(params), ToyotaButtons(cp)
  source.observe(params, cp, parser.sample(), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  assert not any(action for _, action in source.observe(params, cp, parser.sample(1), now_ns=NOW, drive_id=DRIVE))


@pytest.mark.parametrize('key', ('CancelButtonControl', 'LongCancelButtonControl', 'VeryLongCancelButtonControl'))
def test_unreported_toyota_button_receipts_are_rejected(tmp_path, key):
  params, cp = Params(str(tmp_path)), factory()
  code = 8
  params.put(key, code, block=True)
  sender, source = Publisher(), WheelPublisher(params)
  source.publish(((key, code),), cp, sender, now_ns=NOW, drive_id=DRIVE, source_car_ns=NOW, source_control_ns=NOW)
  event = messaging.log_from_bytes(sender.events[-1][1])
  assert WheelConsumer().accept(event, params, cp, services(cp), now_ns=NOW) is None
  source.observe(params, cp, state(), now_ns=NOW - 20_000_000, drive_id=DRIVE)
  button = TYPE.cancel
  source.observe(params, cp, state(True, button=button), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  assert not any(action for _, action in source.observe(params, cp, state(False, button=button), now_ns=NOW, drive_id=DRIVE))


def test_non_toyota_cancel_contract_preserved(tmp_path):
  params = Params(str(tmp_path))
  _, cp = candidate(False)
  assert 'Bookmark' in feature(params, cp).choices('CancelButtonControl')
  params.put('CancelButtonControl', 8, block=True)
  source = WheelPublisher(params)
  source.observe(params, cp, state(), now_ns=NOW - 20_000_000, drive_id=DRIVE)
  source.observe(params, cp, state(True, button=TYPE.cancel), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  assert source.observe(params, cp, state(False, button=TYPE.cancel), now_ns=NOW, drive_id=DRIVE) == (('CancelButtonControl', 8),)


@pytest.mark.parametrize('code', (5, 8))
def test_factory_lkas_reaches_actual_native_action_callback(native_session, monkeypatch, code):
  runtime, session, ui, _, _ = native_session
  ui.CP = factory()
  ui.has_longitudinal_control = ui.CP.openpilotLongitudinalControl
  calls = []
  session._native_favorite_actions = lambda: session.native_favorite_actions(
    lambda: calls.append('bookmark') or True, lambda _: True, lambda: calls.append('experimental') or True)
  queue = [emit(ui.params, ui.CP, code)]
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  session._poll_wheel(NOW)
  session._poll_wheel(NOW)
  assert calls == ['bookmark' if code == 8 else 'experimental']


def test_factory_lkas_traffic_reaches_existing_controller_owner(native_session):
  _, _, ui, _, _ = native_session
  ui.CP = factory()
  event = emit(ui.params, ui.CP, 6)
  runtime = CoastRuntime()
  assert runtime.receive(event, ui.params, ui.CP, ui.sm, now_ns=NOW)
  assert runtime.traffic_toggles == 1
  assert not runtime.receive(event, ui.params, ui.CP, ui.sm, now_ns=NOW)
  assert runtime.traffic_toggles == 1


def test_factory_lkas_cem_assignment_uses_existing_card_arbitration(tmp_path):
  params, cp = Params(str(tmp_path)), factory()
  params.put(KEY, 5, block=True)
  params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(mode=ModeChoice.CEM))), block=True)
  settings, tracker, parser = ConditionalSettingsOwner(params), ButtonTracker(), ToyotaButtons(cp)
  sm = services(cp)
  conditional_manual_candidate(parser.sample(), tracker, params, settings, cp, sm, now_ns=NOW - 10_000_000)
  physical = parser.sample(1)
  receipt = conditional_manual_candidate(physical, tracker, params, settings, cp, sm, now_ns=NOW)
  assert receipt is not None and receipt[3] == ModeChoice.CEM
  source = WheelPublisher(params)
  source.observe(params, cp, state(), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  assert not any(action for _, action in source.observe(params, cp, physical, now_ns=NOW,
                                                       drive_id=DRIVE, blocked_keys={KEY}))


def test_factory_lkas_personality_reaches_native_saved_personality_callback(native_session, monkeypatch):
  runtime, session, ui, _, _ = native_session
  ui.CP = factory()
  ui.has_longitudinal_control = ui.CP.openpilotLongitudinalControl
  ui.params.put('LongitudinalPersonality', 1, block=True)
  def save(value):
    ui.params.put('LongitudinalPersonality', value, block=True)
    return True
  session._native_favorite_actions = lambda: session.native_favorite_actions(lambda: True, save, lambda: True)
  queue = [emit(ui.params, ui.CP, 1)]
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  session._poll_wheel(NOW)
  assert ui.params.get('LongitudinalPersonality') == 0
  session._poll_wheel(NOW)
  assert ui.params.get('LongitudinalPersonality') == 0


@pytest.mark.parametrize('case', ('invalid_can', 'stale_source', 'remapped', 'wrong_vehicle'))
def test_factory_lkas_consumer_rechecks_current_source_and_assignment(tmp_path, case):
  params, cp = Params(str(tmp_path)), factory()
  event = emit(params, cp, 8)
  sm = services(cp)
  if case == 'invalid_can':
    sm['carState'].canValid = False
  elif case == 'stale_source':
    sm.recv_time['carState'] = (NOW - 300_000_000) / 1e9
  elif case == 'remapped':
    params.put(KEY, 5, block=True)
  else:
    cp = CarInterface.get_params(CAR.TOYOTA_COROLLA_TSS2, {0: {}, 1: {}, 2: {}}, [], False, False, False)
  assert WheelConsumer().accept(event, params, cp, sm, now_ns=NOW) is None


class FactoryToyotaInterface:
  """Real CarInterface transport validity with complete factory parser messages."""
  def __init__(self, cp, *, complete):
    self.interface = CarInterface(cp)
    self.interface.update([])
    self.packer = CANPacker(DBC[cp.carFingerprint][Bus.pt])
    self.tracked = {bus: tuple(parser.vl) for bus, parser in self.interface.can_parsers.items()}
    self.complete = complete

  def sample(self, now_ns, lda=0):
    values: dict[str, dict[str, float]] = {'PCM_CRUISE': {'GAS_RELEASED': 1}, 'PCM_CRUISE_2': {'MAIN_ON': 1},
              'GEAR_PACKET': {'GEAR': 0}, 'EPS_STATUS': {'LKA_STATE': 1},
              'BODY_CONTROL_STATE': {}, 'LKAS_HUD': {'LDA_ON_MESSAGE': lda}}
    frames = [self.packer.make_can_msg(name, 0 if bus == Bus.pt else 2, values.get(name, {}))
              for bus, names in self.tracked.items() for name in names
              if self.complete or bus == Bus.cam and name == 'LKAS_HUD']
    return self.interface.update([(now_ns, frames)])


@pytest.mark.parametrize('complete', (True, False))
def test_actual_factory_interface_can_validity_gates_lkas_delivery(tmp_path, complete):
  params, cp = Params(str(tmp_path)), factory()
  params.put(KEY, 8, block=True)
  bench = FactoryToyotaInterface(cp, complete=complete)
  for tick in range(8):
    physical = bench.sample(NOW - 90_000_000 + tick * 10_000_000)
  assert physical.canValid == complete
  if complete:
    assert not physical.canTimeout
    assert physical.cruiseState.available
    assert physical.gearShifter == structs.CarState.GearShifter.drive
    assert not (physical.doorOpen or physical.seatbeltUnlatched or physical.steerFaultPermanent)
  source = WheelPublisher(params)
  neutral = bench.sample(NOW - 10_000_000)
  source.observe(params, cp, neutral, now_ns=NOW - 10_000_000, drive_id=DRIVE)
  physical = bench.sample(NOW, lda=1)
  assert physical.canValid == complete  # Never override parser or interface health.
  assert [(e.type, e.pressed) for e in physical.buttonEvents] == [(TYPE.lkas, True), (TYPE.lkas, False)]
  commands = source.observe(params, cp, physical, now_ns=NOW, drive_id=DRIVE)
  assert commands == (((KEY, 8),) if complete else ())
  sender = Publisher()
  # For missing CAN, even a forged publication cannot pass current source health.
  source.publish(commands if complete else ((KEY, 8),), cp, sender, now_ns=NOW, drive_id=DRIVE,
                 source_car_ns=NOW, source_control_ns=NOW)
  sm = services(cp)
  sm['carState'] = physical
  accepted = WheelConsumer().accept(messaging.log_from_bytes(sender.events[-1][1]), params, cp, sm, now_ns=NOW)
  if complete:
    assert (accepted.key, accepted.action) == (KEY, 8)
  else:
    assert accepted is None


@pytest.mark.parametrize('vehicle', (CAR.TOYOTA_SIENNA_4TH_GEN, CAR.TOYOTA_RAV4_TSS2_2023))
def test_parser_supported_toyota_auxiliary_lkas_does_not_require_aol_admission(tmp_path, vehicle):
  params, cp = Params(str(tmp_path)), factory(vehicle)
  baseline = cp.to_dict()
  policy = policy_for(cp)
  # Bare factory RAV4 uses qualified angle AOL; keyless SecOC Sienna stays refused.
  admitted = vehicle == CAR.TOYOTA_RAV4_TSS2_2023
  assert all(value == admitted for value in (policy.intent_supported, policy.settings_supported, policy.runtime_supported,
                                            policy.normal_runtime_supported, policy.full_axis_runtime_required))
  assert policy.alternative_experience_addition == (32 if admitted else 0)
  assert policy.safety_param_addition == 0
  assert policy.auxiliary_lkas_supported
  wheel = feature(params, cp)
  rows = {row.key: row for row in wheel.rows()}
  row = rows['wheel:' + KEY]
  assert 'Bookmark' in row.choices
  assert not {'Pause steering', 'Pause longitudinal', 'Toggle AOL'} & set(row.choices)
  assert not any('Cancel' in key for key in rows)
  assert ('wheel:MainCruiseButtonControl' in rows) == admitted
  if admitted:
    main = rows['wheel:MainCruiseButtonControl']
    assert main.value == 'Factory cruise / AOL' and not main.available and not main.choices
    assert not wheel.apply(FeatureSettingsRequest(main.key, main.source, 'Toggle AOL', capability=main.capability))
  assert wheel.apply(FeatureSettingsRequest(row.key, row.source, 'Bookmark', capability=row.capability))
  assert not wheel.apply(FeatureSettingsRequest(row.key, b'8', 'Toggle AOL', capability=row.capability))
  # This bench uses full factory parser transport, without forcing CAN validity.
  bench = FactoryToyotaInterface(cp, complete=True)
  for tick in range(8):
    physical = bench.sample(NOW - 90_000_000 + tick * 10_000_000)
  assert physical.canValid and not physical.canTimeout
  source = WheelPublisher(params)
  source.observe(params, cp, bench.sample(NOW - 10_000_000), now_ns=NOW - 10_000_000, drive_id=DRIVE)
  physical = bench.sample(NOW, lda=1)
  assert physical.canValid and not physical.canTimeout
  assert [(e.type, e.pressed) for e in physical.buttonEvents] == [(TYPE.lkas, True), (TYPE.lkas, False)]
  commands = source.observe(params, cp, physical, now_ns=NOW, drive_id=DRIVE)
  assert commands == ((KEY, 8),)
  sender = Publisher()
  source.publish(commands, cp, sender, now_ns=NOW, drive_id=DRIVE, source_car_ns=NOW, source_control_ns=NOW)
  sm = services(cp)
  sm['carState'] = physical
  accepted = WheelConsumer().accept(messaging.log_from_bytes(sender.events[-1][1]), params, cp, sm, now_ns=NOW)
  assert (accepted.key, accepted.action) == (KEY, 8)
  assert cp.to_dict() == baseline  # Auxiliary settings grant no CP/native authority.


@pytest.mark.parametrize('vehicle', (CAR.TOYOTA_SIENNA_4TH_GEN, CAR.TOYOTA_RAV4_TSS2_2023))
def test_aol_unsupported_toyota_bookmark_reaches_real_native_callback(native_session, monkeypatch, vehicle):
  runtime, session, ui, _, _ = native_session
  ui.CP = factory(vehicle)
  ui.has_longitudinal_control = ui.CP.openpilotLongitudinalControl
  assert policy_for(ui.CP).intent_supported == (vehicle == CAR.TOYOTA_RAV4_TSS2_2023)
  calls = []
  session._native_favorite_actions = lambda: session.native_favorite_actions(
    lambda: calls.append('bookmark') or True, lambda _: True, lambda: True)
  queue = [emit(ui.params, ui.CP, 8)]
  monkeypatch.setattr(runtime.messaging, 'recv_one_or_none', lambda _: queue.pop(0) if queue else None)
  session._poll_wheel(NOW)
  session._poll_wheel(NOW)
  assert calls == ['bookmark']


@pytest.mark.parametrize('field', ('notCar', 'passive', 'dashcamOnly'))
def test_toyota_auxiliary_lkas_keeps_existing_eligibility_limits(tmp_path, field):
  cp = factory(CAR.TOYOTA_SIENNA_4TH_GEN)
  setattr(cp, field, True)
  assert not policy_for(cp).auxiliary_lkas_supported
  assert feature(Params(str(tmp_path)), cp).rows() == []


def test_toyota_without_parser_lkas_source_has_no_auxiliary_row(tmp_path):
  cp = factory(CAR.TOYOTA_SIENNA)
  assert not policy_for(cp).auxiliary_lkas_supported
  assert feature(Params(str(tmp_path)), cp).choices(KEY) == ()
