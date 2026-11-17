from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from openpilot.cereal import messaging
from openpilot.common.params import Params
from opendbc.car.structs import car
from opendbc.can import CANPacker, CANParser
from opendbc.car.toyota import toyotacan
from openpilot.selfdrive.car.cruise import VCruiseHelper, V_CRUISE_MAX, V_CRUISE_MIN
from openpilot.starpilot.controllers.cruise_action import CruiseActionPublisher, CruiseActionConsumer
from openpilot.starpilot.controllers.toyota_cruise import capability, ToyotaCruisePreference
from openpilot.starpilot.longitudinal.cruise_intervals import CruiseIntervals, read_cruise_intervals
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, FeatureSettingsRequest, row_change


class State(dict):
  def __init__(self, now):
    super().__init__(deviceState=SimpleNamespace(started=True, startedMonoTime=now-1_000_000_000),
                     carState=SimpleNamespace(canValid=True, canTimeout=False),
                     carControl=SimpleNamespace(enabled=True,longActive=False), selfdriveState=SimpleNamespace(enabled=True))
    self.logMonoTime = dict.fromkeys(self, now - 10000000)
    self.recv_time = dict.fromkeys(self, (now - 5000000) / 1000000000.0)
    self.seen = self.alive = self.valid = dict.fromkeys(self, True)

  def update(self, *args, **kwargs):
    pass


class Publisher:
  def send(self, topic, msg):
    self.topic, self.message = topic, messaging.log_from_bytes(msg.to_bytes())


def cp(pcm=False, brand='mock'):
  return SimpleNamespace(pcmCruise=pcm, brand=brand, carFingerprint='TEST', openpilotLongitudinalControl=True,
                         passive=False, dashcamOnly=False, notCar=False)


