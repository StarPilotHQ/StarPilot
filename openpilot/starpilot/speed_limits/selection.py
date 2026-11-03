"""Select an eligible observation without accepting it or authorizing control."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from openpilot.starpilot.speed_limits.acceptance import Observation, ObservationKind, observation_is_valid


class Source(StrEnum):
  DASHBOARD = "dashboard"
  MAP = "map"
  VISION = "vision"
  ONLINE = "online"


class SelectionMode(StrEnum):
  ORDERED = "ordered"
  HIGHEST = "highest"
  LOWEST = "lowest"


@dataclass(frozen=True)
class SelectionPolicy:
  mode: SelectionMode
  slots: tuple[Source | None, ...]
  online_fallback: bool


@dataclass(frozen=True)
class Selection:
  observation: Observation
  selected_source: Source | None
  reason: str
  considered: tuple[Source, ...] = ()
  unavailable: tuple[tuple[Source, ObservationKind], ...] = ()
  below_minimum: tuple[Source, ...] = ()
  errors: tuple[str, ...] = ()


MINIMUM_LIMIT_MPS = 1.0
PRIMARY_ORDER = (Source.DASHBOARD, Source.MAP, Source.VISION)


def select_limit(observations: Mapping[Source, Observation], policy: SelectionPolicy) -> Selection:
  """Use valid configured sources, then an optional online fallback.

  The caller supplies normalized, eligible, source-specific observations. A
  missing producer is UNKNOWN, not ABSENT. Selection does not infer freshness,
  vision support, map lookahead, road identity or provider request permission.
  """
  errors = _input_errors(observations, policy)
  if errors:
    return Selection(Observation(ObservationKind.UNKNOWN), None, "invalid_input", errors=tuple(errors))

  if policy.mode == SelectionMode.ORDERED:
    considered = tuple(dict.fromkeys(source for source in policy.slots if source is not None))
  else:
    considered = PRIMARY_ORDER if Source.VISION in policy.slots else PRIMARY_ORDER[:2]

  unavailable = []
  below_minimum = []
  eligible = []

  def consider(source):
    observation = observations[source]
    if observation.kind == ObservationKind.VALID:
      if observation.candidate.speed_mps >= MINIMUM_LIMIT_MPS:
        eligible.append(source)
        return
      below_minimum.append(source)
      unavailable.append((source, ObservationKind.ABSENT))
    else:
      unavailable.append((source, observation.kind))

  for source in considered:
    consider(source)

  if eligible:
    if policy.mode == SelectionMode.ORDERED:
      selected = eligible[0]
    else:
      # Stable source ordering makes equal-limit ties independent of input map order.
      choose = max if policy.mode == SelectionMode.HIGHEST else min
      selected = choose(eligible, key=lambda source: observations[source].candidate.speed_mps)
    reason = "primary"
  elif policy.online_fallback:
    considered += (Source.ONLINE,)
    consider(Source.ONLINE)
    selected = eligible[0] if eligible else None
    reason = "online_fallback" if selected is not None else "no_candidate"
  else:
    selected = None
    reason = "no_candidate"

  if selected is not None:
    observation = observations[selected]
  else:
    kinds = {kind for _, kind in unavailable}
    # Preserve availability; a remembered limit cannot make a missing source current.
    kind = (ObservationKind.UNKNOWN if ObservationKind.UNKNOWN in kinds else
            ObservationKind.STALE if ObservationKind.STALE in kinds else ObservationKind.ABSENT)
    observation = Observation(kind)
  return Selection(observation, selected, reason, considered, tuple(unavailable), tuple(below_minimum))


def _input_errors(observations, policy):
  errors = []
  if not isinstance(policy, SelectionPolicy):
    return ["policy must be SelectionPolicy"]
  if not isinstance(policy.mode, SelectionMode):
    errors.append("selection mode must be explicit")
  if (not isinstance(policy.slots, tuple) or len(policy.slots) not in (2, 3) or
      any(source is not None and (not isinstance(source, Source) or source == Source.ONLINE) for source in policy.slots)):
    errors.append("two or three source slots are required; online is fallback only")
  if type(policy.online_fallback) is not bool:
    errors.append("online fallback must be Boolean")
  if not isinstance(observations, Mapping):
    return errors + ["observations must be a source mapping"]
  if len(observations) != len(Source) or any(not isinstance(source, Source) for source in observations) or set(observations) != set(Source):
    return errors + ["every source requires an explicit observation"]
  for source, observation in observations.items():
    if not observation_is_valid(observation):
      errors.append(f"{source.value}: invalid observation")
      continue
    candidate = observation.candidate
    if candidate is not None and candidate.source != source.value:
      errors.append(f"{source.value}: candidate source mismatch")
  return errors
