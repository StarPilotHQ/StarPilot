"""Keep posted-limit evidence tied to real TSR packets and measured producer cadence."""

from dataclasses import dataclass
from enum import Enum
import math


class Status(str, Enum):
  UNKNOWN = "unknown"
  VALID = "valid"
  ABSENT = "absent"


@dataclass(frozen=True)
class Observation:
  status: Status = Status.UNKNOWN
  speed_mps: float = 0.0
  observed_ns: int = 0
  valid_until_ns: int = 0
  episode: int = 0

class Tracker:
  """Bind a decoded sign to CANParser's learned message-timeout contract."""

  def __init__(self):
    self._last_sample_ns = 0
    self._last_value: tuple[Status, float] | None = None
    self._observation = Observation()

  def update(self, sample_ns: int, status: Status, speed_mps: float = 0.0, *, valid_until_ns: int = 0) -> Observation:
    if (type(sample_ns) is not int or sample_ns <= 0 or not isinstance(status, Status) or
        type(speed_mps) not in (int, float) or not math.isfinite(speed_mps) or
        (status is Status.VALID and speed_mps <= 0) or (status is not Status.VALID and speed_mps != 0) or
        type(valid_until_ns) is not int or valid_until_ns < 0):
      return self._observation
    if sample_ns <= self._last_sample_ns:
      return self._observation
    value = (status, float(speed_mps))
    prior = self._observation
    episode = prior.episode + int(value != self._last_value or
                                  (prior.valid_until_ns > 0 and sample_ns > prior.valid_until_ns))
    # Unknown includes unsupported sign codes and parser cadence not yet learned.
    usable = status is not Status.UNKNOWN and valid_until_ns > sample_ns
    self._observation = Observation(status if usable else Status.UNKNOWN, float(speed_mps) if usable else 0.0,
                                    sample_ns, valid_until_ns if usable else 0, episode)
    self._last_sample_ns = sample_ns
    self._last_value = value
    return self._observation

  @property
  def observation(self) -> Observation:
    return self._observation


def parser_expiry(parser, message: str, signal: str) -> tuple[int, int]:
  """Use the parser's learned timeout, never an independently guessed TTL."""
  timestamp = parser.ts_nanos.get(message, {}).get(signal, 0)
  msg = parser.dbc.name_to_msg.get(message)
  state = parser.message_states.get(msg.address) if msg is not None else None
  expiry = int(timestamp + state.timeout_threshold) if timestamp and state is not None and state.frequency > 0 else 0
  return timestamp, expiry


def honda_sign(raw: int) -> tuple[Status, float]:
  if 97 <= raw <= 113:
    return Status.VALID, (raw - 96) * 5.0 * 0.44704
  return (Status.ABSENT, 0.0) if raw == 125 else (Status.UNKNOWN, 0.0)


def toyota_sign(sign: int, speed: int) -> tuple[Status, float]:
  if sign in (1, 36) and 1 <= speed <= 199:
    return Status.VALID, speed * (1 / 3.6 if sign == 1 else 0.44704)
  return (Status.ABSENT, 0.0) if sign == 0 or (sign in (1, 36) and speed == 255) else (Status.UNKNOWN, 0.0)


def ford_sign(speed: int, unit: int) -> tuple[Status, float]:
  if 1 <= speed <= 250 and unit in (1, 2):
    return Status.VALID, speed * (1 / 3.6 if unit == 1 else 0.44704)
  return (Status.ABSENT, 0.0) if speed in (251, 255) else (Status.UNKNOWN, 0.0)


def hyundai_canfd_sign(speed: int, system_status: int, is_metric: bool) -> tuple[Status, float]:
  # FR_CMR_02_100ms defines 0 as no recognition, 253 as unlimited, and
  # 254/255 as reserved/invalid. A failed ISLW system is not evidence of absence.
  if system_status != 0:
    return Status.UNKNOWN, 0.0
  if 1 <= speed <= 252:
    return Status.VALID, speed * (1 / 3.6 if is_metric else 0.44704)
  return (Status.ABSENT, 0.0) if speed in (0, 253) else (Status.UNKNOWN, 0.0)


def hyundai_can_sign(raw: int, is_metric: bool) -> tuple[Status, float]:
  # Classic-CAN Navi_HU SpeedLim_Nav_Clu carries the displayed limit in cluster units, with 0 and 255
  # meaning "nothing shown". Neither value is evidence that no limit is posted (no map match is not a
  # roadway statement), so both are UNKNOWN rather than ABSENT.
  if 1 <= raw <= 254:
    return Status.VALID, raw * (1 / 3.6 if is_metric else 0.44704)
  return Status.UNKNOWN, 0.0
