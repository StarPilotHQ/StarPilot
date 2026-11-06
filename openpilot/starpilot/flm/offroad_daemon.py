"""Automatic parked route tuning. Live optimization is deliberately deferred."""

import json
import os
from pathlib import Path
import signal
import tempfile
import time
import traceback
from typing import Any

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.flm.offroad_worker import OffroadWorker
from openpilot.starpilot.flm.training_report import report_path, READ_MAX_BYTES, _report_bounded


class StatusReport:
  def __init__(self, *, path=None, clock_ns=time.monotonic_ns):
    self.path = report_path() if path is None else Path(path)
    self.clock_ns = clock_ns
    self.started = self.clock_ns()
    self.current: dict[str, Any] = {'status': 'collecting', 'reason': 'waiting_for_completed_drive', 'vehicle': ''}
    self.next_ns = 0

  def set(self, value):
    changed = (self.current.get('status'), self.current.get('reason')) != (value.get('status'), value.get('reason'))
    self.current = dict(value)
    if changed:
      self.next_ns = 0

  def publish(self):
    now = self.clock_ns()
    if now < self.next_ns:
      return
    self.next_ns = now + 1_000_000_000
    value = self.current
    counts = value.get('analysis_counts', {})
    if type(counts) is not dict:
      counts = {}

    def count(key):
      value = counts.get(key, 0)
      return value if type(value) is int and 0 <= value < 2**63 else 0

    details: dict[str, Any] = {'Analysis runs while parked': True, 'Next-drive trial is reversible': True, 'Physical road improvement proven': False}
    for key, title in (('source_sha256', 'Original completed route hash'), ('assessment_sha256', 'Offroad assessment hash')):
      if key in value:
        details[title] = value[key]
    from openpilot.starpilot.flm.canonical_surface_adapter import NAMES
    from openpilot.starpilot.flm.offroad_surface import CURVE_NAMES, route_values

    if 'completed_route' in value:
      details['Completed drive analyzed'] = value['completed_route']
      details['Recorded steering observations'] = count('recorded_tracking')
      details['Joined steering observations'] = count('joined_tracking')
      details['Retained control windows'] = count('qualified_whole_windows')
      details['Retained tracking windows'] = count('stable_qualified_whole_windows')
      details['Original controller observations'] = count('original_control_metadata_rows')
      details['Controller eligibility refusals'] = count('controller_fit_refusals')
      details['Automatic configuration changed'] = bool(value.get('configuration_changed', False))
      details['Analysis decision'] = value['reason']
      suggestions = value.get('suggestions', ())
      details['Recorded route suggestions'] = [[s['parameter'], s['delta'], s['whole_windows'], s['qualification']] for s in suggestions[:16]]
      details['Recorded suggestion count'] = value.get('suggestion_count', len(suggestions))
      details['More recorded suggestions omitted'] = value.get('suggestion_count', len(suggestions)) > len(suggestions)
    proposal = value.get('suggestion')
    if proposal is not None:
      details['Full surface coordinates'] = [[n, proposal['values'][n]] for n in NAMES]
      details['Active trial parameters'] = [proposal['parameter']]
      details['Suggested adjustment is not confirmed'] = True
      if all(n in proposal['values'] for n in CURVE_NAMES):
        details['Proposed friction-curve adjustments'] = [[n, proposal['values'][n]] for n in CURVE_NAMES]
    if 'configured_values' in value:
      details['Full surface coordinates'] = [[n, value['configured_values'][n]] for n in NAMES]
      if all(n in value['configured_values'] for n in CURVE_NAMES):
        details['Saved friction-curve adjustments'] = [[n, value['configured_values'][n]] for n in CURVE_NAMES]
      details['Saved route configuration state'] = value['configuration_state']
    actual_values = None
    if value.get('actual_coordinates') is not None:
      try:
        candidate = route_values(dict(value['actual_coordinates']))
        if tuple(candidate.items()) == tuple(value['actual_coordinates']):
          actual_values = candidate
      except (TypeError, ValueError, OverflowError):
        pass
    if actual_values is not None and 0 < value.get('actual_control_ns', 0) <= now <= value['actual_control_ns'] + 100_000_000:
      actual = actual_values
      details['Actual full surface coordinates'] = [[n, actual[n]] for n in NAMES]
      if all(n in actual for n in CURVE_NAMES):
        details['Actual friction-curve adjustments'] = [[n, actual[n]] for n in CURVE_NAMES]
      details['Actual route control timestamp'] = value['actual_control_ns']
      details['Native delivery is not asserted by this status'] = True
    from openpilot.starpilot.flm.fullrate_collector import ControlEvidence
    from openpilot.starpilot.flm.offroad_surface import REVISION

    control = value.get('actual_control')
    applied = False
    if (
      type(control) is ControlEvidence
      and value.get('enabled') is True
      and control.valid
      and control.enabled
      and control.active
      and not (control.manual or control.override or control.fault or control.limited or control.saturated)
      and 0 < control.control_ns <= now <= control.control_ns + 100_000_000
      and control.route_effect_revision == REVISION
      and len(control.route_configuration_scope) == 64
      and not control.route_configuration_scope.strip('0123456789abcdef')
    ):
      try:
        actual = route_values(dict(control.route_applied_coordinates))
        applied = (
          tuple(actual.items()) == control.route_applied_coordinates
          and value.get('actual_control_ns') == control.control_ns
          and tuple(value.get('actual_coordinates', ())) == control.route_applied_coordinates
        )
      except (TypeError, ValueError, OverflowError):
        pass
    current = {
      'enabled': value.get('enabled'),
      'status': value['status'],
      'reason': value['reason'],
      'progress': {'samples': count('recorded_tracking'), 'blocks': count('qualified_whole_windows'), 'requiredBlocks': 6},
      'fit': None,
      'application': {'applied': applied, 'reason': 'actual_route_stage_observed' if applied else 'saved_configuration_is_not_current_application'},
      'details': details,
    }
    report = {'version': 1, 'observedMonoTime': now, 'vehicle': value.get('vehicle', ''), 'session': 'parked-route-v1', 'current': current, 'history': []}
    raw = json.dumps(report, allow_nan=False, separators=(',', ':')).encode()
    if not _report_bounded(report) or len(raw) > READ_MAX_BYTES:
      return
    temporary = None
    try:
      fd, temporary = tempfile.mkstemp(prefix='.flm-offroad-', dir=self.path.parent)
      with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
      os.replace(temporary, self.path)
    except OSError:
      pass  # Diagnostics cannot provide authority or interrupt Controls.
    finally:
      if temporary is not None:
        Path(temporary).unlink(missing_ok=True)


