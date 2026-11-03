"""Persisted SLC corruption must not silently select a less restrictive default."""

from pathlib import Path
import tempfile
import unittest

from openpilot.common.params import ParamKeyFlag, Params, UnknownKeyName
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits import offset_document as od
from openpilot.starpilot.speed_limits.runtime_settings import PedalOverride, parse, read_params


class RuntimeSettingsTests(unittest.TestCase):
  def test_old_splits_and_renames_do_not_seed_current_settings(self):
    from openpilot.starpilot.settings_retirement import retire_settings
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put('SLCConfirmation', 1, block=True)
      old_values = {'SLCAdvanceSeconds': b'9', 'SLCConfirmationDirection': b'Lower only',
                    'SetSpeedLimit': b'1', 'SLCPedalOverride': b'2'}
      for key, raw in old_values.items():
        self.assertNotIn(key.encode(), params.all_keys())
        Path(params.get_param_path(key)).write_bytes(raw)
      retire_settings(params)
      settings = read_params(params)
      self.assertTrue(settings.acceptance.confirm_lower and settings.acceptance.confirm_higher)
      self.assertEqual((settings.advance_higher_seconds, settings.advance_lower_seconds), (0, 0))
      self.assertIs(settings.pedal_override, PedalOverride.SPEED_REACHED)
      self.assertFalse(params.get_default_value('SLCOnEngage'))
      self.assertFalse(Path(params.get_param_path('SLCOnEngage')).exists())

  def test_confirmation_modes_select_each_direction(self):
    for mode, lower, higher in ((0, False, False), (1, True, True), (3, False, True), (4, True, False)):
      with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
        params = Params(directory)
        params.put_bool('SpeedLimitController', True, block=True)
        params.put('SLCConfirmation', mode, block=True)
        settings = read_params(params)
        self.assertFalse(settings.errors)
        self.assertEqual((settings.acceptance.confirm_lower, settings.acceptance.confirm_higher), (lower, higher))

  def test_confirmation_on_never_autoaccepts_first_changed_or_disengaged_limits(self):
    active = acc.Authority(acc.Mode.COMBINED, acc.LongitudinalOwner.SYSTEM, True, True, False, False)
    stopped = acc.Authority(acc.Mode.COMBINED, acc.LongitudinalOwner.SYSTEM, False, False, False, True)
    policy = parse({'SpeedLimitController': True, 'SLCConfirmation': 1}).acceptance
    for source in ('dashboard', 'map', 'vision', 'online'):
      previous = acc.Candidate(source, acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='accepted'), 20.)
      history = acc.AcceptedLimit(previous, 'drive', 0)
      for authority in (active, stopped):
        for accepted in (None, history):
          for speed in (15., 25.):
            with self.subTest(source=source, authority=authority, accepted=accepted, speed=speed):
              candidate = acc.Candidate(source, acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='new'), speed)
              result = acc.step(acc.new_session('drive', accepted), acc.Observation(acc.ObservationKind.VALID, candidate),
                                authority, policy, now_ns=1)
              self.assertEqual(result.state.accepted, accepted)
              self.assertIsNone(result.history_write)
              self.assertEqual(result.state.pending is not None, authority.can_control)
              self.assertEqual(result.control_target_mps, 20. if accepted is not None and authority.can_control else None)

  def test_fresh_vision_display_default_preserves_explicit_saved_choices(self):
    from openpilot.starpilot.speed_limits.selection import Source
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      settings = read_params(params)
      self.assertTrue(settings.display)
      self.assertFalse(settings.enabled)
      self.assertEqual(settings.selection.slots, (Source.VISION, Source.MAP, None))
      self.assertTrue(settings.acceptance.display_only)
      self.assertFalse(settings.acceptance.confirm_lower or settings.acceptance.confirm_higher)
      self.assertEqual((settings.advance_higher_seconds, settings.advance_lower_seconds), (0, 0))
      params.put_bool('ShowSpeedLimits', False, block=True)
      params.put('SLCPriority1', 'Dashboard', block=True)
      params.put('SLCPriority2', 'Off', block=True)
      saved = read_params(params)
      self.assertFalse(saved.display)
      self.assertFalse(saved.enabled)
      self.assertEqual(saved.selection.slots, (Source.DASHBOARD, None, None))
      self.assertEqual(Path(params.get_param_path('ShowSpeedLimits')).read_bytes(), b'0')
      self.assertEqual(Path(params.get_param_path('SLCPriority1')).read_bytes(), b'Dashboard')
      self.assertEqual(Path(params.get_param_path('SLCPriority2')).read_bytes(), b'Off')

  def test_corrupt_saved_values_disable_control_without_rewriting(self):
    cases = {'SLCConfirmation': b'2',
             'IsMetric': b'2', 'SLCPriority1': b'\xff', 'SLCPriority3': b'M' * 33}
    for key, raw in cases.items():
      with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
        params = Params(directory)
        params.put_bool('SpeedLimitController', True, block=True)
        path = Path(params.get_param_path(key))
        path.write_bytes(raw)
        settings = read_params(params)
        self.assertFalse(settings.enabled)
        self.assertTrue(settings.errors)
        self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(params.get_bool('SpeedLimitController'))

  def test_absent_optional_values_keep_existing_defaults(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      settings = read_params(params)
      self.assertTrue(settings.enabled)
      self.assertFalse(settings.errors)
      self.assertTrue(all(band.offset_mps == 0 for band in settings.offsets.bands))

  def test_valid_native_values_preserve_explicit_zero_and_units(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      params.put('SLCConfirmation', 1, block=True)
      document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0, 0, -5 * od.MPH_TO_MPS, 0, 0, 0, 0))
      params.put('SLCOffsetSchedule', od.to_value(document), block=True)
      settings = read_params(params)
      self.assertTrue(settings.enabled)
      self.assertFalse(settings.errors)
      self.assertTrue(settings.acceptance.confirm_lower)
      self.assertAlmostEqual(settings.offsets.bands[2].offset_mps, -5 * od.MPH_TO_MPS)

  def test_startup_clears_retired_slc_keys_and_preserves_current_mode(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      retired = ('SLCOverride', 'SLCFallback', 'SLCConfirmationHigher', 'SLCConfirmationLower')
      paths = [Path(params.get_param_path(key)) for key in retired]
      for path in paths:
        path.write_bytes(b'1')
      params.put('SLCOverrideMode', 2, block=True)
      params.clear_all(ParamKeyFlag.CLEAR_ON_MANAGER_START)
      self.assertTrue(all(not path.exists() for path in paths))
      self.assertIs(read_params(params).pedal_override, PedalOverride.SET_SPEED)

  def test_pedal_override_modes_and_bad_saved_value(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      self.assertIs(read_params(params).pedal_override, PedalOverride.SPEED_REACHED)
      path = Path(params.get_param_path('SLCOverrideMode'))
      for raw, mode in ((b'0', PedalOverride.OFF), (b'1', PedalOverride.SPEED_REACHED),
                        (b'2', PedalOverride.SET_SPEED), (b'broken', PedalOverride.OFF)):
        with self.subTest(raw=raw):
          path.write_bytes(raw)
          settings = read_params(params)
          self.assertTrue(settings.enabled)
          self.assertIs(settings.pedal_override, mode)

  def test_nonfinite_or_unrepresentable_current_offset_disables_control(self):
    for value in (float('nan'), float('inf'), 10 ** 1000):
      with self.subTest(kind=type(value).__name__):
        document = od.OffsetDocument(od.IMPERIAL_BOUNDS, (0, 0, value, 0, 0, 0, 0))
        settings = parse({'SpeedLimitController': True, 'SLCOffsetSchedule': document})
        self.assertFalse(settings.enabled)
        self.assertTrue(settings.errors)

  def test_wheel_override_is_opt_in_and_bad_bytes_keep_slc_enabled(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool('SpeedLimitController', True, block=True)
      self.assertFalse(params.get_default_value('SLCWheelOverride'))
      self.assertFalse(read_params(params).wheel_override)
      params.put_bool('SLCWheelOverride', True, block=True)
      self.assertTrue(read_params(params).wheel_override)
      Path(params.get_param_path('SLCWheelOverride')).write_bytes(b'bad')
      settings = read_params(params)
      self.assertTrue(settings.enabled)
      self.assertFalse(settings.wheel_override)

  def test_retired_offsets_are_unregistered_and_ignored_with_zero_defaults(self):
    for metric in (False, True):
      with self.subTest(metric=metric), tempfile.TemporaryDirectory() as directory:
        params = Params(directory)
        params.put_bool('SpeedLimitController', True, block=True)
        params.put_bool('IsMetric', metric, block=True)
        for index in range(1, 8):
          key = f'Offset{index}'
          with self.assertRaises(UnknownKeyName):
            params.check_key(key)
          Path(params.get_param_path(key)).write_bytes(b'3' if index % 2 else b'broken')
        settings = read_params(params)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.offsets, od.default_document(metric).schedule())
        self.assertFalse(Path(params.get_param_path('SLCOffsetSchedule')).exists())
        for retired in (3, float('nan'), 'broken', 10 ** 1000):
          self.assertEqual(parse({'SpeedLimitController': True, 'IsMetric': metric, 'Offset1': retired}), settings)


if __name__ == '__main__':
  unittest.main()
