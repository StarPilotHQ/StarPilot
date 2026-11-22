import math
from types import SimpleNamespace

import pytest

from opendbc.car import Bus, DT_CTRL
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.values import CAR
from opendbc.car.volvo.eucd_carcontroller import CarControllerEUCD as CarController
from opendbc.car.volvo.eucd_params import CarControllerEUCDParams as CarControllerParams, LongMode
from opendbc.car.volvo import eucd_can as volvocan
from opendbc.car.volvo.eucd_can import eucd_next_long_mode

CCP = CarControllerParams


def test_traction_continuous_at_drive_switch():
  # Coast and drive traction meet at the DRIVE threshold, like the camera
  a = CCP.EUCD_DRIVE_ENTER
  coast = volvocan.eucd_fsm3_traction(a, LongMode.COAST) - 256
  drive = volvocan.eucd_fsm3_traction(a, LongMode.DRIVE)
  assert drive - coast <= 6


def offset(pitch_deg):
  return CarController._eucd_slope_brake_offset(SimpleNamespace(orientationNED=[0., math.radians(pitch_deg), 0.]))


def test_uphill_coast_is_not_braking():
  # Coast on a 5.4 deg climb is not braking
  assert eucd_next_long_mode(LongMode.COAST, -0.84) == LongMode.BRAKE
  assert eucd_next_long_mode(LongMode.COAST, -0.84, offset(5.4)) == LongMode.COAST
  assert eucd_next_long_mode(LongMode.COAST, -1.5, offset(5.4)) == LongMode.BRAKE


def test_uphill_coast_overshoot_is_not_braking():
  # Coast-cap overshoot on a 5 deg climb
  assert eucd_next_long_mode(LongMode.COAST, -1.15, offset(5.0)) == LongMode.COAST
  assert eucd_next_long_mode(LongMode.COAST, -1.6, offset(5.0)) == LongMode.BRAKE
  # Holds down to the usual exit
  assert eucd_next_long_mode(LongMode.BRAKE, -0.8, offset(5.0)) == LongMode.BRAKE


def test_downhill_brakes_earlier():
  # Downhill brakes earlier
  assert eucd_next_long_mode(LongMode.COAST, -0.4) == LongMode.COAST
  assert eucd_next_long_mode(LongMode.COAST, -0.4, offset(-1.5)) == LongMode.BRAKE
  assert offset(-10.) == CCP.EUCD_DOWNHILL_BRAKE_OFFSET_MAX
  # BRAKE exit stays at or below 0
  assert eucd_next_long_mode(LongMode.BRAKE, 0.01, offset(-10.)) == LongMode.COAST


def test_slope_offset_fades_out_slow():
  cc = SimpleNamespace(orientationNED=[0., math.radians(3.), 0.])  # route 22, 878 s
  lo, hi = CCP.EUCD_BRAKE_ENTER_BP
  assert CarController._eucd_slope_brake_offset(cc, lo) == 0.
  assert CarController._eucd_slope_brake_offset(cc, hi) == CarController._eucd_slope_brake_offset(cc)
  assert CarController._eucd_slope_brake_offset(cc, (lo + hi) / 2) == pytest.approx(CarController._eucd_slope_brake_offset(cc) / 2)


def test_flat_unchanged():
  assert offset(0.) == 0.
  assert CarController._eucd_slope_brake_offset(SimpleNamespace(orientationNED=[])) == 0.


def limited(accel, last):
  cc = CarController.__new__(CarController)
  return cc._eucd_jerk_limit(accel, last)


def test_jerk_limit_only_cuts_jumps():
  assert abs(limited(1.3, 0.0) - CCP.EUCD_JERK_UP * 0.02) < 1e-9               # rising moderate
  assert abs(limited(-3.5, -1.0) - (-1.0 - CCP.EUCD_JERK_DOWN * 0.02)) < 1e-9  # braking only cut on jumps
  assert limited(-0.4, -0.2) == -0.4                   # the planner's own steps pass
  # 0 to -3 m/s^2 in 0.25 s, not 0.75 s as before (route 1f: braking started late)
  a, frames = 0., 0
  while a > -3.:
    a, frames = limited(-3., a), frames + 1
  assert frames * 0.02 <= 0.26


def test_jerk_limit_reaches_target():
  a = 1.3
  for _ in range(100):
    a = limited(-0.4, a)
  assert a == -0.4


