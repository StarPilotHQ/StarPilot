"""Bounded read-only RAM telemetry; never restores a learned profile."""

from typing import cast
import json
import math
import os
import stat
import tempfile
from pathlib import Path
from collections import Counter, deque

from openpilot.starpilot.flm.application_wire import read_application
from openpilot.starpilot.flm.live_tracking_report import tracking_objects

READ_MAX_BYTES = 32_768
READ_MAX_AGE_NS = 2_000_000_000


def _report_pairs(pairs):
  values = {}
  for key, value in pairs:
    if key in values:
      raise ValueError('duplicate report field')
    values[key] = value
  return values


def _report_bounded(value, depth=0):
  if depth > 8:
    return False
  if value is None or type(value) is bool:
    return True
  if type(value) in (int, float):
    return math.isfinite(value) and abs(value) <= 2 ** 64
  if type(value) is str:
    return len(value.encode()) <= 512
  if type(value) is list:
    return len(value) <= 128 and all(_report_bounded(item, depth + 1) for item in value)
  if type(value) is dict:
    return len(value) <= 64 and all(type(key) is str and len(key) <= 96 and _report_bounded(item, depth + 1)
                                    for key, item in value.items())
  return False


def _report_text(value):
  return type(value) is str and len(value.encode()) <= 512


def _report_count(value):
  return type(value) is int and 0 <= value <= 2 ** 63 - 1


def _report_current(value, age_ns):
  if value is None:
    return None
  if (type(value) is not dict or value.get('enabled') is not None and type(value['enabled']) is not bool or
      not _report_text(value.get('status')) or not _report_text(value.get('reason'))):
    raise ValueError('current telemetry')
  progress = value.get('progress')
  if type(progress) is not dict or any(not _report_count(progress.get(key)) for key in ('samples', 'blocks', 'requiredBlocks')):
    raise ValueError('collection telemetry')
  fit = value.get('fit')
  if fit is not None and (type(fit) is not dict or not _report_text(fit.get('status')) or not _report_text(fit.get('reason')) or
                          any(key not in fit or fit[key] is not None and not _report_count(fit[key]) for key in ('trainWindows', 'heldoutWindows'))):
    raise ValueError('fit telemetry')
  application = value.get('application')
  if application is not None and (type(application) is not dict or type(application.get('applied')) is not bool or
                                  not _report_text(application.get('reason'))):
    raise ValueError('application telemetry')
  details = value.get('details')
  if type(details) is not dict:
    raise ValueError('details telemetry')
  fresh = age_ns <= READ_MAX_AGE_NS
  if application is not None:
    application = {'applied': application['applied'] if fresh else False,
                   'reason': application['reason'] if fresh else 'report_stale'}
  return {'fresh': fresh, 'ageSeconds': age_ns / 1e9, 'enabled': value.get('enabled'),
          'status': value['status'], 'reason': value['reason'],
          'progress': {key: progress[key] for key in ('samples', 'blocks', 'requiredBlocks')},
          'fit': None if fit is None else {key: fit[key] for key in ('status', 'reason', 'trainWindows', 'heldoutWindows')},
          'application': application, 'details': details,
          **tracking_objects(value, fresh=fresh)}


