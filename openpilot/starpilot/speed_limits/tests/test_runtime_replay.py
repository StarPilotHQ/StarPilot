"""Native parser packets through typed SLC state and the opt-in planner ceiling."""

import math
import json
import tempfile
import time
import unittest
from dataclasses import replace
from collections import deque
from contextlib import ExitStack
from itertools import product
from types import SimpleNamespace
from typing import cast
from unittest import mock

from opendbc.can import CANPacker, CANParser
from opendbc.car.dashboard_speed_limit import Tracker, Status, ford_sign, honda_sign, hyundai_canfd_sign, parser_expiry, toyota_sign
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.car.hyundai.carstate import CarState as HyundaiCarState
from opendbc.car.hyundai.interface import CarInterface as HyundaiCarInterface
from opendbc.car.hyundai.values import CAR as HyundaiCAR, HyundaiFlags
from opendbc.car import Bus
from opendbc.car.honda.carstate import CarState as HondaCarState
from opendbc.car.toyota.carstate import CarState as ToyotaCarState
from opendbc.car.toyota.interface import CarInterface as ToyotaCarInterface
from opendbc.car.toyota.values import CAR as ToyotaCAR
from opendbc.car.ford.carstate import CarState as FordCarState
from opendbc.car.ford.interface import CarInterface as FordCarInterface
from opendbc.car.ford.values import CAR as FordCAR
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
from openpilot.starpilot.speed_limits.runtime import Action, CruiseEvent, Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse
from openpilot.starpilot.speed_limits.runtime_settings import read_params
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.speed_limits.selection import Source
from openpilot.starpilot.speed_limits.acceptance import ObservationKind
from openpilot.starpilot.speed_limits import acceptance as acc, history
from openpilot.starpilot.speed_limits import physical_actions
from openpilot.selfdrive.car.cruise import VCruiseHelper, SlcPendingConfirmation
from opendbc.car.structs import car


class ReplaySM:
  def __init__(self, data, timestamp):
    self.data = data
    self.valid = dict.fromkeys(data, True)
    self.updated = dict.fromkeys(data, True)
    self.alive = dict.fromkeys(data, True)
    self.logMonoTime = dict.fromkeys(data, timestamp)

  def __getitem__(self, name):
    return self.data[name]

  def advance(self, timestamp):
    self.logMonoTime = dict.fromkeys(self.data, timestamp)
    self.data['slcDashboardObservation'].carStateLogMonoTime = timestamp

  def update(self, timeout: int = 100) -> None:
    pass


