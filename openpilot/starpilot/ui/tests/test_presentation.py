import unittest
import tempfile
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.starpilot.ui.presentation import BitmapFonts, FontRole, Profile, font_filename, font_path, validate_bitmap_font


class TestBitmapFonts(unittest.TestCase):
  def test_supplied_sora_brand_is_pinned_and_independent_of_external_font_bundle(self):
    filename = font_filename(Profile.COMPACT, FontRole.BRAND)
    self.assertEqual(filename, "Sora-800.fnt")
    path = font_path(Path("/unavailable-external-fonts"), filename)
    self.assertEqual(path.parent.name, "fonts")
    validate_bitmap_font(path)
    metadata = json.loads(path.parents[2].joinpath("sora-brand-font.json").read_text())
    self.assertEqual((metadata["source"]["axis"], metadata["source"]["value"]), ("wght", 800))

  def test_invalid_descriptor_is_rejected_before_native_parser(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "font.fnt"
      path.write_text("not a bitmap font\n")
      with self.assertRaisesRegex(ValueError, "Invalid bitmap font"):
        validate_bitmap_font(path)

  def test_changed_atlas_is_rejected_even_with_reviewed_descriptor(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "Inter-Regular.fnt"
      descriptor, atlas = b'reviewed descriptor', b'reviewed atlas'
      path.write_bytes(descriptor)
      path.with_suffix('.png').write_bytes(atlas)
      manifest = {'files': [{'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                            for name, data in ((path.name, descriptor), (path.with_suffix('.png').name, atlas))]}
      with patch.object(Path, 'read_text', return_value=json.dumps(manifest)):
        validate_bitmap_font(path)
        path.with_suffix('.png').write_bytes(b'corrupt atlas')
        with self.assertRaisesRegex(ValueError, 'reviewed font manifest'):
          validate_bitmap_font(path)

  def test_requires_context_before_native_allocation(self):
    with patch("openpilot.starpilot.ui.presentation.rl.is_window_ready", return_value=False), \
         patch("openpilot.starpilot.ui.presentation.rl.load_font") as load:
      with self.assertRaisesRegex(RuntimeError, "graphics context"):
        BitmapFonts(Profile.LARGE, Path("unused"))
      load.assert_not_called()

  def test_failed_resource_load_releases_previously_loaded_fonts(self):
    allocated = SimpleNamespace(texture=SimpleNamespace(id=1), glyphCount=1)
    with patch("openpilot.starpilot.ui.presentation.rl.is_window_ready", return_value=True), \
         patch("openpilot.starpilot.ui.presentation.validate_bitmap_font"), \
         patch("openpilot.starpilot.ui.presentation.rl.get_font_default", return_value=SimpleNamespace(texture=SimpleNamespace(id=99))), \
         patch.object(Path, "is_file", return_value=True), \
         patch("openpilot.starpilot.ui.presentation.rl.load_font", side_effect=[allocated, RuntimeError("broken asset")]), \
         patch("openpilot.starpilot.ui.presentation.rl.gen_texture_mipmaps"), \
         patch("openpilot.starpilot.ui.presentation.rl.set_texture_filter"), \
         patch("openpilot.starpilot.ui.presentation.rl.unload_font") as unload:
      with self.assertRaisesRegex(RuntimeError, "broken asset"):
        BitmapFonts(Profile.LARGE, Path("unused"))
      unload.assert_called_once_with(allocated)

  def test_failed_load_cannot_adopt_or_unload_the_shared_default_font(self):
    default = SimpleNamespace(texture=SimpleNamespace(id=99), glyphCount=95)
    with patch("openpilot.starpilot.ui.presentation.rl.is_window_ready", return_value=True), \
         patch("openpilot.starpilot.ui.presentation.validate_bitmap_font"), \
         patch("openpilot.starpilot.ui.presentation.rl.get_font_default", return_value=default), \
         patch.object(Path, "is_file", return_value=True), \
         patch("openpilot.starpilot.ui.presentation.rl.load_font", return_value=default), \
         patch("openpilot.starpilot.ui.presentation.rl.unload_font") as unload:
      with self.assertRaisesRegex(RuntimeError, "Unable to load bitmap font"):
        BitmapFonts(Profile.LARGE, Path("unused"))
      unload.assert_not_called()

  def test_drawing_does_not_double_apply_an_application_text_scale(self):
    fonts = object.__new__(BitmapFonts)
    fonts.profile = Profile.COMPACT
    fonts._fonts = {"Inter-Regular.fnt": SimpleNamespace(texture=SimpleNamespace(id=1))}
    with patch("openpilot.starpilot.ui.presentation.rl._orig_draw_text_ex", create=True) as raw_draw, \
         patch("openpilot.starpilot.ui.presentation.rl.draw_text_ex") as scaled_draw:
      fonts.draw("sample", FontRole.ROMAN, 36, 10, 20)
      scaled_draw.assert_not_called()
      self.assertAlmostEqual(raw_draw.call_args.args[3], 36 * 1.16)


if __name__ == "__main__":
  unittest.main()


class TestLocalizedPresentation(unittest.TestCase):
  def test_measure_and_draw_use_the_same_translated_text_and_font(self):
    fonts = object.__new__(BitmapFonts)
    fonts.profile = Profile.COMPACT
    fonts._measurements = {}
    font = SimpleNamespace(texture=SimpleNamespace(id=44))
    with patch('openpilot.starpilot.ui.presentation.tr', side_effect=lambda text: {'Home': 'ホーム'}.get(text, text)), \
         patch.object(fonts, '_text_font', return_value=font) as select, \
         patch('openpilot.starpilot.ui.presentation.rl.measure_text_ex', return_value=SimpleNamespace(x=80, y=24)) as measure, \
         patch('openpilot.starpilot.ui.presentation.rl._orig_draw_text_ex', create=True) as draw:
      assert fonts.measure('Home', FontRole.NORMAL, 20).width == 80
      fonts.draw('Home', FontRole.NORMAL, 20, 10, 20)
      assert measure.call_args.args[1] == draw.call_args.args[1] == 'ホーム'
      assert select.call_args.args[:2] == ('ホーム', FontRole.NORMAL)
      fonts.draw('StarPilot', FontRole.BRAND, 20, 10, 20)
      assert draw.call_args.args[1] == 'StarPilot'

  def test_native_language_font_uses_bundled_noto_and_releases_previous(self):
    fonts = object.__new__(BitmapFonts)
    old = SimpleNamespace(texture=SimpleNamespace(id=22), glyphCount=1)
    new = SimpleNamespace(texture=SimpleNamespace(id=23), glyphCount=100)
    fonts._language_fonts = {'vi': old}
    fonts._measurements = {}
    fonts._vertical_ink = {}
    fonts._horizontal_ink = {}
    with patch('openpilot.starpilot.ui.presentation.multilang') as language, \
         patch('openpilot.starpilot.ui.presentation.rl.load_font_ex', return_value=new) as load, \
         patch('openpilot.starpilot.ui.presentation.rl.get_font_default', return_value=SimpleNamespace(texture=SimpleNamespace(id=99))), \
         patch('openpilot.starpilot.ui.presentation.rl.unload_font') as unload, \
         patch('openpilot.starpilot.ui.presentation.rl.gen_texture_mipmaps'), \
         patch('openpilot.starpilot.ui.presentation.rl.set_texture_filter'):
      language.language = 'ja'
      assert fonts._text_font('ホーム', FontRole.NORMAL) is new
      assert load.call_args.args[0].endswith('NotoSansCJKjp-Regular.otf')
      unload.assert_called_once_with(old)
      assert fonts._language_fonts == {'ja': new}
      assert fonts._text_font('保存', FontRole.NORMAL) is new
      load.assert_called_once()

  def test_home_statistics_use_shared_localized_measurement_and_drawing(self):
    from unittest.mock import Mock
    from openpilot.starpilot.ui.home_stats import DriveStatsDashboard
    from openpilot.starpilot.ui.home_state import DriveStatsData, DriveSummary
    fonts = Mock(spec=BitmapFonts, font=Mock(side_effect=[object(), object(), object()]),
                 measure=Mock(return_value=SimpleNamespace(width=80, height=24)), draw=Mock())
    data = DriveStatsData(DriveSummary(), DriveSummary(), DriveSummary(), (), ())
    dashboard = DriveStatsDashboard(fonts, data)
    measured = dashboard._measure(dashboard._font_bold, 'PERSONAL RECORDS', 20)
    assert (measured.x, measured.y) == (80, 24)
    dashboard._draw_text(dashboard._font_bold, 'PERSONAL RECORDS', SimpleNamespace(x=10, y=20), 20, 0, 'color')
    fonts.measure.assert_called_once_with('PERSONAL RECORDS', FontRole.BOLD, 20)
    fonts.draw.assert_called_once_with('PERSONAL RECORDS', FontRole.BOLD, 20, 10, 20, 'color', spacing=0)
