"""Read the device's shared language and expose its existing openpilot catalog."""

from functools import lru_cache

from openpilot.starpilot.galaxy.localization_settings import LANGUAGES
from openpilot.starpilot.saved_source import read_saved
from openpilot.system.ui.lib.multilang import TRANSLATIONS_DIR, load_translations


@lru_cache(maxsize=1)
def catalog(code):
  if code == "en":
    return {}
  messages, _ = load_translations(TRANSLATIONS_DIR.joinpath(f"app_{code}.po"))
  return {source: translated for source, translated in messages.items() if translated and source != translated}


def language_snapshot(params, previous=""):
  raw, readable = read_saved(params, "LanguageSetting", 32)
  code = (raw or b"en").decode("utf-8", errors="replace").removeprefix("main_")
  code = code if readable and code in LANGUAGES.values() else "en"
  return {"language": code, **({} if previous == code else {"messages": catalog(code)})}
