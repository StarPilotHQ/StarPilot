"""Saved Wi-Fi order and per-network choices for the Galaxy Networks page.

NetworkManager stays the owner of each saved network; this module only changes Wi-Fi profiles.
Cellular profiles, the modem and its routes are never touched: cellular data is switched with the
same CellularDisabled setting the comma's own screen uses.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from openpilot.starpilot.galaxy.hotspot import atomic_json, bounded_json

DEFAULT = {'prefer': 'wifi', 'pauseHotspot': True, 'parkedOnly': []}
CELLULAR_FIRST_METRIC = 2000   # above the modem's own default route (1000), so cellular wins while it is up
OWN_PROFILES = ('starpilot-starport',)
UUID = re.compile(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}')
METERED = {'Auto': 'unknown', 'Metered': 'yes', 'Unmetered': 'no'}
MODEM_STATE = Path('/dev/shm/modem')
MAX_NETWORKS = 64


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
  return {**value, 'parkedOnly': sorted(set(value['parkedOnly']))}


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


class NetworkManagerCli:
  def __init__(self, run=subprocess.run):
    self.run = run

  def call(self, *args: str) -> str:
    try:
      result = self.run(['nmcli', *args], capture_output=True, text=True, timeout=10)
    except subprocess.SubprocessError as error:
      raise OSError('NetworkManager did not answer') from error
    if result.returncode != 0:
      raise OSError((result.stderr or 'NetworkManager refused the request').strip()[:200])
    return result.stdout

  def saved(self) -> list[dict]:
    """Saved Wi-Fi networks the comma joins as a client, best auto-join priority first."""
    rows = {}
    for line in self.call('-t', '-f', 'UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,ACTIVE,NAME', 'connection', 'show').splitlines():
      fields = split_terse(line, 6)
      if len(fields) != 6:
        continue
      uuid, kind, autoconnect, priority, active, name = fields
      if kind == '802-11-wireless' and UUID.fullmatch(uuid) and name not in OWN_PROFILES:
        rows[uuid] = {'uuid': uuid, 'autoJoin': autoconnect == 'yes', 'priority': int(priority) if priority.lstrip('-').isdigit() else 0,
                      'connected': active == 'yes'}
    if not rows:
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
      for line in self.call('-t', '-f', 'SSID,SIGNAL', 'device', 'wifi', 'list', '--rescan', 'no').splitlines():
        ssid, _, strength = line.rpartition(':')
        ssid = split_terse(ssid, 1)[0]
        if ssid and strength.isdigit():
          signal[ssid] = max(signal.get(ssid, 0), int(strength))
    except OSError:
      pass
    for row in rows.values():
      row['signal'] = signal.get(row['name'])
    return sorted(rows.values(), key=lambda row: (-row['priority'], row['name'].lower()))[:MAX_NETWORKS]

  def modify(self, uuid: str, *settings: str) -> None:
    self.call('connection', 'modify', uuid, *settings)

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
  def __init__(self, root=None, cli: NetworkManagerCli | None = None, params=None, link=None, clock=time.monotonic):
    self.root, self.cli, self.clock = root, cli or NetworkManagerCli(), clock
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
    networks = [{key: row[key] for key in ('uuid', 'name', 'autoJoin', 'connected', 'metered', 'signal')} |
                {'whileDriving': row['uuid'] not in prefs['parkedOnly']} for row in self.saved(fresh)]
    return {'version': 1, 'editable': bool(parked), 'prefer': prefs['prefer'], 'pauseHotspot': prefs['pauseHotspot'],
            'cellular': cellular_status(self.params), 'link': self.link(), 'networks': networks}

  def _save_prefs(self, prefs: dict) -> None:
    atomic_json(prefs_path(self.root), validate_prefs(prefs))

  def apply(self, payload, permitted) -> dict:
    if type(payload) is not dict or type(payload.get('action')) is not str:
      raise ValueError('Invalid network change')
    with self.lock:
      if not permitted():
        raise PermissionError('Park and sign in before changing network settings.')
      action, prefs = payload['action'], read_prefs(self.root)
      known = {row['uuid']: row for row in self.saved(fresh=True)}
      if action == 'order' and set(payload) == {'action', 'uuids'}:
        order = payload['uuids']
        if type(order) is not list or len(order) != len(known) or set(order) != set(known):
          raise FileExistsError('The saved networks changed; refresh and try again.')
        for index, uuid in enumerate(order):
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
