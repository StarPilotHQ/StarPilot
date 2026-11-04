"""Shared declared-context HTTP fixture; no physical vehicle evidence."""

from pathlib import Path
from types import SimpleNamespace
import http.client
import json
import tempfile
import threading
import unittest

from opendbc.car.structs import car
from opendbc.car.toyota.interface import CarInterface as ToyotaInterface
from opendbc.car.toyota.values import CAR as ToyotaCar
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import AuthorityContext, SettingsGateway
from openpilot.starpilot.galaxy.access import GalaxyAccessOwner
from openpilot.starpilot.galaxy.server import make_server


class MutableContext:
  def __init__(self, cp):
    self.value = AuthorityContext(True, cp, b"verified-cp")

  def sample(self):
    return self.value


class SettingsHttpFixture(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.params = Params(temporary.name)
    native = ToyotaInterface.get_non_essential_params(ToyotaCar.TOYOTA_COROLLA_TSS2)
    cp = SimpleNamespace(carFingerprint="TOYOTA_COROLLA_TSS2", openpilotLongitudinalControl=True,
      pcmCruise=False, notCar=False, dashcamOnly=False, passive=False,
      brand="toyota", steerControlType=native.steerControlType, carVin="",
      lateralTuning=native.lateralTuning, minSteerSpeed=native.minSteerSpeed, safetyConfigs=native.safetyConfigs,
      transmissionType=car.CarParams.TransmissionType.automatic)
    self.context = MutableContext(cp)
    self.gateway = SettingsGateway(self.params, self.context)
    self.access = GalaxyAccessOwner(Path(temporary.name) / "access")
    self.assertTrue(self.access.configure("password123", lambda: True))
    self.server = make_server(port=0, owner=self.access, settings=self.gateway,
                              parked=lambda: self.context.sample().parked)
    self.worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    self.worker.start()
    self.addCleanup(self.stop)

  def stop(self):
    self.server.shutdown()
    self.worker.join(timeout=2)
    self.server.server_close()

  def request(self, path, *, payload=None, cookie="", origin=None):
    connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
    headers = {"Cookie": cookie}
    if payload is not None:
      headers.update({"Content-Type": "application/json",
                      "Origin": origin if origin is not None else f"http://127.0.0.1:{self.server.server_port}"})
    try:
      connection.request("POST" if payload is not None else "GET", path,
                         body=json.dumps(payload) if payload is not None else None, headers=headers)
      result = connection.getresponse()
      return result.status, json.loads(result.read()), dict(result.getheaders())
    finally:
      connection.close()

  def login(self):
    status, _, headers = self.request("/api/auth/login", payload={"password": "password123"})
    self.assertEqual(status, 200)
    return headers["Set-Cookie"].split(";", 1)[0]

