"""Fake Discord webhook responses shared by the log send tests."""

import json

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
