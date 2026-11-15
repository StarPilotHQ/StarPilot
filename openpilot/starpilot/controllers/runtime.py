import logging
import time

from openpilot.starpilot.controllers.input import InputReader
from openpilot.starpilot.controllers.owner import ControllerOwner
from openpilot.starpilot.controllers.transport import ControllerService
from openpilot.starpilot.galaxy.settings import LiveContextSource

LOGGER = logging.getLogger(__name__)


class ControllerRuntime:
  def __init__(self, params, *, actions, favorites, invoke_favorite, authority=None, reader=None, socket_path=None,
               vehicle=lambda: None, messages=lambda: None, input_capture=lambda: None, speed_actions=None, keyed_actions=None):
    self.authority = authority if authority is not None else LiveContextSource(params)
    self.reader = reader if reader is not None else InputReader()
    self.owner = ControllerOwner(params, parked=self.authority.parked, actions=actions, favorites=favorites,
                                 invoke_favorite=invoke_favorite)
    from openpilot.starpilot.controllers.tesla_receiver import TeslaReceiver
    self.tesla = TeslaReceiver(params,actions,vehicle,messages=messages, speed_actions=speed_actions,
                               keyed_actions=keyed_actions)
    self.service = ControllerService(self.owner, socket_path)
    self.input_capture = input_capture
    self._closed = False
    self._next_warm = 0.0
    self._warm_until = time.monotonic() + 2.0
    try:
      self.service.start()
    except (OSError, RuntimeError):
      LOGGER.exception('Controller input could not start')
      self.close()

  def poll(self):
    if self._closed:
      return
    try:
      now = time.monotonic()
      if now >= self._next_warm:
        self.authority.parked()
        self._next_warm = now + (0.1 if now < self._warm_until else 1.0)
      presses = self.reader.poll(time.monotonic_ns())
      self.owner.set_devices(self.reader.devices())
      self.owner.tick()
      # Process input before configuration so queued presses cannot activate a new binding.
      capture = self.input_capture()
      for press in presses:
        if capture is None:
          self.owner.feed(press)
        else:
          self.owner.feed(press, capture=capture)
      try:
        self.tesla.poll()
      except Exception:
        LOGGER.exception('Tesla input stopped')
        self.tesla.close()
        self.tesla.next_start = time.monotonic() + 2.
      self.service.poll()
    except Exception:
      LOGGER.exception('Controller input stopped')
      self.close()

  def close(self):
    if self._closed:
      return
    self._closed = True
    self.tesla.close()
    try:
      self.service.close()
    finally:
      try:
        self.reader.close()
      finally:
        self.authority.close()
