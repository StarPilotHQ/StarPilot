"""Optional parked reporting; failures never participate in driving process health."""
from datetime import datetime, UTC
import math
import signal
import threading
import time
import re
import uuid
from typing import Any

from openpilot.starpilot.gps.source import GPS_SERVICES, select_location
from openpilot.starpilot.analytics.report import ReportClient, build_payload, fetch_branch_commit

SERVICES = ('deviceState', 'carParams', 'carState', 'carControl', 'selfdriveState',
            'modelV2', 'drivingModelData', 'managerState', *GPS_SERVICES)
COUNTERS = ('drives', 'meters', 'seconds', 'current_months_meters', 'total_tracked_seconds',
            'total_lateral_seconds', 'total_longitudinal_seconds', 'total_aol_seconds')


_PEDAL_FLAGS = {'gm': ('GMFlags', 'PEDAL_LONG'), 'honda': ('HondaFlags', 'GAS_INTERCEPTOR'),
                'toyota': ('ToyotaFlags', 'GAS_INTERCEPTOR')}


def pedal_metrics(cp):
  from importlib import import_module
  definition = _PEDAL_FLAGS.get(cp.brand)
  if definition is None:
    return {}
  try:
    flags = getattr(import_module('opendbc.car.' + cp.brand + '.values'), definition[0])
    return {'has_pedal': bool(cp.flags & getattr(flags, definition[1]))}
  except (AttributeError, ImportError, TypeError, ValueError):
    return {}


def executed_model(sm, params, now):
  from openpilot.starpilot.models.jetlink_adapter import hooks
  from openpilot.starpilot.models.runtime import snapshot
  from openpilot.starpilot.models.status import ModelHealth
  from openpilot.starpilot.models.catalog import model_entries
  if bool(sm['modelV2'].big) and (remote := hooks().runtime()) is not None and remote.active and remote.sha256:
    digest = remote.sha256
    return {'driving_model': 'jetlink:' + digest[:16], 'metrics': {'model_sha256': digest, 'model_variant': 'jetlink'}}
  status = snapshot(sm, params, now)
  entries = model_entries()
  if status.health == ModelHealth.ACTIVE and status.loaded_id in entries:
    return {'driving_model': entries[status.loaded_id].name, 'metrics': {'model_sha256': status.artifact_sha256, 'model_variant': str(status.variant)}}
  return {}


