"""Classify live and logged connection failures to select retry delays."""

ASLEEP = "asleep"          # the car did not answer a page at all: off, or still waking
NOT_READY = "not_ready"    # the car answered, but its Android Auto service or Wi-Fi handshake is not up yet
LOCAL = "local"            # this comma's own Bluetooth stack refused or was busy; the car says nothing
OTHER = "other"            # anything else, including every failure after the link is up

CLASSES = (ASLEEP, NOT_READY, LOCAL, OTHER)

_LOCAL = ("device or resource busy", "errno 16]", "operation not permitted", "no such device")
_ASLEEP = ("host is down", "errno 112]", "page-timeout", "page timeout", "br-connection-page")
_NOT_READY = ("connection refused", "errno 111]", "connection reset", "errno 104]", "sdp record missing",
              "does not advertise android auto", "did not answer in time")
_EARLY_STAGES = ("connecting_bluetooth", "discovering", "rfcomm", "wifi_start", "wifi_info", "bootstrap")

# Waiting longer for a radio that is off does not wake it, and every page holds the controller (a page that crosses
# the car's own page can strand the link). These are floors: a class never waits less than the standard backoff.
ASLEEP_FLOOR = (6.0, 8.0, 12.0, 20.0, 30.0)
LOCAL_FLOOR = (8.0, 10.0, 15.0, 30.0)


def classify(stage: str, kind: str, message: str) -> str:
  text = f"{kind} {message}".lower()
  if kind == "BluetoothRejected" or any(word in text for word in _LOCAL):
    return LOCAL
  if stage not in _EARLY_STAGES:
    return OTHER
  if any(word in text for word in _ASLEEP) or (kind == "TimeoutError" and stage == "connecting_bluetooth"):
    return ASLEEP
  if any(word in text for word in _NOT_READY) or kind == "BootstrapTimeout":
    return NOT_READY
  return OTHER


def retry_delay(reach: str, attempt: int, standard: float) -> float:
  """The wait before the next attempt; never shorter than the standard backoff, and unchanged for other failures."""
  floor = {ASLEEP: ASLEEP_FLOOR, LOCAL: LOCAL_FLOOR}.get(reach)
  if floor is None:
    return standard
  return max(standard, floor[min(max(attempt, 0), len(floor) - 1)])


def keeps_cached_channel(reach: str) -> bool:
  """A sleeping car says nothing about which RFCOMM channel is right, so its cached channel is kept."""
  return reach in (ASLEEP, LOCAL)