def owner():
  cc = CarController({Bus.pt: "volvo_v60_2015_pt"}, CarInterface.get_non_essential_params(CAR.VOLVO_V60))
  return cc


def car(engaged=True, cam_enabled=1, v=0., standstill=True, hold=False, gas=False):
  return SimpleNamespace(eucd_ecm_acc_engaged=engaged, eucd_car_hold=hold, eucd_ecm_standstill_ready=False,
                         eucd_stock_FSM3={"ACC_NoTargetCancel": 0, "Byte_6": 0, "FrameKind": 0x81, "ACC_Check": 0},
                         eucd_stock_FSM0_frames=[{"Byte_0": 0, "ACC_AbortCode": 0, "ACC_Enabled": cam_enabled}],
                         eucd_stock_FSM4_frames=[{"Byte_2": 0, "Byte_0": 0x55, "ACC_BrakeRequest": 0}],
                         out=SimpleNamespace(vEgo=v, standstill=standstill, gasPressed=gas))


def control(long_active=True, accel=-1.):
  return SimpleNamespace(enabled=long_active, longActive=long_active, actuators=SimpleNamespace(accel=accel))


def owning(cc, CS, CC):
  """Templates seen (data and type frames), then OP owns the ECM."""
  for kind in (0x81, 0):
    CS.eucd_stock_FSM3 = {**CS.eucd_stock_FSM3, "FrameKind": kind}
    cc._eucd_ownership(CC, CS)
  CS.eucd_stock_FSM3 = {**CS.eucd_stock_FSM3, "FrameKind": 0x81}
  assert cc.eucd_own


def test_template_never_keeps_the_camera_ack():
  """The camera's ACC_Check (engaging at standstill) left in the template made the ECM brake in the drive-off (8a3)."""
  cc, CS = owner(), car()
  CS.eucd_stock_FSM3["ACC_Check"] = 1
  cc._eucd_ownership(control(long_active=False), CS)
  assert cc.eucd_tpl_fsm3["data"]["ACC_Check"] == 0
  assert CS.eucd_stock_FSM3["ACC_Check"] == 1  # the camera's frame untouched


def test_op_owns_the_ecm_at_highway_speed():
  # The camera's ACC shown standby whenever OP's long is on, so it never disagrees with OP (routes 1f, 2d)
  cc, CS, CC = owner(), car(v=30., standstill=False), control(accel=0.5)
  owning(cc, CS, CC)
  CS.eucd_stock_FSM0_frames = [{"Byte_0": 0, "ACC_AbortCode": 0, "ACC_Enabled": 0}]  # the camera in standby
  for _ in range(int(5. / DT_CTRL)):
    cc._eucd_ownership(CC, CS)
  assert cc.eucd_own and not cc.eucd_stopping
  CC.longActive = CC.enabled = False
  cc._eucd_ownership(CC, CS)
  assert not cc.eucd_own and cc.eucd_exit_standby_frames == 1  # standby until the ECM drops


def test_no_ownership_before_the_templates():
  cc, CS = owner(), car(cam_enabled=0)  # the camera not engaged yet: nothing clean to replay
  cc._eucd_ownership(control(), CS)
  assert not cc.eucd_own


def stopped(seconds=3.):
  """OP's stop at standstill, the car holding."""
  cc, CS, CC = owner(), car(hold=True, v=5., standstill=False), control()
  owning(cc, CS, CC)
  CS.out.vEgo, CS.out.standstill = 0., True
  CS.eucd_stock_FSM0_frames = [{"Byte_0": 0, "ACC_AbortCode": 0, "ACC_Enabled": 0}]
  for _ in range(int(seconds / DT_CTRL)):
    cc._eucd_ownership(CC, CS)
  assert cc.eucd_holding and cc.eucd_standstill == (seconds >= CCP.EUCD_STANDSTILL_DELAY)
  return cc, CC, CS


def fsm4_brake_requested(cc, CS):
  dat = cc._eucd_relay_fsm4(CS)[0][1]
  return bool((dat[5] >> 6) & 1)


def test_driver_gas_in_the_stop_drives_off_engaged():
  cc, CC, CS = stopped()
  CS.out.gasPressed, CC.longActive = True, False
  cc._eucd_ownership(CC, CS)
  assert cc.eucd_own and cc.eucd_driver_gas and cc.eucd_driving_off
  assert not cc.eucd_standstill and not cc.eucd_holding
  assert not fsm4_brake_requested(cc, CS)  # no brake from OP on the gas


