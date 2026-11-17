#!/usr/bin/env python3
from itertools import chain
import os
import json
from pathlib import Path
from openpilot.common.basedir import BASEDIR
from openpilot.system.ui.lib.multilang import SYSTEM_UI_DIR, UI_DIR, TRANSLATIONS_DIR, multilang
from openpilot.selfdrive.ui.translations.potools import extract_strings, generate_pot, merge_po, init_po, POEntry

LANGUAGES_FILE = os.path.join(str(TRANSLATIONS_DIR), "languages.json")
POT_FILE = os.path.join(str(TRANSLATIONS_DIR), "app.pot")


def display_catalog_entries():
  path = Path(BASEDIR) / "openpilot/starpilot/ui/translation_messages.json"
  document = json.loads(path.read_text(encoding="utf-8"))
  return [POEntry(msgid=row["source"], source_refs=row["refs"]) for row in document["messages"]]


def update_translations():
  files = []
  for root, _, filenames in chain(os.walk(SYSTEM_UI_DIR),
                                  os.walk(os.path.join(str(UI_DIR), "widgets")),
                                  os.walk(os.path.join(str(UI_DIR), "layouts")),
                                  os.walk(os.path.join(str(UI_DIR), "onroad"))):
    for filename in filenames:
      if filename.endswith(".py"):
        files.append(os.path.relpath(os.path.join(root, filename), BASEDIR))

  files.append("openpilot/starpilot/ui/translation_strings.py")

  # Extract translatable strings and generate .pot template
  entries = extract_strings(files, BASEDIR)
  by_source = {entry.msgid: entry for entry in entries}
  for entry in display_catalog_entries():
    if entry.msgid in by_source:
      by_source[entry.msgid].source_refs = sorted(set(by_source[entry.msgid].source_refs + entry.source_refs))
    else:
      by_source[entry.msgid] = entry
  generate_pot(sorted(by_source.values(), key=lambda entry: entry.msgid), POT_FILE)

  # Generate/update translation files for each language
  for name in multilang.languages.values():
    po_file = os.path.join(str(TRANSLATIONS_DIR), f"app_{name}.po")
    if os.path.exists(po_file):
      merge_po(po_file, POT_FILE)
    else:
      init_po(POT_FILE, po_file, name)


if __name__ == "__main__":
  update_translations()
