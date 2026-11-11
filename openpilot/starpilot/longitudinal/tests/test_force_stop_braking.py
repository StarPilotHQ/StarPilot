from types import SimpleNamespace as NS
from unittest import TestCase
import math
import tempfile
from unittest.mock import patch

from openpilot.starpilot.longitudinal.force_stop_braking import current_braking, distance_target

BASE = 100_000_000_000
NOW = BASE+1_000_000_000


def inputs():
  class Messages(dict):
    pass
  sm = Messages(deviceState=NS(started=True,startedMonoTime=BASE),
                carState=NS(canValid=True,canTimeout=False,brakePressed=False,gasPressed=False),
                longitudinalPlan=NS(shouldStop=True,modelMonoTime=NOW-20_000_000),
                starpilotLongitudinalPlan=NS(version=1,forceStopBraking=True,driveStartMonoTime=BASE,
                  forceStopEpisodeMonoTime=BASE+1,modelMonoTime=NOW-20_000_000,sourcePlanMonoTime=NOW-10_000_000))
  sm.logMonoTime = dict.fromkeys(sm,NOW-10_000_000)
  sm.recv_time = dict.fromkeys(sm,(NOW-5_000_000)/1e9)
  sm.valid = dict.fromkeys(sm,True)
  sm.alive = dict.fromkeys(sm,True)
  sm.seen = dict.fromkeys(sm,True)
  return sm


class ForceStopBrakingTests(TestCase):
  def test_only_current_matching_stop_intent_qualifies(self):
    self.assertTrue(current_braking(inputs(),NOW))
    for field,value in [('version',0),('forceStopBraking',False),('driveStartMonoTime',BASE-1),
                        ('forceStopEpisodeMonoTime',BASE),('forceStopEpisodeMonoTime',NOW+1),
                        ('modelMonoTime',NOW-21_000_000),('sourcePlanMonoTime',NOW-11_000_000)]:
      with self.subTest(field=field,value=value):
        sm = inputs()
        setattr(sm['starpilotLongitudinalPlan'],field,value)
        self.assertFalse(current_braking(sm,NOW))

  def test_withdrawal_override_or_stale_source_keeps_original_stop(self):
    for field in ['brakePressed','gasPressed','canTimeout']:
      sm = inputs()
      setattr(sm['carState'],field,True)
      self.assertFalse(current_braking(sm,NOW))
    for name in inputs():
      for bad in ['seen','valid','alive']:
        sm = inputs()
        getattr(sm,bad)[name] = False
        self.assertFalse(current_braking(sm,NOW))
    sm = inputs()
    sm['longitudinalPlan'].shouldStop = False
    self.assertFalse(current_braking(sm,NOW))
    self.assertFalse(current_braking(inputs(),NOW+2_000_000_000))

  def test_distance_target_strengthens_with_less_room_or_more_latency(self):
    self.assertLess(distance_target(4.,0.,6.,.5),distance_target(4.,0.,12.,.5))
    self.assertLess(distance_target(4.,0.,12.,.5),distance_target(4.,0.,12.,.1))
    self.assertLessEqual(distance_target(4.,1.,12.,.5),distance_target(4.,0.,12.,.5))
    self.assertEqual(distance_target(4.,0.,0.,.5),-3.5)
    self.assertEqual(distance_target(4.,float('nan'),12.,.5),-3.5)
    self.assertEqual(distance_target(4.,0.,12.,-1.),-3.5)
    self.assertEqual(distance_target(0.,0.,0.,.5),0.)


class NativeForceStopBrakingTests(TestCase):
  def test_actual_inputs_control_hold_and_can_request(self):
    from openpilot.cereal import messaging
    from openpilot.common.params import Params
    from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
    from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate, car_state
    from opendbc.can.packer import CANPacker
    from opendbc.car.hyundai.hyundaicanfd import create_acc_control
    from opendbc.car.hyundai.ioniq6_longitudinal import Ioniq6LongitudinalPolicy

    stock,cp = candidate()
    sm = inputs()
    sm.all_checks = lambda names: all(sm.valid.get(name,False) for name in names)
    stop = messaging.new_message('starpilotLongitudinalPlan',valid=True)
    stop.logMonoTime = NOW-10_000_000
    for key,value in vars(sm['starpilotLongitudinalPlan']).items():
      setattr(stop.starpilotLongitudinalPlan,key,value)
    plan = messaging.new_message('longitudinalPlan',valid=True)
    plan.logMonoTime = NOW-10_000_000
    plan.longitudinalPlan.modelMonoTime = NOW-20_000_000
    plan.longitudinalPlan.shouldStop = True
    sm['starpilotLongitudinalPlan'] = messaging.log_from_bytes(stop.to_bytes()).starpilotLongitudinalPlan
    sm['longitudinalPlan'] = messaging.log_from_bytes(plan.to_bytes()).longitudinalPlan
    car = car_state(speed=4.,standstill=False)
    sm['carState'] = car
    with tempfile.TemporaryDirectory() as directory:
      reader = LongitudinalInputs(cp,Params(directory),lambda:sm)
      self.assertIn('starpilotLongitudinalPlan',reader.optional_services)
      with patch('openpilot.starpilot.longitudinal.inputs.time.monotonic_ns',return_value=NOW):
        context = reader.context(True)
      self.assertTrue(context.force_stop_braking)
      control = LongControl(cp)
      control.long_control_state = LongCtrlState.pid
      control.last_output_accel = -1.5
      result = control.update(True,car,-.8,True,(-3.5,2.),context=context)
      self.assertAlmostEqual(result,-.8)
      self.assertEqual(control.long_control_state,LongCtrlState.stopping)
      strong = control.update(True,car,-3.,True,(-3.5,2.),context=context)
      self.assertEqual(strong,-3.)
      self.assertTrue(math.isfinite(control.update(True,car,math.nan,True,(-3.5,2.),context=context)))
      with patch('openpilot.starpilot.longitudinal.inputs.time.monotonic_ns',return_value=NOW+200_000_000):
        stale = reader.context(True)
      self.assertFalse(stale.force_stop_braking)
      control.last_output_accel = -.8
      self.assertLess(control.update(True,car,-.8,True,(-3.5,2.),context=stale),-.8)
      stopped = car_state()
      control.last_output_accel = -1.5
      self.assertLess(control.update(True,stopped,0.,True,(-3.5,2.),context=context),-1.5)
      policy = Ioniq6LongitudinalPolicy()
      request = policy.update(0,active=True,override=False,accel=-.8,speed=4.,measured_accel=-1.5,
                              control_state=LongCtrlState.stopping,last_sent_accel=-1.5)
      packet = create_acc_control(CANPacker('hyundai_canfd_generated'),NS(ECAN=1),True,-1.5,request.accel,
                                  request.stopping,False,40.,NS(leadDistanceBars=2),direct_accel=True)
      bits = int.from_bytes(packet[1],'little')
      self.assertEqual((bits>>184)&1,1)
      self.assertEqual((bits>>68)&7,1)
      normal = LongControl(stock)
      normal.long_control_state = LongCtrlState.pid
      normal.last_output_accel = -1.5
      self.assertLess(normal.update(True,car,-.8,True,(-3.5,2.),context=context),-1.5)