def read_report(path, *, now_ns):
  empty = {'version': 1, 'current': None, 'history': []}
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
      before = os.fstat(fd)
      if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or
          before.st_mode & 0o022 or not 0 < before.st_size <= READ_MAX_BYTES):
        raise ValueError('report file')
      raw = os.read(fd, READ_MAX_BYTES + 1)
      after = os.fstat(fd)
      if ((before.st_ino, before.st_size, before.st_mtime_ns) !=
          (after.st_ino, after.st_size, after.st_mtime_ns) or len(raw) != after.st_size):
        raise ValueError('report changed')
    finally:
      os.close(fd)
    report = json.loads(raw, object_pairs_hook=_report_pairs)
    stamp = report.get('observedMonoTime') if type(report) is dict else None
    if (not _report_bounded(report) or type(report) is not dict or type(report.get('version')) is not int or
        report['version'] != 1 or type(now_ns) is not int or type(stamp) is not int or not 0 < stamp <= now_ns or
        not _report_text(report.get('vehicle')) or not _report_text(report.get('session'))):
      raise ValueError('report fields')
    history = report.get('history')
    if type(history) is not list or len(history) > 20:
      raise ValueError('history telemetry')
    for row in history:
      if (type(row) is not dict or type(row.get('applied')) is not bool or
          any(not _report_text(row.get(key)) for key in ('time', 'status', 'reason'))):
        raise ValueError('history entry')
    return {'version': 1, 'current': _report_current(report.get('current'), now_ns - stamp),
            'history': [{key: row[key] for key in ('time', 'status', 'reason', 'applied')} for row in history]}
  except (OSError, ValueError, TypeError, OverflowError, RecursionError):
    return empty


def report_path():
  prefix = os.environ.get('OPENPILOT_PREFIX', '')
  if len(prefix) > 64 or any(not (c.isalnum() or c in '-_') for c in prefix):
    raise ValueError('report namespace')
  return Path('/dev/shm' if os.path.isdir('/dev/shm') else tempfile.gettempdir()) / ('starpilot-flm-training-' + prefix + '.json')


