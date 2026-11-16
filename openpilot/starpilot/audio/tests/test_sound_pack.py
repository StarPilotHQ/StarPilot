from pathlib import Path
import tempfile
import shutil
import unittest
from unittest.mock import patch
import wave

import numpy as np

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params
from openpilot.selfdrive.ui.soundd import AudibleAlert, Soundd, sound_list
from openpilot.starpilot.audio.sound_pack import BUILTIN_FILES, MAX_WAV_BYTES, SoundPackLoader, installed_packs, read_wav, tici_speaker_samples
from openpilot.starpilot.audio.sound_pack import tici_disengage_samples
from openpilot.starpilot.ui.feature_settings_state import FeatureSettingsRequest
from openpilot.starpilot.ui.sounds_owner import SoundsOwner


def write_wav(path, samples=(100, -200, 300), channels=1, rate=48000):
  path.parent.mkdir(parents=True, exist_ok=True)
  with wave.open(str(path), "wb") as target:
    target.setnchannels(channels)
    target.setsampwidth(2)
    target.setframerate(rate)
    target.writeframes(np.array(samples, dtype=np.int16).tobytes())


class SoundPackTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.base = Path(temporary.name)
    self.params = Params(str(self.base / "params"))
    self.params.put("SoundPack", "stock", block=True)
    self.root = self.base / "packs"
    self.stock = Path(BASEDIR) / "openpilot/selfdrive/assets/sounds"
    self.pack = self.root / "custom" / "sounds"
    self.pack.mkdir(parents=True)
    self.loader = SoundPackLoader(self.params, self.stock, self.root)

  def sound_pack_row(self, owner):
    rows = [row for row in owner.snapshot().rows if row.key == 'SoundPack']
    self.assertEqual(len(rows), 1)
    return rows[0]

  def test_stock_samples_are_exact_and_cached(self):
    filenames = tuple({spec[0] for spec in sound_list.values()})
    loaded = self.loader.refresh(filenames, 0)
    assert loaded is not None
    for filename in filenames:
      with wave.open(str(self.stock / filename), "rb") as source:
        expected = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / (2**16/2)
      np.testing.assert_array_equal(loaded[filename], expected)
      self.assertFalse(loaded[filename].flags.writeable)
    with patch("openpilot.starpilot.audio.sound_pack.read_wav", side_effect=AssertionError("reload")):
      self.assertIsNone(self.loader.refresh(filenames, 0.2))
      self.assertIsNone(self.loader.refresh(filenames, 1))

  def test_logical_alias_and_individual_fallback(self):
    write_wav(self.pack / "prompt.wav")
    self.params.put("SoundPack", "custom", block=True)
    loaded = self.loader.refresh(("warning.wav", "critical.wav"), 0)
    assert loaded is not None
    np.testing.assert_array_equal(loaded["warning.wav"], np.array((100, -200, 300), dtype=np.float32) / 32768)
    np.testing.assert_array_equal(loaded["critical.wav"], read_wav(self.stock / "critical.wav"))
    write_wav(self.pack / "prompt.wav", (500,))
    self.assertIsNone(self.loader.refresh(("warning.wav", "critical.wav"), 0.5))
    refreshed = self.loader.refresh(("warning.wav", "critical.wav"), 1)
    assert refreshed is not None
    self.assertEqual(len(refreshed["warning.wav"]), 1)

  def test_invalid_custom_files_fall_back(self):
    path = self.pack / "engage.wav"
    self.params.put("SoundPack", "custom", block=True)
    for kind in ("empty", "stereo", "rate", "truncated", "garbage", "oversize", "symlink"):
      with self.subTest(kind=kind):
        path.unlink(missing_ok=True)
        if kind == "garbage":
          path.write_bytes(b"invalid")
        elif kind == "oversize":
          with path.open("wb") as target:
            target.truncate(MAX_WAV_BYTES + 1)
        elif kind == "symlink":
          path.symlink_to(self.stock / "engage.wav")
        else:
          write_wav(path, () if kind == "empty" else (100, 200), channels=2 if kind == "stereo" else 1,
                    rate=24000 if kind == "rate" else 48000)
          if kind == "truncated":
            path.write_bytes(path.read_bytes()[:-2])
        loader = SoundPackLoader(self.params, self.stock, self.root)
        loaded = loader.refresh(("engage.wav",), 0)
        assert loaded is not None
        np.testing.assert_array_equal(loaded["engage.wav"], read_wav(self.stock / "engage.wav"))

  def test_missing_traversal_and_symlink_pack_fall_back(self):
    (self.root / "linked").symlink_to(self.root / "custom", target_is_directory=True)
    self.assertEqual(installed_packs(self.root), ("starpilot", "stock", "custom"))
    for name in ("missing", "../custom", "linked"):
      self.params.put("SoundPack", name, block=True)
      loader = SoundPackLoader(self.params, self.stock, self.root)
      fallback = self.stock.parent / "sounds_starpilot" if name == "../custom" else self.stock
      loaded = loader.refresh(("engage.wav",), 0)
      assert loaded is not None
      np.testing.assert_array_equal(loaded["engage.wav"], read_wav(fallback / "engage.wav"))

  def test_owner_parked_saved_source_and_unavailable_repair(self):
    parked = [True]
    owner = SoundsOwner(self.params, lambda: parked[0], self.root)
    row = self.sound_pack_row(owner)
    self.assertEqual(row.choices, ("StarPilot Sounds", "Stock Sounds", "custom"))
    request = FeatureSettingsRequest("SoundPack", row.source, "custom")
    self.assertTrue(owner.apply(request))
    self.assertFalse(owner.apply(request))
    parked[0] = False
    self.assertFalse(owner.apply(FeatureSettingsRequest("SoundPack", b"custom", "stock")))
    parked[0] = True
    self.params.put("SoundPack", "missing", block=True)
    self.assertEqual(self.sound_pack_row(owner).repair_value, "starpilot")
    self.assertTrue(owner.apply(FeatureSettingsRequest("SoundPack", b"missing", "stock")))

  def test_era_labels_preserve_saved_ids_and_select_exact_assets(self):
    from openpilot.starpilot.audio.sound_pack import migrate_removed_selection
    for slug, sample in (("old_engage_sounds", 1000), ("classic_openpilot", 2000)):
      write_wav(self.root / slug / "sounds" / "engage.wav", (sample,))
    owner = SoundsOwner(self.params, lambda: True, self.root)
    self.params.put("SoundPack", "old_engage_sounds", block=True)
    self.assertFalse(migrate_removed_selection(self.params, self.root))
    row = self.sound_pack_row(owner)
    self.assertEqual(row.value, "openpilot 2025")
    self.assertIn("Classic openpilot (pre-C4)", row.choices)
    for now, (label, slug, sample) in enumerate((("Classic openpilot (pre-C4)", "classic_openpilot", 2000),
                                               ("openpilot 2025", "old_engage_sounds", 1000))):
      row = self.sound_pack_row(owner)
      self.assertTrue(owner.apply(FeatureSettingsRequest("SoundPack", row.source, label)))
      self.assertEqual(self.params.get("SoundPack"), slug)
      loaded = self.loader.refresh(("engage.wav",), now)
      assert loaded is not None
      np.testing.assert_array_equal(loaded["engage.wav"], read_wav(self.root / slug / "sounds" / "engage.wav"))
      self.assertAlmostEqual(float(loaded["engage.wav"][0]), sample / 32768)

  def test_callback_does_not_read_files_or_params_and_keeps_chimes(self):
    sound = Soundd(self.params, pack_root=self.root)
    sound.update_alert(AudibleAlert.engage)
    output = np.zeros((32, 1), dtype=np.float32)
    with patch("pathlib.Path.open", side_effect=AssertionError("callback IO")), \
         patch.object(sound.pack_loader, "refresh", side_effect=AssertionError("callback refresh")):
      sound.callback(output, 32, None, None)
    self.assertTrue(np.any(output))
    assert sound.axis_alerts is not None

  def test_service_swap_preserves_gain_and_callback_snapshot(self):
    write_wav(self.pack / "engage.wav", (1000, -1000))
    self.params.put("SoundPack", "custom", block=True)
    sound = Soundd(self.params, pack_root=self.root)
    sound.pack_loader = self.loader
    sound.load_sounds()
    sound.update_alert(AudibleAlert.engage)
    sound.current_volume = 0.25
    sound.saved_volumes["EngageVolume"] = 50
    np.testing.assert_array_equal(sound.get_sound_data(2), np.array((1000, -1000), dtype=np.float32) / 65536)
    previous = sound.loaded_sounds
    self.params.put("SoundPack", "stock", block=True)
    self.loader.checked_at -= 2
    sound.load_sounds()
    self.assertIsNot(previous, sound.loaded_sounds)
    np.testing.assert_array_equal(previous[AudibleAlert.engage], np.array((1000, -1000), dtype=np.float32) / 32768)

  def test_corrupt_selection_can_be_repaired_without_read_mutation(self):
    path = Path(self.params.get_param_path("SoundPack"))
    path.write_bytes(b"\xff")
    owner = SoundsOwner(self.params, lambda: True, self.root)
    row = self.sound_pack_row(owner)
    self.assertEqual(row.source, b"\xff")
    self.assertEqual(row.repair_value, "starpilot")
    self.assertEqual(path.read_bytes(), b"\xff")
    self.assertTrue(owner.apply(FeatureSettingsRequest("SoundPack", b"\xff", "stock")))

  def test_current_names_and_legacy_precedence(self):
    self.params.put("SoundPack", "custom", block=True)
    for current, legacy in (("warning.wav", "prompt.wav"), ("dm_warning.wav", "prompt_distracted.wav"),
                            ("critical.wav", "warning_soft.wav"), ("dm_critical.wav", "warning_immediate.wav")):
      with self.subTest(current=current):
        write_wav(self.pack / current, (700,))
        loader = SoundPackLoader(self.params, self.stock, self.root)
        selected = loader.refresh((current,), 0)
        assert selected is not None
        self.assertEqual(selected[current][0], 700 / 32768)
        write_wav(self.pack / legacy, (900,))
        selected = loader.refresh((current,), 1)
        assert selected is not None
        self.assertEqual(selected[current][0], 900 / 32768)

  def test_service_cache_gate_performs_no_filesystem_reads(self):
    self.loader.refresh(("engage.wav",), 0)
    with patch("pathlib.Path.open", side_effect=AssertionError("cached Params read")), \
         patch("pathlib.Path.lstat", side_effect=AssertionError("cached stat")), \
         patch("pathlib.Path.is_dir", side_effect=AssertionError("cached directory")):
      for frame in range(1, 20):
        self.assertIsNone(self.loader.refresh(("engage.wav",), frame / 20))


