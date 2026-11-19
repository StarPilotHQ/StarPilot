"""Cellular and Wi-Fi strength read together; deviceState only carries the link in use. Read-only."""
import re
import threading
import time
from typing import NamedTuple

TECH = {'nr': '5G', 'lte': 'LTE', 'utran': '3G', 'umts': '3G', 'gsm': '2G'}
WANTED_S = 5.
STALE_S = 10.


class Signal(NamedTuple):
  cell: int | None = None  # 1 to 4, None when not connected
  wifi: int | None = None
  active: str | None = None  # 'cell' or 'wifi': the link carrying data
  tech: str = 'LTE'
  bt: str = 'off'  # 'off', 'on', or 'connected'
  bt_devices: tuple[str, ...] = ()
  wifi_name: str = ''  # the joined network's SSID, empty when unknown


def ssid_text(value: str) -> str:
  """Undoes wpa_supplicant's escaping (\\xNN, \\n, \\\\ and the like) and decodes the SSID bytes as UTF-8."""
  def unescape(match):
    code = match[1]
    return bytes([int(code[1:], 16)]) if code[:1] == b'x' else {b'n': b'\n', b'r': b'\r', b't': b'\t', b'e': b'\x1b'}.get(code, code)
  return re.sub(rb'\\(x[0-9a-fA-F]{2}|.)', unescape, value.encode('utf-8', 'replace')).decode('utf-8', 'replace').strip()


def read_signal() -> Signal:
  from openpilot.common.hardware.comma.hardware import HardwareComma, NetworkType, get_default_route_iface, wpa_supplicant_cmd
  from openpilot.starpilot.ui.bluetooth_status import read_bluetooth_details
  hardware = HardwareComma()
  cell = active = iface = None
  wifi_name = ''
  tech = 'LTE'
  modem = hardware.get_modem_state()
  quality = modem.get('signal_quality', 0)
  if modem.get('connected') and type(quality) in (int, float):
    cell = int(hardware.parse_strength(quality))
    tech = TECH.get(modem.get('network_type'), tech)
  wifi = int(hardware.get_network_strength(NetworkType.wifi)) or None
  if wifi:
    try:
      status = wpa_supplicant_cmd('STATUS')
    except OSError:
      status = {}
    if status.get('wpa_state') == 'COMPLETED':
      wifi_name = ssid_text(status.get('ssid', ''))[:64]
  try:
    iface = get_default_route_iface()
  except (OSError, ValueError, IndexError):
    pass
  bt, bt_devices = read_bluetooth_details()
  if iface and iface.startswith('wlan'):
    active = 'wifi' if wifi else None
  elif cell and not (iface and iface.startswith('eth')):
    active = 'cell'
  return Signal(cell, wifi, active, tech, bt, bt_devices, wifi_name)


class SignalFeed:
  """Poll off-thread while snapshots are requested."""
  def __init__(self, read=read_signal, period=2.0):
    self.read, self.period = read, period
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self._thread = None
    self._signal = Signal()
    self._read_at = self._wanted_at = 0.

  def poll(self):
    try:
      signal = self.read()
    except Exception:
      signal = Signal()
    with self._lock:
      self._signal, self._read_at = signal, time.monotonic()

  def snapshot(self) -> Signal:
    now = time.monotonic()
    with self._lock:
      self._wanted_at = now
      signal, fresh = self._signal, now - self._read_at <= STALE_S
      if self._thread is None:
        self._thread = threading.Thread(target=self._run, name='network-signal', daemon=True)
        self._thread.start()
    return signal if fresh else Signal()

  def _run(self):
    while not self._stop.is_set():
      with self._lock:
        wanted = time.monotonic() - self._wanted_at <= WANTED_S
      if wanted:
        self.poll()
      self._stop.wait(self.period)

  def close(self):
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=2)
