"""Remember the last accepted limit as a fallback, never as fresh source evidence."""

import json
import math

from openpilot.cereal import custom
from openpilot.starpilot.schema_cache import schema_fingerprint
from openpilot.starpilot.speed_limits import acceptance as acc
from openpilot.starpilot.speed_limits.map_observation import repair_legacy_map_speed

FORMAT = "starpilot-slc-accepted"
VERSION = 1
MAX_BYTES = 16_384


def encode(accepted: acc.AcceptedLimit, saved_wall_ns: int) -> str:
  if not isinstance(accepted, acc.AcceptedLimit) or type(saved_wall_ns) is not int or saved_wall_ns <= 0:
    raise ValueError("invalid accepted history")
  candidate = accepted.candidate
  identity = candidate.observation_identity
  if not acc.observation_is_valid(acc.Observation(acc.ObservationKind.VALID, candidate), session_id=accepted.session_id):
    raise ValueError("invalid accepted candidate")
  return json.dumps({"format": FORMAT, "version": VERSION, "source_schema": schema_fingerprint(custom.SlcDashboardObservation.schema),
                     "saved_wall_ns": saved_wall_ns, "source": candidate.source, "speed_mps": candidate.speed_mps,
                     "identity_kind": identity.kind.value, "identity_value": identity.value,
                     "origin_session": accepted.session_id}, sort_keys=True, separators=(",", ":"))


def decode(raw: str | bytes | dict | None, *, session_id: str) -> acc.AcceptedLimit | None:
  if raw is None or type(session_id) is not str or not session_id:
    return None
  try:
    value = raw if isinstance(raw, dict) else json.loads(raw)
    if (type(value) is not dict or set(value) != {"format", "version", "source_schema", "saved_wall_ns", "source",
                                               "speed_mps", "identity_kind", "identity_value", "origin_session"} or
        value["format"] != FORMAT or type(value["version"]) is not int or value["version"] != VERSION or
        value["source_schema"] != schema_fingerprint(custom.SlcDashboardObservation.schema) or
        type(value["saved_wall_ns"]) is not int or value["saved_wall_ns"] <= 0 or
        value['source'] not in ('dashboard', 'map', 'vision') or
        not isinstance(value["origin_session"], str) or not value["origin_session"] or
        type(value["speed_mps"]) not in (int, float) or not math.isfinite(value["speed_mps"])):
      return None
    kind = acc.IdentityKind(value['identity_kind'])
    identity = acc.ObservationIdentity(kind, value['identity_value'],
      value['origin_session'] if kind is acc.IdentityKind.SESSION_VALUE else None)
    candidate = acc.Candidate(value['source'], identity, float(value['speed_mps']))
    acc.new_session(value['origin_session'], acc.AcceptedLimit(candidate, value['origin_session'], 0))
    speed = repair_legacy_map_speed(candidate.speed_mps) if candidate.source == 'map' else candidate.speed_mps
    remembered = acc.Candidate(candidate.source,
      acc.ObservationIdentity(acc.IdentityKind.SESSION_VALUE, session_id=session_id), speed)
    return acc.new_session(session_id, acc.AcceptedLimit(remembered, session_id, 0)).accepted
  except (TypeError, ValueError, OverflowError, RecursionError):
    return None
