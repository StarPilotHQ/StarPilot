"""Send the controller input report to the developer's Discord forum, beside StarPort logs."""

import json

from openpilot.starpilot.controllers.input import controller_input_report
from openpilot.starpilot.galaxy import starport_send
from openpilot.starpilot.system.starport import identity as identity_store

TITLE = "Bluetooth Log"
WEBHOOK_OVERRIDE_PATH = identity_store.DATA_DIR / "bluetooth_webhook_url"
# Obfuscation only; device-local configuration overrides this built-in webhook.
_WEBHOOK_KEY = b"bluetooth-log"
_WEBHOOK_DATA = "".join(("ChgBFQdVQFsMRB8MCBAIWwYbAkAVGERDGAIABBoKHxxARV0aWFdTU11GVU1aVkZcFFhdXk0PFAMMNlgjG1",
                         "RdDiMjOSVURjZaPTxjNStXJB0NExAhDgZZWi0cElEuRTUTHBotWmwqVyE0LkwjWVkoAQ9LVAIeJAFYSA=="))


def webhook_url() -> str:
  return starport_send.webhook_url(WEBHOOK_OVERRIDE_PATH, _WEBHOOK_KEY, _WEBHOOK_DATA)


def summary() -> dict:
  from openpilot.starpilot.system.starport.bugsink_transport import device_metadata
  result = device_metadata()
  try:
    result["car_fingerprint"] = starport_send.car_fingerprint()
  except Exception as error:
    result["car_fingerprint_error"] = str(error)
  return result


def report(**_) -> tuple[str, bytes]:
  return "bluetooth-log.json", json.dumps(controller_input_report(), indent=2).encode()


def send(data: bytes, name: str, note: str, details: dict, *, discord: str, url: str | None = None, post=None, thread_dir=None) -> str | None:
  return starport_send.send(data, name, note, details, url=webhook_url() if url is None else url, post=post, thread_dir=thread_dir,
                            discord=discord, heading=TITLE, fields=(), content_type="application/json",
                            title=f"{TITLE} {starport_send.thread_title(details)}"[:100])


def sender() -> starport_send.StarPortLogSender:
  return starport_send.StarPortLogSender(report, sender=send, summary=summary, url=webhook_url,
                                         busy="The Bluetooth Log is already being sent.")