def test_disengaged_on_the_gas_ends_the_stop():
  cc, CC, CS = stopped()
  CS.out.gasPressed, CC.longActive, CC.enabled = True, False, False
  cc._eucd_ownership(CC, CS)
  assert not cc.eucd_own and not cc.eucd_driver_gas


def test_gas_release_keeps_owning_until_op_long_is_back():
  cc, CC, CS = stopped()
  CS.out.gasPressed, CC.longActive = True, False
  cc._eucd_ownership(CC, CS)
  CS.out.gasPressed = False  # pedal released, OP's long back a frame later
  cc._eucd_ownership(CC, CS)
  assert cc.eucd_own and cc.eucd_driver_gas
  CC.longActive = True
  cc._eucd_ownership(CC, CS)
  assert cc.eucd_own and not cc.eucd_driver_gas


def test_short_stop_drives_off_on_traction():
  cc, CC, CS = stopped(seconds=1.)
  CC.actuators.accel = 0.5
  cc._eucd_ownership(CC, CS)
  assert cc.eucd_own and not cc.eucd_stopping and not cc.eucd_holding and not cc.eucd_standstill


def test_long_stop_waits_for_the_resume():
  cc, CC, CS = stopped()
  CC.actuators.accel = 0.5
  cc._eucd_ownership(CC, CS)
  assert cc.eucd_stopping and cc.eucd_holding and cc.eucd_standstill


def test_ecm_in_standstill_waits_for_the_resume():
  cc, CC, CS = stopped(seconds=1.)
  CS.eucd_ecm_standstill_ready, CC.actuators.accel = True, 0.5
  cc._eucd_ownership(CC, CS)
  assert cc.eucd_stopping and cc.eucd_holding


def resume_buttons_after(driver_resume, own, ecm_ready=True):
  """One frame stopped in standstill: the Resume hold frames left and the drive-off request."""
  cc = owner()
  cc.frame = 1000
  cc.eucd_own = own
  CS = SimpleNamespace(out=SimpleNamespace(cruiseState=SimpleNamespace(enabled=True, standstill=True), vEgo=0.),
                       eucd_acc_distance=0, eucd_stock_CCButtons_frames=[], eucd_driver_resume=driver_resume,
                       eucd_ecm_standstill_ready=ecm_ready)
  cc._eucd_resume_buttons(SimpleNamespace(cruiseControl=SimpleNamespace(resume=False)), CS, [])
  return cc.eucd_resume_hold_frames, cc.eucd_drive_off_request


def test_driver_resume_drives_off_ops_stop():
  assert resume_buttons_after(True, True) == (CCP.EUCD_RESUME_HOLD_FRAMES - 1, True)
  assert resume_buttons_after(False, True) == (0, False)
  assert resume_buttons_after(True, False) == (0, False)  # the camera's own stop takes it by itself


def test_driver_resume_does_not_wait_for_the_ecm_ready():
  # The ECM releases its hold on the driver's Resume at once (route 38, 42 s)
  assert resume_buttons_after(True, True, ecm_ready=False) == (CCP.EUCD_RESUME_HOLD_FRAMES - 1, True)


def test_engaged_at_a_stop_starts_in_standstill():
  # Engaged behind a stopped lead that soon left: OP must hold and resume, not ask traction of a holding car (route 38, 40 s)
  cc, CS, CC = owner(), car(hold=True), control(accel=0.6)
  owning(cc, CS, CC)
  assert cc.eucd_stopping and cc.eucd_standstill
  for _ in range(10):
    cc._eucd_ownership(CC, CS)
  assert cc.eucd_stopping and cc.eucd_holding and cc.eucd_standstill


def test_lead_icon_follows_op_not_the_template():
  # Templates kept from different moments flickered the lead icon on the dash (route 37, seg 3)
  cc = owner()
  cc.eucd_own = True
  cc.eucd_tpl_fsm0 = {0x10: {"Byte_0": 0x10, "ACC_FrontCar": 0}, 0x15: {"Byte_0": 0x15, "ACC_FrontCar": 1}}
  for lead in (True, False):
    assert {cc._eucd_fsm0({"Byte_0": b}, lead)["ACC_FrontCar"] for b in (0x10, 0x15, 0x00)} == {int(lead)}
