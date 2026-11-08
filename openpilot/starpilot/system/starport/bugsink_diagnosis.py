"""Evidence and recovery guidance from ordered Android Auto session events."""

import ipaddress
import math
import re
from datetime import datetime
from typing import Any

ADDRESS = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:_-]){5}[0-9A-Fa-f]{2}\b")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
SECRET = re.compile(r"(?i)\b(password|passwd|psk|ssid|token|secret|authorization)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
PRIVATE_KEYS = {"receiver", "receiver_address", "receiver_name", "bluetooth_name", "display_name", "ssid", "psk",
                "password", "private_key", "certificate", "token", "secret", "authorization", "head_unit_subject"}
EVIDENCE_EVENTS = {"bootstrap_start_response", "wifi_joined", "tls_established", "authentication_rejected", "tls_failed",
                   "video_acknowledged", "link_lost", "link_check_failed", "link_check_slow", "stream_stall", "trust_failed",
                   "tcp_retry", "sdp_retry", "retry_early", "wifi_release_failed", "wifi_dedicated_skipped",
                   "wifi_autoconnect_recover_failed", "usb_state", "usb_configure_timeout", "usb_no_accessory_start",
                   "usb_recover_failed", "encoder_failed", "encoder_recovered", "encoder_reopen_failed", "headunitpad_rejected"}


VERSION_KEYS = {"head_unit_software_version", "head_unit_software_build"}


def sanitize(value, names=()):
  if isinstance(value, dict):
    return {key: _sanitize_version(item) if key in VERSION_KEYS and isinstance(item, str) else sanitize(item, names)
            for key, item in value.items() if str(key).lower() not in PRIVATE_KEYS}
  if isinstance(value, (tuple, list)):
    return [sanitize(item, names) for item in value]
  if isinstance(value, float) and not math.isfinite(value):
    return None
  if not isinstance(value, str):
    return value
  for name in names:
    if name:
      value = value.replace(str(name), "<name>")
  value = SECRET.sub(lambda m: m[1] + "=<redacted>", ADDRESS.sub("<address>", value))
  for candidate in re.findall(r"[0-9a-fA-F:]+(?:%[\w]+)?", value):
    if ":" not in candidate:
      continue
    try:
      ipaddress.IPv6Address(candidate)
    except ValueError:
      continue
    value = value.replace(candidate, "<ip>")
  return IPV4.sub("<ip>", value)[:8000]


def _sanitize_version(value: str) -> str:
  """A head unit's software version is public build text; a dotted version is not an address."""
  return SECRET.sub(lambda m: m[1] + "=<redacted>", ADDRESS.sub("<address>", value))[:80]


def elapsed(start, end):
  try:
    if isinstance(start.get("mono_s"), (float, int)) and isinstance(end.get("mono_s"), (float, int)):
      seconds = end["mono_s"] - start["mono_s"]
    else:
      seconds = (datetime.fromisoformat(end["t"]) - datetime.fromisoformat(start["t"])).total_seconds()
    return round(seconds, 1) if math.isfinite(seconds) and 0 <= seconds <= 86400 else None
  except (KeyError, TypeError, ValueError):
    return None


def attempt_records(events):
  attempts: list[dict] = []
  current: dict[str, Any] | None = None
  first_frame = None
  shutdown = False
  start = events[0] if events else {}
  for event in events:
    name = event["event"]
    if name in ("session_start", "session_stop", "log_dropped"):
      if name == "session_stop" and current is not None and first_frame is not None:
        current["streamed_s"] = elapsed(first_frame, event)
      continue
    if current is None and name not in ("attempt_start", "stage", "attempt_failed", "video_acknowledged"):
      continue
    if name == "attempt_start" or current is None:
      current = {"attempt": len(attempts) + 1, "at_s": elapsed(start, event), "stages": [],
                 "video_acknowledged": False, "streamed_s": None, "ended_by_car": False,
                 "after_car_shutdown": shutdown, "evidence": []}
      attempts.append(current)
      first_frame = None
    if name == "stage":
      current["stages"] = (current["stages"] + [{"stage": event.get("state", ""), "at_s": elapsed(start, event)}])[-40:]
    if name == "video_acknowledged":
      first_frame = event
      current["video_acknowledged"] = True
      current["after_car_shutdown"] = shutdown = False
    if name in EVIDENCE_EVENTS:
      current["evidence"] = (current["evidence"] + [{"event": name, "at_s": elapsed(start, event),
        **{key: sanitize(event[key]) for key in ("status", "reason", "error", "version", "ms", "state", "attempt", "encode_ms")
                 if key in event}}])[-20:]
    if name == "attempt_failed":
      current.update(failed_stage=str(event.get("stage", "")), error_kind=str(event.get("kind", "")),
                     error=sanitize(str(event.get("error", ""))), failed_at_s=elapsed(start, event),
                     where=sanitize(event.get("where", [])[-4:]), reach=str(event.get("reach", "")))
    if name == "session_ended":
      current["ended_by_car"] = str(event.get("reason", "")).startswith("Head unit ended projection")
      shutdown = shutdown or current["ended_by_car"]
    if name in ("attempt_failed", "session_ended"):
      if first_frame is not None:
        current["streamed_s"] = elapsed(first_frame, event)
      current = first_frame = None
  return attempts


STREAM_LOSS = {
  "shared_radio": ("comma's own Wi-Fi", "The car stopped acknowledging video while the comma's own Wi-Fi joined or roamed; both share one radio.",
                   ["Check the other_wifi_changed and ack_grace events for when it changed and how long acks stopped.",
                    "Repeated drops at home or on a hotspot mean the 8 s grace is too short or the car takes its screen back first."]),
  "stalled": ("Video acknowledgement older than", "The car kept the link but stopped acknowledging video for too long, so the stream was ended.",
              ["Compare the Wi-Fi signal, frame age and encoder recoveries around the stall.",
               "If it repeats at the same place or speed, look for the comma stalling (CPU, thermal) rather than the car."]),
  "silent": ("Head unit stopped answering", "The head unit stopped answering protocol messages while the link looked up.",
             ["Check whether the car's screen turned off or switched source at that time.",
              "Compare with other drives on the same head unit before changing timings."]),
  "disconnected": ("Head unit disconnected", "The head unit closed the connection, which is what a car shutdown or a source change looks like.",
                   ["Check whether the drive ended or the driver changed source on the head unit.",
                    "Only investigate further if it happened while the car stayed on and Android Auto was showing."]),
}


def stream_loss_kind(error: str) -> str:
  """Why a stream ended, from the logged error text: stalled, silent, disconnected or other."""
  return next((kind for kind, (marker, _, _) in STREAM_LOSS.items() if marker.lower() in error.lower()), "other")


def diagnosis(session):
  stage, error = session.get("failed_stage", ""), session.get("error", "").lower()
  category, confidence = "unknown", "unknown"
  explanation = "The session log identifies the failed stage but does not establish the underlying cause."
  actions = ["Download the matching Galaxy Android Auto log bundle before retrying.",
             "Compare the attempt timeline with NetworkManager and wpa_supplicant journals from the same boot."]
  if "unmanaged" in error:
    category, confidence = "wifi_not_ready", "observed"
    explanation = "The Wi-Fi interface was unmanaged when Android Auto tried to join."
    actions = ["Check NetworkManager device state and the wlan0 managed transition at this boot time.",
               "Once wlan0 is managed, retry projection while parked; keep Bluetooth bonds intact."]
  elif stage == "joining_wifi":
    category, confidence = "wifi_join_failed", "suspected"
    explanation = "Joining car Wi-Fi failed. Scan contention is one possibility; the session log alone cannot prove it."
    actions = ["Inspect NetworkManager and wpa_supplicant for association rejection, timeout=scan, or pending scans.",
               "Correlate Galaxy hotspot restarts/channel changes and home Wi-Fi autoconnect with the failed join.",
               "While parked, retry once the car is awake; verify Wi-Fi joined and video was acknowledged."]
  elif "certificate" in error or "identity" in error or "authenticationrejected" in session.get("error_kind", "").lower():
    category, confidence = "identity_rejected", "suspected"
    explanation = "Identity or certificate validation failed; check expiry and the device clock before replacing credentials."
    actions = ["Inspect identity status and certificate expiry in Galaxy, and verify device time.",
               "If expired, upload a current Android Auto package through Galaxy and retry while parked.",
               "Keep TLS verification enabled; confirm authentication and acknowledged video on the retry."]
  elif stage == "authenticating":
    category, confidence = "authentication_incomplete", "observed"
    explanation = "Authentication did not complete. A timeout alone does not prove a bad certificate."
    actions = ["Check whether TLS completed and whether the head unit replied to authentication.",
               "Retry with the head unit fully awake; compare with any successful attempt in this session."]
  elif stage in ("wifi_start", "wifi_info"):
    category, confidence = "head_unit_bootstrap_stalled", "observed"
    explanation = "Bluetooth connected and the version exchange ran, but the head unit stopped answering the Wi-Fi setup."
    actions = ["Read the attached handshake trace for the last messages the head unit sent and the ones it ignored.",
               "Compare the head unit make, model and protocol version with other reports before changing timings."]
  elif stage in ("connecting_bluetooth", "discovering", "rfcomm", "bootstrap"):
    category, confidence = "head_unit_unavailable", "suspected"
    explanation = "The Bluetooth/bootstrap exchange did not complete; the head unit may still be waking or already off."
    actions = ["Confirm the selected car is awake and inspect Bluetooth/bootstrap responses in the bundle.",
               "Retry while parked before considering re-pairing; preserve existing bonds for diagnosis."]
    reach = session.get("reach", "")
    if reach == "asleep":
      category = "car_asleep"
      explanation = "The car did not answer the Bluetooth page at all. Its radio is off or still waking, so waiting is the fix."
      actions = ["Wait for the car to finish waking and let the retry run; the saved pairing and RFCOMM channel were kept.",
                 "Only re-pair if the car is awake with its Bluetooth screen open and pages still go unanswered."]
    elif reach == "not_ready":
      category = "car_not_ready"
      explanation = "The car answered Bluetooth but its Android Auto service refused or dropped the connection; it is not ready yet."
      actions = ["Let the retry run; the car usually accepts a few seconds later.",
                 "If it repeats on every drive, compare the head unit make and model with other reports."]
    elif reach == "local":
      category = "bluetooth_stack_busy"
      explanation = "This comma's own Bluetooth stack was busy or refused, which says nothing about the car."
      actions = ["Check for another process or a second pairing attempt holding the controller.",
                 "Retry once the controller is idle; preserve existing bonds."]
  elif stage == "streaming":
    category, confidence = "projection_interrupted", "observed"
    explanation = "Projection was interrupted. EOF or a timeout alone cannot distinguish a car shutdown from link loss."
    actions = ["Compare video acknowledgments, Wi-Fi signal, stalls, encoder recoveries and head-unit shutdown events.",
               "Retry while parked and verify sustained acknowledged frames, not just a streaming status."]
    if (loss := stream_loss_kind(session.get("error", ""))) != "other":
      explanation, actions = STREAM_LOSS[loss][1], STREAM_LOSS[loss][2]
  return {"category": category, "confidence": confidence, "summary": explanation,
          "evidence": session.get("error", ""), "recovered": session.get("outcome") == "recovered",
          "next_steps": actions, "log_file": session.get("name", ""),
          "verification": "A later attempt must authenticate and acknowledge video; compare time to first frame and stream stability."}
