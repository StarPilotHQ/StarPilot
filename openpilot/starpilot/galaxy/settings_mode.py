from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved

KEY = "SettingsAdvanced"


def read_mode(params) -> dict:
  raw, readable = read_saved(params, KEY, 1)
  return {"mode": "advanced" if readable and raw == b"1" else "simple"}


def save_mode(params, mode: str, *, authorized) -> dict:
  if type(mode) is not str or mode not in ("simple", "advanced"):
    raise ValueError("Invalid settings mode")
  raw, readable = read_saved(params, KEY, 1)
  if not readable or not commit_exact(params, key=KEY, max_bytes=1, raw=b"1" if mode == "advanced" else b"0",
                                    expected=raw, authorized=authorized, temp_prefix=".settings-mode-").verified:
    raise RuntimeError("Settings mode could not be saved")
  return read_mode(params)