class NativeSourceTests(unittest.TestCase):
  def test_brand_carstate_trackers_receive_real_packets(self):
    cases = (
      (CarInterface, CAR.HONDA_CIVIC_BOSCH, HondaCarState, 'CAMERA_MESSAGES',
       {'SPEED_LIMIT_SIGN': 104}, {'SPEED_LIMIT_SIGN': 1}, 17.8816),
      (ToyotaCarInterface, ToyotaCAR.TOYOTA_RAV4, ToyotaCarState, 'RSA1',
       {'TSGN1': 36, 'SPDVAL1': 45}, {'TSGN1': 35, 'SPDVAL1': 45}, 20.1168),
      (FordCarInterface, FordCAR.FORD_ESCAPE_MK4, FordCarState, 'Traffic_RecognitnData',
       {'TsrVLim1MsgTxt_D_Rq': 45, 'TsrVlUnitMsgTxt_D_Rq': 2},
       {'TsrVLim1MsgTxt_D_Rq': 45, 'TsrVlUnitMsgTxt_D_Rq': 0}, 20.1168),
      (HyundaiCarInterface, HyundaiCAR.HYUNDAI_IONIQ_6, HyundaiCarState, 'FR_CMR_02_100ms',
       {'ISLW_SysSta': 0, 'ISLW_SpdCluMainDis': 72},
       {'ISLW_SysSta': 1, 'ISLW_SpdCluMainDis': 72}, 20.0),
    )
    for interface, fingerprint, state_type, message, signals, unsupported, expected in cases:
      with self.subTest(fingerprint=fingerprint):
        cp = interface.get_non_essential_params(fingerprint)
        state = state_type(cp)
        parsers = state.get_can_parsers(cp)
        subscribed = [(bus, parser) for bus, parser in parsers.items()
                      if (definition := parser.dbc.name_to_msg.get(message)) is not None and
                      definition.address in parser.message_states]
        self.assertEqual(len(subscribed), 1)
        _, parser = subscribed[0]
        packer = CANPacker(parser.dbc.name)
        for i in range(6):
          timestamp = 1_000_000_000 + i * 300_000_000
          parser.update([timestamp, [packer.make_can_msg(message, parser.bus, {key: float(value) for key, value in signals.items()})]])
        state.update(parsers)
        observation = state.dashboard_limit.observation
        self.assertEqual(observation.status, Status.VALID)
        self.assertAlmostEqual(observation.speed_mps, expected)
        self.assertEqual(observation.observed_ns, timestamp)
        self.assertGreater(observation.valid_until_ns, timestamp)
        parser.update([timestamp + 300_000_000, [packer.make_can_msg(message, parser.bus, {key: float(value) for key, value in unsupported.items()})]])
        state.update(parsers)
        self.assertEqual(state.dashboard_limit.observation.status, Status.UNKNOWN)
        self.assertEqual(state.dashboard_limit.observation.speed_mps, 0.0)

  def test_real_dbc_packets_and_parser_expiry(self):
    cases = (
      ('honda_civic_hatchback_ex_2017_can_generated', 'CAMERA_MESSAGES',
       {'SPEED_LIMIT_SIGN': 104}, 'SPEED_LIMIT_SIGN', lambda: honda_sign(104), 17.8816),
      ('toyota_nodsu_pt_generated', 'RSA1', {'TSGN1': 36, 'SPDVAL1': 45},
       'TSGN1', lambda: toyota_sign(36, 45), 20.1168),
      ('ford_lincoln_base_pt', 'Traffic_RecognitnData',
       {'TsrVLim1MsgTxt_D_Rq': 45, 'TsrVlUnitMsgTxt_D_Rq': 2},
       'TsrVLim1MsgTxt_D_Rq', lambda: ford_sign(45, 2), 20.1168),
      ('hyundai_canfd_generated', 'FR_CMR_02_100ms',
       {'ISLW_SysSta': 0, 'ISLW_SpdCluMainDis': 72},
       'ISLW_SpdCluMainDis', lambda: hyundai_canfd_sign(72, 0, True), 20.0),
    )
    for dbc, message, signals, signal, decode, expected_mps in cases:
      with self.subTest(message=message):
        packer = CANPacker(dbc)
        parser = CANParser(dbc, [(message, math.nan)], 0)
        self.assertEqual(parser_expiry(parser, message, signal), (0, 0))
        for i in range(6):
          timestamp = 1_000_000_000 + i * 300_000_000
          parser.update([timestamp, [packer.make_can_msg(message, 0, {key: float(value) for key, value in signals.items()})]])
        observed, expiry = parser_expiry(parser, message, signal)
        self.assertEqual(observed, timestamp)
        self.assertGreater(expiry, observed)
        self.assertEqual(parser.vl[message][signal], signals[signal])
        kind, value = decode()
        self.assertIs(kind, Status.VALID)
        self.assertAlmostEqual(value, expected_mps)

  def test_invalid_sign_does_not_preserve_previous_limit_and_reconnects(self):
    tracker = Tracker()
    first = tracker.update(100, *honda_sign(104), valid_until_ns=200)
    self.assertEqual(first.status, Status.VALID)
    unknown = tracker.update(150, *honda_sign(1), valid_until_ns=250)
    self.assertEqual(unknown.status, Status.UNKNOWN)
    self.assertEqual(unknown.speed_mps, 0)
    again = tracker.update(300, *honda_sign(104), valid_until_ns=400)
    self.assertEqual(again.status, Status.VALID)
    self.assertGreater(again.episode, first.episode)
    self.assertEqual(tracker.update(350, Status.VALID, math.inf, valid_until_ns=450), again)
    self.assertEqual(hyundai_canfd_sign(0, 0, True), (Status.ABSENT, 0.0))
    self.assertEqual(hyundai_canfd_sign(253, 0, True), (Status.ABSENT, 0.0))
    self.assertEqual(hyundai_canfd_sign(72, 1, True), (Status.UNKNOWN, 0.0))

  def test_hyundai_optional_sign_subscription_follows_source_bus(self):
    cp = HyundaiCarInterface.get_non_essential_params(HyundaiCAR.HYUNDAI_IONIQ_6)
    for hda2, expected_bus in ((False, Bus.cam), (True, Bus.pt)):
      with self.subTest(hda2=hda2):
        if hda2:
          cp.flags = int(cp.flags | HyundaiFlags.CANFD_LKA_STEER_MSG)
        parsers = HyundaiCarState(cp).get_can_parsers(cp)
        for bus, parser in parsers.items():
          message = parser.dbc.name_to_msg['FR_CMR_02_100ms']
          subscribed = message.address in parser.message_states
          self.assertEqual(subscribed, bus == expected_bus)
          if subscribed:
            self.assertTrue(parser.message_states[message.address].ignore_alive)


