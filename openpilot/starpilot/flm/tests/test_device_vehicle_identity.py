"""Declared registered-device identities; never proof of a unique physical VIN."""
import bz2
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import zstandard as zstd

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR
from openpilot.starpilot.flm.offroad_runtime import current_scope, configuration_scope, _current_controller, VehicleIdentityUnavailable
from openpilot.starpilot.flm.offroad_store import KEY, RouteStorage
from openpilot.starpilot.flm.offroad_worker import OffroadWorker
from openpilot.starpilot.flm.runtime_adapter_registry import read_source
from openpilot.starpilot.flm.tests import test_offroad_coordinate_policy as saved_fixtures
from openpilot.starpilot.flm.tests import test_offroad_fault_isolation as route_fixtures
from openpilot.starpilot.flm.vehicle_identity import identity_keys, persistent_unit


class TestRegisteredDeviceVehicleIdentity(unittest.TestCase):
  def setUp(self):
    self.enterContext(OpenpilotPrefix())
    directory = self.enterContext(tempfile.TemporaryDirectory())
    self.root = Path(directory)
    self.params = Params(str(self.root / 'params'))
    self.params.put_bool('OpenpilotEnabledToggle', True, block=True)
    self.params.put('DongleId', b'abcdef0123456789', block=True)
    self.cp = interfaces[CAR.HYUNDAI_SONATA_HYBRID].get_non_essential_params(CAR.HYUNDAI_SONATA_HYBRID)
    self.cp.carVin = '0' * 17
    self.cp.init('carFw', 1)[0].fwVersion = b'declared synthetic Sonata ECU firmware'

  def live(self, **changes):
    options = {'now_ns': 1_000_000_000, 'calibration': (0., 0., 0.),
               'vehicle_session': 'declared-fresh-drive', 'calibration_generation': '1' * 32} | changes
    return read_source(self.cp, self.params, **options)

  def binding(self):
    scope = current_scope(self.cp, self.params)
    live = self.live()
    self.assertTrue(live.enabled and live.configuration_known)
    self.assertEqual(scope.persistent_source_epoch, live.persistent_source_epoch)
    self.assertEqual(self.cp.carVin, '0' * 17, 'No fabricated VIN is written')
    return scope

  def test_actual_registered_sonata_parked_live_binding_and_manual_coordinate_status_agree(self):
    scope = self.binding()
    ci, controller = _current_controller(self.cp, self.params)
    configured, _ = configuration_scope(self.cp, ci, controller, self.params, manual_status='configured')
    self.assertEqual(configured, scope, 'A manual coordinate does not disable the remaining learner')

  def test_device_firmware_full_configuration_and_reset_are_separate_namespaces_without_erasing_history(self):
    saved = saved_fixtures.TestSavedSetupCapacity.row(1)
    self.assertTrue(RouteStorage(self.params).save(saved, expected=None, authorized=lambda: True))
    original = self.params.get(KEY)
    first = self.binding()
    self.params.put('DongleId', b'0123456789abcdef', block=True)
    second = self.binding()
    self.cp.carFw[0].fwVersion = b'different declared firmware'
    third = self.binding()
    self.cp.mass += 10.
    fourth = self.binding()
    self.params.put('AutoSteeringLearningReset', '1', block=True)
    fifth = self.binding()
    self.assertEqual(len({scope.key() for scope in (first, second, third, fourth, fifth)}), 5)
    self.assertEqual(self.params.get(KEY), original)

  def test_identical_cars_on_one_device_require_explicit_reset_not_an_invented_unique_id(self):
    first = self.binding()
    next_session = self.live(vehicle_session='another-declared-drive', calibration_generation='2' * 32)
    self.assertEqual(first.persistent_source_epoch, next_session.persistent_source_epoch)
    self.assertNotEqual(self.live().source_epoch, next_session.source_epoch)
    self.params.put('AutoSteeringLearningReset', 'abc123', block=True)
    reset = self.binding()
    self.assertNotEqual(first.key(), reset.key())
    self.assertEqual(reset.reset_generation, 'abc123')

  def test_missing_invalid_or_unreadable_registration_keeps_fresh_session_collection_review_only(self):
    for device in (None, b'', b'UnregisteredDevice', b'F' * 16, b'a' * 15):
      with self.subTest(device=device):
        if device is None:
          self.params.remove('DongleId')
        else:
          self.params.put('DongleId', device, block=True)
        self.assertTrue(self.live().enabled)
        self.assertEqual(self.live().persistent_source_epoch, '')
        with self.assertRaises(VehicleIdentityUnavailable):
          current_scope(self.cp, self.params)
    from openpilot.starpilot.flm import runtime_adapter_registry as registry
    original = registry.read_saved
    with patch.object(registry, 'read_saved', side_effect=lambda params, key, bound:
                      (None, False) if key == 'DongleId' else original(params, key, bound)):
      self.assertTrue(self.live().enabled)
      self.assertEqual(self.live().persistent_source_epoch, '')

  def test_missing_firmware_cannot_authorize_persistence(self):
    for firmware in ([], [{'fwVersion': b''}]):
      with self.subTest(firmware=firmware):
        self.cp.carFw = firmware
        self.assertIsNone(persistent_unit(self.cp, {'DongleId': b'a' * 16}))
        self.assertEqual(self.live().persistent_source_epoch, '')
        with self.assertRaises(VehicleIdentityUnavailable):
          current_scope(self.cp, self.params)

  def test_fallback_never_substitutes_for_current_session_calibration_or_controls(self):
    for changes in ({'vehicle_session': ''}, {'calibration': ()}, {'calibration_generation': 'invalid'}):
      with self.subTest(changes=changes):
        source = self.live(**changes)
        self.assertFalse(source.enabled)
        self.assertFalse(source.persistent_source_epoch)
    self.assertFalse(self.live(calibration_generation='').persistent_source_epoch)
    self.params.put_bool('OpenpilotEnabledToggle', False, block=True)
    self.assertFalse(self.live().enabled)
    with self.assertRaisesRegex(ValueError, 'Main controls disabled'):
      current_scope(self.cp, self.params)

  def test_registration_changed_during_source_read_refuses_instead_of_granting_old_binding(self):
    from openpilot.starpilot.flm import runtime_adapter_registry as registry
    original = registry.read_saved
    devices = iter((b'a' * 16, b'b' * 16))
    with patch.object(registry, 'read_saved', side_effect=lambda params, key, bound:
                      (next(devices), True) if key == 'DongleId' else original(params, key, bound)):
      source = self.live()
    self.assertFalse(source.enabled)
    self.assertFalse(source.configuration_known)
    self.assertEqual(source.persistent_source_epoch, '')

  def test_real_vin_recovery_uses_original_vin_path_and_ignores_device_registration(self):
    fallback = self.binding()
    self.cp.carVin = 'KMHL341JBLU000001'
    real = current_scope(self.cp, self.params)
    self.assertNotEqual(fallback.key(), real.key())
    self.assertEqual(real.persistent_source_epoch, self.live().persistent_source_epoch)
    self.assertEqual(identity_keys(self.cp), ())
    self.assertEqual(persistent_unit(self.cp, {}), str(self.cp.carVin))
    self.params.remove('DongleId')
    self.cp.init('carFw', 0)
    before = self.live()
    self.params.put('DongleId', b'fedcba9876543210', block=True)
    self.assertEqual(before.persistent_source_epoch, self.live().persistent_source_epoch)
    self.assertEqual(before.source_epoch, self.live().source_epoch)

  def test_late_registration_invalidates_review_checkpoint_without_requalifying_missing_original_metadata(self):
    directory = str(self.root / 'late')
    params, cp, logs = route_fixtures.TestUnidentifiedVehicleReview.setup(directory, firmware=b'declared firmware')
    worker = OffroadWorker(params, logs, self.root / 'cache', permitted=lambda: True,
                           checker=lambda *a, **k: self.fail('No original controller metadata was recorded'))
    original = worker.run_once()
    self.assertEqual(original['reason'], 'vehicle_identity_unavailable')
    params.put('DongleId', b'abcdef0123456789', block=True)
    updated = worker.run_once()
    self.assertNotEqual(original['assessment_sha256'], updated['assessment_sha256'])
    self.assertNotEqual(updated['reason'], 'vehicle_identity_unavailable')
    self.assertEqual(updated['analysis_counts']['qualified_whole_windows'], 0)
    self.assertFalse(updated['configuration_changed'])
    self.assertGreater(updated['recommendation_count'], 0)
    self.assertEqual(worker.cache.get(original['assessment_sha256'])['kind'], 'identity_review')
    # A changed/invalid configuration must not reuse a previously valid report.
    params.put('SteeringDelayPreferences', b'not a valid delay document', block=True)
    with self.assertRaisesRegex(ValueError, 'Invalid steering delay baseline'):
      worker.run_once()
    params.remove('SteeringDelayPreferences')
    self.assertEqual(worker.run_once()['assessment_sha256'], updated['assessment_sha256'])

  def test_registered_unknown_vin_final_truncated_zstd_dispatch_is_partial_and_never_saves(self):
    directory = str(self.root / 'tail')
    params, cp, logs = route_fixtures.TestUnidentifiedVehicleReview.setup(directory, firmware=b'declared firmware')
    params.put('DongleId', b'abcdef0123456789', block=True)
    segment = next(logs.iterdir())
    raw = bz2.decompress((segment / 'rlog.bz2').read_bytes())
    bodies = [[], []]
    for event in log.Event.read_multiple_bytes(raw):
      for number in (0, 1):
        message = event.as_builder()
        message.logMonoTime += number * 14_000_000_000
        if message.which() == 'clocks':
          message.clocks.wallTimeNanos += number * 14_000_000_000
        elif message.which() == 'sentinel' and number == 0:
          message.sentinel.type = 'endOfSegment'
        bodies[number].append(message.to_bytes())
    (segment / 'rlog.bz2').unlink()
    first = zstd.ZstdCompressor().compress(b''.join(bodies[0]))
    tail = zstd.ZstdCompressor().compress(b''.join(bodies[1]))
    (segment / 'rlog.zst').write_bytes(first)
    last = logs / (segment.name[:-1] + '1')
    last.mkdir()
    (last / 'rlog.zst').write_bytes(tail[:-1])
    saved = saved_fixtures.TestSavedSetupCapacity.row(1)
    self.assertTrue(RouteStorage(params).save(saved, expected=None, authorized=lambda: True))
    previous = params.get(KEY)
    worker = OffroadWorker(params, logs, self.root / 'partial-cache', permitted=lambda: True,
                           checker=lambda *a, **k: self.fail('Partial evidence cannot qualify an automatic tune'))
    result = worker.run_once()
    self.assertTrue(result['partial_drive'])
    self.assertEqual(result['partial_segments'], [1, 2])
    self.assertEqual(result['analysis_counts']['evidence_drives'], 0)
    self.assertEqual(result['analysis_counts']['evidence_windows'], 0)
    self.assertEqual(worker.cache.get(result['assessment_sha256'])['kind'], 'partial_assessment')
    self.assertFalse(result['configuration_changed'])
    self.assertEqual(params.get(KEY), previous)
    self.assertEqual((segment / 'rlog.zst').read_bytes(), first)
    self.assertEqual((last / 'rlog.zst').read_bytes(), tail[:-1])
