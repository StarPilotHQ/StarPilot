"""Saved Wi-Fi order and per-network choices for the Galaxy Networks page.

NetworkManager stays the owner of each saved network; this module only changes Wi-Fi profiles.
Cellular profiles, the modem and its routes are never touched: cellular data is switched with the
same CellularDisabled setting the comma's own screen uses.
"""
from __future__ import annotations

from functools import partial
from ipaddress import ip_address, ip_network
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import cast

from openpilot.starpilot.galaxy import hotspot
from openpilot.starpilot.galaxy.hotspot import atomic_json, bounded_json

DEFAULT = {'prefer': 'wifi', 'pauseHotspot': True, 'parkedOnly': []}
CELLULAR_FIRST_METRIC = 2000   # above the modem's own default route (1000), so cellular wins while it is up
OWN_PROFILES = ('starpilot-starport',)
UUID = re.compile(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}')
METERED = {'Auto': 'unknown', 'Metered': 'yes', 'Unmetered': 'no'}
MODEM_STATE = Path('/dev/shm/modem')
MAX_NETWORKS = 64
JOIN_WAIT_S = 15
HOTSPOT_PAUSE_WAIT_S = 10.0
JOIN_START_DELAY_S = 1.0
JOIN_RUNNING_MAX_S = 90.0
JOIN_RESULT_TTL_S = 600.0


def split_terse(line: str, count: int) -> list[str]:
  """Split one `nmcli -t` line into ``count`` fields; nmcli escapes ':' and '\\' inside values."""
  fields, current, index = [], '', 0
  while index < len(line):
    character = line[index]
    if character == '\\' and index + 1 < len(line):
      current += line[index + 1]
      index += 2
      continue
    if character == ':' and len(fields) < count - 1:
      fields.append(current)
      current = ''
    else:
      current += character
    index += 1
  return [*fields, current]


def validate_prefs(value) -> dict:
  if (type(value) is not dict or set(value) != set(DEFAULT) or value['prefer'] not in ('wifi', 'cellular') or
      type(value['pauseHotspot']) is not bool or type(value['parkedOnly']) is not list or len(value['parkedOnly']) > MAX_NETWORKS or
      any(type(item) is not str or not UUID.fullmatch(item) for item in value['parkedOnly'])):
    raise ValueError('Invalid network preferences')
  return {**value, 'parkedOnly': sorted(set(cast(list[str], value['parkedOnly'])))}


def prefs_path(root=None) -> Path:
  if root is None:
    from openpilot.starpilot.storage import galaxy_storage_root
    root = galaxy_storage_root()
  return Path(root) / 'network.json'


def read_prefs(root=None) -> dict:
  try:
    return validate_prefs(bounded_json(prefs_path(root)))
  except (OSError, ValueError):
    return {**DEFAULT, 'parkedOnly': []}


def valid_ssid(value) -> bool:
  return type(value) is str and 1 <= len(value.strip()) <= 64 and value not in OWN_PROFILES and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def valid_password(value) -> bool:
  return type(value) is str and (value == '' or (8 <= len(value) <= 64 and not any(ord(c) < 32 or ord(c) == 127 for c in value)))


def parse_wifi_list(text: str) -> tuple[dict[str, int], list[dict]]:
  signal: dict[str, int] = {}
  nearby: dict[str, dict] = {}
  for line in text.splitlines():
    fields = split_terse(line, 64)
    if len(fields) < 2:
      continue
    ssid, strength = fields[0][:64], fields[1]
    sec_raw = fields[2] if len(fields) > 2 else ''
    if not ssid or not strength.isdigit():
      continue
    level = int(strength)
    signal[ssid] = max(signal.get(ssid, 0), level)
    if ssid in OWN_PROFILES or ssid.startswith(('TheGalaxy-', 'weedle-')):
      continue
    security = 'open' if sec_raw in ('', '--') else 'unsupported' if ('802.1X' in sec_raw or 'EAP' in sec_raw) else 'wpa'
    prior = nearby.get(ssid)
    if prior is None or level > prior['signal']:
      nearby[ssid] = {'ssid': ssid, 'signal': level, 'security': security if prior is None or prior['security'] == 'open' else prior['security']}
  return signal, sorted(nearby.values(), key=lambda row: (-row['signal'], row['ssid'].lower()))[:MAX_NETWORKS]


def via_hotspot(address) -> bool:
  try:
    return ip_address(address) in ip_network(hotspot.NETWORK)
  except ValueError:
    return False


def start_thread(work) -> None:
  threading.Thread(target=work, name='galaxy-wifi-join', daemon=True).start()


