from openpilot.common.params import Params
from openpilot.system.ui.lib.multilang import Multilang, PLURAL_SELECTORS


def test_external_language_selection_and_english_fallback(tmp_path):
  language = Multilang()
  class SavedLanguage(Params):
    def get(self, key, block=False, return_default=False):
      assert key == 'LanguageSetting'
      return super().get(key, block=block, return_default=return_default)

  saved = SavedLanguage(str(tmp_path))
  saved.put('LanguageSetting', 'vi', block=True)
  language._params = saved
  language.refresh_language()
  assert language.language == 'vi'
  assert language.tr('Cancel') == 'Hủy'
  assert language.tr('Untranslated text') == 'Untranslated text'
  assert language.trn('{} day ago', '{} days ago', 2) == '{} ngày trước'
  assert PLURAL_SELECTORS['vi'](0) == PLURAL_SELECTORS['vi'](2) == 0
  saved.put('LanguageSetting', 'main_ja', block=True)
  language.refresh_language()
  assert language.language == 'ja' and language.requires_font_fallback()
  saved.put('LanguageSetting', 'unknown', block=True)
  language.refresh_language()
  assert language.language == 'en' and language.tr('Cancel') == 'Cancel'
