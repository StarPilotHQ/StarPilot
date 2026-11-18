"""Explicit parked acceptance is a user starting value, never automatic qualification."""
from dataclasses import replace
import math
import re

from openpilot.starpilot.flm.manual_coordinates import ManualCoordinates, context, selected_manual
from openpilot.starpilot.flm.offroad_cache import AssessmentCache, directory_for_params
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
  if current.get('fresh') is not True or details.get('Offroad assessment hash') != assessment:
    raise ValueError('Refresh the drive findings before accepting this change.')
  rows = details.get('Recorded coordinate recommendations', ())
  matches = [row for row in rows if type(row) is list and len(row) == 5 and row[0] == parameter and row[2] == target
             and type(row[3]) is int and row[3] > 0 and row[4] not in (
               'conflicting_recorded_candidates', 'heldout_profile_does_not_support_training_step', 'controller_checks_did_not_support_change')]
  if len(matches) != 1:
    raise ValueError('This finding is no longer available. Refresh the drive review.')
  return report.get('vehicle'), details.get('Original completed route hash'), tuple(matches[0])


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
    dependencies = {key: read_saved(settings.params, key, MAX_DOCUMENT_BYTES) for key in (CONTROLLER_KEY, LEARNING_KEY, 'ForceAutoTuneOff')}
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
    # The canonical reader omits the report envelope's vehicle; the original
    # cached assessment's exact CarParams digest below is mandatory either way.
    if found[0] not in (None, str(cp.carFingerprint)):
      raise ValueError('This finding belongs to a different vehicle.')
    directory = directory_for_params(settings.params)
    if not directory.exists():
      raise ValueError('The original drive review is unavailable. Refresh the findings.')
    document = AssessmentCache(directory).get(assessment)
    route = document['assessment']['route']
    if (document.get('kind') not in ('completed_assessment', 'partial_assessment') or
        route['cp_sha256'] != car_params_digest(cp) or route['source_sha256'] != found[1]):
      raise ValueError('The original drive review does not match this vehicle.')
    raw, readable = read_saved(settings.params, DOCUMENT_KEY, MAX_DOCUMENT_BYTES)
    if not readable:
      raise ValueError('Saved steering settings are unreadable. Nothing was changed.')
    baseline = cp.lateralTuning.torque
    basis = (float(baseline.latAccelFactor), float(baseline.latAccelOffset), float(baseline.friction))
    profiles = {} if raw is None else parse_document(raw)
    fingerprint = str(cp.carFingerprint)
    profile = profiles.get(fingerprint, PlatformProfile(basis, FieldChoice(), FieldChoice()))
    if profile.basis != basis or profile.flm is not None:
      raise ValueError('Review the existing manual tune before accepting this finding.')
    previous = selected_manual(cp, settings.params)
    if previous is not None and previous.compatibility:
      from openpilot.starpilot.flm.offroad_runtime import current_compatibility
      from openpilot.starpilot.flm.runtime_adapter_registry import digest
      if previous.compatibility != digest(current_compatibility(cp, settings.params)):
        raise ValueError('Review the recovered tune before accepting this finding.')
    values = dict.fromkeys(ROUTE_NAMES, 0.) | ({} if previous is None else dict(previous.values))
    values[parameter] = target
    automatic = set(() if previous is None else previous.automatic or ()) | {parameter}
    accepted = ManualCoordinates(identity, tuple(values.items()), '' if previous is None else previous.compatibility,
                                 tuple(name for name in ROUTE_NAMES if name in automatic)).validate()
    profiles[fingerprint] = replace(profile, manual_coordinates=accepted)
    desired = serialize_document(profiles)
    def permitted():
      fresh = settings.context.sample()
      return (authorized() and fresh.parked and fresh.cp_raw == ctx.cp_raw and owner.authority('parked_preferences')
              and owner.authority('torque') and _finding(reports.snapshot(), parameter, target, assessment) == found
              and all(read_saved(settings.params, key, MAX_DOCUMENT_BYTES) == value for key, value in dependencies.items()))

    if not permitted():
      raise ValueError('The vehicle or finding changed. Review it again.')
    saved = commit_exact(settings.params, key=DOCUMENT_KEY, max_bytes=MAX_DOCUMENT_BYTES, raw=desired,
                         expected=raw, authorized=permitted, temp_prefix='.accepted-steering-')
    if not saved.committed or not saved.verified:
      raise ValueError('Could not confirm that the finding was saved. Refresh the saved settings.')
    return {'status': 'saved', 'parameter': parameter, 'value': target, 'learning': 'automatic', 'for_next_drive': True}
