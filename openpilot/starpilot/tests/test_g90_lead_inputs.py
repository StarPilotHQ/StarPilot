"""Actual scoped controller/provider callers with explicit synthetic radar transport."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from opendbc.can import CANParser
from opendbc.car import Bus, structs
from opendbc.car.hyundai.g90_lead import G90LeadState
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_g90_lead import observation
from opendbc.car.hyundai.tests.test_g90_longitudinal import params
from opendbc.car.hyundai.values import CAR, DBC
from openpilot.starpilot.controller_extensions import configure_controller
from openpilot.starpilot.longitudinal import g90_lead, radar_lead_context


class RadarMessages(dict):
  def __init__(self, auto=False):
    super().__init__(radarState=SimpleNamespace(
      leadOne=SimpleNamespace(present=True, dRel=26., vRel=-.5),
      radarErrors=SimpleNamespace(canError=False, radarFault=False, wrongConfig=False, radarUnavailableTemporary=False)),
      carState=SimpleNamespace(canValid=True, canTimeout=False),
      deviceState=SimpleNamespace(started=True, startedMonoTime=500_000_000))
    self.now = 1_000_000_000
    self.auto = auto
    self.seen = dict.fromkeys(self, True)
    self.alive = dict.fromkeys(self, True)
    self.valid = dict.fromkeys(self, True)
    self.logMonoTime = {}
    self.recv_time = {}
    self.refresh(*self)

  def refresh(self, *names):
    for name in names:
      self.logMonoTime[name] = self.now - 1000
      self.recv_time[name] = (self.now - 500) / 1e9

  def update(self, *args, **kwargs):
    if self.auto:
      self.now += 10_000_000
      self.refresh(*self)


class TestG90LeadInputs(unittest.TestCase):
  def provider(self, messages):
    with patch.object(radar_lead_context.messaging, 'SubMaster', return_value=messages) as constructor:
      provider = g90_lead.G90LeadInputs()
    constructor.assert_called_once_with(['radarState', 'deviceState', 'carState'])
    return provider

  def sample(self, provider, messages):
    with patch.object(radar_lead_context, 'clock_pair_ns', side_effect=lambda messages=messages: (messages.now, messages.now+20_000_000_000)):
      return provider.update()

  def test_staggered_producers_recover_without_a_moving_floor(self):
    messages = RadarMessages()
    provider = self.provider(messages)
    self.assertIsNone(self.sample(provider, messages))
    floor = provider.floor_ns
    for service in ('deviceState', 'carState', 'radarState'):
      messages.now += 10_000_000
      messages.refresh(service)
      result = self.sample(provider, messages)
      self.assertEqual(provider.floor_ns, floor)
    assert result is not None
    messages.now += 10_000_000
    assert self.sample(provider, messages) is not None
    messages.valid['radarState'] = False
    self.assertIsNone(self.sample(provider, messages))
    revoked_floor = provider.floor_ns
    messages.valid['radarState'] = True
    for service in ('radarState', 'carState', 'deviceState'):
      messages.now += 10_000_000
      messages.refresh(service)
      result = self.sample(provider, messages)
      self.assertEqual(provider.floor_ns, revoked_floor)
    assert result is not None

  def test_stale_invalid_and_expected_transport_errors_are_optional(self):
    for failure in ('stale', 'future', 'receipt', 'radar_fault', 'nonfinite', 'transport'):
      messages = RadarMessages(auto=True)
      provider = self.provider(messages)
      self.sample(provider, messages)
      assert self.sample(provider, messages) is not None
      messages.auto = False
      if failure == 'stale':
        messages.now += 150_000_001
      elif failure == 'future':
        messages.logMonoTime['radarState'] = messages.now+1
      elif failure == 'receipt':
        messages.recv_time['radarState'] = (messages.now+1)/1e9
      elif failure == 'radar_fault':
        messages['radarState'].radarErrors.radarFault = True
      elif failure == 'nonfinite':
        messages['radarState'].leadOne.vRel = float('nan')
      else:
        vars(messages)['update'] = lambda timeout: (_ for _ in ()).throw(OSError('explicit transport failure'))
      self.assertIsNone(self.sample(provider, messages), failure)
    ci = CarInterface(params())
    with patch.object(radar_lead_context.messaging, 'SubMaster', side_effect=OSError('explicit constructor failure')), \
         patch('openpilot.starpilot.controller_extensions.cloudlog.exception') as diagnostic:
      configure_controller(ci, None)
    self.assertIsNone(ci.CC.g90_lead_inputs)  # Existing fallback wire, not fresh lead evidence.
    diagnostic.assert_called_once()
    state = G90LeadState()
    for _ in range(50):
      state.update(observation(), False)
    self.assertFalse(state.update(None, True).lead_visible)  # Missing source is not a fake20m lead.
    self.assertFalse(state.update(observation(drive=2), False).lead_visible)

  def test_provider_clock_and_drive_epochs_require_new_staggered_sources(self):
    messages = RadarMessages(auto=True)
    provider = self.provider(messages)
    self.sample(provider, messages)
    assert self.sample(provider, messages) is not None
    messages.auto = False
    offset = 40_000_000_000
    def sample_epoch():
      with patch.object(radar_lead_context, 'clock_pair_ns', side_effect=lambda: (messages.now, messages.now+offset)):
        return provider.update()
    self.assertIsNone(sample_epoch())  # Suspend changed BOOTTIME/MONOTONIC offset.
    floor = provider.floor_ns
    self.assertIsNone(sample_epoch())  # The old cache cannot reauthorize.
    for service in ('deviceState', 'radarState', 'carState'):
      messages.now += 10_000_000
      messages.refresh(service)
      result = sample_epoch()
      self.assertEqual(provider.floor_ns, floor)
    assert result is not None
    messages.now += 10_000_000
    messages['deviceState'].startedMonoTime = messages.now-2000
    messages.refresh('deviceState')
    self.assertIsNone(sample_epoch())  # A new drive cannot reuse old radar/car.
    floor = provider.floor_ns
    for service in ('radarState', 'carState', 'deviceState'):
      messages.now += 10_000_000
      messages.refresh(service)
      result = sample_epoch()
      self.assertEqual(provider.floor_ns, floor)
    assert result is not None

  def test_actual_controller_provider_bind_and_100hz_to_50hz_wire(self):
    for car in (CAR.GENESIS_G90, CAR.HYUNDAI_KONA_EV, CAR.HYUNDAI_SONATA, CAR.KIA_STINGER_2022):
      cp = params(car)
      ci = CarInterface(cp)
      ci.update([])
      messages = RadarMessages(auto=True)
      with patch.object(radar_lead_context.messaging, 'SubMaster', return_value=messages):
        configure_controller(ci, None)
      self.assertIsInstance(ci.CC.g90_lead_inputs, g90_lead.G90LeadInputs)
      command = structs.CarControl(enabled=True, longActive=True)
      command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
      parser = CANParser(DBC[car][Bus.pt], [('SCC11', 0), ('SCC14', 0)], 0)
      obj = parser.dbc.name_to_msg['SCC14'].sigs['ObjDistStat']
      self.assertEqual((obj.start_bit, obj.size, obj.factor, obj.offset), (42, 2, 1, 0))
      captures = {}
      for frame in range(52):
        with patch.object(radar_lead_context, 'clock_pair_ns', side_effect=lambda messages=messages: (messages.now, messages.now+20_000_000_000)):
          _, packets = ci.apply(command.as_reader(), (frame+1)*10_000_000)
        scc = [packet for packet in packets if packet[0] in (0x420, 0x389)]
        self.assertEqual(bool(scc), frame % 2 == 0)
        if scc:
          parser.update([(1_000_000_000+frame*10_000_000, scc)])
          captures[frame] = (parser.vl['SCC11']['ObjValid'], parser.vl['SCC14']['ObjGap'])
      self.assertEqual(captures[48], (0, 0))
      self.assertEqual(captures[50], (1, 4))
      self.assertEqual(parser.vl['SCC11']['ACC_ObjDist'], 26)
      self.assertEqual(parser.vl['SCC11']['ACC_ObjRelSpd'], -.5)
      self.assertEqual(parser.vl['SCC14']['ObjDistStat'], 2)

