import json
import math
import threading
import functools
import time

FIELDS = {"latitude", "longitude", "altitude", "speed", "bearing", "horizontalAccuracy", "verticalAccuracy",
          "speedAccuracy", "bearingAccuracy", "timestamp", "ageMs"}
STREAM_FIELDS = {"sessionId", "sequence", "sampleElapsedRealtimeNanos", "sentElapsedRealtimeNanos"}
FIELDS |= STREAM_FIELDS | {"serverMonoTimeNs"}
MAX_AGE_MS = 2000
CLOCK_MARGIN_NS = 50_000_000  # Drift budget for a phone calibration refreshed at least every 60 seconds.
MAX_SESSIONS = 1024


def _number(payload, key, required=False):
  value = payload.get(key)
  if value is None and not required:
    return math.nan
  if type(value) not in (int, float) or not math.isfinite(value):
    raise ValueError(f"Invalid {key}")
  return float(value)


def _optional(payload, key, low, high):
  # Phones report an unknown value as null or a negative number.
  value = _number(payload, key)
  return value if low <= value < high else math.nan


def phone_gps(payload, now_ns):
  if not isinstance(payload, dict) or set(payload) - FIELDS:
    raise ValueError("Unexpected location fields")
  latitude, longitude = _number(payload, "latitude", True), _number(payload, "longitude", True)
  accuracy, age_ms = _number(payload, "horizontalAccuracy", True), _number(payload, "ageMs", True)
  if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
    raise ValueError("Invalid position")
  if not 0 < accuracy <= 10_000:
    raise ValueError("Invalid accuracy")
  if not 0 <= age_ms <= MAX_AGE_MS:
    raise ValueError("Location is too old")
  timestamp = payload.get("timestamp", 0)
  if type(timestamp) is not int or not 0 <= timestamp < 2 ** 53:
    raise ValueError("Invalid timestamp")
  bearing_accuracy = _optional(payload, "bearingAccuracy", 0, 180)
  return {
    "sourceMonoTime": now_ns - int(age_ms * 1e6),
    "latitude": latitude,
    "longitude": longitude,
    "altitude": _optional(payload, "altitude", -1000, 10_000),
    "speed": _optional(payload, "speed", 0, 100),
    "bearingDeg": _optional(payload, "bearing", 0, 360) if math.isfinite(bearing_accuracy) else math.nan,
    "horizontalAccuracy": accuracy,
    "verticalAccuracy": _optional(payload, "verticalAccuracy", 0, 10_000),
    "bearingAccuracyDeg": bearing_accuracy,
    "speedAccuracy": _optional(payload, "speedAccuracy", 0, 100),
    "unixTimestampMillis": timestamp,
    "hasFix": True,
  }


def handoff_path():
  import tempfile
  from pathlib import Path
  shm = Path("/dev/shm")
  return (shm if shm.is_dir() else Path(tempfile.gettempdir())) / "starpilot_phone_gps.json"


@functools.lru_cache(maxsize=1)
def _boot_id():
  from openpilot.starpilot.system.starport.identity import boot_id
  return boot_id()


def _stream(payload):
  if not STREAM_FIELDS.intersection(payload):
    if "serverMonoTimeNs" in payload:
      raise ValueError("Missing location session")
    return None
  session = payload.get("sessionId")
  if not isinstance(session, str) or not 1 <= len(session) <= 128 or not session.isascii() or not session.isprintable():
    raise ValueError("Invalid sessionId")
  for name in STREAM_FIELDS - {"sessionId"}:
    value = payload.get(name)
    if type(value) is not int or not 0 <= value < (2 ** 53 if name == "sequence" else 2 ** 63):
      raise ValueError(f"Invalid {name}")
  sample, sent = payload["sampleElapsedRealtimeNanos"], payload["sentElapsedRealtimeNanos"]
  if sample > sent or (sent - sample) / 1e6 > payload["ageMs"] + 1:
    raise ValueError("Invalid monotonic age")
  return {"sessionId": session, "sequence": payload["sequence"], "sample": sample}


def _read_handoff(path):
  try:
    with path.open("rb") as source:
      value = json.loads(source.read(1 << 18))
    return value if isinstance(value, dict) else {}
  except (OSError, ValueError, RecursionError):
    return {}