class AnalyticsWorker:
  def __init__(self, params, source, *, metadata=None, identity=executed_model, collector=None,
               client=None, token_reader=None, resolver=None, clock_valid=None, commit_reader=fetch_branch_commit,
               monotonic=time.monotonic, wall=lambda: datetime.now(UTC).timestamp()):
    from openpilot.common.time_helpers import system_time_valid
    from openpilot.starpilot.analytics.credentials import read_token
    from openpilot.starpilot.analytics.location import resolve_region
    from openpilot.starpilot.analytics.settings import collect_settings
    self.params, self.source = params, source
    self.metadata = dict(metadata or {})
    self.identity = identity
    self.collector = collector or collect_settings
    self.client = client or ReportClient(None)
    self.token_reader = token_reader or read_token
    self.resolver = resolver or resolve_region
    self.clock_valid = clock_valid or system_time_valid
    self.commit_reader, self.remote_commit = commit_reader, None
    self.monotonic, self.wall = monotonic, wall
    self.stop_event = threading.Event()
    self.thread = None
    saved = params.get('UsageStatsState')
    saved = saved if isinstance(saved, dict) and saved.get('version') == 1 else {}
    self.state: dict[str, Any] = {'version': 1}
    if isinstance(saved.get('month'), str) and len(saved['month']) == 7:
      self.state['month'] = saved['month']
    self.state['counters'] = saved.get('counters', {})
    region = saved.get('region')
    cell = saved.get('coarse_cell')
    if isinstance(region, dict) and isinstance(cell, list) and len(cell) == 2:
      from openpilot.starpilot.analytics.location import coarse_cell
      verified = coarse_cell({'latitude': cell[0], 'longitude': cell[1]})
      if verified is not None and list(verified) == cell:
        self.state['coarse_cell'] = cell
        self.state['region'] = {key: value[:96] for key, value in region.items()
                                if key in ('city', 'state', 'country') and isinstance(value, str)}
        self.state['region'].update(latitude=cell[0], longitude=cell[1])
    saved_counters = self.state.get('counters', {})
    if not isinstance(saved_counters, dict):
      saved_counters = {}
    self.counters = {key: saved_counters.get(key, 0) for key in COUNTERS}
    self.counters = {key: float(value) if type(value) in (int, float) and math.isfinite(value) and 0 <= value < (1 << 63) else 0.0
                     for key, value in self.counters.items()}
    stored_context = saved.get('context', {})
    if not isinstance(stored_context, dict):
      stored_context = {}
    self.context: dict[str, Any] = {key: value for key, value in stored_context.items()
                    if key in ('car_make', 'car_model', 'driving_model') and isinstance(value, str) and len(value) <= 128}
    stored_metrics = stored_context.get('metrics', {})
    if isinstance(stored_metrics, dict):
      self.context['metrics'] = {key: value for key, value in stored_metrics.items()
                                 if (key in ('model_sha256', 'model_variant') and isinstance(value, str) and len(value) <= 64) or
                                    (key in ('has_openpilot_longitudinal', 'has_pedal', 'using_stock_acc') and type(value) is bool)}
    self.cp, self.gps = None, None
    self.last_identity = -10.0
    self.drive_engaged = False
    self.uncommitted_seconds = 0.0
    dongle = params.get('DongleId')
    if isinstance(dongle, bytes):
      dongle = dongle.decode('ascii', errors='ignore')
    if not isinstance(dongle, str) or re.fullmatch(r'[a-zA-Z0-9_-]{8,64}', dongle) is None:
      dongle = saved.get('anonymous_id')
      if not isinstance(dongle, str) or re.fullmatch(r'anon-[0-9a-f]{32}', dongle) is None:
        dongle = 'anon-' + uuid.uuid4().hex
      self.state['anonymous_id'] = dongle
    self.metadata['dongle_id'] = dongle
    self.previous_started = None
    self.last_poll = self.monotonic()
    self.last_persist = self.last_poll
    self.pending, self.initial_sent = False, False
    self.awaiting_identity = False
    self.attempts, self.retry_at = 0, 0.0

  def fresh(self, service, max_age=2.0):
    stamp = self.source.logMonoTime[service]
    now = int(self.monotonic() * 1e9)
    return bool(self.source.valid[service] and self.source.alive[service] and 0 < stamp <= now and now - stamp <= max_age * 1e9)

  def allowed(self):
    self.source.update(0)
    return (not self.stop_event.is_set() and
            self.params.get_bool('IsOffroad') and self.fresh('deviceState') and not self.source['deviceState'].started)

  def persist(self):
    self.counters = {key: min(value, float((1 << 62) - 1)) for key, value in self.counters.items()}
    self.state.update(counters=dict(self.counters), context=dict(self.context))
    self.params.put('UsageStatsState', self.state, block=True)
    self.last_persist = self.monotonic()

  def poll_once(self):
    self.source.update(0)
    now = self.monotonic()
    elapsed = max(0.0, min(now - self.last_poll, 1.0))
    self.last_poll = now
    if not self.fresh('deviceState'):
      return
    if self.clock_valid():
      month = time.strftime('%Y-%m', time.gmtime(self.wall()))
      if self.state.get('month') != month:
        self.state['month'] = month
        self.counters['current_months_meters'] = 0
    started = bool(self.source['deviceState'].started)
    if started:
      if self.previous_started is not True:
        self.context = {}
        self.cp = None
        self.last_identity = -10.0
        self.drive_engaged = False
        self.uncommitted_seconds = 0.0
      if self.fresh('carParams'):
        self.cp = self.source['carParams'].as_builder()
        self.context.update(car_make=str(self.cp.brand), car_model=str(self.cp.carFingerprint))
        self.context.setdefault('metrics', {}).update(has_openpilot_longitudinal=bool(self.cp.openpilotLongitudinalControl),
                                                     using_stock_acc=not bool(self.cp.openpilotLongitudinalControl),
                                                     **pedal_metrics(self.cp))
      if self.fresh('modelV2', .25) and now - self.last_identity >= 1:
        identity = self.identity(self.source, self.params, int(now * 1e9))
        self.context.setdefault('metrics', {}).update(identity.pop('metrics', {}))
        self.context.update(identity)
        self.last_identity = now
      if self.fresh('carState', .25):
        speed = float(self.source['carState'].vEgo)
        if math.isfinite(speed) and 0 <= speed <= 100:
          self.uncommitted_seconds += elapsed
          engaged = ((self.fresh('selfdriveState', .25) and self.source['selfdriveState'].enabled) or
                     (self.fresh('carControl', .25) and self.source['carControl'].latActive))
          if engaged and not self.drive_engaged:
            self.drive_engaged = True
            self.counters['drives'] += 1
          if self.drive_engaged:
            self.counters['seconds'] += self.uncommitted_seconds
            self.counters['total_tracked_seconds'] += self.uncommitted_seconds
            self.uncommitted_seconds = 0
          self.counters['meters'] += speed * elapsed
          if self.clock_valid():
            month = time.strftime('%Y-%m', time.gmtime(self.wall()))
            if self.state.get('month') != month:
              self.state['month'] = month
              self.counters['current_months_meters'] = 0
            self.counters['current_months_meters'] += speed * elapsed
          if self.fresh('carControl', .25):
            control = self.source['carControl']
            self.counters['total_lateral_seconds'] += elapsed * bool(control.latActive)
            self.counters['total_longitudinal_seconds'] += elapsed * bool(control.longActive)
            if self.fresh('selfdriveState', .25) and not self.source['selfdriveState'].enabled:
              self.counters['total_aol_seconds'] += elapsed * bool(control.latActive)
      fix = select_location(self.source, int(now * 1e9))
      if fix is not None:
        self.gps = {'latitude': fix[1].latitude, 'longitude': fix[1].longitude}
    else:
      if self.previous_started is True:
        self.persist()
        self.pending, self.attempts, self.retry_at = True, 0, now
      if self.clock_valid() and not self.initial_sent:
        self.pending, self.initial_sent = True, True
    self.previous_started = started
    if now - self.last_persist >= 60:
      self.persist()
    if not self.pending or self.attempts >= 5 or now < self.retry_at or not self.clock_valid() or not self.allowed():
      return
    model_name = self.context.get('driving_model')
    if not isinstance(model_name, str) or model_name.strip().lower() in ('', 'unknown'):
      if not self.awaiting_identity:
        self.params.put('UsageStatsStatus', {'state': 'awaiting_model_identity'})
        self.awaiting_identity = True
      return
    self.awaiting_identity = False
    token = self.token_reader()
    if token is None:
      self.params.put('UsageStatsStatus', {'state': 'awaiting_credentials'})
      self.retry_at = now + 30
      return
    if self.remote_commit is None:
      self.remote_commit = self.commit_reader(self.metadata.get('branch', ''), allowed=self.allowed)
    from openpilot.starpilot.analytics.location import coarse_cell
    cell = coarse_cell(self.gps)
    if cell is not None and list(cell) != self.state.get('coarse_cell'):
      region = self.resolver(self.gps, allowed=self.allowed)
      if self.allowed():
        self.state.update(coarse_cell=list(cell), region=region)
        self.persist()
    report = dict(self.metadata, **self.context, counters=dict(self.counters))
    report['metrics'] = dict(self.metadata.get('metrics', {}), **self.context.get('metrics', {}))
    if self.remote_commit is not None:
      report['branch_commit'] = self.remote_commit
      report['metrics']['up_to_date'] = report['metrics'].get('commit') == self.remote_commit
    region = self.state.get('region', {})
    if not isinstance(region, dict):
      region = {}
    report.update({key: region[key] for key in ('city', 'state', 'country') if key in region})
    report['coarse_location'] = region
    payload = build_payload(report, self.collector(self.params, self.cp), int(self.wall() * 1e9))
    success = self.client.send(payload, token, allowed=self.allowed)
    self.attempts += 1
    self.pending = not success
    self.retry_at = now + min(300, 15 * 2 ** self.attempts)
    self.params.put('UsageStatsStatus', {'state': 'sent' if success else 'retrying', 'attempts': self.attempts})

  def run(self):
    while not self.stop_event.is_set():
      try:
        self.poll_once()
      except Exception:
        self.attempts += 1
        self.retry_at = self.monotonic() + min(300, 15 * 2 ** min(self.attempts, 5))
        try:
          self.params.put('UsageStatsStatus', {'state': 'unavailable', 'attempts': min(self.attempts, 5)})
        except Exception:
          pass
      self.stop_event.wait(.1)
    self.client.close()

  def start(self):
    self.thread = threading.Thread(target=self.run, name='usage_stats', daemon=True)
    self.thread.start()
    return self

  def close(self):
    self.stop_event.set()
    if self.thread is not None:
      self.thread.join(timeout=.25)
    if self.thread is None:
      self.client.close()


def build_worker():
  from openpilot.common.hardware import COMMA_HARDWARE, HARDWARE
  if not COMMA_HARDWARE:
    return None
  from openpilot.cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.version import get_build_metadata
  from openpilot.starpilot.ui.brand import DISPLAY_VERSION
  metadata = get_build_metadata()
  return AnalyticsWorker(Params(), messaging.SubMaster(list(SERVICES)),
                         metadata={'branch': metadata.channel, 'device': HARDWARE.get_device_type(),
                                   'metrics': {'commit': metadata.openpilot.git_commit,
                                               'software_major': int(DISPLAY_VERSION.split('.')[0])}})


def main():
  # Own process so the manager's heap stays small when it forks the onroad daemons.
  worker = build_worker()
  if worker is None:
    return
  for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: worker.stop_event.set())
  worker.run()
  worker.persist()


if __name__ == "__main__":
  main()
