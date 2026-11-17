from openpilot.system.ui.lib.display_translation import DisplayText, display_templates, translate_display


def test_translated_text_is_not_translated_twice_after_formatting_or_elision():
  messages = {'Network': 'Red', 'Red': 'Rojo'}
  result = translate_display('Network', messages, {})
  assert isinstance(result, DisplayText) and result == 'Red'
  assert translate_display(result, messages, {}) == 'Red'
  assert translate_display(result.format(), messages, {}) == 'Red'
  assert isinstance(result[:], DisplayText)
  assert isinstance(result.lower(), DisplayText)
  assert all(isinstance(part, DisplayText) for part in result.split())


def test_formatted_display_templates_preserve_values_and_named_reordering():
  messages = {'{} drives': '{} trayectos', 'Saved {count} items for {name}': 'Para {name}: {count} elementos guardados',
              'Preparing {} ({}%)': 'Preparando {} ({}%)'}
  templates = display_templates(messages)
  assert translate_display('3 drives', messages, templates) == '3 trayectos'
  assert translate_display('Saved 12 items for Dom', messages, templates) == 'Para Dom: 12 elementos guardados'
  assert translate_display('Preparing Alpha (23%)', messages, templates) == 'Preparando Alpha (23%)'
  assert translate_display('SpeedLimitController', messages, templates) == 'SpeedLimitController'
  assert translate_display('unknown', messages, templates) == 'unknown'


def test_optional_plural_suffix_and_specific_template_take_priority():
  messages = {'{} day': '{}일', 'Expires in {} day{}': '{}일{} 후 만료', '{} minute{} Left': '{}분{} 남음',
              'Expires in {} day': '{}일 후 만료', 'Expires in {} days': '{}일 후 만료'}
  templates = display_templates(messages)
  assert translate_display('Expires in 1 day', messages, templates) == '1일 후 만료'
  assert translate_display('Expires in 3 days', messages, templates) == '3일 후 만료'
  assert translate_display('1 minute Left', messages, templates) == '1분 남음'


def test_common_label_boundary_preserves_translated_or_literal_marker(monkeypatch):
  from openpilot.system.ui.widgets.label import UnifiedLabel
  from openpilot.system.ui.lib.multilang import multilang
  monkeypatch.setattr(multilang, '_translations', {'Default': 'Standard', 'Standard': 'Standardmodus'})
  translated = DisplayText('Standard')
  label = object.__new__(UnifiedLabel)
  label._text = lambda: translated
  assert label.text == 'Standard' and isinstance(label.text, DisplayText)
  label._text = DisplayText('Default')  # A literal SSID named Default must not be localized.
  assert label.text == 'Default'


def test_retained_lazy_label_follows_language_changes(monkeypatch):
  from openpilot.system.ui.widgets.label import UnifiedLabel
  from openpilot.system.ui.lib.multilang import multilang
  label = object.__new__(UnifiedLabel)
  label._text = lambda: multilang.tr('Advanced')
  monkeypatch.setattr(multilang, '_translations', {'Advanced': 'Erweitert'})
  assert label.text == 'Erweitert'
  monkeypatch.setattr(multilang, '_translations', {'Advanced': '고급'})
  assert label.text == '고급'


def test_ui_label_placeholders_localize_but_user_names_stay_literal():
  messages = {'Language': 'Sprache', 'Home': 'Startseite', 'Reset {label} to default': '{label} zurücksetzen',
              'Saved for {name}': 'Gespeichert für {name}'}
  templates = display_templates(messages)
  assert translate_display('Reset Language to default', messages, templates) == 'Sprache zurücksetzen'
  assert translate_display('Saved for Home', messages, templates) == 'Gespeichert für Home'
