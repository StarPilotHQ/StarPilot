"""Tests for one-way log relay staging and Galaxy /api/logs/stage endpoints."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.parse import unquote

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.log_relay_stage import (
  CLAIM_RE,
  MODEL_LIMIT,
  NOTE_LIMIT,
  STAGE_URL,
  LogRelayStager,
  StageBusy,
  StageRejected,
  clean_stage_request,
)
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.starport_send import DISCORD_FILE_LIMIT

VALID_CLAIM = "https://starpilot-log-relay.nathan-g-hubbard.workers.dev/report/galaxy/claim/" + ("a1b2" * 16)
SAMPLE_ZIP = b"PK\x03\x04" + b"\x00" * 32


def wait_until(predicate, timeout=2.0):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    value = predicate()
    if value:
      return value
    time.sleep(0.01)
  raise AssertionError("Timed out waiting for condition")


class CleanStageRequestTest(unittest.TestCase):
  def test_valid_requests_per_kind(self):
    self.assertEqual(
      clean_stage_request({"kind": "starport", "note": "  Black\nscreen\ton start  "}),
      ("starport", "android", "Black screen on start"),
    )
    self.assertEqual(
      clean_stage_request({"kind": "connection", "phone": "ios", "note": "App keeps spinning"}),
      ("connection", "ios", "[iPhone] App keeps spinning"),
    )
    self.assertEqual(
      clean_stage_request({"kind": "connection", "phone": "android", "note": "Stuck on pairing"}),
      ("connection", "android", "[Android] Stuck on pairing"),
    )
    self.assertEqual(
      clean_stage_request({"kind": "controller", "model": " 8BitDo\tMicro ", "note": "No response"}),
      ("controller", "ios", "Controller (8BitDo Micro): No response"),
    )

  def test_rejects_invalid_payloads(self):
    invalid = [
      None,
      [],
      {},
      {"kind": "crash", "note": "nope"},
      {"kind": "starport", "note": "   "},
      {"kind": "starport", "note": 123},
      {"kind": "starport", "note": "x" * (NOTE_LIMIT + 1)},
      {"kind": "starport", "note": "ok", "phone": "ios"},
      {"kind": "starport", "note": "ok", "extra": 1},
      {"kind": "connection", "note": "ok"},
      {"kind": "connection", "phone": "windows", "note": "ok"},
      {"kind": "controller", "note": "ok"},
      {"kind": "controller", "model": "\n\t  ", "note": "ok"},
      {"kind": "controller", "model": "m" * (MODEL_LIMIT + 1), "note": "ok"},
    ]
    for payload in invalid:
      with self.subTest(payload=payload):
        with self.assertRaises(StageRejected):
          clean_stage_request(payload)


class LogRelayStagerTest(unittest.TestCase):
  def test_stages_starport_connection_and_controller_bundles(self):
    calls = []

    def fake_post(url, data, headers, allow_redirects, timeout):
      calls.append({"url": url, "data": data, "headers": headers, "allow_redirects": allow_redirects, "timeout": timeout})
      response = Mock(status_code=200)
      response.json.return_value = {"claimUrl": VALID_CLAIM}
      return response

    stager = LogRelayStager(
      lambda **_kw: ("starport-logs.zip", b"starport-zip"),
      lambda: b"connection-zip",
      lambda: b"controller-zip",
      post=fake_post,
      official=lambda: False,
    )
    initial = stager.status()
    self.assertEqual(initial["state"], "idle")
    self.assertTrue(initial["available"])
    self.assertTrue(initial["fork"])
    self.assertEqual(initial["noteLimit"], NOTE_LIMIT)
    self.assertEqual(initial["modelLimit"], MODEL_LIMIT)

    cases = [
      ({"kind": "starport", "note": "Dropped after 2m"}, b"starport-zip", "starport", "android", "Dropped after 2m"),
      ({"kind": "connection", "phone": "android", "note": "mDNS fail"}, b"connection-zip", "connection", "android", "[Android] mDNS fail"),
      ({"kind": "controller", "model": "Xbox Series", "note": "D-pad dead"}, b"controller-zip", "controller", "ios", "Controller (Xbox Series): D-pad dead"),
    ]
    for payload, expected_zip, expected_kind, expected_platform, expected_note in cases:
      with self.subTest(kind=expected_kind):
        started = stager.start(payload)
        self.assertEqual(started["state"], "staging")
        ready = wait_until(lambda: stager.status() if stager.status()["state"] == "ready" else None)
        self.assertEqual(ready["claimUrl"], VALID_CLAIM)
        self.assertEqual(ready["kind"], expected_kind)
        self.assertTrue(CLAIM_RE.fullmatch(ready["claimUrl"]))
        call = calls[-1]
        self.assertEqual(call["url"], STAGE_URL)
        self.assertEqual(call["data"], expected_zip)
        self.assertFalse(call["allow_redirects"])
        self.assertEqual(call["headers"]["Content-Type"], "application/zip")
        self.assertEqual(call["headers"]["X-Galaxy-Kind"], expected_kind)
        self.assertEqual(call["headers"]["X-Galaxy-Platform"], expected_platform)
        self.assertEqual(unquote(call["headers"]["X-Galaxy-Note"]), expected_note)

  def test_retries_starport_without_system_snapshot_when_oversize(self):
    seen_args = []
    posts = []

    def build_starport(*, system_snapshot=True):
      seen_args.append(system_snapshot)
      return ("starport-logs.zip", b"x" * (DISCORD_FILE_LIMIT + 1) if system_snapshot else b"small-zip")

    def fake_post(url, data, **_kwargs):
      posts.append(data)
      response = Mock(status_code=200)
      response.json.return_value = {"claimUrl": VALID_CLAIM}
      return response

    stager = LogRelayStager(build_starport, lambda: b"c", lambda: b"k", post=fake_post, official=lambda: True)
    stager.start({"kind": "starport", "note": "Trim snapshot"})
    ready = wait_until(lambda: stager.status() if stager.status()["state"] == "ready" else None)
    self.assertEqual(seen_args, [True, False])
    self.assertEqual(posts, [b"small-zip"])
    self.assertFalse(ready["fork"])

  def test_failures_surface_clear_fallback_messages(self):
    def run_failure(response_or_exc, *, zip_bytes=b"zip"):
      def fake_post(*_args, **_kwargs):
        if isinstance(response_or_exc, Exception):
          raise response_or_exc
        return response_or_exc

      stager = LogRelayStager(lambda **_kw: ("s.zip", zip_bytes), lambda: zip_bytes, lambda: zip_bytes, post=fake_post)
      stager.start({"kind": "connection", "phone": "ios", "note": "Fail test"})
      return wait_until(lambda: stager.status() if stager.status()["state"] == "failed" else None)

    r404 = Mock(status_code=404)
    self.assertIn("updating", run_failure(r404)["message"])

    r413 = Mock(status_code=413)
    self.assertIn("upload limit", run_failure(r413)["message"])
    self.assertIn("upload limit", run_failure(Mock(status_code=200), zip_bytes=b"x" * (DISCORD_FILE_LIMIT + 1))["message"])

    r429 = Mock(status_code=429)
    r429.json.return_value = {"error": "Too many uploads from this IP"}
    self.assertEqual(run_failure(r429)["message"], "Too many uploads from this IP")

    bad_claim = Mock(status_code=200)
    bad_claim.json.return_value = {"claimUrl": "https://evil.example/claim/" + ("a" * 64)}
    self.assertIn("Invalid relay claim link", run_failure(bad_claim)["message"])

    bad_json = Mock(status_code=200)
    bad_json.json.side_effect = ValueError("not json")
    self.assertIn("Invalid response", run_failure(bad_json)["message"])

    self.assertIn("internet", run_failure(RuntimeError("dns failed"))["message"])

  def test_busy_and_unavailable_guards(self):
    entered = threading.Event()
    release = threading.Event()

    def slow_post(*_args, **_kwargs):
      entered.set()
      release.wait(2)
      response = Mock(status_code=200)
      response.json.return_value = {"claimUrl": VALID_CLAIM}
      return response

    stager = LogRelayStager(lambda **_kw: ("s.zip", b"zip"), lambda: b"c", lambda: b"k", post=slow_post)
    stager.start({"kind": "starport", "note": "First"})
    self.assertTrue(entered.wait(2))
    try:
      with self.assertRaises(StageBusy):
        stager.start({"kind": "starport", "note": "Second"})
    finally:
      release.set()
    wait_until(lambda: stager.status()["state"] == "ready")

    disabled = LogRelayStager(lambda **_kw: ("s.zip", b"zip"), lambda: b"c", lambda: b"k", stage_url="")
    self.assertFalse(disabled.status()["available"])
    with self.assertRaises(StageRejected):
      disabled.start({"kind": "starport", "note": "Disabled"})


class LogStageHTTPTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.access = GalaxyAccessOwner(Path(self.temp.name))
    self.access.configure("test-password-123", lambda: True)
    self.release = threading.Event()
    self.entered = threading.Event()

    def slow_or_fast_post(*_args, **_kwargs):
      self.entered.set()
      self.release.wait(2)
      response = Mock(status_code=200)
      response.json.return_value = {"claimUrl": VALID_CLAIM}
      return response

    self.stager = LogRelayStager(
      lambda **_kw: ("starport-logs.zip", SAMPLE_ZIP),
      lambda: SAMPLE_ZIP,
      lambda: SAMPLE_ZIP,
      post=slow_or_fast_post,
      official=lambda: True,
    )
    self.server = make_server(port=0, owner=self.access, parked=lambda: True, log_relay_stager=self.stager)
    self.addCleanup(self.server.server_close)
    self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    self.thread.start()
    self.addCleanup(lambda: (self.server.shutdown(), self.thread.join(2)))
    self.cookie = ""

  def request(self, path, *, method="GET", payload=None, origin=None, remote=False):
    headers = {"Origin": origin or f"http://127.0.0.1:{self.server.server_port}", "Content-Type": "application/json"}
    if self.cookie:
      headers["Cookie"] = self.cookie
    if remote:
      headers["Forwarded"] = "for=203.0.113.9"
    conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
    try:
      conn.request(method, path, None if payload is None else json.dumps(payload), headers)
      resp = conn.getresponse()
      cookie = resp.getheader("Set-Cookie")
      return resp.status, json.loads(resp.read()), cookie.split(";")[0] if cookie else ""
    finally:
      conn.close()

  def test_http_validation_busy_status_and_auth(self):
    self.assertEqual(self.request("/api/logs/stage", remote=True)[0], 401)
    _, _, self.cookie = self.request("/api/auth/session")
    self.assertEqual(self.request("/api/logs/stage", origin="https://other.example")[0], 403)
    self.assertEqual(self.request("/api/logs/stage", method="POST", payload={"kind": "starport", "note": ""})[0], 400)

    status, body, _ = self.request("/api/logs/stage", method="POST", payload={"kind": "controller", "model": "DualSense", "note": "Not detected"})
    self.assertEqual(status, 202)
    self.assertEqual(body["state"], "staging")
    self.assertTrue(self.entered.wait(2))
    self.assertEqual(self.request("/api/logs/stage", method="POST", payload={"kind": "starport", "note": "Busy"})[0], 409)
    self.release.set()

    ready = wait_until(lambda: res if (res := self.request("/api/logs/stage")[1])["state"] == "ready" else None)
    self.assertEqual(ready["claimUrl"], VALID_CLAIM)
    first_cookie, self.cookie = self.cookie, ""
    _, _, self.cookie = self.request("/api/auth/session")
    self.assertNotEqual(self.cookie, first_cookie)
    self.assertEqual(self.request("/api/logs/stage")[1]["claimUrl"], "")
    self.cookie = first_cookie
    self.assertEqual(self.request("/api/logs/stage")[1]["claimUrl"], VALID_CLAIM)



if __name__ == "__main__":
  unittest.main()
