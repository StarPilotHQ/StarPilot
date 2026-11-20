"""Explicit parked acceptance is a user starting value, never automatic qualification."""
from dataclasses import replace
import math
import re

from openpilot.starpilot.flm.manual_coordinates import ManualCoordinates, context, selected_manual
from openpilot.starpilot.flm.review_settings import review_settings, same_review_controller
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
from openpilot.starpilot.flm.offroad_store import KEY, MAX_BYTES, NAMESPACE, RouteScope
from openpilot.starpilot.flm.offroad_runtime import current_scope
from openpilot.starpilot.flm.offroad_surface import ROUTE_NAMES
from openpilot.starpilot.flm.runtime_adapter_registry import car_params_digest
from openpilot.starpilot.lateral.controller_selection import read_selection, DOCUMENT_KEY as CONTROLLER_KEY
from openpilot.starpilot.lateral.learning_preferences import read_preference, KEY as LEARNING_KEY
from openpilot.starpilot.lateral.torque_settings import DOCUMENT_KEY, MAX_DOCUMENT_BYTES, FieldChoice, PlatformProfile, parse_document, serialize_document
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved


def _finding(report, parameter, target, assessment):
  current = report.get('current') or {}
  details = current.get('details') or {}
  if current.get('fresh') is not True:
    raise ValueError('Refresh the drive findings before accepting this change.')
  rows = details.get('Recorded coordinate recommendations', ())
  matches = [row for row in rows if type(row) is list and len(row) == 5 and row[0] == parameter and row[2] == target
             and type(row[3]) is int and row[3] > 0 and row[4] not in (
               'conflicting_recorded_candidates', 'heldout_profile_does_not_support_training_step', 'controller_checks_did_not_support_change')]
  if len(matches) != 1:
    raise ValueError('This finding is no longer available. Refresh the drive review.')
  provenance = details.get('Recorded suggestion assessments', ())
  if type(provenance) not in (list, tuple):
    raise ValueError('The original finding source is unavailable.')
  links = [row for row in provenance if type(row) in (list, tuple) and len(row) == 3 and row[0] == parameter]
  if len(links) > 1 or links and (links[0][1] != assessment or type(links[0][2]) is not str
                               or re.fullmatch('[0-9a-f]{64}', links[0][2]) is None):
    raise ValueError('The original finding source changed. Refresh the review.')
  if not links and details.get('Offroad assessment hash') != assessment:
    raise ValueError('Refresh the drive findings before accepting this change.')
  return report.get('vehicle'), links[0][2] if links else details.get('Original completed route hash'), tuple(matches[0])


REVIEW_REFUSALS = {
  'vehicle_identity_unavailable': 'The vehicle must identify itself before a finding from this review can be accepted.',
  'unexpected_review_type': 'This drive review is unavailable for accepting findings. Refresh the findings.',
  'recording_mismatch': 'This finding’s recording no longer matches its drive review. Refresh the findings.',
  'vehicle_mismatch': 'This finding belongs to a different vehicle fingerprint. Review a drive from the current vehicle.',
  'review_fingerprint_unavailable': 'This older finding is missing its recorded vehicle fingerprint. Review a newer drive.',
}


def review_binding_issue(cache, assessment, cp, source_digest):
  document = cache.get(assessment)
  kind = document.get('kind')
  if kind == 'identity_review':
    return document, 'vehicle_identity_unavailable'
  if kind not in ('completed_assessment', 'partial_assessment'):
    return document, 'unexpected_review_type'
  route = document['assessment']['route']
  if route['source_sha256'] != source_digest:
    return document, 'recording_mismatch'
  fingerprint = document['assessment'].get('vehicle')
  if fingerprint is None and route['cp_sha256'] != car_params_digest(cp):
    # Older documents stored their vehicle in the checkpoint, not inside the
    # assessment. Only a checkpoint for this exact recording can supply it.
    vehicles = set()
    for checkpoint in cache.checkpoints():
      result = checkpoint['result']
      for review in (result, result.get('pending_review', {})):
        vehicle = review.get('vehicle')
        if (type(vehicle) is str and vehicle and review.get('assessment_sha256') == assessment
            and review.get('source_sha256') == source_digest and review.get('completed_route') == route.get('route_id')):
          vehicles.add(vehicle)
    if len(vehicles) != 1:
      return document, 'review_fingerprint_unavailable'
    fingerprint = next(iter(vehicles))
  return document, 'vehicle_mismatch' if fingerprint is not None and fingerprint != str(cp.carFingerprint) else None