class NetworkManagerCli:
  def __init__(self, run=subprocess.run):
    self.run = run
    self.last_available: list[dict] = []

  def call(self, *args: str, timeout: float = 10) -> str:
    try:
      result = self.run(['nmcli', *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.SubprocessError as error:
      raise OSError('NetworkManager did not answer') from error
    if result.returncode != 0:
      raise OSError((result.stderr or 'NetworkManager refused the request').strip()[:200])
    return result.stdout

  def scan(self, rescan: bool = False) -> tuple[dict[str, int], list[dict]]:
    signal, nearby = parse_wifi_list(self.call('-t', '-f', 'SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list', '--rescan', 'yes' if rescan else 'no', timeout=20))
    self.last_available = nearby
    return signal, nearby

  def saved(self) -> list[dict]:
    """Saved Wi-Fi networks the comma joins as a client, best auto-join priority first."""
    rows: dict[str, dict] = {}
    for line in self.call('-t', '-f', 'UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,ACTIVE,NAME', 'connection', 'show').splitlines():
      fields = split_terse(line, 6)
      if len(fields) != 6:
        continue
      uuid, kind, autoconnect, priority, active, name = fields
      if kind == '802-11-wireless' and UUID.fullmatch(uuid) and name not in OWN_PROFILES:
        rows[uuid] = {'uuid': uuid, 'autoJoin': autoconnect == 'yes', 'priority': int(priority) if priority.lstrip('-').isdigit() else 0,
                      'connected': active == 'yes'}
    if not rows:
      try:
        self.scan(rescan=False)
      except OSError:
        pass
      return []
    details = self.call('-t', '-f', 'connection.uuid,802-11-wireless.ssid,802-11-wireless.mode,connection.metered,ipv4.route-metric',
                        'connection', 'show', *rows)
    for block in details.split('\n\n'):
      values = dict(pair for pair in (split_terse(line, 2) for line in block.splitlines()) if len(pair) == 2)
      row = rows.get(values.get('connection.uuid', ''))
      if row is None:
        continue
      if values.get('802-11-wireless.mode') != 'infrastructure':
        del rows[row['uuid']]
        continue
      metered = values.get('connection.metered', 'unknown')
      row.update(name=values.get('802-11-wireless.ssid', '')[:64], metered=next((k for k, v in METERED.items() if v == metered), 'Auto'),
                 metric=int(values['ipv4.route-metric']) if values.get('ipv4.route-metric', '').lstrip('-').isdigit() else -1)
    rows = {uuid: row for uuid, row in rows.items() if 'name' in row}
    signal = {}
    try:
      signal, _ = self.scan(rescan=False)
    except OSError:
      pass
    for row in rows.values():
      row['signal'] = signal.get(row['name'])
    return sorted(rows.values(), key=lambda row: (-row['priority'], row['name'].lower()))[:MAX_NETWORKS]

  def modify(self, uuid: str, *settings: str) -> None:
    self.call('connection', 'modify', uuid, *settings)

  def join(self, ssid: str, password: str = '', hidden: bool = False) -> None:
    args = ['--wait', str(JOIN_WAIT_S), 'device', 'wifi', 'connect', ssid, 'ifname', 'wlan0']
    if password:
      args += ['password', password]
    if hidden:
      args += ['hidden', 'yes']
    self.call(*args, timeout=JOIN_WAIT_S + 5)

  def activate(self, uuid: str) -> None:
    self.call('--wait', str(JOIN_WAIT_S), 'connection', 'up', 'uuid', uuid, 'ifname', 'wlan0', timeout=JOIN_WAIT_S + 5)

  def delete(self, uuid: str) -> None:
    self.call('connection', 'delete', 'uuid', uuid)

  def reapply(self, interface: str = 'wlan0') -> None:
    try:
      self.call('device', 'reapply', interface)
    except OSError:
      pass


def apply_preference(root=None, cli: NetworkManagerCli | None = None) -> int:
  """Make every saved Wi-Fi network's route cost match the Wi-Fi/cellular preference; returns profiles changed."""
  cli = cli or NetworkManagerCli()
  wanted = CELLULAR_FIRST_METRIC if read_prefs(root)['prefer'] == 'cellular' else -1
  changed, live = 0, False
  for row in cli.saved():
    if row['metric'] != wanted:
      cli.modify(row['uuid'], 'ipv4.route-metric', str(wanted), 'ipv6.route-metric', str(wanted))
      changed += 1
      live = live or row['connected']
  if live:
    cli.reapply()
  return changed


def cellular_status(params) -> dict:
  status = {'enabled': not params.get_bool('CellularDisabled'), 'connected': False, 'type': '', 'operator': ''}
  try:
    modem = json.loads(MODEM_STATE.read_text())
    status.update(connected=modem.get('connected') is True, type=str(modem.get('network_type') or '')[:12],
                  operator=str(modem.get('operator') or '')[:40])
  except (OSError, ValueError, AttributeError):
    pass
  return status


class NetworkSettings:
  def __init__(self, root=None, cli: NetworkManagerCli | None = None, params=None, link=None, clock=time.monotonic,
               hotspot_iface: Path = Path('/sys/class/net') / hotspot.INTERFACE, sleep=time.sleep, spawn=start_thread):
    self.root, self.cli, self.clock = root, cli or NetworkManagerCli(), clock
    self.hotspot_iface, self.sleep, self.spawn = hotspot_iface, sleep, spawn
    self._join_state: dict | None = None
    self._params, self._link = params, link
    self.lock = threading.Lock()
    self._cache: tuple[float, list[dict]] | None = None

  @property
  def params(self):
    if self._params is None:
      from openpilot.common.params import Params
      self._params = Params()
    return self._params

  def link(self) -> dict:
    if self._link is not None:
      return self._link()
    from openpilot.starpilot.system import netwatch
    status = netwatch.read_status()
    return {'link': status.get('link', 'unknown'), 'changedAt': status.get('changedAt', 0.0), 'changes': status.get('changes', 0)}

  def saved(self, fresh: bool = False) -> list[dict]:
    if fresh or self._cache is None or self.clock() - self._cache[0] > 3:
      self._cache = (self.clock(), self.cli.saved())
    return self._cache[1]

  def snapshot(self, parked: bool, fresh: bool = False) -> dict:
    prefs = read_prefs(self.root)
    saved_rows = self.saved(fresh)
    saved_names = {row['name'] for row in saved_rows}
    networks = [{key: row[key] for key in ('uuid', 'name', 'autoJoin', 'connected', 'metered', 'signal')} |
                {'whileDriving': row['uuid'] not in prefs['parkedOnly']} for row in saved_rows]
    available = [row for row in getattr(self.cli, 'last_available', ()) if row['ssid'] not in saved_names]
    return {'version': 1, 'editable': bool(parked), 'prefer': prefs['prefer'], 'pauseHotspot': prefs['pauseHotspot'],
            'cellular': cellular_status(self.params), 'link': self.link(), 'networks': networks, 'available': available,
            'lastJoin': self._last_join()}

  def _last_join(self) -> dict | None:
    state = self._join_state
    if state is None or self.clock() - state['at'] > JOIN_RESULT_TTL_S:
      return None
    return {key: state[key] for key in ('ssid', 'state', 'error')}

  def _joining(self) -> bool:
    state = self._join_state
    return state is not None and state['state'] == 'joining' and self.clock() - state['at'] < JOIN_RUNNING_MAX_S

  def _join_later(self, name: str, effect, parked: bool) -> dict:
    self._join_state = {'ssid': name, 'state': 'joining', 'error': '', 'at': self.clock()}

    def work():
      self.sleep(JOIN_START_DELAY_S)
      result = {'state': 'joined', 'error': ''}
      try:
        self._with_hotspot_paused(effect)
      except Exception as error:  # the hotspot client is offline; keep the reason for when it reconnects
        result = {'state': 'failed', 'error': str(error)[:200] or 'Could not join that network.'}
      self._cache = None
      self._join_state = {'ssid': name, **result, 'at': self.clock()}

    self.spawn(work)
    return self.snapshot(parked)

  def _pause_hotspot(self) -> bool:
    """Stand the Galaxy hotspot down before the radio joins Wi-Fi; returns whether it was paused."""
    if not self.hotspot_iface.exists():
      return False
    hotspot.request_pause(HOTSPOT_PAUSE_WAIT_S + JOIN_WAIT_S + 15)
    deadline = self.clock() + HOTSPOT_PAUSE_WAIT_S
    while self.hotspot_iface.exists():
      if self.clock() >= deadline:
        hotspot.clear_pause()
        raise ValueError('The Galaxy hotspot did not pause; try again in a moment.')
      self.sleep(0.2)
    return True

  def _with_hotspot_paused(self, effect) -> None:
    paused = self._pause_hotspot()
    try:
      effect()
    finally:
      if paused:
        hotspot.clear_pause()

  def _save_prefs(self, prefs: dict) -> None:
    atomic_json(prefs_path(self.root), validate_prefs(prefs))

  def _join(self, known: dict, ssid: str, password: str, hidden: bool) -> None:
    try:
      self.cli.join(ssid, password, hidden)
    except OSError as error:
      for row in self.cli.saved():
        if row['uuid'] not in known and row['name'] == ssid:
          try:
            self.cli.delete(row['uuid'])
          except OSError:
            pass
      raise ValueError('Could not join that network. Check the password and signal, then try again.') from error
    apply_preference(self.root, self.cli)

  def _activate(self, uuid: str) -> None:
    try:
      self.cli.activate(uuid)
    except OSError as error:
      raise ValueError('Could not connect to that network right now.') from error

  def apply(self, payload, permitted, background: bool = False) -> dict:
    if type(payload) is not dict or type(payload.get('action')) is not str:
      raise ValueError('Invalid network change')
    with self.lock:
      if not permitted():
        raise PermissionError('Park and sign in before changing network settings.')
      if self._joining():
        raise FileExistsError('The comma is still joining Wi-Fi; try again in a moment.')
      action, prefs = payload['action'], read_prefs(self.root)
      if action == 'scan' and set(payload) == {'action'}:
        try:
          self.cli.scan(rescan=True)
        except OSError:
          pass
        return self.snapshot(permitted(), fresh=True)
      known = {row['uuid']: row for row in self.saved(fresh=True)}
      if action == 'join' and set(payload) <= {'action', 'ssid', 'password', 'hidden'} and 'ssid' in payload:
        ssid, password, hidden = payload['ssid'], payload.get('password', ''), payload.get('hidden', False)
        if not valid_ssid(ssid) or not valid_password(password) or type(hidden) is not bool:
          raise ValueError('Enter a valid network name and an 8–64 character password (or leave blank for an open network).')
        effect = partial(self._join, known, ssid, password, hidden)
        if background:
          return self._join_later(ssid, effect, permitted())
        self._with_hotspot_paused(effect)
      elif action == 'connect' and set(payload) == {'action', 'uuid'}:
        if payload['uuid'] not in known:
          raise FileExistsError('That network is no longer saved; refresh and try again.')
        effect = partial(self._activate, payload['uuid'])
        if background:
          return self._join_later(known[payload['uuid']]['name'], effect, permitted())
        self._with_hotspot_paused(effect)
      elif action == 'forget' and set(payload) == {'action', 'uuid'}:
        uuid = payload['uuid']
        if uuid not in known:
          raise FileExistsError('That network is no longer saved; refresh and try again.')
        self.cli.delete(uuid)
        if uuid in prefs['parkedOnly']:
          self._save_prefs({**prefs, 'parkedOnly': sorted((set(prefs['parkedOnly']) & set(known)) - {uuid})})
      elif action == 'order' and set(payload) == {'action', 'uuids'}:
        order = payload['uuids']
        if (type(order) is not list or any(type(uuid) is not str for uuid in order) or
            len(order) != len(known) or set(order) != set(known)):
          raise FileExistsError('The saved networks changed; refresh and try again.')
        for index, uuid in enumerate(cast(list[str], order)):
          priority = len(order) - index
          if known[uuid]['priority'] != priority:
            self.cli.modify(uuid, 'connection.autoconnect-priority', str(priority))
      elif action == 'network' and set(payload) == {'action', 'uuid', 'setting', 'value'}:
        uuid, setting, value = payload['uuid'], payload['setting'], payload['value']
        if uuid not in known:
          raise FileExistsError('That network is no longer saved; refresh and try again.')
        if setting == 'autoJoin' and type(value) is bool:
          self.cli.modify(uuid, 'connection.autoconnect', 'yes' if value else 'no')
        elif setting == 'metered' and value in METERED:
          self.cli.modify(uuid, 'connection.metered', METERED[value])
          if known[uuid]['connected']:
            self.cli.reapply()
        elif setting == 'whileDriving' and type(value) is bool:
          parked_only = set(prefs['parkedOnly']) & set(known)
          parked_only = parked_only - {uuid} if value else parked_only | {uuid}
          self._save_prefs({**prefs, 'parkedOnly': sorted(parked_only)})
        else:
          raise ValueError('Invalid network setting')
      elif action == 'prefer' and set(payload) == {'action', 'value'} and payload['value'] in ('wifi', 'cellular'):
        self._save_prefs({**prefs, 'prefer': payload['value']})
        apply_preference(self.root, self.cli)
      elif action == 'pauseHotspot' and set(payload) == {'action', 'value'} and type(payload['value']) is bool:
        self._save_prefs({**prefs, 'pauseHotspot': payload['value']})
      elif action == 'cellular' and set(payload) == {'action', 'value'} and type(payload['value']) is bool:
        self.params.put_bool('CellularDisabled', not payload['value'])
      else:
        raise ValueError('Invalid network change')
      return self.snapshot(permitted(), fresh=True)
