"""The support preset changes real saved inputs without claiming an active mode."""

from openpilot.starpilot.ui.feature_settings_compact import FeatureSession, NavScroller
from unittest.mock import Mock
from openpilot.starpilot.galaxy.settings import ContextSource
from dataclasses import replace
import fcntl
import os
from threading import Timer
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from opendbc.car.car_helpers import interfaces
from opendbc.car.hyundai.values import CAR as HYUNDAI
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsChanged, SettingsGateway
from openpilot.starpilot.lateral.controller_selection import DOCUMENT_KEY, ControllerMode, read_selection, replace_mode
from openpilot.starpilot.lateral.learning_preferences import KEY as LEARNING_KEY, read_preference, replace_preference
from openpilot.starpilot.lateral.lane_change_preferences import KEY as LANE_KEY, LaneChangePolicy, read_saved, to_value
from openpilot.starpilot.lateral.lane_runtime import read_settings as lane_settings
from openpilot.starpilot.lateral.lane_centering import LaneCenteringSettings
from openpilot.starpilot.ui import tuning_preparation as preparation
from openpilot.starpilot.ui.controller_feature import SETUP_ACTION, SETUP_DEFAULTS
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest


def controller_keys():
  from openpilot.starpilot.ui.controller_feature import SETUP_LIMITS
  return SETUP_LIMITS


def cp_for(vehicle):
  return interfaces[vehicle].get_non_essential_params(vehicle)