def _distance_m(a, b):
  lat1, lat2 = math.radians(a.latitude), math.radians(b["latitude"])
  dlat, dlon = lat2 - lat1, math.radians(b["longitude"] - a.longitude)
  haversine = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
  return 12_742_000 * math.asin(math.sqrt(min(1., max(0., haversine))))


class PhoneGpsPublisher:
  """Validate once at ingress; navigationd retains the actual measurement age."""

  def __init__(self, path=None, mono_clock=time.monotonic_ns):
    self._path, self._mono_clock = path, mono_clock
    self._lock = threading.Lock()

  def timing(self):
    # Client advances this lower clock bound using its elapsed-realtime clock.
    return {"ok": True, "serverMonoTimeNs": self._mono_clock()}

  def publish(self, payload):
    from openpilot.starpilot.gps.source import PHONE_MAX_AGE_NS, MAX_HORIZONTAL_ACCURACY
    path = self._path or handoff_path()
    with self._lock:
      now = self._mono_clock()
      timing = {"timingBounded": False, "timingUncertaintyMs": None}
      try:
        gps = phone_gps(payload, now)
        stream = _stream(payload)
        if "serverMonoTimeNs" in payload:
          server_ns = payload["serverMonoTimeNs"]
          if type(server_ns) is not int or not 0 < server_ns <= now + CLOCK_MARGIN_NS:
            raise ValueError("Invalid serverMonoTimeNs")
          gps["sourceMonoTime"] = min(gps["sourceMonoTime"], server_ns - CLOCK_MARGIN_NS - int(payload["ageMs"] * 1e6))
          uncertainty = (now - server_ns + CLOCK_MARGIN_NS) / 1e9
          timing = {"timingBounded": True, "timingUncertaintyMs": uncertainty * 1000}
          # Position at an uncertain time has additional motion uncertainty.
          # Unknown speed uses the same conservative road-speed bound as jumps.
          speed = gps["speed"]
          speed_error = gps["speedAccuracy"]
          bound = min(100., speed + (3 * speed_error if math.isfinite(speed_error) else 10.)) if math.isfinite(speed) else 100.
          gps["horizontalAccuracy"] += bound * uncertainty
      except ValueError as error:
        self._status(path, now, False, str(error), **timing)
        raise
      def reject(reason):
        return self._status(path, now, False, reason, **timing)

      old_value = _read_handoff(path)
      old_stream = old_value.get("_stream", {})
      if (old_value.get("_bootId") != _boot_id() or not isinstance(old_stream, dict) or
          not isinstance(old_stream.get("sessionId"), str) or
          not all(type(old_stream.get(key)) is int and old_stream[key] >= 0 for key in ("received", "sequence", "sample", "stamp")) or
          old_stream.get("received", now + 1) > now or
          not isinstance(old_stream.get("retired"), list) or
          not all(isinstance(value, str) for value in old_stream.get("retired", []))):
        old_stream = {}
      retired = old_stream.get("retired", [])
      same_session = stream is not None and stream["sessionId"] == old_stream.get("sessionId")
      if stream is not None:
        if stream["sessionId"] in retired:
          return reject("retiredSession")
        if same_session:
          if stream["sequence"] <= old_stream["sequence"] or stream["sample"] <= old_stream["sample"]:
            return reject("outOfOrder")
          # A fresh calibration independently bounds transport age and can recover
          # from an earlier slow probe. Without one, retain the session anchor.
          if not timing["timingBounded"]:
            gps["sourceMonoTime"] = min(gps["sourceMonoTime"], old_stream["stamp"] + stream["sample"] - old_stream["sample"])
        elif old_stream:
          if len(retired) >= MAX_SESSIONS:
            return reject("sessionLimit")
          retired = [*retired, old_stream["sessionId"]]
      if now - gps["sourceMonoTime"] > MAX_AGE_MS * 1_000_000:
        return reject("delayed")
      # Preserve the good measurement through brief accuracy dropouts and jumps.
      if gps["horizontalAccuracy"] > MAX_HORIZONTAL_ACCURACY:
        return reject("poorAccuracy")
      previous = read_phone_gps(now, path)
      if previous is not None:
        stamp, old = previous
        timestamp, old_timestamp = gps["unixTimestampMillis"], old.unixTimestampMillis
        if gps["sourceMonoTime"] <= stamp:
          return reject("outOfOrder")
        # Legacy clients have no monotonic session clock. Keep their existing
        # wall-clock queue detection, allowing recovery once the held fix expires.
        if stream is None:
          if timestamp and old_timestamp and timestamp <= old_timestamp:
            return reject("outOfOrder")
          if timestamp and old_timestamp:
            elapsed = (timestamp - old_timestamp) * 1_000_000
            if elapsed <= PHONE_MAX_AGE_NS:
              gps["sourceMonoTime"] = min(gps["sourceMonoTime"], stamp + elapsed)
              if now - gps["sourceMonoTime"] > MAX_AGE_MS * 1_000_000:
                return reject("delayed")
        # Permit road motion up to 100m/s plus a generous uncertainty envelope.
        allowance = 100 * (gps["sourceMonoTime"] - stamp) / 1e9 + 5 * (old.horizontalAccuracy + gps["horizontalAccuracy"]) + 50
        if _distance_m(old, gps) > allowance:
          return reject("implausibleJump")
      if stream is not None:
        old_stream = {**stream, "retired": retired, "stamp": gps["sourceMonoTime"], "received": now}
      if old_stream:
        gps["_stream"] = old_stream
      gps["_bootId"] = _boot_id()
      temporary = path.with_name(path.name + ".tmp")
      temporary.write_text(json.dumps(gps))
      temporary.replace(path)
      result = self._status(path, now, True, "accepted", **timing)
      return result if stream is not None else {"ok": True}

  @staticmethod
  def _status(path, now, accepted, reason, **timing):
    status = path.with_name(path.name + ".status")
    previous = _read_handoff(status)
    counts = {key: previous.get(key, 0) if type(previous.get(key)) is int and previous[key] >= 0 else 0
              for key in ("acceptedCount", "rejectedCount")}
    counts["acceptedCount" if accepted else "rejectedCount"] += 1
    temporary = status.with_name(status.name + ".tmp")
    temporary.write_text(json.dumps({"receivedMonoTime": now, "accepted": accepted, "reason": reason, "bootId": _boot_id(), **counts, **timing}))
    temporary.replace(status)
    return {"ok": True, "accepted": accepted, "reason": reason}


