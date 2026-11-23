"""Android Auto tuning selected from the saved head-unit fingerprint or an override."""
import re

DEFAULTS = {
  "connect_delay_s": 0.0,       # wait before paging the car over Bluetooth
  "negotiate_delay_s": 0.0,     # wait after authentication before service discovery
  "max_window": 2,
  "phone_services": False,
  "phone_frames": False,
  "native_reconnects": 0,       # a car that never grants the screen: shut down cleanly and reconnect this many times
  "reuse_wifi_join": False,     # the adapter restarted the handshake with the same network: keep the join in progress
  "tcp_stops_with_rfcomm": False,  # the adapter closed RFCOMM before accepting TCP: stop retrying, reconnect instead
  "hfp_gate": False,            # the car grants the screen only once its own hands-free link to the comma is up: keep opening it
  "recycle_silent_link": False,  # with hfp_gate: hang up a Bluetooth link from the car that carries no hands-free, so it calls again
  "remember_wifi_channel": False,  # pin the first join to the channel the car's access point used last time
  "remember_wifi_address": False,  # join with the address the car's access point leased last time instead of waiting for DHCP
  "setup_restarts": 0,          # the adapter restarted its handshake while TCP/TLS were being set up: answer it and connect again
  "bootstrap_experiments": False,
  "stay_while_projecting": False,
  "dhcp_patience_s": 0.0,
  "own_handsfree": False,       # the comma keeps its own hands-free link to the adapter (AAWireless)
  "static_ip": False,           # join on a made-up address beside the adapter's own before asking its DHCP server
}

FINGERPRINT_KEYS = ("head_unit_make", "head_unit_model", "car_make", "car_model", "car_year")

RULES: list[dict] = [
  {"id": "aawireless", "label": "AAWireless", "match": {},
   "tuning": {"max_window": 30, "phone_services": True, "phone_frames": True, "stay_while_projecting": True,
              "dhcp_patience_s": 6.0, "own_handsfree": True, "static_ip": True}},
  # OTTOCAST (Wi-Fi "CAR2-xxxx"), 2020 Honda Accord, 2026-10-28..11-02 over 43 projections. The Honda grants the screen
  # if and only if its own hands-free link to the comma is up (24 of 24 once the Bluetooth channel was open): it then
  # answers the pairing request twice with status 1 and hands over within a second, whether that link came up before
  # the connection or 38 s into it. How fast the dongle answered never mattered. So: open the Bluetooth channel like a
  # phone (phone_services), keep the car's hands-free link up (hfp_gate), and stop paying for the old theory (no
  # negotiate delay, no native-screen reconnects). The dongle restarts its handshake ~12 s after sending its network;
  # an unpinned first join (hidden-SSID scan, median 6.5 s, up to 11.4 s) ran into that on every session, so pin the
  # remembered channel and answer a restart during setup instead of waiting for the dead TCP endpoint. Everything
  # below is the operator's set for that dongle and applies to no other adapter.
  # Video window (2026-11-02 drives, session logs 27/28): the Accord offers a window of 4 and the stream ran at a steady
  # 7 fps whenever its acknowledgements took ~280 ms with only 2 frames in flight (fps = window / ack latency), while the
  # renderer and encoder kept 30 fps. Use the car's full window; the ACK timeout still catches a silent receiver.
  # Silent links (2026-11-04..13, sessions 37-56): in 7 of 20 drives the Honda's first Bluetooth link at ignition came up
  # and then carried nothing, and the Honda held it for 68.2 s before calling again (twice it took three such links,
  # 221-228 s to the screen). A working link has its hands-free up within half a second, so hang a silent one up
  # (recycle_silent_link) instead of waiting out the car's timer. Whether the Honda calls back as fast after the comma
  # hangs up as after its own hang-up is not known yet: "companion_link_recycled" and the next hfp_connected show it.
  # The join (same 19 sessions): 6.2 s at the median, 5.4-13.9, on a channel already pinned; the dongle leased the same
  # address every time. remember_wifi_address joins with that address and checks the dongle answers from it, falling
  # back to DHCP within 1.5 s if it does not. Not driven yet: "wifi_joined address_source=remembered" shows it worked.
  {"id": "ottocast", "label": "OTTOCAST", "match": {},
   "tuning": {"phone_services": True, "reuse_wifi_join": True, "tcp_stops_with_rfcomm": True, "hfp_gate": True,
              "recycle_silent_link": True, "remember_wifi_channel": True, "remember_wifi_address": True,
              "setup_restarts": 2, "max_window": 30}},
  {"id": "unofficial", "label": "Unofficial dongle", "match": {}, "tuning": {"bootstrap_experiments": True, "dhcp_patience_s": 6.0}},
]


def profile_ids() -> list[str]:
  return [rule["id"] for rule in RULES]


def valid_override(value: str) -> bool:
  return value in ("", "default", *profile_ids())


def fingerprint_from(head_unit: dict, address: str) -> dict:
  found = {key: str(head_unit[key])[:80] for key in FINGERPRINT_KEYS if head_unit.get(key)}
  return {"address": address.upper(), **found} if found else {}


def _matches(rule: dict, fingerprint: dict) -> bool:
  match = rule.get("match") or {}
  return bool(match) and all(re.search(pattern, str(fingerprint.get(key, "")), re.IGNORECASE) for key, pattern in match.items())


def resolve(config: dict) -> tuple[str, str]:
  """(profile id, source) where source is "override", "detected" or "standard"."""
  override = config.get("car_profile_override", "")
  if override == "default":
    return "default", "override"
  if override in profile_ids():
    return override, "override"
  fingerprint = config.get("car_fingerprint") or {}
  if fingerprint.get("address") == str(config.get("receiver_address", "")).upper():
    for rule in RULES:
      if _matches(rule, fingerprint):
        return rule["id"], "detected"
  return "default", "standard"


def tuning(config: dict) -> dict:
  profile, _ = resolve(config)
  rule = next((item for item in RULES if item["id"] == profile), {})
  return {**DEFAULTS, **{key: value for key, value in (rule.get("tuning") or {}).items() if key in DEFAULTS}}


def summary(config: dict) -> dict:
  """What the UI may show: nothing at all while the standard behavior applies."""
  profile, source = resolve(config)
  rule = next((item for item in RULES if item["id"] == profile), None)
  return {"car_profile": profile, "car_profile_source": source, "car_profile_label": rule["label"] if rule else "",
          "car_profile_ids": profile_ids()}
