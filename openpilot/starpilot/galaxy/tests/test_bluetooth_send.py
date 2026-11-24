"""Bluetooth Log: the controller report sent to the developer's Discord forum and its routes."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from openpilot.starpilot.galaxy import bluetooth_send
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.starport_send import SendBusy, SendRejected
from openpilot.starpilot.galaxy.tests.discord_fakes import SUMMARY, URL, Poster, Response


class BluetoothSendTest(unittest.TestCase):
  def test_posts_the_report_as_a_bluetooth_log_thread(self):
    post = Poster(Response(body={"channel_id": "777"}))
    with tempfile.TemporaryDirectory() as directory:
      self.assertIsNone(bluetooth_send.send(b"{}", "bluetooth-log.json", "Pad not found", SUMMARY, url=URL, post=post,
                                                    thread_dir=Path(directory), discord="starfan_42"))
    call = post.calls[0]
    content = call["payload"]["content"]
    self.assertTrue(content.startswith("**Bluetooth Log from "))
    self.assertIn("Car: HONDA_CIVIC_2022", content)
    self.assertIn("Follow-up: Discord `starfan_42`", content)
    self.assertNotIn("Head unit", content)
    self.assertTrue(call["payload"]["thread_name"].startswith("Bluetooth Log HONDA_CIVIC_2022 "))
    self.assertEqual(call["files"]["files[0]"], ("bluetooth-log.json", b"{}", "application/json"))

  def test_report_is_json(self):
    with mock.patch.object(bluetooth_send, "controller_input_report", return_value={"externalInputInterfaces": []}):
      name, data = bluetooth_send.report(system_snapshot=False)
    self.assertEqual((name, json.loads(data)), ("bluetooth-log.json", {"externalInputInterfaces": []}))

  def test_unset_webhook_refuses_and_override_wins(self):
    with tempfile.TemporaryDirectory() as directory:
      override = Path(directory) / "bluetooth_webhook_url"
      with mock.patch.object(bluetooth_send, "WEBHOOK_OVERRIDE_PATH", override), mock.patch.object(bluetooth_send, "_WEBHOOK_DATA", ""):
        self.assertEqual(bluetooth_send.webhook_url(), "")
        with self.assertRaisesRegex(SendRejected, "isn't set up"):
          bluetooth_send.send(b"{}", "bluetooth-log.json", "hi", SUMMARY, post=Poster(), discord="starfan_42")
        override.write_text(URL + "\n")
        self.assertEqual(bluetooth_send.webhook_url(), URL)


class BluetoothSendHTTPTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.owner = GalaxyAccessOwner(Path(temporary.name) / "access")
    self.job = mock.Mock()
    self.job.status.return_value = {"state": "idle", "message": "", "available": True, "messageLimit": 60}
    self.job.start.return_value = {"state": "sending", "message": "Collecting logs", "available": True, "messageLimit": 60}
    self.starport = mock.Mock()
    self.server = make_server(port=0, owner=self.owner, bluetooth_log_sender=self.job, starport_log_sender=self.starport)
    self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    self.thread.start()
    self.addCleanup(self.close_server)
    self.assertTrue(self.owner.configure("password123", lambda: True))

  def close_server(self):
    self.server.shutdown()
    self.thread.join(timeout=2)
    self.server.server_close()

  def request(self, path, *, method="GET", body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
    try:
      origin = {"Origin": f"http://127.0.0.1:{self.server.server_port}"} if method == "POST" else {}
      connection.request(method, path, body=body, headers={"Forwarded": "for=203.0.113.8", **origin, **(headers or {})})
      response = connection.getresponse()
      return response.status, response.read(), dict(response.getheaders())
    finally:
      connection.close()

  def post(self, body, cookie=None):
    headers = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}
    return self.request("/api/controllers/log/send", method="POST", body=json.dumps(body), headers=headers)

  def login(self):
    status, _, headers = self.request("/api/auth/login", method="POST", body=json.dumps({"password": "password123"}),
                                      headers={"Content-Type": "application/json"})
    self.assertEqual(status, 200)
    return headers["Set-Cookie"].split(";", 1)[0]

  def test_requires_a_session(self):
    self.assertEqual(self.request("/api/controllers/log/send")[0], 401)
    self.assertEqual(self.post({"message": "hi"})[0], 401)
    self.job.start.assert_not_called()

  def test_status_start_and_rejections(self):
    cookie = self.login()
    status, body, _ = self.request("/api/controllers/log/send", headers={"Cookie": cookie})
    self.assertEqual((status, json.loads(body)["state"]), (200, "idle"))
    status, body, _ = self.post({"message": "Pad not found", "discord": "starfan_42"}, cookie)
    self.assertEqual((status, json.loads(body)["state"]), (202, "sending"))
    self.job.start.assert_called_once_with("Pad not found", "starfan_42")
    self.starport.start.assert_not_called()
    self.assertEqual(self.post({"message": "hi"}, cookie)[0], 400)
    self.assertEqual(self.post({"message": "hi", "discord": "starfan_42", "extra": 1}, cookie)[0], 400)
    self.job.start.side_effect = SendRejected("Keep the message to 60 characters.")
    self.assertEqual(self.post({"message": "x" * 61, "discord": "starfan_42"}, cookie)[0], 400)
    self.job.start.side_effect = SendBusy("The Bluetooth Log is already being sent.")
    self.assertEqual(self.post({"message": "hi", "discord": "starfan_42"}, cookie)[0], 409)


if __name__ == "__main__":
  unittest.main()