class TestLateralSetup(unittest.TestCase):
  def setUp(self):
    self.params_directory = TemporaryDirectory()
    self.addCleanup(self.params_directory.cleanup)
    environment = mock.patch.dict(os.environ, PARAMS_ROOT=self.params_directory.name)
    environment.start()
    self.addCleanup(environment.stop)
    self.prefix = OpenpilotPrefix()
    self.prefix.__enter__()
    self.addCleanup(self.prefix.__exit__, None, None, None)
    self.params = Params()
    self.cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
    self.parked = True
    self.owner = FeatureSettingsOwner(self.params, lambda _group: self.parked,
                                      vehicle_fingerprint=lambda: str(self.cp.carFingerprint), vehicle_params=lambda: self.cp)

  def row(self):
    state = self.owner.snapshot('torque', parked=self.parked, system_long=True, lateral_context=True, metric=False)
    return next((row for row in state.rows if row.key == SETUP_ACTION), None)

  def request(self, value='On'):
    row = self.row()
    return FeatureSettingsRequest(row.key, row.source, value, confirmation=True,
                                  vehicle_fingerprint=row.vehicle_fingerprint, capability=row.capability,
                                  dependencies=row.dependencies)

  def seed(self):
    for key, raw in {'AdvancedLateralTune': b'1', 'ForceAutoTuneOff': b'0', 'LaneCentering': b'1',
                     'LaneCenterOffset': b'0.2', 'LaneCenteringE2EAuthority': b'0.3', 'LaneCenteringPauseOnSignal': b'0',
                     'TorqueOverrideDocument': b'{"custom":"preserved"}', 'SteerLatAccel': b'3.3', 'SteerFriction': b'0.12',
                     'CalibrationParams': b'preserved calibration', 'CustomPersonalities': b'1',
                     'LongitudinalPersonalityProfiles': b'{"custom":"preserved"}'}.items():
      Path(self.params.get_param_path(key)).write_bytes(raw)
    Path(self.params.get_param_path(DOCUMENT_KEY)).write_bytes(replace_mode(None, self.cp, ControllerMode.STANDARD))
    lane = LaneChangePolicy(False, 1.0, True, True, 3.0, 3.0, True, 0.9)
    self.params.put(LANE_KEY, to_value(lane), block=True)

  def test_active_prep_can_turn_off_after_user_edits_without_overwriting_them(self):
    self.seed()
    prior = {key: Path(self.params.get_param_path(key)).read_bytes()
             if Path(self.params.get_param_path(key)).exists() else None for key in controller_keys()}
    self.assertTrue(self.owner.apply(self.request()))
    changed = {'LaneCenterOffset': b'0.37', LANE_KEY: b'broken'}
    for key, value in changed.items():
      Path(self.params.get_param_path(key)).write_bytes(value)
    self.assertEqual(self.row().value, 'On')
    self.assertTrue(self.row().available)
    self.assertTrue(self.owner.apply(self.request('Off')))
    self.assertIsNone(self.params.get(preparation.KEY))
    for key, value in prior.items():
      path = Path(self.params.get_param_path(key))
      self.assertEqual(path.read_bytes() if path.exists() else None, changed.get(key, value))

  def test_galaxy_active_prep_can_turn_off_after_new_lane_preference(self):
    self.seed()
    self.assertTrue(self.owner.apply(self.request()))
    self.params.put('LaneCenterOffset', 0.37, block=True)
    current = SimpleNamespace(parked=True, cp=self.cp, raw=b'current-cp')
    gateway = SettingsGateway(self.params, Mock(spec=ContextSource, sample=lambda: AuthorityContext(current.parked, current.cp, current.raw)),
                              clock=lambda: 10)
    page = gateway.page('torque', 'session', b'generation')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Prep My Vehicle for Tuning')
    intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Off')
    self.assertEqual(intent['proposed'], 'Off')
    self.assertTrue(gateway.confirm(intent['intent'], 'session', b'generation'))
    self.assertEqual(Path(self.params.get_param_path('LaneCenterOffset')).read_bytes(), b'0.37')
    self.assertIsNone(self.params.get(preparation.KEY))

  def test_preparation_journals_scoped_choice_and_restores_other_vehicles_exactly(self):
    self.seed()
    other = cp_for(TOYOTA.TOYOTA_COROLLA_TSS2)
    raw = replace_preference(replace_preference(None, other, False), self.cp, True)
    self.params.put(LEARNING_KEY, raw, block=True)
    self.assertTrue(self.owner.apply(self.request()))
    self.assertFalse(read_preference(self.params, self.cp).enabled)
    self.assertFalse(read_preference(self.params, other).enabled)
    journal = preparation.decode(Path(self.params.get_param_path(preparation.KEY)).read_bytes(), controller_keys())
    assert isinstance(journal, dict)
    self.assertEqual(journal['prior'][LEARNING_KEY], raw)
    self.assertTrue(self.owner.apply(self.request('Off')))
    self.assertEqual(self.params.get(LEARNING_KEY), raw)
    self.assertTrue(read_preference(self.params, self.cp).enabled)

  def test_existing_legacy_preparation_restores_its_original_keys(self):
    self.seed()
    scoped = replace_preference(None, self.cp, True)
    self.params.put(LEARNING_KEY, scoped, block=True)
    limits = {key: limit for key, limit in controller_keys().items() if key != LEARNING_KEY}
    sources = {key: Path(self.params.get_param_path(key)).read_bytes()
               if Path(self.params.get_param_path(key)).exists() else None for key in limits}
    desired = self.owner.controller._setup_values(self.cp, sources)
    self.assertTrue(preparation.transition(self.params, str(self.cp.carFingerprint), sources, desired,
                                           None, True, lambda: True))
    self.assertTrue(self.row().available)
    self.assertEqual(self.row().value, 'On')
    self.assertTrue(self.owner.apply(self.request('Off')))
    for key, raw in sources.items():
      path = Path(self.params.get_param_path(key))
      self.assertEqual(path.read_bytes() if path.exists() else None, raw)
    self.assertEqual(self.params.get(LEARNING_KEY), scoped)
    self.assertIsNone(self.params.get(preparation.KEY))

  def test_preparation_waits_for_brief_native_writer_contention(self):
    self.seed()
    request = self.request()
    lock_path = Path(self.params.get_param_path(preparation.KEY)).parent.parent / '.lock'
    with lock_path.open('a') as lock:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      release = Timer(.05, lambda: fcntl.flock(lock, fcntl.LOCK_UN))
      release.start()
      try:
        self.assertTrue(self.owner.apply(request))
      finally:
        release.cancel()
        release.join()
        fcntl.flock(lock, fcntl.LOCK_UN)
    self.assertEqual(self.row().value, 'On')

  def test_preparation_lock_deadline_preserves_every_saved_source(self):
    self.seed()
    request = self.request()
    before = {key: Path(self.params.get_param_path(key)).read_bytes()
              if Path(self.params.get_param_path(key)).exists() else None for key in controller_keys()}
    lock_path = Path(self.params.get_param_path(preparation.KEY)).parent.parent / '.lock'
    with lock_path.open('a') as lock:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      self.assertFalse(self.owner.apply(request))
    for key, raw in before.items():
      path = Path(self.params.get_param_path(key))
      self.assertEqual(path.read_bytes() if path.exists() else None, raw)
    self.assertIsNone(self.params.get(preparation.KEY))

  def test_malformed_journal_is_displayed_unavailable_without_setting_changes(self):
    self.seed()
    prior = Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes()
    Path(self.params.get_param_path(preparation.KEY)).write_bytes(b'{"version":1,"vehicle":"x","prior":[],"prepared":{}}')
    self.assertFalse(self.row().available)
    self.assertFalse(self.owner.apply(self.request()))
    self.assertEqual(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes(), prior)

  def test_native_big_and_small_toggle_use_actual_owner_and_restore(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.starpilot.ui.shell import ShellMode
    from openpilot.starpilot.ui.settings_state import Destination
    from openpilot.starpilot.ui.feature_settings_state import FeatureUiAction
    from openpilot.starpilot.ui.feature_settings_compact import FeatureSettingsCompact
    self.seed()
    before = Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes()
    state = self.owner.snapshot('torque', parked=True, system_long=True, lateral_context=True, metric=False)
    session = Mock(spec=StarShellSession, _mode=ShellMode.SETTINGS, selected=Destination.DRIVING_CONTROLS,
                              feature_snapshot=lambda: state, feature_request=self.owner.apply)
    StarShellSession._feature_ui(session, FeatureUiAction('change', self.row()))
    self.assertEqual(self.row().value, 'On')

    class Button:
      def __init__(self, *_args):
        self.click = None

      def set_click_callback(self, callback):
        self.click = callback

    compact = FeatureSettingsCompact(Mock(spec=FeatureSession, feature_request=self.owner.apply))
    refreshed = []
    with mock.patch('openpilot.starpilot.ui.feature_settings_compact.BigButton', Button):
      buttons = compact._editable(self.row(), lambda: refreshed.append(True), Mock(spec=NavScroller))
    buttons[0].click()
    self.assertEqual(refreshed, [True])
    self.assertEqual(self.row().value, 'Off')
    self.assertEqual(Path(self.params.get_param_path(DOCUMENT_KEY)).read_bytes(), before)

  def test_off_restores_exact_prior_inputs_after_owner_restart(self):
    self.seed()
    before = {key: (Path(self.params.get_param_path(key)).read_bytes() if Path(self.params.get_param_path(key)).exists() else None)
              for key in controller_keys()}
    self.assertTrue(self.owner.apply(self.request()))
    self.owner = FeatureSettingsOwner(self.params, lambda _group: self.parked,
                                      vehicle_fingerprint=lambda: str(self.cp.carFingerprint), vehicle_params=lambda: self.cp)
    self.assertEqual(self.row().value, 'On')
    self.assertTrue(self.owner.apply(self.request('Off')))
    for key, raw in before.items():
      path = Path(self.params.get_param_path(key))
      self.assertEqual(path.read_bytes() if path.exists() else None, raw)
    self.assertFalse(Path(self.params.get_param_path(preparation.KEY)).exists())

  def test_prepare_updates_real_inputs_and_preserves_manual_calibration_and_longitudinal_bytes(self):
    self.seed()
    preserved = {key: Path(self.params.get_param_path(key)).read_bytes() for key in
                 ('TorqueOverrideDocument', 'SteerLatAccel', 'SteerFriction', 'CalibrationParams',
                  'CustomPersonalities', 'LongitudinalPersonalityProfiles')}
    self.assertTrue(self.owner.apply(self.request()))
    self.assertEqual(read_selection(self.params, self.cp).mode, ControllerMode.STARPILOT)
    for key, raw in SETUP_DEFAULTS.items():
      self.assertEqual(Path(self.params.get_param_path(key)).read_bytes(), raw)
    self.assertEqual(lane_settings(self.params), LaneCenteringSettings())
    lane = read_saved(self.params).policy
    self.assertEqual(lane, replace(LaneChangePolicy(), close_gap=True, close_gap_seconds=0.9))
    for key, raw in preserved.items():
      self.assertEqual(Path(self.params.get_param_path(key)).read_bytes(), raw)
    self.assertTrue(self.owner.apply(self.request()))  # Idempotent; no new mode or active flag.

  def test_preparation_uses_exact_supported_platforms_and_requires_parked_authority(self):
    for vehicle in (HYUNDAI.HYUNDAI_IONIQ_6, HYUNDAI.GENESIS_G70_2020, TOYOTA.TOYOTA_COROLLA_TSS2):
      self.cp = cp_for(vehicle)
      self.assertTrue(self.row().available)
    self.cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
    request = self.request()
    self.assertTrue(self.owner.apply(replace(request, confirmation=False)))
    self.assertTrue(self.owner.apply(self.request('Off')))
    self.parked = False
    self.assertFalse(self.row().available)
    self.assertFalse(self.owner.apply(request))
    self.parked = True
    self.cp = cp_for(HYUNDAI.KIA_EV6)
    self.assertTrue(self.row().available)
    self.assertFalse(self.owner.apply(request))
    self.cp = cp_for(HYUNDAI.HYUNDAI_IONIQ_6)
    self.cp.passive = True
    self.assertIsNone(self.row())

  def test_displayed_settings_and_final_vehicle_revoke_stale_action(self):
    self.seed()
    request = self.request()
    self.params.put('LaneCenterOffset', 0.1, block=True)
    self.assertFalse(self.owner.apply(request))
    self.assertTrue(self.params.get_bool('AdvancedLateralTune'))
    request = self.request()
    self.cp.lateralTuning.torque.latAccelFactor *= 1.05
    self.assertFalse(self.owner.apply(request))
    Path(self.params.get_param_path(LANE_KEY)).write_bytes(b'broken')
    self.assertFalse(self.row().available)
    self.assertFalse(self.owner.apply(self.request()))

  def test_revocation_leaves_recoverable_journal_and_off_restores_prior_state(self):
    self.seed()
    request = self.request()
    original = preparation.replace_saved

    def commit(*args, **kwargs):
      result = original(*args, **kwargs)
      self.parked = False
      return result

    with mock.patch.object(preparation, 'replace_saved', side_effect=commit):
      self.assertFalse(self.owner.apply(request))
    self.assertTrue(self.params.get_bool('AdvancedLateralTune'))
    self.assertEqual(read_selection(self.params, self.cp).mode, ControllerMode.STANDARD)
    self.assertEqual(Path(self.params.get_param_path('SteerLatAccel')).read_bytes(), b'3.3')
    self.parked = True
    self.assertEqual(self.row().value, 'On')
    self.assertIn('interrupted', self.row().reason)
    self.assertTrue(self.owner.apply(self.request('Off')))
    self.assertEqual(self.row().value, 'Off')

  def test_prepared_drive_constructs_actual_starpilot_without_manual_or_learned_torque(self):
    self.seed()
    self.assertTrue(self.owner.apply(self.request()))
    self.params.put('CarParams', self.cp.to_bytes(), block=True)
    with mock.patch.dict(os.environ, {'REPLAY': '1', 'TORQUE_REPLAY_RUNTIME': '0', 'AOL_REPLAY_RUNTIME': '0',
                                     'LANE_CENTERING_REPLAY_RUNTIME': '0'}), \
         mock.patch('openpilot.selfdrive.controls.controlsd.messaging.PubMaster'), \
         mock.patch('openpilot.selfdrive.controls.controlsd.cloudlog.info') as info:
      controls = Controls()
    self.assertEqual(controls.LaC.controller_mode, ControllerMode.STARPILOT)
    self.assertFalse(controls.torque_learning_allowed)
    self.assertIsNone(controls.torque_host)
    self.assertAlmostEqual(controls.LaC.pid.pos_limit, 3.0 * 1.22, places=6)
    event = next(call.args[0] for call in info.call_args_list if isinstance(call.args[0], dict) and
                 call.args[0].get('event') == 'torque controller selected')
    self.assertEqual(event['controller'], 'starpilot')
    self.assertFalse(event['automaticLearning'])
    self.assertEqual(event['startupParameterSource'], 'vehicle')
    self.assertEqual(event['startupTorque']['factor'], controls.LaC.torque_params.latAccelFactor)
    self.assertEqual(event['startupPidLimits'], [controls.LaC.pid.neg_limit, controls.LaC.pid.pos_limit])
    self.assertEqual(event['vehicle'], self.cp.carFingerprint)

  def test_galaxy_preview_explains_persistent_setup_and_confirms_same_owner(self):
    self.seed()
    current = SimpleNamespace(parked=True, cp=self.cp, raw=b'current-cp')
    gateway = SettingsGateway(self.params, Mock(spec=ContextSource, sample=lambda: AuthorityContext(current.parked, current.cp, current.raw)),
                              clock=lambda: 10)
    page = gateway.page('torque', 'session', b'generation')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Prep My Vehicle for Tuning')
    intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
    self.assertIn('restores your previous', intent['question'])
    self.assertEqual(intent['proposed'], 'On')
    current.parked = False
    with self.assertRaises(SettingsChanged):
      gateway.confirm(intent['intent'], 'session', b'generation')
    current.parked = True
    page = gateway.page('torque', 'session', b'generation')
    intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='On')
    self.assertTrue(gateway.confirm(intent['intent'], 'session', b'generation'))
    self.assertEqual(read_selection(self.params, self.cp).mode, ControllerMode.STARPILOT)
    page = gateway.page('torque', 'session', b'generation')
    index = next(i for i, row in enumerate(page['rows']) if row['label'] == 'Prep My Vehicle for Tuning')
    intent = gateway.preview(page['view'], index, 0, 'session', b'generation', value='Off')
    self.assertEqual(intent['proposed'], 'Off')
    self.assertTrue(gateway.confirm(intent['intent'], 'session', b'generation'))
    self.assertEqual(read_selection(self.params, self.cp).mode, ControllerMode.STANDARD)
    self.assertTrue(self.params.get_bool('AdvancedLateralTune'))
    self.assertEqual(Path(self.params.get_param_path('SteerLatAccel')).read_bytes(), b'3.3')


if __name__ == '__main__':
  unittest.main()
