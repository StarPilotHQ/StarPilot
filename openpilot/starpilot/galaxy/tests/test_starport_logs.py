"""Android Auto logs in Logs & Diagnostics: the read-only reader and its authenticated list route."""

import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.starport_logs import StarPortLogs
from openpilot.starpilot.galaxy.server import make_server

SESSION = "\n".join(json.dumps(event) for event in (
  {"t": "2026-10-05T16:45:51.000+00:00", "event": "session_start", "receiver": "Honda CIVIC", "trigger": "auto"},
  {"t": "2026-10-05T16:45:52.000+00:00", "event": "stage", "stage": "rfcomm"},
  {"t": "2026-10-05T16:46:52.000+00:00", "event": "attempt_failed", "stage": "rfcomm", "error": "RFCOMM: timed out", "kind": "TimeoutError"},
  {"t": "2026-10-05T16:46:53.000+00:00", "event": "session_stop"},
)) + "\n"


def fill(directory: Path) -> None:
  directory.mkdir(parents=True)
  (directory / "session-000001-20261004-221339.jsonl").write_text(SESSION)
  (directory / "session-000002-20261005-094551.jsonl").write_text(SESSION)
  (directory / "car_ui.log").write_text("car view pipeline: nv12, async readback\n")
  (directory / "notes.txt").write_text("not an Android Auto log")


class StarPortLogsReaderTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    self.logs = self.root / "logs"
    fill(self.logs)
    self.config = self.root / "config.json"
    self.config.write_text(json.dumps({"config_version": 3, "receiver_address": "AA:BB:CC:DD:EE:FF", "receiver_name": "Honda CIVIC"}))
    self.reader = StarPortLogs(self.logs, self.config)

  def test_lists_sessions_newest_first_with_a_summary_and_only_known_logs(self):
    listing = self.reader.list()
    self.assertEqual([item["name"] for item in listing["sessions"]],
                     ["session-000002-20261005-094551.jsonl", "session-000001-20261004-221339.jsonl"])
    session = listing["sessions"][0]
    self.assertTrue(session["outcome"].startswith("failed"))
    self.assertEqual(session["trigger"], "auto")
    self.assertEqual([item["name"] for item in listing["others"]], ["car_ui.log"])

  def test_symlinked_logs_are_not_listed(self):
    (self.logs / "render_profile.txt").symlink_to(self.config)
    self.assertNotIn("render_profile.txt", [item["name"] for item in self.reader.list()["others"]])

  def test_bundle_is_a_zip_without_bluetooth_addresses(self):
    name, data = self.reader.bundle()
    self.assertRegex(name, r"^starpilot-starport-logs-\d{8}-\d{6}\.zip$")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
      names = archive.namelist()
      self.assertIn("logs/session-000002-20261005-094551.jsonl", names)
      self.assertIn("reports/session-000002-20261005-094551.json", names)
      self.assertIn("logs/car_ui.log", names)
      self.assertNotIn("logs/notes.txt", names)
      config = archive.read("config.json").decode()
    self.assertNotIn("AA:BB:CC:DD:EE:FF", config)

  def test_missing_directory_is_an_empty_listing(self):
    reader = StarPortLogs(self.root / "absent")
    self.assertEqual(reader.list(), {"schemaVersion": 1, "sessions": [], "others": []})


class StarPortLogsHTTPTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    fill(root / "logs")
    (root / "config.json").write_text("{}")
    self.owner = GalaxyAccessOwner(root / "access")
    self.reader = StarPortLogs(root / "logs", root / "config.json")
    self.server = make_server(port=0, owner=self.owner, starport_logs=self.reader)
    self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    self.thread.start()
    self.addCleanup(self.close_server)

  def close_server(self):
    self.server.shutdown()
    self.thread.join(timeout=2)
    self.server.server_close()

  def request(self, path, *, method="GET", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
    try:
      connection.request(method, path, body=body, headers={"Forwarded": "for=203.0.113.8", **(headers or {})})
      response = connection.getresponse()
      return response.status, response.read(), dict(response.getheaders())
    finally:
      connection.close()

  def login(self):
    status, _, headers = self.request("/api/auth/login", method="POST", body=json.dumps({"password": "password123"}),
                                      headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.server.server_port}"})
    self.assertEqual(status, 200)
    return headers["Set-Cookie"].split(";", 1)[0]

  def test_requires_a_session_before_reading_anything(self):
    self.assertTrue(self.owner.configure("password123", lambda: True))
    with mock.patch.object(self.reader, "list", wraps=self.reader.list) as listing:
      self.assertEqual(self.request("/api/starport/logs")[0], 401)
      listing.assert_not_called()

  def test_list(self):
    self.assertTrue(self.owner.configure("password123", lambda: True))
    cookie = {"Cookie": self.login()}
    status, body, headers = self.request("/api/starport/logs", headers=cookie)
    self.assertEqual(status, 200)
    self.assertEqual(headers["Cache-Control"], "no-store")
    self.assertEqual(len(json.loads(body)["sessions"]), 2)

  def test_logs_cannot_be_downloaded(self):
    self.assertTrue(self.owner.configure("password123", lambda: True))
    cookie = {"Cookie": self.login()}
    with mock.patch.object(self.reader, "bundle") as bundle:
      for path in ("/api/starport/logs/bundle", "/api/starport/logs/file/car_ui.log"):
        self.assertEqual(self.request(path, headers=cookie)[0], 404)
      bundle.assert_not_called()


if __name__ == "__main__":
  unittest.main()
