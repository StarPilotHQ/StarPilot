"""Automatic download preference; manual updater requests remain independent."""

from openpilot.starpilot.saved_source import read_saved

AUTOMATIC_DOWNLOADS = 'UpdaterAutomaticDownloads'


def automatic_downloads(params) -> bool | None:
  try:
    raw, readable = read_saved(params, AUTOMATIC_DOWNLOADS, 1)
  except (OSError, KeyError, ValueError):
    return None
  if not readable or raw not in (None, b'0', b'1'):
    return None
  return raw != b'0'  # Preserve the upstream schedule for existing installations.


def download_permitted(params, *, manual: bool) -> bool:
  return manual or automatic_downloads(params) is True