def pe_pair():
  from opendbc.car.hyundai.tests.test_ioniq5pe_stock import params
  from opendbc.car.hyundai.ioniq5pe_long_profile import candidate
  stock = params()
  return stock, candidate(stock, enabled=True, is_release=False)


class Ioniq5PEDistanceBrakingTests(TestCase):
  def test_only_the_existing_sealed_pe_owner_is_admitted(self):
    from openpilot.starpilot.longitudinal.force_stop_braking import eligible
    stock, cp = pe_pair()
    self.assertFalse(eligible(stock))
    self.assertTrue(eligible(cp))
    for field, value in [('passive', True), ('dashcamOnly', True), ('notCar', True),
                         ('pcmCruise', True), ('openpilotLongitudinalControl', False),
                         ('alphaLongitudinalAvailable', False), ('alternativeExperience', 1),
                         ('longitudinalActuatorDelay', math.nan)]:
      with self.subTest(field=field):
        _, invalid = pe_pair()
        setattr(invalid, field, value)
        self.assertFalse(eligible(invalid))
    _, invalid = pe_pair()
    invalid.safetyConfigs[0].safetyParam = 0x5491
    self.assertFalse(eligible(invalid))

  def test_real_inputs_and_control_keep_the_pe_stop_without_ioniq6_start(self):
    from openpilot.common.params import Params
    from openpilot.selfdrive.controls.lib.longcontrol import LongControl, LongCtrlState
    from openpilot.starpilot.longitudinal.inputs import LongitudinalInputs
    from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import car_state
    from opendbc.can.packer import CANPacker
    from opendbc.car.hyundai.hyundaicanfd import CanBus, create_acc_control
    stock, cp = pe_pair()
    sm = inputs()
    sm.all_checks = lambda names: all(sm.valid.get(name, False) for name in names)
    state = car_state(speed=4., standstill=False)
    sm['carState'] = state
    with tempfile.TemporaryDirectory() as directory:
      reader = LongitudinalInputs(cp, Params(directory), lambda: sm)
      self.assertFalse(reader.ioniq6_start_enabled)
      self.assertIn('starpilotLongitudinalPlan', reader.optional_services)
      if reader.ev9_long_enabled:
        self.assertIn('slcState', reader.optional_services)
      with patch('openpilot.starpilot.longitudinal.inputs.time.monotonic_ns', return_value=NOW):
        context = reader.context(True)
      self.assertTrue(context.force_stop_braking)
      control = LongControl(cp)
      self.assertIsNone(control.extension.ioniq6_start)
      control.long_control_state = LongCtrlState.pid
      control.last_output_accel = -1.5
      output = control.update(True, state, -.8, True, (-3.5, 2.), context=context)
      self.assertAlmostEqual(output, -.8)
      self.assertEqual(control.long_control_state, LongCtrlState.stopping)
      packet = create_acc_control(CANPacker('hyundai_canfd_generated'), CanBus(cp), True,
                                  -1.5, output, True, False, 40., NS(leadDistanceBars=2))
      self.assertEqual((int.from_bytes(packet[1], 'little') >> 184) & 1, 1)
      with patch('openpilot.starpilot.longitudinal.inputs.time.monotonic_ns', return_value=NOW+200_000_000):
        stale = reader.context(True)
      self.assertFalse(stale.force_stop_braking)
      control.last_output_accel = -.8
      self.assertLess(control.update(True, state, -.8, True, (-3.5, 2.), context=stale), -.8)
      normal = LongControl(stock)
      normal.long_control_state = LongCtrlState.pid
      normal.last_output_accel = -1.5
      self.assertLess(normal.update(True, state, -.8, True, (-3.5, 2.), context=context), -1.5)
