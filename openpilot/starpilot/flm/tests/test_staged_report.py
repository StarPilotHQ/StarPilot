"""Retained numerical progress must not become current target authority."""

from dataclasses import replace

from openpilot.starpilot.flm.tests.test_staged_source_study import pause, baseline_ack


def test_held_progress_does_not_claim_target_or_confirmed_application():
  o, _original, scores, incoming = pause()
  report = o.report(incoming.end_ns)
  assert report['phase'] == 'rollback' and not report['applicationAcknowledged']
  assert (report['beforeEpisodes'], report['futureEpisodes'], report['requiredEpisodes']) == (3, 2, 3)
  assert report['reason'] == 'trial_input_regime_ended' and not any(o.accepted.values())
  ack = baseline_ack(o, incoming.end_ns + 10_000_000)
  report = o.report(ack)
  assert report['status'] == report['phase'] == 'rollback'
  assert report['applicationAcknowledged'] and report['reason'] == 'original_input_regime_after_baseline_ack_required'
  assert (report['beforeEpisodes'], report['futureEpisodes']) == (len(scores[0]), len(scores[1]))
  assert report['lastCorrectionMonoTime'] is None and not any(o.accepted.values())


def test_stale_baseline_does_not_report_current_restoration():
  o, _original, _scores, incoming = pause()
  ack = baseline_ack(o, incoming.end_ns + 10_000_000)
  report = o.report(ack + 100_000_001)
  assert not report['applicationAcknowledged'] and not report['bindingCurrent']
  assert report['phase'] is None and report['modelStatus'] == 'stale'


def test_expired_changed_or_discarded_study_has_no_retained_progress():
  for change in ('expiry', 'certificate', 'baseline', 'manual'):
    o, original, _scores, incoming = pause()
    now = baseline_ack(o, incoming.end_ns + 10_000_000)
    if change == 'expiry':
      now = original['deadline_ns']
    elif change == 'certificate':
      o.certificate = replace(o.certificate, model_revision='9' * 64)
    elif change == 'baseline':
      o.accepted['ff_gain_left'] = 0.001
    else:
      o.interrupt('manual_override')
    report = o.report(now)
    assert report['beforeEpisodes'] == report['futureEpisodes'] == 0
  for invalid_now in (None, True, 'now', float('nan')):
    o, _original, _scores, incoming = pause()
    baseline_ack(o, incoming.end_ns + 10_000_000)
    report = o.report(invalid_now)
    assert report['beforeEpisodes'] == report['futureEpisodes'] == 0
    assert not report['applicationAcknowledged'] and not report['bindingCurrent']