def parked_status(sm, params, *, stopped=False, now=None):
  """Same fresh parked physical-state checks as the existing bench supervisor.

  Offroad analysis needs current status, not the nominal publication cadence.
  Do not rewrite freq_ok or use this predicate for current Controls authority.
  """
  services = ('deviceState', 'pandaStates', 'peripheralState')
  if stopped or not params.get_bool('IsOffroad') or params.get_bool('IsEngaged'):
    return False
  if not all(sm.seen[k] and sm.alive[k] and sm.valid[k] for k in services):
    return False
  if now is None:
    now = time.monotonic()
  if any(not 0 <= now - sm.recv_time[k] <= 2.0 for k in services):
    return False
  pandas = sm['pandaStates']
  return bool(not sm['deviceState'].started and pandas and all(not (p.ignitionLine or p.ignitionCan or p.controlsAllowed or p.faults) for p in pandas))


def main():
  from openpilot.common.hardware.hw import Paths
  from openpilot.starpilot.flm.offroad_cache import directory_for_params
  from openpilot.starpilot.flm.offroad_source import record_drive

  params = Params()
  stopped = False

  def stop(signum, frame):
    nonlocal stopped
    stopped = True

  previous = signal.signal(signal.SIGTERM, stop)
  sm = None
  report = StatusReport()
  try:
    sm = messaging.SubMaster(['deviceState', 'pandaStates', 'peripheralState'])

    def parked():
      sm.update(0)
      return parked_status(sm, params, stopped=stopped)

    def permitted():
      okay = parked()
      report.publish()
      return okay

    cache = directory_for_params(params)
    worker = OffroadWorker(
      params,
      Paths.log_root(),
      cache,
      permitted=permitted,
      on_analysis=lambda: report.set({'status': 'fitting', 'reason': 'analyzing_last_completed_drive'}),
      on_assessment=lambda a: report.set(
        {'status': 'fitting', 'reason': 'checking_recorded_steering_recommendations', 'completed_route': a.receipt.route_id, 'analysis_counts': dict(a.counts)}
      ),
    )
    next_analysis = 0.0
    while not stopped:
      if not params.get_bool('IsOffroad'):
        report.set({'enabled': True, 'status': 'collecting', 'reason': 'recording_for_offroad_analysis'})
        record_drive(
          params,
          stopped=lambda: stopped,
          on_status=lambda enabled, actual, withdrawn: (
            report.set(
              {
                'enabled': enabled,
                'status': 'collecting',
                'reason': 'quality_guard_returned_to_prior_map' if withdrawn else 'recording_for_offroad_analysis',
                'actual_control': actual,
                'actual_coordinates': None if actual is None else actual.route_applied_coordinates,
                'actual_control_ns': 0 if actual is None else actual.control_ns,
              }
            ),
            report.publish(),
          ),
        )
      elif parked() and time.monotonic() >= next_analysis:
        next_analysis = time.monotonic() + 10.0
        try:
          report.set(worker.run_once())
        except (ValueError, OSError, RuntimeError) as error:
          traceback.print_exc()
          worker.record_failure(error)
          report.set({'status': 'unavailable', 'reason': str(error)[:300]})
      report.publish()
      if not stopped:
        time.sleep(0.05)
  finally:
    signal.signal(signal.SIGTERM, previous)
    if sm is not None:
      for sock in sm.sock.values():
        close = getattr(sock, 'close', None)
        if close is not None:
          close()


if __name__ == '__main__':
  main()