def accept_finding(settings, reports, payload, *, authorized):
  if (type(payload) is not dict or set(payload) != {'parameter', 'target', 'assessment'} or payload['parameter'] not in ROUTE_NAMES
      or type(payload['target']) not in (int, float) or not math.isfinite(payload['target']) or
      type(payload['assessment']) is not str or re.fullmatch('[0-9a-f]{64}', payload['assessment']) is None):
    raise ValueError('Select a current finding to accept.')
  parameter, target, assessment = payload['parameter'], payload['target'], payload['assessment']
  with settings.lock:
    ctx = settings.context.sample()
    owner = settings._owner(ctx, live=True, session_valid=authorized)
    if ctx.cp is None or not ctx.parked or ctx.editing_saved_vehicle or not owner.authority('torque') or not authorized():
      raise ValueError('Park with the current vehicle connected before accepting a finding.')
    cp = ctx.cp
    dependencies = {key: read_saved(settings.params, key, MAX_BYTES if key == KEY else MAX_DOCUMENT_BYTES)
                    for key in (CONTROLLER_KEY, LEARNING_KEY, 'ForceAutoTuneOff', KEY)}
    if not all(value[1] for value in dependencies.values()):
      raise ValueError('The saved learning settings are unreadable.')
    selection = read_selection(settings.params, cp)
    if selection.source == 'invalid':
      raise ValueError('The selected steering controller is unavailable.')
    identity = context(cp, selection.mode, recovered=True)
    preference = read_preference(settings.params, cp, preference_snapshot=dependencies[LEARNING_KEY], legacy_snapshot=dependencies['ForceAutoTuneOff'])
    if not preference.valid or not preference.enabled:
      raise ValueError('Enable Auto Learn before accepting a finding for continued learning.')
    found = _finding(reports.snapshot(), parameter, target, assessment)
    if found[0] not in (None, str(cp.carFingerprint)):
      raise ValueError('This finding belongs to a different vehicle.')
    directory = directory_for_params(settings.params)
    if not directory.exists():
      raise ValueError('The original drive review is unavailable. Refresh the findings.')
    document, issue = review_binding_issue(AssessmentCache(directory), assessment, cp, found[1])
    if issue is not None:
      raise ValueError(REVIEW_REFUSALS[issue])
    from openpilot.starpilot.flm.learned_lifecycle import decode

    scope = current_scope(cp, settings.params)
    origin_scope = document['assessment'].get('current_scope')
    origin_settings = document['assessment'].get('review_settings_sha256')
    settings_match = origin_settings is not None and origin_settings == review_settings(cp, settings.params)
    if origin_scope is not None and not same_review_controller(RouteScope(**origin_scope).validate(), scope, same_fingerprint=settings_match):
      raise ValueError('This finding belongs to a different controller setup. Review its current findings.')
    if origin_settings is not None and not settings_match:
      raise ValueError('The steering settings changed since this finding. Review the current findings.')
    active = decode(dependencies[KEY][0]).get(NAMESPACE, {}).get(scope.key())
    if active is not None and active['state'] == 'ready_for_next_drive':
      raise ValueError('Complete the saved tune’s next-drive review before accepting another finding. The other findings remain available.')
    raw, readable = read_saved(settings.params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    if not readable:
      raise ValueError('Saved steering settings are unreadable. Nothing was changed.')
    baseline = cp.lateralTuning.torque
    basis = (float(baseline.latAccelFactor), float(baseline.latAccelOffset), float(baseline.friction))
    profiles = {} if raw is None else parse_document(raw)
    fingerprint = str(cp.carFingerprint)
    profile = profiles.get(fingerprint, PlatformProfile(basis, FieldChoice(), FieldChoice()))
    if profile.basis != basis or profile.active_legacy_flm:
      raise ValueError('Review the existing manual tune before accepting this finding.')
    previous = selected_manual(cp, settings.params)
    if previous is not None and previous.compatibility:
      from openpilot.starpilot.flm.offroad_runtime import current_compatibility
      from openpilot.starpilot.flm.runtime_adapter_registry import digest
      if previous.compatibility != digest(current_compatibility(cp, settings.params)):
        raise ValueError('Review the recovered tune before accepting this finding.')
    source_values = dict(active['values']) if active is not None else {} if previous is None else dict(previous.values)
    values = dict.fromkeys(ROUTE_NAMES, 0.) | source_values
    if previous is not None and any(values[name] != dict(previous.values)[name] for name in previous.locked):
      raise ValueError('The saved automatic tune conflicts with a manual setting. Review the saved tune.')
    if previous is not None and parameter in previous.locked:
      raise ValueError('This setting is manually locked. Select Default before accepting a finding.')
    if values[parameter] not in (found[2][1], target):
      raise ValueError('This setting changed since the finding. Review the current value.')
    values[parameter] = target
    automatic = set(() if previous is None else previous.automatic or ()) | {parameter}
    if active is not None:
      automatic.update(name for name, value in source_values.items() if value != 0 and (previous is None or name not in previous.locked))
    accepted = ManualCoordinates(identity, tuple(values.items()), '' if previous is None else previous.compatibility,
                                 tuple(name for name in ROUTE_NAMES if name in automatic)).validate()
    profiles[fingerprint] = replace(profile, manual_coordinates=accepted)
    desired = serialize_document(profiles)
    def permitted():
      fresh = settings.context.sample()
      return (authorized() and fresh.parked and fresh.cp_raw == ctx.cp_raw and owner.authority('parked_preferences')
              and owner.authority('torque') and _finding(reports.snapshot(), parameter, target, assessment) == found
              and all(read_saved(settings.params, key, MAX_BYTES if key == KEY else MAX_DOCUMENT_BYTES) == value for key, value in dependencies.items()))

    if not permitted():
      raise ValueError('The vehicle or finding changed. Review it again.')
    saved = commit_exact(settings.params, key=DOCUMENT_KEY, max_bytes=MAX_DOCUMENT_BYTES, raw=desired,
                         expected=raw, authorized=permitted, temp_prefix='.accepted-steering-')
    if not saved.committed or not saved.verified:
      raise ValueError('Could not confirm that the finding was saved. Refresh the saved settings.')
    return {'status': 'saved', 'parameter': parameter, 'value': target, 'learning': 'automatic', 'for_next_drive': True}
