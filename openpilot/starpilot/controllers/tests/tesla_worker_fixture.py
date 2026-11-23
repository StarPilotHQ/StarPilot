"""The Tesla input worker's pump and the UI's receiver joined by an in-memory channel, for the worker tests."""
import collections
import json
from types import SimpleNamespace as NS

from openpilot.cereal import messaging
from openpilot.starpilot.common.car_control_config import PARAM, SPEED_UP, comma_speed
from openpilot.starpilot.controllers.tesla_input_worker import InputPump
from openpilot.starpilot.controllers.tesla_receiver import TeslaReceiver
from openpilot.starpilot.tests.port_settings_fixture import Saved
from openpilot.starpilot.tests.tesla_set_speed_fixture import switch, tesla

CUSTOM = 'custom'


class Messages(dict):
  def refresh(self, now, *, started=True):
    self['deviceState'] = NS(started=started, startedMonoTime=100_000_000)
    self['carState'] = NS(canValid=True, canTimeout=False, cruiseState=NS(enabled=getattr(self, 'engaged', False)))
    self.seen = self.alive = self.valid = dict.fromkeys(self, True)
    self.logMonoTime = {k: int(now * 1e9) for k in self}
    self.recv_time = dict.fromkeys(self, now)


class Channel:
  def __init__(self):
    self.queue = collections.deque()
    self.closed = False
    self.receives = 0

  def receive(self):
    self.receives += 1
    return self.queue.popleft() if self.queue else None

  def close(self):
    self.closed = True


def stalk(value, counter):
  return ((counter << 8) | (value << 12)).to_bytes(3, 'little')


def build(tmp_path, cp, *, comma=False):
  """The worker's pump and the UI's receiver joined by an in-memory channel, for the car `cp`. comma: the saved switch
  Comma can set speed on Tesla dashboard. One clock drives everything: CAN is stamped with it as the boot clock."""
  params = Saved(tmp_path)
  switch(params, comma)
  doc = {'version': 1, 'vehicle': 'TESLA_MODEL_3', 'bindings': {
    'right_wheel_left': {'key': CUSTOM, 'value': None, 'label': 'Custom'}}}
  params.put(PARAM, json.dumps(doc).encode(), block=True)
  clock = [1.]
  sm = Messages()
  sm.refresh(clock[0])
  calls = []
  channel = Channel()
  frames = collections.deque()
  actions = {SPEED_UP: NS(available=True, invoke_value=calls.append, invoke=None),
             CUSTOM: NS(available=True, invoke=lambda: calls.append(CUSTOM), invoke_value=None)}
  receiver = TeslaReceiver(params, lambda: actions, lambda: cp, clock=lambda: clock[0],
                           messages=lambda: sm, channel_factory=lambda: channel)
  assert receiver.channel is None, 'nothing starts before a supported Tesla is known'
  receiver.poll()
  pump = InputPump(params, channel.queue.append, clock=lambda: clock[0], boot_clock_ns=lambda: round(clock[0] * 1e9),
                   socket_factory=object, receive=lambda _: frames.popleft() if frames else None)
  pump.set_vehicle(cp)

  def feed(data, delta=.1, bus=1, valid=True, address=0x3c2, dispatch=True):
    clock[0] += delta
    sm.refresh(clock[0])
    ev = messaging.new_message('can', 1, valid=valid, logMonoTime=round(clock[0] * 1e9))
    ev.can[0] = {'address': address, 'dat': data, 'src': bus}
    frames.append(ev)
    pump.poll(sm)
    if dispatch:
      receiver.poll()

  return NS(params=params, cp=cp, doc=doc, clock=clock, sm=sm, calls=calls,
            channel=channel, frames=frames, receiver=receiver, pump=pump, feed=feed, actions=actions)


def save(s):
  s.params.put(PARAM, json.dumps(s.doc).encode(), block=True)


def selected_rig(tmp_path):
  """A Model 3 with the cable and Comma can set speed on Tesla dashboard on: the scroll sets the comma speed."""
  s = build(tmp_path, tesla(selected=True), comma=True)
  assert s.receiver.channel is s.channel and comma_speed(s.params, s.cp)
  return s