class TestControllerCruise(unittest.TestCase):
  def setUp(self):
    self.now = 5_000_000_000
    self.cp, self.sm = cp(), State(self.now)
    self.cs = car.CarState.new_message(canValid=True, vEgo=20, cruiseState={'available':True})
    self.helper = VCruiseHelper(self.cp)
    self.helper.v_cruise_kph = 80
    self.publisher, self.sender, self.consumer = Publisher(), CruiseActionPublisher(), CruiseActionConsumer()

  def request(self, increase=True):
    self.assertTrue(self.sender.dispatch(increase,self.sm,self.cp,self.publisher,now_ns=self.now))
    return self.publisher.message

  def apply(self, msg):
    return self.consumer.apply(msg,self.cp,self.cs,self.sm,self.helper,now_ns=self.now,is_metric=True,enabled=True)

  def test_real_message_changes_card_owned_speed_without_buttons_and_replay(self):
    msg=self.request()
    self.assertTrue(self.apply(msg))
    self.assertEqual(self.helper.v_cruise_kph,81)
    self.assertEqual(self.helper.v_cruise_cluster_kph,81)
    self.assertEqual(list(self.cs.buttonEvents),[])
    self.assertEqual(self.helper.slc_cruise_change,(80/3.6,81/3.6,'accel',False))
    self.now+=100_000_000
    self.assertFalse(self.apply(msg))

  def test_expiry_drive_pcm_can_and_control_denials(self):
    msg=self.request()
    self.now+=300_000_000
    self.assertFalse(self.apply(msg))
    self.now=5_000_000_000
    for field in ('pcmCruise','passive','dashcamOnly','notCar'):
      setattr(self.cp,field,True)
      self.assertFalse(self.apply(msg))
      setattr(self.cp,field,False)
    self.cs.canValid=False
    self.assertFalse(self.apply(msg))
    self.cs.canValid=True
    self.sm['carControl'].enabled=False
    self.assertFalse(self.apply(msg))
    self.sm['carControl'].enabled=True
    self.sm['deviceState'].startedMonoTime+=1
    self.assertFalse(self.apply(msg))

  def test_normal_steps_units_bounds_override_and_standstill(self):
    up,down=car.CarState.ButtonEvent.Type.accelCruise,car.CarState.ButtonEvent.Type.decelCruise
    self.helper.adjust_v_cruise(up,self.cs,False)
    self.assertEqual(self.helper.v_cruise_kph,81.6)
    self.helper.v_cruise_kph=V_CRUISE_MAX
    self.assertFalse(self.helper.adjust_v_cruise(up,self.cs,True))
    self.helper.v_cruise_kph=V_CRUISE_MIN
    self.assertFalse(self.helper.adjust_v_cruise(down,self.cs,True))
    self.helper.v_cruise_kph=60
    self.cs.gasPressed=True
    self.helper.adjust_v_cruise(down,self.cs,True)
    self.assertEqual(self.helper.v_cruise_kph,72)
    self.cs.cruiseState.standstill=True
    self.assertFalse(self.helper.adjust_v_cruise(up,self.cs,True))

  def test_actual_card_consumes_action_and_publishes_same_set_speed(self):
    from openpilot.selfdrive.car import card
    request = self.request()
    instance = card.Car.__new__(card.Car)
    instance.CP = self.cp
    vars(instance)['sm'] = self.sm
    instance.CC_prev = car.CarControl(enabled=True)
    instance.CS_prev = self.cs
    vars(instance)['CI'] = SimpleNamespace(CC=None, CS=SimpleNamespace(), update=lambda packets:self.cs)
    instance.car_gps_publisher = card.CarGpsPublisher()
    self.enterContext(patch.object(instance, 'pm', SimpleNamespace(send=Mock()), create=True))
    vars(instance)['RI'] = SimpleNamespace(update=lambda packets:None)
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    instance.params = Params(directory.name)
    instance.can_sock = object()
    instance.can_rcv_cum_timeout_counter = 0
    instance.ioniq6_long_prearmed = False
    instance.conditional_replay = instance.slc_replay = instance.curve_replay = False
    instance.switchback_button_tracker = instance.switchback_settings_owner = None
    instance.wheel_publisher = card.WheelPublisher(instance.params)
    instance.aol_card_intent = None
    instance.is_metric = True
    instance.experimental_mode = False
    instance.v_cruise_helper = self.helper
    instance.slc_command_sock = None
    instance.controller_cruise_sock = object()
    instance.controller_cruise_consumer = self.consumer
    with (patch.object(card.messaging,'drain_sock_raw',return_value=[messaging.new_message('can',1).to_bytes()]),
          patch.object(card.messaging,'recv_one_or_none',side_effect=[request,None]),
          patch.object(card.time,'monotonic_ns',return_value=self.now)):
      selected,_ = instance.state_update()
    self.assertEqual(selected.vCruise,81)
    self.assertEqual(selected.vCruiseCluster,81)
    self.assertEqual(list(selected.buttonEvents),[])
    assert instance.v_cruise_helper.slc_cruise_change is not None
    self.assertEqual(instance.v_cruise_helper.slc_cruise_change[2],'accel')

  def test_toyota_original_signal_and_default_packet(self):
    packer=CANPacker('toyota_nodsu_pt_generated')
    parser=CANParser('toyota_nodsu_pt_generated',[('ACC_CONTROL',50)],0)
    args=(packer,0.2,False,True,False,True,1,False,2)
    normal=toyotacan.create_accel_command(*args)
    self.assertEqual(normal,toyotacan.create_accel_command(*args,reverse_cruise=False))
    parser.update([self.now,[normal]])
    self.assertEqual(parser.vl['ACC_CONTROL']['ALLOW_LONG_PRESS'],1)
    parser.update([self.now+1,[toyotacan.create_accel_command(*args,reverse_cruise=True)]])
    self.assertEqual(parser.vl['ACC_CONTROL']['ALLOW_LONG_PRESS'],2)
    self.assertEqual(parser.vl['ACC_CONTROL']['ACCEL_CMD'],0.2)

  def test_actual_settings_capability_dependency_and_runtime_refresh(self):
    with tempfile.TemporaryDirectory() as directory:
      params=Params(directory)
      vehicle=car.CarParams.new_message(pcmCruise=True, brand='toyota', carFingerprint='TEST',
                                       openpilotLongitudinalControl=True)
      owner=FeatureSettingsOwner(params,lambda group:group in ('long', 'toyota_cruise'),
                                 vehicle_fingerprint=lambda:vehicle.carFingerprint,
                                 vehicle_params=lambda:vehicle)
      def rows():
        return {r.key:r for r in owner.snapshot(FeaturePage.WHEEL,parked=True,system_long=True,
                                               lateral_context=False,metric=True).rows}
      self.assertNotIn('CustomCruise',rows())
      self.assertNotIn('CustomCruiseLong',rows())
      self.assertNotIn('QOLLongitudinal', rows())
      owner.configuration_longitudinal = lambda: True
      self.assertIsNone(owner._cruise_capability())
      for key in ('CustomCruise', 'CustomCruiseLong'):
        stale = FeatureSettingsRequest(key, None, '2', confirmation=True, vehicle_fingerprint=vehicle.carFingerprint,
                                       capability=(vehicle.carFingerprint, True, False, False, False, False),
                                       dependencies=(('IsMetric', None),), display_unit='mph')
        self.assertFalse(owner.apply(stale))
        self.assertFalse(Path(params.get_param_path(key)).exists())
      change=row_change(rows()['ReverseCruise'])
      assert change is not None
      self.assertTrue(owner.apply(change))
      self.assertEqual(params.get_bool('ReverseCruise'),True)
      source=ToyotaCruisePreference(vehicle,params)
      self.assertTrue(source.update())
      vehicle.pcmCruise=False
      self.assertIsNone(capability(vehicle))
      self.assertFalse(owner.apply(change))
      source.next_read=0
      self.assertFalse(source.update())
      vehicle.pcmCruise=True
      vehicle.openpilotLongitudinalControl=False
      self.assertIsNone(capability(vehicle))

  def test_toyota_native_and_software_steps_share_saved_value_validation(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      vehicle = cp(True, 'toyota')
      for choice, safe, expected in ((b'0', b'0', False), (b'1', b'0', True), (b'1', b'1', False),
                                     (b'invalid', b'0', False), (b'1', b'invalid', False), (b'1' * 9, b'0', False)):
        with self.subTest(choice=choice, safe=safe):
          Path(params.get_param_path('ReverseCruise')).write_bytes(choice)
          Path(params.get_param_path('SafeMode')).write_bytes(safe)
          self.assertEqual(ToyotaCruisePreference(vehicle, params).update(), expected)
          self.assertEqual(read_cruise_intervals(params, vehicle), CruiseIntervals(5, 1) if expected else CruiseIntervals())
