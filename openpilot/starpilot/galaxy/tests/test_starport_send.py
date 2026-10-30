"""Send StarPort Logs: the Discord upload, its per-device thread cache, the background job and its routes."""

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from openpilot.starpilot.galaxy import starport_send
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server
from openpilot.starpilot.galaxy.starport_send import (DISCORD_FILE_LIMIT, MESSAGE_LIMIT, SendBusy, SendRejected, StarPortLogSender,
                                                      clean_message, send, thread_title)

URL = "https://discord.example/api/webhooks/1/token"
SUMMARY = {"device_id": "0123456789abcdef", "car_fingerprint": "HONDA_CIVIC_2022", "head_unit": "Honda Display Audio",
           "branch": "Dom", "commit": "abcdef1234567890"}


class Response:
  def __init__(self, status_code=200, body=None):
    self.status_code, self.body = status_code, body if body is not None else {"channel_id": "555"}

  def json(self):
    return self.body


class Poster:
  def __init__(self, *responses):
    self.responses, self.calls = list(responses), []

  def __call__(self, url, data, files, timeout):
    self.calls.append({"url": url, "payload": json.loads(data["payload_json"]), "files": files})
    return self.responses.pop(0)


def query(call):
  return {key: values[0] for key, values in parse_qs(urlsplit(call["url"]).query).items()}


class SendTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.threads = Path(temporary.name)

  def send(self, post, note="Dropped at the light", data=b"zip"):
    return send(data, "logs.zip", note, SUMMARY, url=URL, post=post, thread_dir=self.threads)

  def test_first_send_opens_a_device_thread_and_later_sends_reuse_it(self):
    first = Poster(Response(body={"channel_id": "555"}))
    self.assertIsNone(self.send(first))
    call = first.calls[0]
    self.assertEqual(call["payload"]["thread_name"], thread_title(SUMMARY))
    self.assertTrue(call["payload"]["thread_name"].startswith("HONDA_CIVIC_2022 "))
    self.assertEqual(call["payload"]["allowed_mentions"], {"parse": []})
    self.assertIn("Note: Dropped at the light", call["payload"]["content"])
    self.assertIn("Head unit: Honda Display Audio", call["payload"]["content"])
    self.assertNotIn(SUMMARY["device_id"], call["payload"]["content"])
    self.assertEqual(query(call), {"wait": "true"})
    self.assertEqual(call["files"]["files[0]"], ("logs.zip", b"zip", "application/zip"))
    self.assertNotIn("token", "".join(path.read_text() for path in self.threads.iterdir()))

    second = Poster(Response())
    self.assertIsNone(self.send(second))
    self.assertEqual(query(second.calls[0])["thread_id"], "555")
    self.assertNotIn("thread_name", second.calls[0]["payload"])

  def test_deleted_thread_is_forgotten_and_a_new_one_opened(self):
    self.send(Poster(Response(body={"channel_id": "555"})))
    poster = Poster(Response(404, {"code": 10003}), Response(body={"channel_id": "777"}))
    self.assertIsNone(self.send(poster))
    self.assertEqual(query(poster.calls[0])["thread_id"], "555")
    self.assertNotIn("thread_id", query(poster.calls[1]))
    self.assertEqual(poster.calls[1]["payload"]["thread_name"], thread_title(SUMMARY))
    third = Poster(Response())
    self.send(third)
    self.assertEqual(query(third.calls[0])["thread_id"], "777")

  def test_refusals_and_oversize_raise_without_posting(self):
    with self.assertRaisesRegex(SendRejected, "HTTP 401: Invalid Webhook Token"):
      self.send(Poster(Response(401, {"message": "Invalid Webhook Token"})))
    poster = Poster()
    with self.assertRaisesRegex(SendRejected, "10 MB"):
      self.send(poster, data=b"x" * (DISCORD_FILE_LIMIT + 1))
    self.assertEqual(poster.calls, [])
    with self.assertRaisesRegex(SendRejected, "isn't set up"):
      send(b"zip", "logs.zip", "", SUMMARY, url="", post=poster, thread_dir=self.threads)

  def test_message_is_limited_and_single_line(self):
    self.assertEqual(MESSAGE_LIMIT, 60)
    self.assertEqual(clean_message("  car\nwent black  "), "car went black")
    self.assertEqual(clean_message("x" * 60), "x" * 60)
    for bad in ("x" * 61, None, 5):
      with self.assertRaises(SendRejected):
        clean_message(bad)

  def test_built_in_webhook_and_override(self):
    self.assertTrue(starport_send.webhook_url(self.threads / "absent").startswith("https://discord.com/api/webhooks/"))
    override = self.threads / "webhook_url"
    override.write_text(f"{URL}\n")
    self.assertEqual(starport_send.webhook_url(override), URL)