class RuntimeReplayTests(unittest.TestCase):
  def test_card_reserves_optional_slc_before_saved_admission(self):
    from opendbc.car.gm.values import CAR as GM_CAR
    from opendbc.car.gm.tests.test_bolt_cc import params as bolt_params
    from openpilot.starpilot.aol.tests.test_gm import TestGmAol
    from openpilot.cereal import messaging
    for active in (False, True):
      with self.subTest(active=active), OpenpilotPrefix(), mock.patch.dict('os.environ', {
        'SLC_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0',
        'CONDITIONAL_MODE_REPLAY_RUNTIME': '0', 'CURVE_REPLAY_RUNTIME': '0',
      }):
        messaging.reset_context()
        params = Params()
        params.put_bool('SpeedLimitController', False, block=True)
        params.put_bool('ShowSpeedLimits', False, block=True)
        params.put('SLCPriority1', 'Dashboard', block=True)
        params.put_bool('DisableOpenpilotLongitudinal', not active, block=True)
        process = TestGmAol.card(bolt_params(GM_CAR.CHEVROLET_BOLT_CC_2018_2021,
                                           alpha=True, present=True), params)
        self.assertTrue(process.slc_transport_available)
        self.assertFalse(process.slc_replay)
        self.assertFalse(process.slc_control_enabled)
        self.assertIn('slcState', process.sm.data)
        self.assertIn('slcState', process.sm.ignore_alive)
        self.assertIn('slcState', process.sm.ignore_valid)
        self.assertIn('slcState', process.sm.ignore_average_freq)
        self.assertFalse(hasattr(process, 'slc_command_sock'))
        self.assertIn('slcDashboardObservation', process.pm.sock)
        shared_producer = process.slc_producer_session
        params.put_bool('SpeedLimitController', True, block=True)
        process.slc_requested_configuration = process.read_slc_configuration()
        process.refresh_slc_configuration(1_000_000_000)
        self.assertTrue(process.slc_replay)
        self.assertEqual(process.slc_control_enabled, active)
        self.assertEqual(process.CP.openpilotLongitudinalControl, active)
        self.assertTrue(params.get_bool('SpeedLimitController'))
        params.put_bool('SpeedLimitController', False, block=True)
        params.put_bool('ShowSpeedLimits', True, block=True)
        process.slc_requested_configuration = process.read_slc_configuration()
        process.refresh_slc_configuration(2_000_000_000)
        self.assertTrue(process.slc_replay)
        self.assertFalse(process.slc_control_enabled)
        self.assertEqual(process.slc_producer_session, shared_producer)
        params.put('SLCPriority1', 'broken source', block=True)
        process.slc_requested_configuration = process.read_slc_configuration()
        process.refresh_slc_configuration(3_000_000_000)
        self.assertFalse(process.slc_replay)
        self.assertFalse(process.slc_control_enabled)

  def test_isolated_ipc_submaster_handles_independent_card_and_model_cadences(self):
    from openpilot.cereal import messaging
    with OpenpilotPrefix():
      messaging.reset_context()
      pm = messaging.PubMaster(['carState', 'slcDashboardObservation', 'slcState', 'modelV2'])
      sm = messaging.SubMaster(['carState', 'slcDashboardObservation', 'slcState', 'modelV2'], poll='modelV2')
      base_ns = time.monotonic_ns() - 50_000_000

      def publish(service, stamp_ns):
        message = messaging.new_message(service)
        message.valid = True
        message.logMonoTime = stamp_ns
        pm.send(service, message)
        return message

      car = messaging.new_message('carState')
      car.valid = True
      car.logMonoTime = base_ns + 10_000_000
      pm.send('carState', car)
      source = messaging.new_message('slcDashboardObservation')
      source.valid = True
      source.logMonoTime = base_ns
      source.slcDashboardObservation.producerSessionId = 'ipc-card'
      source.slcDashboardObservation.carStateLogMonoTime = base_ns
      source.slcDashboardObservation.status = 'valid'
      source.slcDashboardObservation.speedMps = 25.0
      source.slcDashboardObservation.observedMonoTime = base_ns - 20_000_000
      source.slcDashboardObservation.validUntilMonoTime = base_ns + 1_000_000_000
      source.slcDashboardObservation.episode = 1
      pm.send('slcDashboardObservation', source)
      state = messaging.new_message('slcState')
      state.valid = True
      state.logMonoTime = base_ns + 5_000_000
      state.slcState.sessionId = 'ipc-drive'
      state.slcState.frameMonoTime = base_ns + 5_000_000
      state.slcState.enabled = True
      state.slcState.observationKind = 'valid'
      state.slcState.source = 'dashboard'
      state.slcState.speedLimit = 25.0
      state.slcState.hasPending = True
      state.slcState.pendingSpeedLimit = 25.0
      state.slcState.pendingSource = 'dashboard'
      state.slcState.pendingSinceMonoTime = base_ns
      state.slcState.decisionId = 1
      state.slcState.presentationId = 1
      state.slcState.sourceProducerSessionId = 'ipc-card'
      state.slcState.sourceEpisode = 1
      state.slcState.sourceObservedMonoTime = base_ns - 20_000_000
      state.slcState.sourceValidUntilMonoTime = base_ns + 1_000_000_000
      pm.send('slcState', state)
      publish('modelV2', base_ns + 5_000_000)
      sm.update(100)
      now_ns = time.monotonic_ns()
      self.assertGreater(sm.logMonoTime['carState'], sm.logMonoTime['modelV2'])
      self.assertEqual(sm.logMonoTime['carState'] - sm.logMonoTime['slcDashboardObservation'], 10_000_000)
      self.assertTrue(physical_actions.pending_confirmation(
        sm, now_ns, long_active=True))
      runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}), session_id='ipc-drive')
      self.assertEqual(runtime._observations(sm, self.cp, now_ns)[Source.DASHBOARD].kind, ObservationKind.VALID)

      newer = messaging.new_message('carState')
      newer.valid = True
      newer.logMonoTime = base_ns + 35_000_000
      pm.send('carState', newer)
      publish('modelV2', base_ns + 40_000_000)
      sm.update(100)
      self.assertNotEqual(runtime._observations(sm, self.cp, time.monotonic_ns())[Source.DASHBOARD].kind,
                          ObservationKind.VALID)

      # Card runs at 100 Hz while the planner state runs at 20 Hz. A new TSR
      # packet can outrun the planner; the fresh presented decision still owns confirmation.
      local = SimpleNamespace(status=Status.VALID, speed_mps=25.0, observed_ns=base_ns - 20_000_000,
                              valid_until_ns=base_ns + 1_000_000_000, episode=1)
      qualified = 0
      for tick in range(20):
        if tick in (7, 17):
          local.observed_ns = time.monotonic_ns()
          local.episode += 1
        if tick % 5 == 0:
          stamp = time.monotonic_ns()
          cadence_state = messaging.new_message('slcState')
          cadence_state.valid = True
          cadence_state.logMonoTime = stamp
          snapshot = cadence_state.slcState
          snapshot.frameMonoTime = stamp
          snapshot.sessionId = 'ipc-drive'
          snapshot.enabled = True
          snapshot.observationKind = 'valid'
          snapshot.source = 'dashboard'
          snapshot.speedLimit = 25.0
          snapshot.hasPending = True
          snapshot.pendingSpeedLimit = 25.0
          snapshot.pendingSource = 'dashboard'
          snapshot.pendingSinceMonoTime = base_ns
          snapshot.decisionId = 1
          snapshot.presentationId = 1
          snapshot.sourceProducerSessionId = 'ipc-card'
          snapshot.sourceEpisode = local.episode
          snapshot.sourceObservedMonoTime = local.observed_ns
          snapshot.sourceValidUntilMonoTime = local.valid_until_ns
          pm.send('slcState', cadence_state)
        sm.update(0)
        pending = physical_actions.pending_confirmation(sm, time.monotonic_ns(), long_active=True)
        qualified += pending is not None
        time.sleep(0.01)
      self.assertGreaterEqual(qualified, 12)

  def test_card_consumes_physical_press_through_release_without_replay(self):
    helper = VCruiseHelper(car.CarParams(pcmCruise=False))
    helper.v_cruise_kph = 70.0
    pending = SlcPendingConfirmation('drive', 1, 1)
    press = car.CarState(cruiseState={'available': True}, buttonEvents=[car.CarState.ButtonEvent(type='accelCruise', pressed=True)])
    held = car.CarState(cruiseState={'available': True})
    release = car.CarState(cruiseState={'available': True}, buttonEvents=[car.CarState.ButtonEvent(type='accelCruise', pressed=False)])
    helper.update_v_cruise(press, True, False, pending)
    self.assertEqual(helper.slc_consumed_button.pending, pending)
    self.assertEqual(helper.v_cruise_kph, 70.0)
    for _ in range(55):
      helper.update_v_cruise(held, True, False)
      self.assertEqual(helper.v_cruise_kph, 70.0)
      self.assertIsNone(helper.slc_consumed_button)
    helper.update_v_cruise(release, True, False)
    self.assertEqual(helper.v_cruise_kph, 70.0)
    self.assertFalse(helper.slc_suppressed_buttons)
    helper.update_v_cruise(press, True, False)
    helper.update_v_cruise(release, True, False)
    self.assertGreater(helper.v_cruise_kph, 70.0)

    helper.v_cruise_kph = 70.0
    helper.update_v_cruise(press, True, False, pending)
    helper.update_v_cruise(held, False, False)
    for _ in range(55):
      helper.update_v_cruise(held, True, False)
      self.assertEqual(helper.v_cruise_kph, 70.0)
    helper.update_v_cruise(release, True, False)
    self.assertEqual(helper.v_cruise_kph, 70.0)
    self.assertFalse(helper.slc_suppressed_buttons)


  def test_physical_decel_rejects_without_selected_speed_command(self):
    settings = parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1})
    runtime = Runtime(settings, session_id='physical-reject')
    first = runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    self.assertTrue(first.message.slcState.hasPending)
    self.sm.advance(2_010_000_000)
    first = runtime.step(self.sm, self.cp, now_ns=2_010_000_000,
                         request=Action(runtime.session_id, 1, first.message.slcState.decisionId,
                                        first.message.slcState.presentationId, 'accept'))
    self.assertTrue(first.message.slcState.hasAccepted)
    source = self.sm['slcDashboardObservation']
    source.speedMps = 15.0
    source.observedMonoTime = 2_040_000_000
    source.episode = 2
    self.sm.advance(2_050_000_000)
    pending = runtime.step(self.sm, self.cp, now_ns=2_050_000_000).message.slcState
    self.assertTrue(pending.hasPending)
    self.sm.advance(2_100_000_000)
    rejected = runtime.step(self.sm, self.cp, now_ns=2_100_000_000,
                            cruise_event=CruiseEvent(1, 2_100_000_000, 0.0, 0.0, 'decel', 'card-producer',
                                                     2_100_000_000, 'confirmationReject', runtime.session_id,
                                                     pending.decisionId, pending.presentationId))
    self.assertEqual(rejected.message.slcState.actionStatus, 'reject')
    self.assertEqual(rejected.result.acceptance.basis, 'driver_reject')
    self.assertEqual(rejected.result.acceptance.state.accepted, first.result.acceptance.state.accepted)
    self.assertFalse(rejected.message.slcState.hasPending)
    self.assertEqual(runtime.ledger.transactions, ())

  def test_identified_ui_adopt_updates_the_cap_without_changing_set(self):
    runtime = Runtime(parse({'SpeedLimitController': True, 'SLCConfirmation': 1, 'SLCPriority1': 'Dashboard'}))
    shown = runtime.step(self.sm, self.cp, now_ns=2_000_000_000).message.slcState
    self.sm.advance(2_050_000_000)
    adopted = runtime.step(self.sm, self.cp, now_ns=2_050_000_000,
      request=Action(runtime.session_id, 1, 0, shown.presentationId, 'adopt'))
    self.assertEqual(adopted.message.slcState.actionStatus, 'adopt')
    self.assertEqual(adopted.result.ceiling.speed_mps, 25.)
    self.assertEqual(self.sm['carState'].vCruise, 100.)


  def _card_with_slc_state(self, pending, observation, now_ns):
    from openpilot.selfdrive.car import card
    fake = card.Car.__new__(card.Car)
    fake.conditional_replay = False
    fake.switchback_button_tracker = None
    fake.switchback_settings_owner = None
    from openpilot.starpilot.controllers.wheel_actions import WheelPublisher
    fake.params = Params()
    from openpilot.starpilot.vehicle_startup import VehicleStartupOwner
    fake.vehicle_startup = VehicleStartupOwner()
    fake.wheel_publisher = WheelPublisher(fake.params)
    fake.car_gps_publisher = card.CarGpsPublisher()
    fake.pm = mock.Mock()
    fake.ioniq6_long_prearmed = False
    fake.slc_replay = True
    fake.slc_control_enabled = True
    fake.slc_source_floor_ns = 0
    fake.slc_configuration = (True, parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}))
    fake.slc_requested_configuration = fake.slc_configuration
    fake.aol_card_intent = None
    fake.slc_producer_session = 'card-producer'
    fake.slc_command_sock = object()
    fake.slc_commands = deque(maxlen=8)
    fake.slc_command_session = ''
    fake.slc_last_command_id = 0
    fake.slc_receipts = []
    fake.can_sock = object()
    fake.can_rcv_cum_timeout_counter = 0
    fake.is_metric = False
    fake.CP = car.CarParams(openpilotLongitudinalControl=True, pcmCruise=False)
    fake.CC_prev = car.CarControl(enabled=True)
    fake.v_cruise_helper = VCruiseHelper(fake.CP)
    fake.v_cruise_helper.v_cruise_kph = 60.0
    fake.sm = cast(card.messaging.SubMaster, ReplaySM(
      {'carControl': car.CarControl(enabled=True, longActive=True), 'slcState': pending, 'deviceState': SimpleNamespace(startedMonoTime=1)}, now_ns))
    current_cs = [car.CarState(canValid=True, cruiseState={'available': True},
                               buttonEvents=[car.CarState.ButtonEvent(type='accelCruise', pressed=True)])]
    self.enterContext(mock.patch.object(fake, 'CI', SimpleNamespace(
      CC=None, CS=SimpleNamespace(dashboard_limit=SimpleNamespace(observation=observation) if observation is not None else None),
      update=lambda packets: current_cs[0]), create=True))
    self.enterContext(mock.patch.object(fake, 'RI', SimpleNamespace(update=lambda packets: None), create=True))
    return fake, current_cs

  def test_wheel_confirmation_all_sources_and_normal_cruise_without_pending(self):
    from openpilot.cereal import messaging
    from openpilot.selfdrive.car import card
    from openpilot.selfdrive.controls.plannerd import current_cruise_event, queue_cruise_event
    from openpilot.starpilot.speed_limits.tests.test_maps_runtime import BOOT, packet
    from openpilot.starpilot.speed_limits.vision.observation import MODEL_ID

    start = 2_000_000_000
    sources = (('Dashboard', True), ('Maps', True), ('Maps', False), ('Vision', True), ('Vision', False))
    buttons = (('accelCruise', 'accept', 61.0), ('decelCruise', 'reject', 59.0))
    for (source_name, dashboard_present), (button, outcome, normal_speed), has_pending in product(sources, buttons, (True, False)):
      with self.subTest(source=source_name, button=button, pending=has_pending, dashboard=dashboard_present):
        self.sm.advance(start)
        self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = 60.0
        dashboard = self.sm['slcDashboardObservation']
        dashboard.status, dashboard.producerSessionId, dashboard.episode = 'valid', 'card-producer', 1
        dashboard.speedMps, dashboard.observedMonoTime, dashboard.validUntilMonoTime = 25.0, start - 100_000_000, start + 1_000_000_000
        if source_name == 'Maps':
          self.sm.data['mapdOut'] = packet(speedLimit=25.0).mapdOut
        if source_name == 'Vision':
          vision_event = messaging.new_message('slcVisionObservation')
          vision = vision_event.slcVisionObservation.init('vision')
          vision.producerSessionId, vision.status, vision.stream, vision.modelId = 'camera', 'valid', 'road', MODEL_ID
          vision.speedMps, vision.confidence, vision.supportCount, vision.episode, vision.frameId = 25.0, 0.9, 2, 1, 1
          vision.observedMonoTime, vision.validUntilMonoTime, vision.cameraFrameEofBootTime = start, start + 250_000_000, BOOT
          self.sm.data['slcVisionObservation'] = vision_event.slcVisionObservation
          self.sm.logMonoTime['slcVisionObservation'] = start
        self.sm.logMonoTime['mapdOut'] = BOOT
        for service in self.sm.data:
          for metadata in (self.sm.valid, self.sm.alive, self.sm.updated):
            metadata[service] = True
        settings = parse({'SpeedLimitController': True, 'SLCPriority1': source_name,
                          'SLCConfirmation': 1})
        runtime = Runtime(settings, session_id='wheel-drive', vision_enabled=True, vision_control_qualified=True)
        wire = runtime.step(self.sm, self.cp, now_ns=start, now_boot_ns=BOOT).message.slcState
        self.assertTrue(wire.hasPending)
        self.assertEqual(str(wire.source), {'Dashboard': 'dashboard', 'Maps': 'map', 'Vision': 'vision'}[source_name])
        now = start
        if not has_pending:
          now += 10_000_000
          self.sm.advance(now)
          self.sm.logMonoTime['mapdOut'] = BOOT
          wire = runtime.step(self.sm, self.cp, now_ns=now, now_boot_ns=BOOT + now - start,
                              request=Action(runtime.session_id, 1, wire.decisionId, wire.presentationId, 'accept')).message.slcState
          self.assertFalse(wire.hasPending)
        evidence = self.sm['slcDashboardObservation']
        observation = (SimpleNamespace(status=Status.VALID, speed_mps=evidence.speedMps, episode=evidence.episode,
                                       observed_ns=evidence.observedMonoTime, valid_until_ns=evidence.validUntilMonoTime)
                       if dashboard_present else None)
        fake, current_cs = self._card_with_slc_state(wire, observation, now)
        fake.is_metric = True
        fake.car_params_published = True
        fake.sm.frame = 1
        fake.sm.all_checks = lambda services: True
        fake.last_actuators_output = car.CarControl.Actuators()
        fake.rk = SimpleNamespace(remaining=0.0)
        fake.aol_replay = False
        fake.slc_cruise_event_id = 0
        fake.radar_publish = mock.Mock()
        clock = [now]
        with (mock.patch.object(card, 'REPLAY', False),
              mock.patch.object(card.time, 'monotonic_ns', side_effect=lambda clock=clock: clock[0]),
              mock.patch.object(card.time, 'monotonic', side_effect=lambda clock=clock: clock[0] / 1e9),
              mock.patch.object(card.messaging, 'drain_sock_raw', return_value=[]),
              mock.patch.object(card.messaging, 'recv_one_or_none', return_value=None)):
          for pressed in (True, False):
            current_cs[0] = car.CarState(canValid=True, cruiseState={'available': True},
                                         buttonEvents=[car.CarState.ButtonEvent(type=button, pressed=pressed)])
            cs, radar = fake.state_update()
            fake.state_publish(cs, radar)
            clock[0] += 10_000_000
            fake.sm.logMonoTime['carControl'] = clock[0]
        events = [call.args[1] for call in fake.pm.send.call_args_list if call.args[0] == 'slcCruiseEvent']
        self.assertEqual(len(events), 1)
        event = events[0].slcCruiseEvent
        self.assertEqual(str(event.kind), ('confirmationAccept' if outcome == 'accept' else 'confirmationReject')
                         if has_pending else 'driverChange')
        self.assertEqual(fake.v_cruise_helper.v_cruise_kph, 60.0 if has_pending else normal_speed)
        self.assertFalse(fake.v_cruise_helper.slc_suppressed_buttons)
        slc_events, curve_events = deque(), deque()
        queue_cruise_event(events[0], slc_events, curve_events)
        self.sm.advance(clock[0])
        self.sm.logMonoTime['mapdOut'] = BOOT
        publications = {call.args[0]: call.args[1] for call in fake.pm.send.call_args_list}
        for service in ('carState', 'slcDashboardObservation'):
          self.sm.data[service] = getattr(publications[service], service)
          self.sm.logMonoTime[service] = int(publications[service].logMonoTime)
        result = runtime.step(self.sm, self.cp, now_ns=clock[0], now_boot_ns=BOOT + clock[0] - start,
                              cruise_event=current_cruise_event(slc_events, clock[0]))
        self.assertFalse(result.message.slcState.hasPending)
        if has_pending:
          self.assertEqual(result.message.slcState.actionStatus, outcome)


  def test_physical_wheel_override_receipt_through_card_and_planner_queue(self):
    from openpilot.cereal import messaging
    from openpilot.selfdrive.car import card
    from openpilot.selfdrive.controls.plannerd import current_cruise_event, queue_cruise_event, wheel_override_blocked
    from openpilot.starpilot.curve_speed.host import CurveHost

    self.cp.pcmCruise = False
    self.sm['carState'].canValid = True
    self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = 90.
    settings = parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCWheelOverride': True})
    for curve_claims, curve_backlog in ((False, False), (True, False), (False, True)):
      with self.subTest(curve_claims=curve_claims, curve_backlog=curve_backlog):
        start = 2_000_000_000
        self.sm.advance(start)
        self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = 90.
        runtime = Runtime(settings, is_metric=True)
        state = runtime.step(self.sm, self.cp, now_ns=start).message.slcState
        fake, current_cs = self._card_with_slc_state(state, None, start)
        fake.slc_configuration = fake.slc_requested_configuration = (True, settings)
        fake.v_cruise_helper.v_cruise_kph = fake.v_cruise_helper.v_cruise_cluster_kph = 90.
        fake.is_metric = True
        fake.curve_replay = True
        fake.car_params_published = True
        fake.sm.frame = 1
        fake.sm.all_checks = lambda services: True
        fake.last_actuators_output = car.CarControl.Actuators()
        fake.slc_cruise_event_id = 0
        fake.radar_publish = mock.Mock()
        fake.rk = SimpleNamespace(remaining=0.0)
        fake.aol_replay = False
        clock = [start + 10_000_000]

        def can_batch(*args, clock=clock, **kwargs):
          return [messaging.new_message('can', size=0, logMonoTime=clock[0]).to_bytes()]

        with (mock.patch.object(card, 'REPLAY', True),
              mock.patch.object(card.messaging, 'drain_sock_raw', side_effect=can_batch)):
          for pressed in (True, False):
            current_cs[0] = car.CarState(canValid=True, cruiseState={'available': True},
                                        buttonEvents=[car.CarState.ButtonEvent(type='accelCruise', pressed=pressed)])
            cs, radar = fake.state_update()
            fake.state_publish(cs, radar)
            clock[0] += 10_000_000
            fake.sm.logMonoTime['carControl'] = clock[0]
        slc_events, curve_events = deque(), deque()
        for call in fake.pm.send.call_args_list:
          name, message = call.args
          if name == 'slcCruiseEvent':
            queue_cruise_event(messaging.log_from_bytes(message.to_bytes()), slc_events, curve_events)
        self.assertEqual(len(curve_events), 1)
        self.assertEqual(len(slc_events), 1)
        self.assertEqual(cs.vCruise, 91.)
        self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = cs.vCruise
        self.sm.advance(clock[0])
        event = current_cruise_event(slc_events, clock[0])
        self.assertEqual(event.wheel_override_since_ns, start)
        curve = CurveHost()
        curve.runtime.was_controlling = curve_claims
        output = runtime.step(self.sm, self.cp, now_ns=clock[0], cruise_event=event,
                              wheel_override_blocked=wheel_override_blocked(None, curve, curve_backlog=curve_backlog))
        self.assertEqual(output.message.slcState.driverOverrideActive, not (curve_claims or curve_backlog))

  def test_malformed_saved_priorities_disable_runtime_without_crashing(self):
    for key in ('SLCPriority1', 'SLCPriority2'):
      for malformed in ([], {}, 1, None):
        with self.subTest(key=key, malformed=malformed):
          settings = parse({'SpeedLimitController': True, key: malformed})
          self.assertFalse(settings.enabled)
          self.assertTrue(settings.errors)

  def test_actual_plannerd_loop_publishes_same_tick_state_and_plan(self):
    from openpilot.selfdrive.controls import plannerd
    from openpilot.cereal import messaging

    class StopReplay(Exception):
      pass

    class Publisher:
      def __init__(self, services):
        self.services = services
        self.sent = []

      def send(self, service, message):
        self.sent.append((service, message))

    self.sm.data['deviceState'] = messaging.new_message('deviceState').deviceState
    self.sm['deviceState'].startedMonoTime = 1_000_000_000
    self.sm.valid['deviceState'] = True
    self.sm.updated['deviceState'] = True
    self.sm.alive['deviceState'] = True
    self.sm.logMonoTime['deviceState'] = 2_000_000_000
    self.sm.frame = 0
    self.sm.all_checks = lambda services=None: True
    calls = 0

    def update(timeout=100):
      nonlocal calls
      calls += 1
      if calls > 1:
        raise StopReplay
      self.sm.logMonoTime['modelV2'] = 2_010_000_000
      self.sm.logMonoTime['carState'] = 2_010_000_000  # latest CarState arrives one card tick ahead of source

    publisher = Publisher([])
    with tempfile.TemporaryDirectory() as directory, ExitStack() as resources:
      params = Params(directory)
      # Drain native asynchronous writes before deleting their temporary directory.
      resources.callback(params._finalizer)
      params.put('CarParams', self.cp.to_bytes(), block=True)
      params.put_bool('SpeedLimitController', True, block=True)
      params.put('SLCPriority1', 'Dashboard', block=True)
      with (mock.patch.object(self.sm, 'update', side_effect=update),
            mock.patch.object(plannerd, 'Params', return_value=params),
            mock.patch.object(plannerd, 'config_realtime_process'),
            mock.patch.object(plannerd.messaging, 'SubMaster', return_value=self.sm),
            mock.patch.object(plannerd.messaging, 'PubMaster', return_value=publisher),
            mock.patch.object(plannerd.messaging, 'sub_sock', return_value=object()),
            mock.patch.object(plannerd.messaging, 'recv_one_or_none', return_value=None),
            mock.patch.dict('os.environ', {'SLC_REPLAY_RUNTIME': '1', 'REPLAY': '1'})):
        with self.assertRaises(StopReplay):
          plannerd.main()
    services = [service for service, _ in publisher.sent]
    self.assertIn('slcState', services)
    self.assertIn('longitudinalPlan', services)
    state = next(message.slcState for service, message in publisher.sent if service == 'slcState')
    self.assertTrue(state.hasCeiling)
    self.assertTrue(state.hasAccepted)
    self.assertFalse(state.hasPending)
    self.assertFalse(state.automaticAdoptionPending)
    self.assertEqual(state.observationKind, 'valid')
    self.assertEqual(state.frameMonoTime, 2_010_000_000)

  def test_stored_history_is_schema_bound_and_restored_as_a_value_not_a_dashboard_episode(self):
    candidate = acc.Candidate('dashboard', acc.ObservationIdentity(acc.IdentityKind.PRODUCER_EPISODE,
                                                                     value='drive:card:car:1'), 20.0)
    raw = history.encode(acc.AcceptedLimit(candidate, 'drive', 2), 1000)
    self.assertIn('source_schema', json.loads(raw))
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put('SLCQualifiedHistory', json.loads(raw), block=True)
      self.assertEqual(params.get('SLCQualifiedHistory')['source_schema'], json.loads(raw)['source_schema'])
      remembered = history.decode(params.get('SLCQualifiedHistory'), session_id='next-drive')
      self.assertEqual(remembered.candidate.speed_mps, 20.)
      self.assertEqual(remembered.candidate.observation_identity.kind, acc.IdentityKind.SESSION_VALUE)
      self.assertEqual(remembered.candidate.observation_identity.session_id, 'next-drive')
      self.assertNotEqual(remembered.candidate.observation_identity, candidate.observation_identity)
    tampered = json.loads(raw)
    tampered['source_schema'] = 'wrong'
    self.assertIsNone(history.decode(json.dumps(tampered), session_id='next-drive'))

  def test_native_saved_settings_default_off_and_preserve_choices(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      fresh = read_params(params)
      self.assertFalse(fresh.enabled)
      params.put_bool('SpeedLimitController', True, block=True)
      params.put('SLCConfirmation', 1, block=True)
      params.put('SLCPriority1', 'Map Data', block=True)
      params.put('SLCPriority2', 'Dashboard', block=True)
      restored = read_params(params)
      self.assertTrue(restored.enabled)
      self.assertTrue(restored.acceptance.confirm_lower)
      self.assertEqual(restored.selection.slots[0], Source.MAP)
      self.assertEqual(restored.selection.slots[1], Source.DASHBOARD)
      self.assertFalse(restored.errors)

  def accepted_runtime(self, settings, *, session_id):
    runtime = Runtime(settings, session_id=session_id)
    candidate = runtime._observations(self.sm, self.cp, 2_000_000_000)[Source.DASHBOARD].candidate
    assert candidate is not None
    accepted = acc.AcceptedLimit(candidate, session_id, 0)
    runtime.state = replace(runtime.state, acceptance=replace(acc.new_session(session_id, accepted), received_speed_mps=candidate.speed_mps))
    return runtime

  def setUp(self):
    self.cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
    self.assertTrue(self.cp.openpilotLongitudinalControl)
    data, self.envelopes = messages()
    from openpilot.cereal import messaging
    data['slcDashboardObservation'] = messaging.new_message('slcDashboardObservation').slcDashboardObservation
    self.sm = ReplaySM(data, 2_000_000_000)
    self.sm['carState'].vCruiseCluster = 100.0
    self.sm['carState'].vEgoCluster = 20.0
    self.sm['carControl'].enabled = True
    self.sm['carControl'].longActive = True
    self.sm['carControl'].latActive = False
    source = self.sm['slcDashboardObservation']
    source.producerSessionId = 'card-producer'
    source.carStateLogMonoTime = 2_000_000_000
    source.status = 'valid'
    source.speedMps = 25.0
    source.observedMonoTime = 1_900_000_000
    source.validUntilMonoTime = 9_000_000_000
    source.episode = 1

  def test_saved_choice_and_native_planner_replay(self):
    runtime = self.accepted_runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}), session_id='ceiling-drive')
    planner = LongitudinalPlanner(self.cp, init_v=20.)
    first = runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    self.assertTrue(first.message.slcState.hasCeiling)
    planner.update(self.sm, cruise_ceiling=first.result.ceiling)
    self.assertEqual(planner.last_cruise_ceiling_status, 'applied')
    self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = 105.
    self.sm.advance(2_050_000_000)
    changed = runtime.step(self.sm, self.cp, now_ns=2_050_000_000,
      cruise_event=CruiseEvent(1, 2_010_000_000, 100/3.6, 105/3.6, 'accel', 'replay-card', 2_020_000_000))
    self.assertFalse(changed.message.slcState.driverOverrideActive)
    self.assertEqual(changed.result.ceiling.speed_mps, 25.)
    self.sm.advance(9_100_000_000)
    stale = runtime.step(self.sm, self.cp, now_ns=9_100_000_000)
    self.assertEqual(stale.message.slcState.observationKind, 'stale')
    self.assertEqual(stale.result.ceiling.speed_mps, 25.)
    self.assertEqual(stale.result.acceptance.control_target_mps, 25.)
    self.assertEqual(self.sm['carState'].vCruise, 105.)

  def test_decrease_keeps_lower_slc_ceiling_in_native_planner(self):
    mph_to_mps = 0.44704
    self.sm['slcDashboardObservation'].speedMps = 45 * mph_to_mps
    self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = 70 * mph_to_mps * 3.6
    runtime = self.accepted_runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}), session_id='ceiling-drive')
    runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    self.sm['carState'].vCruise = self.sm['carState'].vCruiseCluster = 69 * mph_to_mps * 3.6
    self.sm.advance(2_050_000_000)
    result = runtime.step(self.sm, self.cp, now_ns=2_050_000_000,
      cruise_event=CruiseEvent(1, 2_010_000_000, 70*mph_to_mps, 69*mph_to_mps, 'decel', 'replay-card', 2_020_000_000))
    self.assertFalse(result.result.errors)
    self.assertFalse(result.message.slcState.driverOverrideActive)
    self.assertAlmostEqual(result.result.ceiling.speed_mps, 45*mph_to_mps, places=5)
    planner = LongitudinalPlanner(self.cp, init_v=45*mph_to_mps)
    planner.update(self.sm, cruise_ceiling=result.result.ceiling)
    self.assertEqual(planner.last_cruise_ceiling_status, 'applied')


  def test_pending_action_receipt_and_stock_gate(self):
    settings = parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCConfirmation': 1})
    runtime = Runtime(settings, session_id='receipt-drive')
    runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    source = self.sm['slcDashboardObservation']
    source.speedMps = 15.0
    source.observedMonoTime = 2_040_000_000
    source.validUntilMonoTime = 9_000_000_000
    source.episode = 2
    self.sm.advance(2_050_000_000)
    pending = runtime.step(self.sm, self.cp, now_ns=2_050_000_000)
    self.assertTrue(pending.message.slcState.hasPending)
    self.sm.advance(2_100_000_000)
    request = Action(runtime.session_id, 17, pending.message.slcState.decisionId, pending.message.slcState.presentationId, 'reject')
    rejected = runtime.step(self.sm, self.cp, now_ns=2_100_000_000, request=request)
    self.assertEqual(rejected.action_result.status, 'resolved')
    self.assertEqual(rejected.message.slcState.actionSequenceId, 17)
    self.assertEqual(rejected.message.slcState.actionStatus, 'reject')

    self.cp.openpilotLongitudinalControl = False
    self.sm.advance(2_150_000_000)
    stock = runtime.step(self.sm, self.cp, now_ns=2_150_000_000)
    self.assertFalse(stock.message.slcState.hasCeiling)
    self.assertIsNone(stock.result.ceiling)

  def test_source_requires_matching_car_frame_and_restart_changes_identity(self):
    runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}), session_id='paired-drive')
    source = self.sm['slcDashboardObservation']
    source.carStateLogMonoTime = 1_950_000_000
    mismatched = runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    self.assertEqual(runtime._observations(self.sm, self.cp, 2_000_000_000)[Source.DASHBOARD].kind,
                     ObservationKind.UNKNOWN)
    self.assertFalse(mismatched.message.slcState.hasCeiling)

    source.carStateLogMonoTime = 2_000_000_000
    matched = runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    first_identity = matched.result.selection.observation.candidate.observation_identity.value
    self.assertTrue(matched.message.slcState.hasAccepted)
    self.assertFalse(matched.message.slcState.hasPending)
    self.sm.logMonoTime['carState'] = 2_010_000_000  # independent sockets deliver the next card tick first
    lagged = runtime.step(self.sm, self.cp, now_ns=2_010_000_000)
    self.assertTrue(lagged.message.slcState.hasAccepted)
    self.assertFalse(lagged.message.slcState.hasPending)
    source.producerSessionId = 'restarted-card'
    restarted = runtime.step(self.sm, self.cp, now_ns=2_010_000_000)
    second_identity = restarted.result.selection.observation.candidate.observation_identity.value
    self.assertNotEqual(first_identity, second_identity)

  def test_lateral_authority_uses_car_control_axis(self):
    runtime = self.accepted_runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}), session_id='axis-drive')
    self.sm['selfdriveState'].active = True
    long_only = runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    self.assertEqual(long_only.result.acceptance.authority.mode.value, 'longitudinal_only')
    self.sm['carControl'].latActive = True
    combined = runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    self.assertEqual(combined.result.acceptance.authority.mode.value, 'combined')

  def test_rejected_and_malformed_ui_requests_do_not_fill_action_ledger(self):
    runtime = Runtime(parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard'}), session_id='many-actions')
    runtime.step(self.sm, self.cp, now_ns=2_000_000_000)
    wrong = Action(runtime.session_id, 1, 0, 99999, 'adopt')
    runtime.step(self.sm, self.cp, now_ns=2_000_000_000, request=wrong)
    self.assertEqual(runtime.ledger.transactions, ())
    self.assertEqual(runtime.ledger.last_action_id, -1)
    for sequence in range(2, 202):
      timestamp = 2_000_000_000 + sequence
      self.sm.advance(timestamp)
      runtime.step(self.sm, self.cp, now_ns=timestamp,
                   request=Action(runtime.session_id, sequence, 99999, 0, 'reject'))
      self.assertFalse(runtime.ledger.reset_required)
      self.assertEqual(runtime.ledger.transactions, ())


if __name__ == '__main__':
  unittest.main()