class TiciSpeakerTests(unittest.TestCase):
  def test_bass_reduction_preserves_presence_and_never_boosts(self):
    t = np.arange(48000) / 48000
    for frequency, bounds in ((100, (.23, .27)), (400, (.03, .05)), (1500, (.98, 1.01)), (4000, (.98, 1.01))):
      with self.subTest(frequency=frequency):
        samples = (.25 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
        original = samples.copy()
        output = tici_speaker_samples(samples)
        ratio = np.sqrt(np.mean(output[1024:-1024] ** 2) / np.mean(samples[1024:-1024] ** 2))
        self.assertGreaterEqual(ratio, bounds[0])
        self.assertLessEqual(ratio, bounds[1])
        self.assertLessEqual(np.max(np.abs(output)), np.max(np.abs(samples)))
        self.assertEqual(output.shape, samples.shape)
        self.assertEqual(output.dtype, np.float32)
        self.assertFalse(output.flags.writeable)
        np.testing.assert_array_equal(samples, original)

  def test_short_and_silent_clips_keep_length_and_finite_output(self):
    for samples in (np.zeros(1, np.float32), np.zeros(512, np.float32), np.array([.1, -.2, .3], np.float32)):
      output = tici_speaker_samples(samples)
      self.assertEqual(output.shape, samples.shape)
      self.assertTrue(np.isfinite(output).all())
      self.assertLessEqual(np.max(np.abs(output)), np.max(np.abs(samples)))

  def test_only_tici_builtin_changes_and_retains_cached_identity(self):
    with tempfile.TemporaryDirectory() as temporary:
      params = Params(str(Path(temporary) / 'params'))
      params.put('SoundPack', 'starpilot', block=True)
      stock = Path(BASEDIR) / 'openpilot/selfdrive/assets/sounds'
      names = tuple(BUILTIN_FILES)
      for device in ('tici', 'tizi', 'mici', 'pc'):
        with self.subTest(device=device):
          loader = SoundPackLoader(params, stock, Path(temporary) / 'packs', device_type=device)
          loaded = loader.refresh(names, 0)
          assert loaded is not None
          for name, samples in loaded.items():
            original = read_wav(stock.parent / 'sounds_starpilot' / BUILTIN_FILES[name])
            self.assertEqual(len(samples), len(original))
            self.assertTrue(loader.is_builtin(samples))
            self.assertFalse(samples.flags.writeable)
            if device == 'tici':
              self.assertEqual(samples[0], 0.)
              self.assertEqual(samples[-1], 0.)
              self.assertLess(np.sqrt(np.mean(samples ** 2)), .8 * np.sqrt(np.mean(original ** 2)))
              self.assertLessEqual(np.max(np.abs(samples)), np.max(np.abs(original)))
            else:
              np.testing.assert_array_equal(samples, original)
          self.assertIs(loaded['warning.wav'], loaded['pre_alert.wav'])
          with patch('openpilot.starpilot.audio.sound_pack.tici_speaker_samples', side_effect=AssertionError('cached EQ')):
            self.assertIsNone(loader.refresh(names, 1))

  def test_tici_stock_and_custom_packs_are_bit_exact(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      params = Params(str(root / 'params'))
      stock = Path(BASEDIR) / 'openpilot/selfdrive/assets/sounds'
      custom = root / 'packs/custom/sounds/engage.wav'
      write_wav(custom)
      for name in ('stock', 'custom'):
        params.put('SoundPack', name, block=True)
        with patch('openpilot.starpilot.audio.sound_pack.tici_speaker_samples', side_effect=AssertionError('unexpected EQ')):
          loaded = SoundPackLoader(params, stock, root / 'packs', device_type='tici').refresh(('engage.wav',), 0)
        assert loaded is not None
        np.testing.assert_array_equal(loaded['engage.wav'], read_wav(stock / 'engage.wav' if name == 'stock' else custom))

  def test_actual_tici_callback_uses_prepared_samples_without_filtering(self):
    with tempfile.TemporaryDirectory() as temporary:
      with patch('openpilot.selfdrive.ui.soundd.HARDWARE.get_device_type', return_value='tici'):
        daemon = Soundd(Params(str(Path(temporary) / 'params')), pack_root=Path(temporary) / 'packs')
      for alert in (AudibleAlert.engage, AudibleAlert.promptRepeat, AudibleAlert.warningSoft, AudibleAlert.warningImmediate):
        samples = daemon.loaded_sounds[alert]
        daemon.current_alert, daemon.current_sound = alert, alert
        daemon.current_sound_frame, daemon.current_volume = 0, 1.
        output = np.zeros((len(samples), 1), np.float32)
        with patch('openpilot.starpilot.audio.sound_pack.tici_speaker_samples', side_effect=AssertionError('callback EQ')):
          daemon.callback(output, len(output), None, None)
        np.testing.assert_array_equal(output[:, 0], samples)
        self.assertEqual(daemon.current_sound_frame, len(samples))


def test_tici_speaker_targets_reported_resonance_without_cutting_chimes():
  from openpilot.starpilot.audio.sound_pack import tici_speaker_samples
  time = np.arange(48000) / 48000
  amplitudes = {}
  for frequency in (370, 420, 470, 880, 1200, 1570):
    samples = (0.5 * np.sin(2 * np.pi * frequency * time)).astype(np.float32)
    output = tici_speaker_samples(samples)
    amplitudes[frequency] = np.sqrt(np.mean(output[4096:-4096] ** 2) / np.mean(samples[4096:-4096] ** 2))
    assert len(output) == len(samples) and output.dtype == np.float32
    assert not output.flags.writeable and np.isfinite(output).all()
    assert np.max(np.abs(output)) <= np.max(np.abs(samples)) + 1e-7
  for frequency in (370, 420, 470):
    assert amplitudes[frequency] < 0.05
  for frequency in (880, 1200, 1570):
    assert 0.95 < amplitudes[frequency] <= 1.001


ASSETS = Path(__file__).resolve().parents[3] / 'selfdrive/assets'


class FileSelection:
  def __init__(self, root):
    self.root = root

  def get_param_path(self, name):
    return str(self.root / name)


class TiciDisengageTests(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    self.params = FileSelection(self.root)
    self.stock = ASSETS / 'sounds'
    self.packs = self.root / 'packs'

  def test_only_tici_builtin_disengage_gets_additional_refinement(self):
    names = ('engage.wav', 'disengage.wav', 'warning.wav', 'critical.wav', 'dm_critical.wav')
    for device in ('tici', 'tizi', 'mici', 'pc', ''):
      with self.subTest(device=device):
        loaded = SoundPackLoader(self.params, self.stock, self.packs, device_type=device).refresh(names, 0)
        assert loaded is not None
        for name in names:
          expected = read_wav(ASSETS / 'sounds_starpilot' / BUILTIN_FILES[name])
          if device == 'tici':
            expected = tici_speaker_samples(expected)
            if name == 'disengage.wav':
              expected = tici_disengage_samples(expected)
          np.testing.assert_array_equal(loaded[name], expected)
          self.assertFalse(loaded[name].flags.writeable)

  def test_stock_selection_bypasses_refinement(self):
    (self.root / 'SoundPack').write_bytes(b'stock')
    with patch('openpilot.starpilot.audio.sound_pack.tici_disengage_samples', side_effect=AssertionError('stock EQ')):
      loaded = SoundPackLoader(self.params, self.stock, self.packs, device_type='tici').refresh(('disengage.wav',), 0)
    assert loaded is not None
    np.testing.assert_array_equal(loaded['disengage.wav'], read_wav(self.stock / 'disengage.wav'))

  def test_custom_selection_bypasses_refinement(self):
    (self.root / 'SoundPack').write_bytes(b'custom')
    directory = self.packs / 'custom/sounds'
    directory.mkdir(parents=True)
    shutil.copyfile(ASSETS / 'sounds_starpilot/disengage.wav', directory / 'disengage.wav')
    with patch('openpilot.starpilot.audio.sound_pack.tici_disengage_samples', side_effect=AssertionError('custom EQ')):
      loaded = SoundPackLoader(self.params, self.stock, self.packs, device_type='tici').refresh(('disengage.wav',), 0)
    assert loaded is not None
    np.testing.assert_array_equal(loaded['disengage.wav'], read_wav(directory / 'disengage.wav'))

  def test_refinement_keeps_shape_peak_and_input_and_reduces_low_body_and_tail(self):
    samples = tici_speaker_samples(read_wav(ASSETS / 'sounds_starpilot/disengage.wav'))
    original = samples.copy()
    output = tici_disengage_samples(samples)
    self.assertEqual(output.shape, samples.shape)
    self.assertEqual(output.dtype, np.float32)
    self.assertFalse(output.flags.writeable)
    self.assertEqual(output[0], 0.)
    self.assertEqual(output[-1], 0.)
    self.assertTrue(np.isfinite(output).all())
    self.assertLessEqual(np.max(np.abs(output)), np.max(np.abs(samples)))
    np.testing.assert_array_equal(samples, original)
    frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
    low = frequencies < 600
    before, after = (np.abs(np.fft.rfft(x)) ** 2 for x in (samples, output))
    self.assertLess(after[low].sum(), before[low].sum() * .3)
    self.assertLess(np.mean(output[-9600:] ** 2), np.mean(samples[-9600:] ** 2) * .7)
    upper = frequencies > 750
    self.assertGreater(np.corrcoef(np.log1p(before[upper]), np.log1p(after[upper]))[0, 1], .98)

  def test_silent_and_short_clips_remain_finite_and_same_length(self):
    for samples in (np.zeros(1, np.float32), np.zeros(512, np.float32), np.array([.1, -.2, .3], np.float32)):
      output = tici_disengage_samples(samples)
      self.assertEqual(output.shape, samples.shape)
      self.assertTrue(np.isfinite(output).all())
      self.assertLessEqual(np.max(np.abs(output)), np.max(np.abs(samples)))

  def test_refinement_is_cached_at_load_time(self):
    loader = SoundPackLoader(self.params, self.stock, self.packs, device_type='tici')
    with patch('openpilot.starpilot.audio.sound_pack.tici_disengage_samples', wraps=tici_disengage_samples) as refine:
      loaded = loader.refresh(('disengage.wav',), 0)
      assert loaded is not None
      prepared = loaded['disengage.wav']
      self.assertEqual(refine.call_count, 1)
      for timestamp in (.1, .5, 1.1, 2.2):
        self.assertIsNone(loader.refresh(('disengage.wav',), timestamp))
      self.assertEqual(refine.call_count, 1)
      self.assertIs(loader.builtin_sounds['disengage.wav'], prepared)
