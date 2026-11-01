"""Failures before video are told apart by why the car could not be reached, using the texts Bugsink actually showed."""

import pytest

from openpilot.starpilot.system.starport import bugsink_report as br, reachability as r
from openpilot.starpilot.system.starport.tests.test_bugsink_report import write_session

CASES = [
  ("discovering", "RuntimeError", "Could not find the car's Android Auto service: [Errno 112] Host is down", r.ASLEEP),
  ("connecting_bluetooth", "RuntimeError", "org.bluez.Error.Failed: br-connection-page-timeout", r.ASLEEP),
  ("connecting_bluetooth", "TimeoutError", "", r.ASLEEP),
  ("rfcomm", "OSError", "starting wireless setup: [Errno 112] Host is down", r.ASLEEP),
  ("rfcomm", "ConnectionRefusedError", "[Errno 111] Connection refused", r.NOT_READY),
  ("rfcomm", "ConnectionResetError", "starting wireless setup: [Errno 104] Connection reset by peer", r.NOT_READY),
  ("discovering", "RuntimeError", "Head unit does not advertise Android Auto Wireless (SDP record missing)", r.NOT_READY),
  ("wifi_start", "BootstrapTimeout", "waiting for car: wifi_start: head unit did not answer in time", r.NOT_READY),
  ("rfcomm", "OSError", "[Errno 16] Device or resource busy", r.LOCAL),
  ("connecting_bluetooth", "BluetoothRejected", "pairing rejected", r.LOCAL),
  ("streaming", "TimeoutError", "projecting: Video acknowledgement older than 2.5 s", r.OTHER),  # after the link is up
  ("authenticating", "ConnectionResetError", "Connection reset by peer", r.OTHER),
  ("joining_wifi", "BootstrapError", "joining_wifi: ", r.OTHER),
]


@pytest.mark.parametrize("stage,kind,message,expected", CASES)
def test_classify_known_bugsink_texts(stage, kind, message, expected):
  assert r.classify(stage, kind, message) == expected


def test_waits_are_never_shorter_than_the_standard_backoff_and_other_failures_are_unchanged():
  standard = (2.0, 4.0, 8.0, 15.0, 30.0)
  for attempt, base in enumerate(standard):
    assert r.retry_delay(r.OTHER, attempt, base) == base and r.retry_delay(r.NOT_READY, attempt, base) == base
    for reach in (r.ASLEEP, r.LOCAL):
      assert r.retry_delay(reach, attempt, base) >= base
  assert r.retry_delay(r.ASLEEP, 0, 2.0) == 6.0 and r.retry_delay(r.ASLEEP, 99, 30.0) == 30.0


def test_a_sleeping_car_keeps_its_cached_channel_but_a_refusing_one_does_not():
  assert r.keeps_cached_channel(r.ASLEEP) and r.keeps_cached_channel(r.LOCAL)
  assert not r.keeps_cached_channel(r.NOT_READY) and not r.keeps_cached_channel(r.OTHER)


def test_report_names_a_sleeping_car_instead_of_unknown(tmp_path):
  events = [(0, 0, "session_start", {"receiver": "Car"}), (0, 1, "stage", {"state": "discovering"}),
            (0, 9, "attempt_failed", {"stage": "discovering", "kind": "RuntimeError",
                                      "error": "finding android auto: Could not find the car's Android Auto service: [Errno 112] Host is down"})]
  record = br.session_record(write_session(tmp_path, 1, events))
  assert record["reach"] == "asleep"  # derived from the text for logs written before the supervisor recorded it
  event = br.drive_event([record])
  assert event["tags"]["aa.cause"] == "car_asleep" and event["tags"]["aa.reach_class"] == "asleep"
  recorded = [(0, 0, "session_start", {}), (0, 9, "attempt_failed", {"stage": "rfcomm", "kind": "ConnectionRefusedError",
                                                                      "error": "refused", "reach": "not_ready"})]
  assert br.session_record(write_session(tmp_path, 2, recorded))["reach"] == "not_ready"


def test_stream_losses_are_told_apart_by_what_the_log_says(tmp_path):
  from openpilot.starpilot.system.starport.bugsink_diagnosis import stream_loss_kind
  assert stream_loss_kind("projecting: Video acknowledgement older than 2.5 s") == "stalled"
  assert stream_loss_kind("projecting: Head unit stopped answering for 6 s") == "silent"
  assert stream_loss_kind("projecting: Head unit disconnected after 4/8 bytes") == "disconnected"
  assert stream_loss_kind("something else") == "other"
  for text, kind in (("Video acknowledgement older than 2.5 s", "stalled"), ("Head unit disconnected after 4/8 bytes", "disconnected")):
    events = [(0, 0, "session_start", {}), (0, 1, "stage", {"state": "streaming"}), (0, 9, "attempt_failed", {"stage": "streaming", "kind": "EOFError", "error": text})]
    event = br.drive_event([br.session_record(write_session(tmp_path, 3 if kind == "stalled" else 4, events))])
    assert event["tags"]["aa.cause"] == "projection_interrupted" and event["tags"]["aa.loss_kind"] == kind


def test_every_real_bugsink_failure_text_is_classified_as_intended():
  import json
  from pathlib import Path
  rows = json.loads((Path(__file__).parent / "fixtures" / "bugsink_failure_texts.json").read_text())["failures"]
  assert sum(row["count"] for row in rows) > 600 and len(rows) > 40
  seen = {r.ASLEEP: 0, r.NOT_READY: 0, r.LOCAL: 0, r.OTHER: 0}
  for row in rows:
    got = r.classify(row["stage"], row["kind"], row["evidence"])
    text = row["evidence"].lower()
    seen[got] += row["count"]
    if row["stage"] in ("streaming", "authenticating", "negotiating", "connecting_tcp", "joining_wifi"):
      assert got == r.OTHER, row            # once the link is up, none of this is about reaching the car
      continue
    if "host is down" in text or "page-timeout" in text:
      assert got == r.ASLEEP, row
    if "connection refused" in text or "did not answer in time" in text:
      assert got == r.NOT_READY, row
    if "galaxy bluetooth first" in text:
      assert got == r.OTHER, row            # a setup step to take, not a reachability problem
  early = seen[r.ASLEEP] + seen[r.NOT_READY] + seen[r.LOCAL]
  assert early > 0.6 * sum(seen.values()), seen   # most failures before video are now explained instead of "unknown"
