"""Generic feature displays follow actual capability and fresh owner receipts."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest import mock

from opendbc.car import gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from opendbc.car.ford.values import CAR as FordCar
from opendbc.car.honda.values import CAR as HondaCar
from opendbc.car.nissan.values import CAR as NissanCar
from opendbc.car.toyota.values import CAR as ToyotaCar
from opendbc.car.volkswagen.interface import CarInterface as VolkswagenInterface
from opendbc.car.volkswagen.values import CAR as VolkswagenCar
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.manual import read_button_map
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.aol.vehicle import policy_for
from openpilot.starpilot.controllers.tests.test_wheel_actions import Publisher, services, state, TYPE
from openpilot.starpilot.controllers.wheel_actions import ACTIONS, WheelConsumer, WheelPublisher
from openpilot.starpilot.galaxy.settings import AuthorityContext, _qualified
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner, LANE_LIVE_KEYS
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest, row_change
from openpilot.starpilot.ui.runtime_snapshot import RuntimeSnapshotAdapter
from openpilot.starpilot.ui.shell import ShellMode
from openpilot.starpilot.ui.tests.test_runtime_snapshot import BOOT_OFFSET_NS, ui_fake
from openpilot.starpilot.ui.tests.test_traffic_status import BOOT, DRIVE, NOW, receipt
from openpilot.starpilot.ui.wheel_feature import WheelFeature


def params_for(candidate):
  return interfaces[candidate].get_non_essential_params(candidate)


class TestGlobalFeaturePresentation(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.params = Params(directory.name)

  def traffic_ui(self, cp, *, controller_source):
    ui = ui_fake()
    ui.CP, ui.params = cp, self.params
    ui.sm['deviceState'].startedMonoTime = DRIVE
    ui.sm['carState'].canValid, ui.sm['carState'].canTimeout = True, False
    ui.sm['carControl'].longActive = True
    ui.sm['selfdriveState'].enabled = True
    ui.sm.put('controlsState', NS(longControlState='pid'))
    self.params.put('DistanceButtonControl', 6, block=True)
    snapshot = ConditionalSettingsOwner(self.params).refresh(NOW)
    mapping = read_button_map(self.params, include_ioniq_media=True, cp=cp)
    self.assertIsNotNone(mapping)
    event = receipt(settings_fingerprint(snapshot), '' if controller_source else mapping.fingerprint()).as_builder()
    event.slcState.trafficMode.controllerSource = controller_source
    if controller_source:
      event.slcState.trafficMode.sourceBootTime = 0
    ui.sm.put('slcState', messaging.log_from_bytes(event.to_bytes()).slcState)
    # SLC is an independent owner. Its validity cannot veto a valid nested Traffic receipt.
    ui.sm.valid['slcState'] = False
    return ui

  def test_actual_toyota_and_pid_honda_traffic_receipts_reach_onroad_state(self):
    for candidate in (ToyotaCar.TOYOTA_RAV4_TSS2, HondaCar.HONDA_CIVIC):
      for controller_source in (False, True):
        with self.subTest(candidate=candidate, controller_source=controller_source):
          cp = params_for(candidate)
          self.assertTrue(cp.openpilotLongitudinalControl)
          ui = self.traffic_ui(cp, controller_source=controller_source)
          adapter = RuntimeSnapshotAdapter(ui, mono_clock=lambda: NOW, boot_clock=lambda: NOW + BOOT_OFFSET_NS)
          with mock.patch('openpilot.starpilot.ui.runtime_snapshot.paired_clocks_ns', return_value=(NOW, BOOT, 0)):
            state = adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad
            self.assertTrue(state.traffic_mode)
            self.assertEqual((state.traffic_display.state, state.traffic_display.label), ('active', 'TRAFFIC'))
            self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 100_000_001).onroad.traffic_mode)

  def test_traffic_never_claims_active_without_capability_or_current_effective_control(self):
    for changed in ('stock_long', 'passive', 'dashcam', 'not_car', 'empty_identity', 'missing_cp',
                    'long_inactive', 'selfdrive_disabled', 'invalid_can', 'ineffective', 'wrong_settings', 'wrong_drive'):
      with self.subTest(changed=changed):
        cp = params_for(ToyotaCar.TOYOTA_RAV4_TSS2)
        ui = self.traffic_ui(cp, controller_source=True)
        if changed == 'stock_long':
          cp.openpilotLongitudinalControl = False
        elif changed == 'passive':
          cp.passive = True
        elif changed == 'dashcam':
          cp.dashcamOnly = True
        elif changed == 'not_car':
          cp.notCar = True
        elif changed == 'empty_identity':
          cp.carFingerprint = ''
        elif changed == 'missing_cp':
          ui.CP = None
        elif changed == 'long_inactive':
          ui.sm['carControl'].longActive = False
        elif changed == 'selfdrive_disabled':
          ui.sm['selfdriveState'].enabled = False
        elif changed == 'invalid_can':
          ui.sm['carState'].canValid = False
        else:
          event = messaging.new_message('slcState', valid=False)
          event.slcState = ui.sm['slcState'].to_dict()
          if changed == 'ineffective':
            event.slcState.trafficMode.effective = False
          elif changed == 'wrong_settings':
            event.slcState.trafficMode.settingsFingerprint = 'b' * 64
          else:
            event.slcState.trafficMode.driveStartMonoTime = DRIVE + 1
          ui.sm.put('slcState', messaging.log_from_bytes(event.to_bytes()).slcState)
        adapter = RuntimeSnapshotAdapter(ui, mono_clock=lambda: NOW, boot_clock=lambda: NOW + BOOT_OFFSET_NS)
        with mock.patch('openpilot.starpilot.ui.runtime_snapshot.paired_clocks_ns', return_value=(NOW, BOOT, 0)):
          self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.traffic_mode)

  def test_switchback_badge_and_settings_are_generic_lateral_with_original_receipt_expiry(self):
    for candidate in (ToyotaCar.TOYOTA_RAV4_TSS2, HondaCar.HONDA_CIVIC, FordCar.FORD_ESCAPE_MK4):
      with self.subTest(candidate=candidate):
        cp = params_for(candidate)
        ui = ui_fake()
        ui.CP = cp
        ui.sm['deviceState'].startedMonoTime = DRIVE
        ui.sm['carState'].canValid, ui.sm['carState'].canTimeout = True, False
        event = messaging.new_message('slcState', valid=True, logMonoTime=NOW)
        event.slcState.switchbackMode = {
          'version': 1, 'sessionId': 'c' * 32, 'sequence': 1,
          'observedMonoTime': NOW, 'validUntilMonoTime': NOW + 100_000_000,
          'driveStartMonoTime': DRIVE, 'requested': True, 'effective': True,
          'sourceCarControlMonoTime': NOW,
        }
        ui.sm.put('slcState', messaging.log_from_bytes(event.to_bytes()).slcState)
        adapter = RuntimeSnapshotAdapter(ui)
        self.assertTrue(adapter.build(ShellMode.ONROAD, now_ns=NOW).onroad.switchback_mode)
        self.assertFalse(adapter.build(ShellMode.ONROAD, now_ns=NOW + 100_000_001).onroad.switchback_mode)
        self.assertTrue(_qualified(AuthorityContext(False, cp, None), 'switchback_wheel'))
        cp.passive = True
        self.assertFalse(_qualified(AuthorityContext(False, cp, None), 'switchback_wheel'))

  def test_every_lane_control_matches_runtime_and_rechecks_before_writing(self):
    self.params.put_bool("LaneCentering", False, block=True)
    cp = params_for(ToyotaCar.TOYOTA_RAV4_TSS2)
    owner = FeatureSettingsOwner(self.params, lambda _group: True,
      vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
    rows = owner.snapshot('lane', parked=True, system_long=True, lateral_context=True, metric=False).rows
    row = next(row for row in rows if row.key == 'LaneCentering')
    request = row_change(row, 1)
    self.assertIsNotNone(request)
    assert request is not None
    self.assertTrue(row.available)
    self.assertTrue(owner.apply(request))
    self.assertTrue(self.params.get_bool('LaneCentering'))
    rows = owner.snapshot('lane', parked=True, system_long=True, lateral_context=True, metric=False).rows
    self.assertTrue(all(row.available for row in rows if row.key in LANE_LIVE_KEYS))
    requests = [FeatureSettingsRequest(row.key, row.source, row.value, vehicle_fingerprint=row.vehicle_fingerprint)
                for row in rows if row.key in LANE_LIVE_KEYS]
    before = {key: Path(self.params.get_param_path(key)).read_bytes()
              if Path(self.params.get_param_path(key)).exists() else None for key in LANE_LIVE_KEYS}
    cp.dashcamOnly = True
    blocked = owner.snapshot('lane', parked=True, system_long=True, lateral_context=True, metric=False).rows
    self.assertTrue(all(not row.available for row in blocked if row.key in LANE_LIVE_KEYS))
    self.assertTrue(all(not owner.apply(request) for request in requests))
    after = {key: Path(self.params.get_param_path(key)).read_bytes()
             if Path(self.params.get_param_path(key)).exists() else None for key in LANE_LIVE_KEYS}
    self.assertEqual(before, after)

  def test_reported_distance_actions_do_not_depend_on_aol_policy(self):
    nissan = params_for(NissanCar.NISSAN_ROGUE)
    fingerprint = gen_empty_fingerprint()
    fingerprint[1][0x40] = 8
    volkswagen = VolkswagenInterface.get_params(VolkswagenCar.VOLKSWAGEN_GOLF_MK7,
      fingerprint, [], True, False, False)
    self.assertFalse(nissan.openpilotLongitudinalControl)
    self.assertTrue(volkswagen.alphaLongitudinalAvailable)
    self.assertTrue(volkswagen.openpilotLongitudinalControl)
    for cp, options in ((nissan, ('Bookmark', 'Switchback Mode')),
                        (volkswagen, ('Bookmark', 'Cycle personality', 'Experimental override', 'Traffic mode', 'Switchback Mode'))):
      with self.subTest(candidate=cp.carFingerprint):
        actual_policy = policy_for(cp)
        self.assertTrue(actual_policy.settings_supported)
        for settings_supported in (False, True):
          with self.subTest(settings_supported=settings_supported), mock.patch(
              "openpilot.starpilot.ui.wheel_feature.policy_for",
              return_value=replace(actual_policy, settings_supported=settings_supported)):

            owner = FeatureSettingsOwner(self.params, lambda _group: True,
              vehicle_fingerprint=lambda cp=cp: cp.carFingerprint, vehicle_params=lambda cp=cp: cp)
            wheel = WheelFeature(owner)
            choices = wheel.choices('DistanceButtonControl')
            self.assertTrue(set(options).issubset(choices))
            aol_actions = {'Pause steering', 'Pause longitudinal', 'Toggle AOL'}
            if settings_supported:
              self.assertTrue(aol_actions.issubset(choices))
            else:
              self.assertTrue(aol_actions.isdisjoint(choices))
            self.assertEqual(wheel.choices('LKASButtonControl'), ())
            self.assertEqual(wheel.choices('MainCruiseButtonControl'), ())
            self.assertEqual(wheel.choices('ModeButtonControl'), ())
            self.assertEqual(wheel.choices('StarButtonControl'), ())
            for option in options:
              with self.subTest(option=option):
                row = next(row for row in wheel.rows() if row.key == 'wheel:DistanceButtonControl')
                request = FeatureSettingsRequest(row.key, row.source, option, capability=row.capability)
                self.assertTrue(wheel.apply(request))
                publisher = WheelPublisher(self.params)
                publisher.observe(self.params, cp, state(), now_ns=NOW, drive_id=DRIVE)
                publisher.observe(self.params, cp, state(True), now_ns=NOW + 10_000_000, drive_id=DRIVE)
                when = NOW + 20_000_000
                commands = publisher.observe(self.params, cp, state(False), now_ns=when, drive_id=DRIVE)
                self.assertEqual(commands, (('DistanceButtonControl', ACTIONS[option]),))
                output = Publisher()
                publisher.publish(commands, cp, output, now_ns=when, drive_id=DRIVE,
                                  source_car_ns=when, source_control_ns=when)
                event = messaging.log_from_bytes(output.events[0][1])
                sm = services(cp, now=when)
                # Use this test's drive identifier in the exact original wire checks.
                sm['deviceState'].startedMonoTime = DRIVE
                command = WheelConsumer().accept(event, self.params, cp, sm, now_ns=when)
                self.assertIsNotNone(command)
                self.assertEqual((command.key, command.action), ('DistanceButtonControl', ACTIONS[option]))
                self.params.put('DistanceButtonControl', 0, block=True)
                self.assertIsNone(WheelConsumer().accept(event, self.params, cp, sm, now_ns=when))

  def test_toyota_lkas_generic_bookmark_still_preserves_fixed_main_and_absent_cancel(self):
    cp = params_for(ToyotaCar.TOYOTA_RAV4_TSS2)
    owner = FeatureSettingsOwner(self.params, lambda _group: True,
      vehicle_fingerprint=lambda: cp.carFingerprint, vehicle_params=lambda: cp)
    wheel = WheelFeature(owner)
    self.assertIn('Bookmark', wheel.choices('LKASButtonControl'))
    self.assertEqual(wheel.choices('MainCruiseButtonControl'), ())
    self.assertEqual(wheel.choices('CancelButtonControl'), ())
    row = next(row for row in wheel.rows() if row.key == 'wheel:LKASButtonControl')
    self.assertTrue(wheel.apply(FeatureSettingsRequest(row.key, row.source, 'Bookmark', capability=row.capability)))
    publisher = WheelPublisher(self.params)
    publisher.observe(self.params, cp, state(), now_ns=NOW, drive_id=DRIVE)
    when = NOW + 10_000_000
    commands = publisher.observe(self.params, cp, state(True, button=TYPE.lkas), now_ns=when, drive_id=DRIVE)
    self.assertEqual(commands, (('LKASButtonControl', 8),))
    output = Publisher()
    publisher.publish(commands, cp, output, now_ns=when, drive_id=DRIVE,
                      source_car_ns=when, source_control_ns=when)
    sm = services(cp, now=when)
    sm['deviceState'].startedMonoTime = DRIVE
    event = messaging.log_from_bytes(output.events[0][1])
    self.assertEqual(WheelConsumer().accept(event, self.params, cp, sm, now_ns=when).action, 8)


if __name__ == '__main__':
  unittest.main()