def phone_gps_status(now_ns=None, path=None):
  """Coordinate-free diagnostics; a successful upload need not be a usable fix."""
  now_ns = time.monotonic_ns() if now_ns is None else now_ns
  path = path or handoff_path()
  try:
    value = json.loads(path.with_name(path.name + ".status").read_text())
    received = value["receivedMonoTime"]
    if value.get("bootId", _boot_id()) != _boot_id() or type(received) is not int or not 0 < received <= now_ns:
      return None
    fix = read_phone_gps(now_ns, path)
    return {"reason": value["reason"], "accepted": value["accepted"], "usable": fix is not None,
            "receivedAgeMs": (now_ns - received) / 1e6,
            "fixAgeMs": (now_ns - fix[0]) / 1e6 if fix is not None else None,
            **{key: value.get(key) for key in ("acceptedCount", "rejectedCount", "timingBounded", "timingUncertaintyMs")}}
  except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
    return None


def read_phone_gps(now_ns, path=None):
  """The handed-off fix as a Gps-like object, or None when absent, malformed or older than the phone hold."""
  from types import SimpleNamespace
  from openpilot.starpilot.gps.source import PHONE_MAX_AGE_NS, valid_fix
  try:
    gps = _read_handoff(path or handoff_path())
    gps.pop("_stream", None)  # Receiver metadata never enters the cereal GPS schema.
    if gps.pop("_bootId", _boot_id()) != _boot_id():
      return None
    stamp = gps["sourceMonoTime"]
    if type(stamp) is not int:
      return None
    return valid_fix(SimpleNamespace(**gps), stamp, stamp, now_ns, PHONE_MAX_AGE_NS)
  except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
    return None