class SenderJobTest(unittest.TestCase):
  def wait(self, job):
    deadline = time.monotonic() + 2
    while job.status()["state"] == "sending" and time.monotonic() < deadline:
      time.sleep(0.01)
    return job.status()

  def test_sends_in_the_background_and_drops_the_snapshot_when_too_big(self):
    bundles, sent = [], []

    def bundle(system_snapshot=True):
      bundles.append(system_snapshot)
      return "logs.zip", b"x" * (DISCORD_FILE_LIMIT + 1 if system_snapshot else 10)

    job = StarPortLogSender(bundle, sender=lambda *args: sent.append(args), summary=lambda: SUMMARY, url=lambda: URL)
    self.assertEqual(job.start("hello")["state"], "sending")
    status = self.wait(job)
    self.assertEqual(status["state"], "sent")
    self.assertIn("snapshot was left out", status["message"])
    self.assertEqual(bundles, [True, False])
    self.assertEqual(sent[0][2], "hello")

  def test_one_send_at_a_time_and_failures_are_reported(self):
    release = threading.Event()

    def sender(*_):
      release.wait(2)
      raise SendRejected("Discord refused the logs (HTTP 500).")

    job = StarPortLogSender(lambda **_: ("logs.zip", b"zip"), sender=sender, summary=lambda: SUMMARY, url=lambda: URL)
    job.start("")
    with self.assertRaises(SendBusy):
      job.start("again")
    release.set()
    self.assertEqual(self.wait(job), {"state": "failed", "message": "Discord refused the logs (HTTP 500).", "available": True,
                                      "messageLimit": 60})


class SendHTTPTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.owner = GalaxyAccessOwner(Path(temporary.name) / "access")
    self.job = mock.Mock()
    self.job.status.return_value = {"state": "idle", "message": "", "available": True, "messageLimit": 60}
    self.job.start.return_value = {"state": "sending", "message": "Collecting logs", "available": True, "messageLimit": 60}
    self.server = make_server(port=0, owner=self.owner, starport_log_sender=self.job)
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
    return self.request("/api/starport/logs/send", method="POST", body=json.dumps(body), headers=headers)

  def login(self):
    status, _, headers = self.post_login()
    self.assertEqual(status, 200)
    return headers["Set-Cookie"].split(";", 1)[0]

  def post_login(self):
    return self.request("/api/auth/login", method="POST", body=json.dumps({"password": "password123"}),
                        headers={"Content-Type": "application/json"})

  def test_requires_a_session(self):
    self.assertEqual(self.request("/api/starport/logs/send")[0], 401)
    self.assertEqual(self.post({"message": "hi"})[0], 401)
    self.job.start.assert_not_called()

  def test_status_start_and_rejections(self):
    cookie = self.login()
    status, body, _ = self.request("/api/starport/logs/send", headers={"Cookie": cookie})
    self.assertEqual((status, json.loads(body)["state"]), (200, "idle"))

    status, body, _ = self.post({"message": "Black screen after unlock"}, cookie)
    self.assertEqual((status, json.loads(body)["state"]), (202, "sending"))
    self.job.start.assert_called_once_with("Black screen after unlock")

    self.assertEqual(self.post({"message": "hi", "extra": 1}, cookie)[0], 400)
    self.job.start.side_effect = SendRejected("Keep the message to 60 characters.")
    self.assertEqual(self.post({"message": "x" * 61}, cookie)[0], 400)
    self.job.start.side_effect = SendBusy("StarPort logs are already being sent.")
    self.assertEqual(self.post({"message": "hi"}, cookie)[0], 409)


if __name__ == "__main__":
  unittest.main()
