"""Validated, non-secret Spotify settings owned by this feature (kept out of Params and settings exports)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field

from openpilot.starpilot.soloist.paths import Layout, private_dir

ADDRESS = re.compile(r'^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$')
NAME = re.compile(r'^[^\x00-\x1f\x7f]{1,40}$')
CACHE_CHOICES = (100, 256, 512, 1024)
OUTPUTS = ('bluetooth', 'starport', 'test')
MUTE_MAX = 1.5


@dataclass(frozen=True)
class Preferences:
  device_name: str = 'StarPilot'
  cache_mb: int = 256
  initial_volume: int = 100
  output: str = 'bluetooth'           # or 'starport' (the car's media channel), or 'test' (a silent sink)
  output_address: str = ''
  output_name: str = ''
  source: str = 'spotify'
  phone_address: str = ''
  phone_name: str = ''
  # Live-tunable while driving: excluded from equality so changing them never restarts the player.
  pause_mute: float = field(default=0.5, compare=False)
  skip_mute: float = field(default=0.25, compare=False)
  pull_onroad: bool = field(default=False, compare=False)

  def public(self) -> dict:
    return {'deviceName': self.device_name, 'cacheMb': self.cache_mb, 'initialVolume': self.initial_volume,
            'output': self.output, 'outputAddress': self.output_address, 'outputName': self.output_name,
            'source': self.source, 'phoneAddress': self.phone_address, 'phoneName': self.phone_name,
            'pauseMute': self.pause_mute, 'skipMute': self.skip_mute, 'muteMax': MUTE_MAX,
            'pullOnroad': self.pull_onroad}


def _mute(value: object) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MUTE_MAX:
    raise ValueError(f'Mute must be 0–{MUTE_MAX} seconds')
  return round(float(value), 2)


def validated(values: dict) -> Preferences:
  values = {**values, 'output': 'starport' if values.get('output') == 'android_auto' else values.get('output', 'bluetooth')}
  prefs = Preferences(**values)
  name = prefs.device_name.strip() if isinstance(prefs.device_name, str) else ''
  if not NAME.fullmatch(name):
    raise ValueError('Use a device name of 1–40 characters')
  if type(prefs.cache_mb) is not int or prefs.cache_mb not in CACHE_CHOICES:
    raise ValueError('Choose one of the offered cache sizes')
  if type(prefs.initial_volume) is not int or not 0 <= prefs.initial_volume <= 100:
    raise ValueError('Volume must be 0–100')
  if prefs.output not in OUTPUTS:
    raise ValueError('Unknown audio output')
  address = prefs.output_address.upper() if isinstance(prefs.output_address, str) else ''
  if address and not ADDRESS.fullmatch(address):
    raise ValueError('Invalid Bluetooth address')
  output_name = prefs.output_name.strip()[:80] if isinstance(prefs.output_name, str) else ''
  if prefs.source not in ('spotify', 'phone'):
    raise ValueError('Unknown music source')
  phone = prefs.phone_address.upper() if isinstance(prefs.phone_address, str) else ''
  if phone and not ADDRESS.fullmatch(phone):
    raise ValueError('Invalid phone Bluetooth address')
  if prefs.source == 'phone' and phone and prefs.output == 'bluetooth' and phone == address:
    raise ValueError('Choose a different device for the phone and speaker')
  phone_name = prefs.phone_name.strip()[:80] if isinstance(prefs.phone_name, str) else ''
  if type(prefs.pull_onroad) is not bool:
    raise ValueError('Invalid Spotify settings')
  return Preferences(name, prefs.cache_mb, prefs.initial_volume, prefs.output, address, output_name if address else '',
                     prefs.source, phone, phone_name if phone else '', _mute(prefs.pause_mute), _mute(prefs.skip_mute),
                     prefs.pull_onroad)


def load(layout: Layout | None = None) -> Preferences:
  try:
    raw = json.loads((layout or Layout.default()).config.read_text())
    fields = {key: raw[key] for key in Preferences.__dataclass_fields__ if key in raw}
    return validated(fields)
  except (OSError, ValueError, TypeError):
    return Preferences()


def save(prefs: Preferences, layout: Layout | None = None) -> Preferences:
  layout = layout or Layout.default()
  prefs = validated(asdict(prefs))
  private_dir(layout.root)
  temporary = layout.config.with_suffix('.tmp')
  temporary.write_text(json.dumps({'version': 1, **asdict(prefs)}, sort_keys=True))
  os.replace(temporary, layout.config)
  return prefs


def set_enabled(value: bool, layout: Layout | None = None) -> None:
  layout = layout or Layout.default()
  if value:
    private_dir(layout.root)
    layout.enabled_flag.touch()
  else:
    layout.enabled_flag.unlink(missing_ok=True)