class TrainingReporter:
  """One bounded RAM write per second, exclusively in the optional learner."""
  def __init__(self, vehicle, *, path=None):
    self.vehicle = str(vehicle)[:128]
    self.path = report_path() if path is None else Path(path)
    self.last_write = self.started = 0
    self.control = None
    self.resets = Counter()
    self.history = deque(maxlen=20)
    self.last_state = None
    self.fit_attempts = self.fit_results = 0
    self.last_fit = None
    self.last_fit_rejection = None

  def reset(self, reason):
    self.control = None
    if reason in self.resets or len(self.resets) < 24:
      self.resets[str(reason)[:96]] += 1

  def fitted(self, result, objective):
    self.fit_results += 1
    self.last_fit = (str(result.status)[:64], str(result.reason)[:96], str(objective)[:128])
    if result.profile is None:
      self.last_fit_rejection = self.last_fit

  def publish(self, **kwargs):
    try:
      self._publish(**kwargs)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, OverflowError):
      # The report is never a prerequisite for collection or driving.
      pass

  def _publish(self, *, now_ns, message, collector, source, objective, pending, native_current, fit_result=None):
    if self.last_write and now_ns - self.last_write < 1_000_000_000:
      return
    self.last_write = now_ns
    self.started = self.started or now_ns
    value = message.autoSteeringLearning.learning
    raw = bool(collector is not None and getattr(collector.adapter, 'raw_collection', False))
    blocks = tuple(collector.blocks) if collector is not None else ()
    required = getattr(getattr(collector.adapter, 'objective', None), 'required_cohorts', None) if collector is not None else None
    selected = tuple(b for b in blocks if required is None or b.cohort in required)
    points = len(collector.raw_points if raw else collector.points) if collector is not None else 0
    progress = {'samples': sum(len(b.samples) for b in selected) + points,
                'blocks': len(selected), 'requiredBlocks': 16 if raw else 8}
    profile = None if fit_result is None else fit_result.profile
    fit = None if fit_result is None else {'status': str(fit_result.status), 'reason': str(fit_result.reason),
      'trainWindows': None if profile is None else len(profile.train_ids),
      'heldoutWindows': None if profile is None else len(profile.heldout_ids)}
    application = {'applied': False, 'reason': 'no_current_controller_application'}
    coordinates = []
    applied_sequence = 0
    actual_full = None
    if self.control is not None and collector is not None and collector.ready and native_current:
      event, evidence = self.control
      if (event.which() == 'starpilotLateralState' and evidence.identity == collector.ready[-1][0].identity and
          0 < evidence.control_ns <= now_ns <= evidence.control_ns + 100_000_000):
        outcome = read_application(event.starpilotLateralState, now_ns=now_ns)
        if outcome is not None and outcome.identity == evidence.identity:
          trace = getattr(evidence, 'effect_trace', None)
          if trace is not None:
            trace.validate()
            group_coordinates = tuple(dict(trace.applied)[n] for n in trace.envelope.trial_names)
            matched = bool(trace.qualified and (outcome.status != 'applied' or any(v for _,v in trace.applied)) and
              trace.control_ns == evidence.effect_prepare_ns and
              outcome.profile_sequence == evidence.applied_profile_sequence and
              outcome.actual_coordinates == group_coordinates and
              outcome.effect_manifest_sha256 == trace.envelope.manifest_sha256 and
              outcome.effect_original_anchor == trace.envelope.full_original_anchor and
              outcome.effect_applied_profile_sha256 == trace.applied_full_profile_sha256)
          else:
            matched = (not outcome.effect_applied_profile_sha256 and
              outcome.profile_sequence == evidence.applied_profile_sequence and
              outcome.actual_coordinates == evidence.applied_coordinates)
          leases = bool(evidence.enabled and evidence.active and evidence.valid and not evidence.manual and
            not evidence.fault and source is not None and source.enabled and not source.manual and
            evidence.source_epoch == source.source_epoch and source.observed_ns <= now_ns <= source.expires_ns and
            0 < evidence.source_receipt_ns <= evidence.control_ns <= now_ns <= evidence.source_receipt_ns + 500_000_000 and
            0 < evidence.applied_profile_observed_ns <= evidence.control_ns <= now_ns <= evidence.applied_profile_expires_ns and
            outcome.baseline_revision == evidence.baseline_revision and
            (not evidence.native_required or evidence.native_allowed and
              0 < evidence.native_observed_ns <= evidence.control_ns <= now_ns <= evidence.native_expires_ns))
          matched = matched and leases
          applied = bool(outcome.status == 'applied' and matched)
          application = {'applied': applied, 'reason': outcome.reason if outcome.status != 'applied' or matched else 'application_evidence_mismatch'}
          coordinates = list(outcome.actual_coordinates) if matched else []
          applied_sequence = outcome.profile_sequence if matched else 0
          actual_full = trace if matched and trace is not None else None
    actual_live = None
    live = getattr(collector, 'live_tracking', None) if collector is not None else None
    live_offer = None if live is None else live.latest_offer
    restoring = live_offer is None and live is not None and getattr(live, 'latest_restore_offer', None) is not None
    if restoring:
      live_offer = live.latest_restore_offer
    if self.control is not None and live_offer is not None and native_current and source is not None:
      event, evidence = self.control
      try:
        from openpilot.starpilot.flm.live_trial_wire import read_application as read_tracking_application, read_restore_application
        from openpilot.starpilot.flm.live_tracking_response import validate_response_witness
        validate_response_witness(live_offer.response_witness, live_offer.binding, now_ns)
        reader = read_restore_application if restoring else read_tracking_application
        ack = reader(event.starpilotLateralState, now_ns=now_ns, offer=live_offer)
        matched = bool(ack is not None and ack.status == ('restored' if restoring else 'applied') and
          event.which() == 'starpilotLateralState' and evidence.live_applied_coordinates and
          tuple(value for _, value in ack.applied) == evidence.live_applied_coordinates and
          ack.control_ns == evidence.effect_prepare_ns and
          0 <= evidence.control_ns-ack.control_ns <= 30_000_000 and
          0 < evidence.control_ns <= now_ns <= evidence.control_ns+100_000_000 and
          evidence.identity == live_offer.binding.identity and evidence.source_epoch == source.source_epoch and
          source.source_epoch == live_offer.binding.source_epoch and source.enabled and not source.manual and
          (source.cp_sha256, source.reset, source.calibration_generation) ==
            (live_offer.binding.cp_sha256, live_offer.binding.reset_generation, live_offer.binding.calibration_generation) and
          (evidence.controller_revision, evidence.baseline_revision) ==
            (live_offer.binding.controller_revision, live_offer.binding.baseline_revision) and
          source.observed_ns <= now_ns <= source.expires_ns and
          live_offer.observed_ns <= now_ns <= live_offer.expires_ns and
          evidence.active and evidence.enabled and evidence.valid and not evidence.manual and not evidence.fault and
          0 < evidence.source_receipt_ns <= evidence.control_ns <= now_ns <= evidence.source_receipt_ns+500_000_000 and
          (not evidence.native_required or evidence.native_allowed and
            evidence.native_observed_ns <= evidence.control_ns <= now_ns <= evidence.native_expires_ns))
        if matched:
          actual_live = ack
          application = {'applied': any(value for _, value in ack.applied), 'reason': ack.reason}
          applied_sequence = ack.sequence
          coordinates = list(evidence.live_applied_coordinates)
      except (AttributeError, TypeError, ValueError, OverflowError):
        # Telemetry never grants application or substitutes an offered target.
        pass
    details = {
      'Vehicle': self.vehicle, 'Collection session': '' if collector is None else collector.session,
      'Current objective': str(objective)[:128],
      'Total retained blocks': len(blocks), 'Current block samples': points,
      'Maneuver block coverage': dict(Counter(b.cohort for b in blocks)),
      'Required maneuver groups': list(required or ()),
      'Maximum block samples': 80 if raw else 240,
      'Block sealed; waiting for a different maneuver': bool(collector is not None and collector.sealed),
      'Discarded collection sessions': sum(self.resets.values()), 'Collection reset reasons': dict(self.resets),
      'Capture interruptions': {} if collector is None else dict(collector.training_interruptions),
      'Overrun batches': 0 if collector is None else collector.dropped,
      'Fit attempts': self.fit_attempts, 'Fit results': self.fit_results,
      'Fit in progress': bool(pending), 'Last fit': list(self.last_fit) if self.last_fit else [],
      'Current native evidence healthy': bool(native_current),
      'Applied profile sequence': applied_sequence, 'Applied correction coordinates': coordinates,
      'Calibration generation': '' if source is None else source.calibration_generation[:96],
      'Source receipt age seconds': None if source is None else max(0., (now_ns - source.observed_ns) / 1e9),
      'Collection duration seconds': (now_ns - self.started) / 1e9,
    }
    if collector is not None and getattr(collector.adapter, 'event_collection', False):
      if getattr(collector.adapter, 'source_plant_protocol', None) is not None:
        capture = getattr(collector, 'original_source_capture', None)
        replay = collector.adapter.replay
        receipts = getattr(collector.adapter, 'original_context_receipts', None)
        details['GLOBAL original source capture'] = {
          'poses': 0 if capture is None else len(capture.rows),
          'epoch': None if capture is None else capture.epoch,
          'epochStartNs': None if capture is None else capture.epoch_start_ns,
          'epochCutoffNs': None if capture is None else capture.epoch_cutoff_ns,
          'sealed': False if capture is None else capture.sealed,
          'reason': None if capture is None else capture.reason,
          'rejected': 0 if capture is None else capture.rejected,
          'frames': len(replay.records), 'checkpoints': len(replay.checkpoints),
          'emissions': len(replay.emissions.events),
          'controls': 0 if receipts is None else len(receipts.controls),
          'readyPairs': len(collector.ready),
        }
        from openpilot.starpilot.flm.source_observation_report import refusal_report
        details['GLOBAL source observation refusals'] = refusal_report(collector)
        details['GLOBAL response bootstrap readiness'] = dict(getattr(collector, '_live_bootstrap_readiness',
          {'reason': 'response_snapshot_not_attempted'}))
      readiness_reader = getattr(collector, 'snapshot_readiness_report', None)
      readiness: dict[str, object] = readiness_reader() if callable(readiness_reader) else {'reason': 'snapshot_not_attempted'}
      stamp = cast(int | None, readiness.pop('observedMonoTime', None))
      readiness['ageSeconds'] = None if stamp is None else max(0., (now_ns - stamp) / 1e9)
      details.update({'Snapshot readiness': readiness,
        'Snapshot selection attempts (collector lifetime)': getattr(collector, 'snapshot_attempts', 0),
        'Snapshot selections declined (collector lifetime)': getattr(collector, 'snapshot_declines', 0),
        'Submitted fit jobs (reporter lifetime)': self.fit_attempts,
        'Last unsuccessful fit': list(self.last_fit_rejection) if self.last_fit_rejection else [],
        'Parent fit profile retained': bool(collector.fit_result is not None and collector.fit_result.profile is not None),
        'Collector admission result retained': bool(collector.result is not None and collector.result.profile is not None)})
    if actual_live is not None:
      details.update({'Actual full surface coordinates': [[name, value] for name, value in actual_live.applied],
        'Actual full profile hash': actual_live.applied_sha256,
        'Actual live trial ID': actual_live.trial_id,
        'Actual live target sequence': actual_live.target_sequence,
        'Actual live control timestamp': actual_live.control_ns})
    if actual_full is not None:
      details.update({'Actual full surface coordinates': [[n,v] for n,v in actual_full.applied],
        'Actual full profile hash': actual_full.applied_full_profile_sha256,
        'Actual full surface manifest': actual_full.envelope.manifest_sha256,
        'Actual full original anchor': actual_full.envelope.full_original_anchor})
    if profile is not None:
      details.update({'Fit baseline loss': profile.baseline_loss, 'Fit candidate loss': profile.candidate_loss,
                      'Candidate correction coordinates': list(profile.coefficients),
                      'Candidate parameters': [p.name for p in profile.parameters]})
    if profile is not None and getattr(profile, 'effect_envelope', None) is not None:
      full = profile.effect_envelope
      full.validate(profile)
      details.update({'FLM manifest': full.manifest_sha256,
        'Original full surface anchor': full.full_original_anchor,
        'Prior full profile hash': full.prior_full_profile_sha256,
        'Proposed full profile hash': full.full_profile_sha256,
        'Full surface coordinates': [[name, coordinate] for name, coordinate in full.coordinates],
        'Active trial parameters': list(full.trial_names),
        'Persisted history is prior only': True})
    status = str(value.status)
    reason = str(value.reason)
    if collector is not None and status == 'collecting':
      reason = str(collector.reason)
      if pending:
        status, reason = 'fitting', 'evaluating_collected_maneuvers'
      elif collector.sealed:
        reason = 'awaiting_different_maneuver'
      elif points:
        reason = 'capturing_steering_evidence'
    current = {'enabled': None if source is None else bool(source.enabled), 'status': status, 'reason': reason,
               'progress': progress, 'fit': fit, 'application': application, 'details': details}
    live = getattr(collector, 'live_tracking', None) if collector is not None else None
    if live is not None:
      current.update(tracking_objects(live.report(now_ns), fresh=True))
    tracking = current.get('tracking')
    history_status, history_reason = (tracking['status'], tracking['reason']) if tracking is not None else (status, reason)
    state = (history_status, history_reason, application['applied'], None if tracking is None else tracking['trialId'],
             None if fit is None else (fit['status'], fit['reason']))
    if state != self.last_state:
      self.history.append({'time': f'{(now_ns - self.started) / 1e9:.1f} s', 'status': history_status,
                           'reason': history_reason, 'applied': application['applied']})
      self.last_state = state
    payload = {'version': 1, 'observedMonoTime': now_ns, 'vehicle': self.vehicle,
               'session': '' if collector is None else collector.session, 'current': current, 'history': list(self.history)}
    temporary = None
    try:
      encoded = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode()
      if len(encoded) > READ_MAX_BYTES:
        return
      fd, temporary = tempfile.mkstemp(prefix='.flm-training-', dir=self.path.parent)
      with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded)
      os.replace(temporary, self.path)
    except (OSError, ValueError, TypeError, OverflowError):
      # Diagnostics cannot stop collection or supply controller authority.
      pass
    finally:
      if temporary is not None:
        try:
          Path(temporary).unlink(missing_ok=True)
        except OSError:
          pass
