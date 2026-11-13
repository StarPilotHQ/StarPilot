#!/usr/bin/env python3
import time
import json
import jwt
from typing import cast
from pathlib import Path

from datetime import datetime, timedelta, UTC
from openpilot.common.api import api_get, get_key_pair
from openpilot.starpilot.connect.provider import active_provider
import os
import re
from openpilot.common.params import Params
from openpilot.common.spinner import Spinner
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.common.hardware import HARDWARE, PC
from openpilot.common.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog


UNREGISTERED_DONGLE_ID = "UnregisteredDevice"

def is_registered_device() -> bool:
  dongle = Params().get("DongleId")
  return dongle not in (None, UNREGISTERED_DONGLE_ID)


def register(show_spinner=False) -> str | None:
  """
  All devices built since March 2024 come with all
  info stored in /persist/. This is kept around
  only for devices built before then.

  With a backend update to take serial number instead
  of dongle ID to some endpoints, this can be removed
  entirely.
  """
  params = Params()
  if os.getenv('STARPILOT_CLOUD_DISABLED') == '1':
    return UNREGISTERED_DONGLE_ID
  if active_provider().name in ('konik', 'custom'):
    return register_konik(params)

  dongle_id: str | None = params.get("DongleId")
  if dongle_id is None and Path(Paths.persist_root()+"/comma/dongle_id").is_file():
    # not all devices will have this; added early in comma 3X production (2/28/24)
    with open(Paths.persist_root()+"/comma/dongle_id") as f:
      dongle_id = f.read().strip()

  # Create registration token, in the future, this key will make JWTs directly
  jwt_algo, private_key, public_key = get_key_pair()

  if not public_key:
    dongle_id = UNREGISTERED_DONGLE_ID
    cloudlog.warning("missing public key")
  elif dongle_id is None:
    if show_spinner:
      spinner = Spinner()
      spinner.update("registering device")

    # Block until we get the imei
    serial = HARDWARE.get_serial()
    start_time = time.monotonic()
    imei: str | None = None
    while imei is None:
      try:
        imei = HARDWARE.get_imei()
      except Exception:
        cloudlog.exception("Error getting imei, trying again...")
        time.sleep(1)

      if time.monotonic() - start_time > 60 and show_spinner:
        spinner.update(f"registering device - serial: {serial}, IMEI: {imei}")

    backoff = 0
    start_time = time.monotonic()
    while True:
      try:
        register_token = jwt.encode({'register': True, 'exp': datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)},
                                    cast(str, private_key), algorithm=jwt_algo)
        cloudlog.info("getting pilotauth")
        resp = api_get("v2/pilotauth/", method='POST', timeout=15,
                       imei=imei, imei2="", serial=serial, public_key=public_key, register_token=register_token)

        if resp.status_code in (402, 403):
          cloudlog.info(f"Unable to register device, got {resp.status_code}")
          dongle_id = UNREGISTERED_DONGLE_ID
        else:
          dongleauth = json.loads(resp.text)
          dongle_id = dongleauth["dongle_id"]
        break
      except NotImplementedError:
        # dependency issues with PyJWT will hang the registration test in backoff loop otherwise
        raise
      except Exception:
        cloudlog.exception("failed to authenticate")
        backoff = min(backoff + 1, 15)
        time.sleep(backoff)

      if time.monotonic() - start_time > 60 and show_spinner:
        spinner.update(f"registering device - serial: {serial}, IMEI: {imei}")

    if show_spinner:
      spinner.close()

  if dongle_id:
    params.put("DongleId", dongle_id, block=True)
    set_offroad_alert("Offroad_UnregisteredHardware", (dongle_id == UNREGISTERED_DONGLE_ID) and not PC)
  return dongle_id



def register_konik(params):
  known = params.get('DongleId')
  if known and re.fullmatch(r'[a-f0-9]{16}', known):
    return known
  try:
    algorithm, private, public = get_key_pair()
    if private is None or public is None:
      return UNREGISTERED_DONGLE_ID
    token = jwt.encode({'register': True, 'exp': datetime.now(UTC) + timedelta(hours=1)}, private, algorithm=algorithm)
    response = api_get('v2/pilotauth/', method='POST', timeout=10, imei=HARDWARE.get_imei() or '', imei2='',
                       serial=HARDWARE.get_serial(), public_key=public, register_token=token)
    identity = response.json().get('dongle_id') if response.status_code == 200 else None
    if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{16}', identity):
      return UNREGISTERED_DONGLE_ID
    params.put('DongleId', identity, block=True)
    return identity
  except Exception:
    cloudlog.warning('Konik registration unavailable; cloud services will retry')
    return UNREGISTERED_DONGLE_ID


if __name__ == "__main__":
  print(register())
