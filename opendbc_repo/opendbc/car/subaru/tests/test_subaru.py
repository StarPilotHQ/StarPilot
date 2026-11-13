import inspect
from collections import defaultdict
from types import SimpleNamespace

import pytest

from opendbc.can import CANPacker, CANParser
from opendbc.car import Bus, fw_versions, gen_empty_fingerprint, structs
from opendbc.car.fw_query_definitions import StdQueries
from opendbc.car.subaru import subarucan
from opendbc.car.subaru.carcontroller import CarController, _ASCENT_AOL_ARM_FRAMES, _ascent_adaptive_accel_request, \
  _throttle_max_for_car
from opendbc.car.subaru.carstate import CarState
from opendbc.car.subaru.fingerprints import FW_VERSIONS
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.values import CAR, DBC, FW_QUERY_CONFIG, SUBARU_ALT_VERSION_REQUEST, SUBARU_VERSION_REQUEST, CanBus, \
  CarControllerParams, SubaruFlags, SubaruSafetyFlags
from opendbc.car.structs import CarParams


def test_ascent_adaptive_accel_feedback_is_speed_gated_and_bounded():
  accel, filtered_error = _ascent_adaptive_accel_request(
    requested_accel=0.4, measured_accel=0.0, v_ego=80 / 3.6, filtered_error=0.0, dt=0.01,
  )
  assert filtered_error > 0.0
  assert 0.4 < accel < 0.41

  for _ in range(1000):
    accel, filtered_error = _ascent_adaptive_accel_request(
      requested_accel=0.4, measured_accel=-0.2, v_ego=110 / 3.6, filtered_error=filtered_error, dt=0.01,
    )
  assert accel == pytest.approx(1.05)


def test_ascent_adaptive_accel_feedback_reaches_full_weight_by_80_kph():
  accel, _ = _ascent_adaptive_accel_request(
    requested_accel=0.4, measured_accel=0.4, v_ego=75 / 3.6, filtered_error=1.0, dt=0.01,
  )

  assert accel == pytest.approx(0.725, abs=0.001)


def test_ascent_adaptive_accel_feedback_does_not_boost_braking_or_low_speed():
  low_speed_accel, low_speed_error = _ascent_adaptive_accel_request(
    requested_accel=0.4, measured_accel=0.0, v_ego=60 / 3.6, filtered_error=0.0, dt=0.01,
  )
  braking_accel, braking_error = _ascent_adaptive_accel_request(
    requested_accel=-0.5, measured_accel=-1.0, v_ego=110 / 3.6, filtered_error=0.4, dt=0.01,
  )

  assert low_speed_accel == pytest.approx(0.4)
  assert low_speed_error == pytest.approx(0.0)
  assert braking_accel == pytest.approx(-0.5)
  assert braking_error == pytest.approx(0.0)


def make_sng_controller(flags=0, prev_close_distance=4.0):
  controller = object.__new__(CarController)
  controller.CP = SimpleNamespace(flags=flags)
  controller.frame = 60
  controller.last_standstill_frame = 0
  controller.prev_close_distance = prev_close_distance
  controller.epb_resume_frames_remaining = -1
  return controller


def make_sng_state(close_distance=4.0, standstill=True):
  cc = SimpleNamespace(enabled=True, hudControl=SimpleNamespace(leadVisible=True))
  cs = SimpleNamespace(
    close_distance=close_distance,
    out=SimpleNamespace(standstill=standstill),
  )
  return cc, cs


def test_global_sng_keeps_standstill_alive_without_manual_parking_brake_toggle():
  controller = make_sng_controller()
  cc, cs = make_sng_state()

  throttle_cmd, speed_cmd = controller.stop_and_go(cc, cs, manual_parking_brake=False)

  assert throttle_cmd is False
  assert speed_cmd is True


def test_manual_parking_brake_sng_still_sends_resume_throttle():
  controller = make_sng_controller(prev_close_distance=3.9)
  cc, cs = make_sng_state(close_distance=4.0)

  throttle_cmd, speed_cmd = controller.stop_and_go(cc, cs, manual_parking_brake=True)

  assert throttle_cmd is True
  assert speed_cmd is True


def test_preglobal_sng_does_not_send_standstill_keepalive_without_manual_toggle():
  controller = make_sng_controller(flags=SubaruFlags.PREGLOBAL)
  cc, cs = make_sng_state()

  throttle_cmd, speed_cmd = controller.stop_and_go(cc, cs, manual_parking_brake=False)

  assert throttle_cmd is False
  assert speed_cmd is False


def test_redneck_cruise_buttons_use_resume_for_increase_and_set_for_decrease():
  dbc = DBC[CAR.SUBARU_IMPREZA_2020][Bus.pt]
  packer = CANPacker(dbc)
  parser = CANParser(dbc, [("Cruise_Buttons", 0)], CanBus.main)
  stock_buttons = defaultdict(int)

  resume_msg = subarucan.create_cruise_buttons(
    packer, 1, stock_buttons, subarucan.CRUISE_BUTTON_RESUME, CanBus.main,
  )
  parser.update([(1, [resume_msg])])
  assert parser.vl["Cruise_Buttons"]["Resume"] == 1
  assert parser.vl["Cruise_Buttons"]["Set"] == 0

  set_msg = subarucan.create_cruise_buttons(
    packer, 2, stock_buttons, subarucan.CRUISE_BUTTON_SET, CanBus.main,
  )
  parser.update([(2, [set_msg])])
  assert parser.vl["Cruise_Buttons"]["Resume"] == 0
  assert parser.vl["Cruise_Buttons"]["Set"] == 1


def test_redneck_cruise_is_only_available_on_the_experimental_impreza(monkeypatch):
  class FakeParams:
    def __init__(self, **_kwargs):
      pass

    def get_bool(self, key):
      return key == "SubaruRedneckCruise"

  monkeypatch.setattr("opendbc.car.interfaces.Params", FakeParams)
  toggles = SimpleNamespace(subaru_sng=False)

  impreza_cp = CarInterface.get_non_essential_params(CAR.SUBARU_IMPREZA_2020)
  impreza_fpcp = CarInterface.get_starpilot_params(
    CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], impreza_cp, toggles,
  )
  assert impreza_fpcp.redneckCruiseAvailable
  assert not impreza_fpcp.pcmCruiseSpeed
  assert impreza_cp.openpilotLongitudinalControl
  assert impreza_cp.safetyConfigs[0].safetyParam & SubaruSafetyFlags.REDNECK_CRUISE

  old_impreza_cp = CarInterface.get_non_essential_params(CAR.SUBARU_IMPREZA)
  old_impreza_fpcp = CarInterface.get_starpilot_params(
    CAR.SUBARU_IMPREZA, gen_empty_fingerprint(), [], old_impreza_cp, toggles,
  )
  assert not old_impreza_fpcp.redneckCruiseAvailable
  assert old_impreza_fpcp.pcmCruiseSpeed
  assert not old_impreza_cp.openpilotLongitudinalControl


class TestSubaruFingerprint:
  def test_eyesight_queries_do_not_change_diagnostic_state(self, monkeypatch):
    camera_requests = [request for request in FW_QUERY_CONFIG.requests if CarParams.Ecu.fwdCamera in request.whitelist_ecus]

    assert CarParams.Ecu.fwdCamera in FW_QUERY_CONFIG.non_tester_present_ecus
    assert {tuple(request.request) for request in camera_requests} == {
      (SUBARU_VERSION_REQUEST,),
      (SUBARU_ALT_VERSION_REQUEST,),
    }
    for request in camera_requests:
      assert StdQueries.TESTER_PRESENT_REQUEST not in request.request
      assert StdQueries.DEFAULT_DIAGNOSTIC_REQUEST not in request.request

    queried_ecus = set()

    def collect_queries(_can_recv, _can_send, queries, _responses, timeout):
      queried_ecus.update(queries)
      return set()

    monkeypatch.setattr(fw_versions, "REQUESTS", [("subaru", FW_QUERY_CONFIG, request) for request in FW_QUERY_CONFIG.requests])
    monkeypatch.setattr(fw_versions, "VERSIONS", {"subaru": FW_VERSIONS})
    monkeypatch.setattr(fw_versions, "get_ecu_addrs", collect_queries)
    fw_versions.get_present_ecus(lambda **_kwargs: [], lambda _msgs: None, lambda _enabled: None)

    assert queried_ecus
    assert all(address != 0x787 for address, _subaddress, _bus in queried_ecus)

  def test_fw_version_format(self):
    for platform, fws_per_ecu in FW_VERSIONS.items():
      for (ecu, _, _), fws in fws_per_ecu.items():
        fw_size = len(fws[0])
        for fw in fws:
          if platform in (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_LEGACY_2025) and ecu == CarParams.Ecu.fwdCamera:
            assert len(fw) > 0, f"{platform} {ecu}: empty firmware response"
          else:
            assert len(fw) == fw_size, f"{platform} {ecu}: {len(fw)} {fw_size}"

  def test_outback_2024_firmware(self):
    outback_fw = FW_VERSIONS[CAR.SUBARU_OUTBACK_2023]
    assert b'\xa1 $\x17\x00' in outback_fw[(CarParams.Ecu.abs, 0x7b0, None)]
    assert b'\xfb,\xa2p\x07' in outback_fw[(CarParams.Ecu.engine, 0x7a2, None)]
    assert b'\xa9\x17w!r' in outback_fw[(CarParams.Ecu.transmission, 0x7a3, None)]

    car_fw = [
      CarParams.CarFw(ecu=CarParams.Ecu.abs, fwVersion=b'\xa1 $\x17\x00', address=0x7b0, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.eps, fwVersion=b'+\xc0\x12\x11\x00', address=0x746, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.fwdCamera, fwVersion=b'\t!\x08\x046\x05!\x08\x01/', address=0x787, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.engine, fwVersion=b'\xfb,\xa2p\x07', address=0x7a2, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.transmission, fwVersion=b'\xa9\x17w!r', address=0x7a3, brand="subaru"),
    ]
    exact, matches = match_fw_to_car(car_fw, "4S4BTGUD6R3155987", allow_fuzzy=False, log=False)
    assert exact
    assert matches == {CAR.SUBARU_OUTBACK_2023}

  def test_legacy_2025_firmware(self):
    car_fw = [
      CarParams.CarFw(ecu=CarParams.Ecu.abs, fwVersion=b'\xa1 $\x11\x00', address=0x7b0, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.eps, fwVersion=b'[\xc0\xd1\x10\x00', address=0x746, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.fwdCamera, fwVersion=b'\x1a!\x08\x00C\x0e!\x08\x018', address=0x787, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.fwdCamera, fwVersion=b'\x20\x02\x0e', address=0x787, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.engine, fwVersion=b'\x08,\xa0p\x07', address=0x7a2, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.transmission, fwVersion=b'\xeb\x17U!r', address=0x7a3, brand="subaru"),
    ]
    exact, matches = match_fw_to_car(car_fw, "4S3BWGG67S3011945", allow_fuzzy=False, log=False)
    assert exact
    assert matches == {CAR.SUBARU_LEGACY_2025}

  def test_ascent_2025_firmware(self):
    car_fw = [
      CarParams.CarFw(ecu=CarParams.Ecu.abs, fwVersion=b'\xa5 %\x03\x01', address=0x7b0, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.eps, fwVersion=b'\x55\xc0\xd0\x10', address=0x746, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.fwdCamera, fwVersion=b'\x17!\x08\x01A\x12!\x08\x00;', address=0x787, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.fwdCamera, fwVersion=b'\x20\x02\x0e', address=0x787, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.engine, fwVersion=b'\x11,\xa00\x07', address=0x7a2, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.transmission, fwVersion=b'\x05\xfe\xe7\x00\x00', address=0x7a3, brand="subaru"),
    ]
    exact, matches = match_fw_to_car(car_fw, "4S4WMAAD9S3414980", allow_fuzzy=False, log=False)
    assert exact
    assert matches == {CAR.SUBARU_ASCENT_2023}

  def test_ascent_2025_firmware_without_engine_response(self):
    car_fw = [
      CarParams.CarFw(ecu=CarParams.Ecu.abs, fwVersion=b'\xa5 %\x03\x01', address=0x7b0, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.eps, fwVersion=b'\x55\xc0\xd0\x10', address=0x746, brand="subaru"),
      CarParams.CarFw(ecu=CarParams.Ecu.fwdCamera, fwVersion=b'\x17!\x08\x01A\x00\x00\x00\x00\x00', address=0x787, brand="subaru"),
    ]
    exact, matches = match_fw_to_car(car_fw, "4S4WMAAD9S3414980", allow_fuzzy=False, log=False)
    assert exact
    assert matches == {CAR.SUBARU_ASCENT_2023}


ANGLE_PLATFORMS = (
  CAR.SUBARU_FORESTER_2022,
  CAR.SUBARU_OUTBACK_2023,
  CAR.SUBARU_LEGACY_2025,
  CAR.SUBARU_ASCENT_2023,
  CAR.SUBARU_CROSSTREK_2025,
)


@pytest.mark.parametrize("platform", ANGLE_PLATFORMS)
def test_angle_platform_params(platform):
  CP = CarInterface.get_non_essential_params(platform)

  assert CP.flags & SubaruFlags.LKAS_ANGLE
  assert CP.steerControlType == CarParams.SteerControlType.angle
  assert CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.LKAS_ANGLE
  assert not CP.dashcamOnly
  assert not CP.alphaLongitudinalAvailable


def test_torque_platform_does_not_enable_angle_safety():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_IMPREZA_2020)

  assert not (CP.flags & SubaruFlags.LKAS_ANGLE)
  assert CP.steerControlType == CarParams.SteerControlType.torque
  assert not (CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.LKAS_ANGLE)


def test_ascent_throttle_limit_is_vehicle_specific():
  ascent_cp = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT)
  other_cp = CarInterface.get_non_essential_params(CAR.SUBARU_IMPREZA_2020)

  assert CarControllerParams.THROTTLE_MAX == 3400
  assert CarControllerParams.ASCENT_THROTTLE_MAX == 3800
  assert _throttle_max_for_car(CAR.SUBARU_ASCENT) == 3800
  assert _throttle_max_for_car(CAR.SUBARU_ASCENT_2023) == 3400
  assert _throttle_max_for_car(CAR.SUBARU_IMPREZA_2020) == 3400
  assert ascent_cp.safetyConfigs[0].safetyParam & SubaruSafetyFlags.ASCENT_LONG_THROTTLE
  assert not (other_cp.safetyConfigs[0].safetyParam & SubaruSafetyFlags.ASCENT_LONG_THROTTLE)


def test_outback_2023_uses_d_platform_bus_layout():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_OUTBACK_2023)
  parsers = CarState.get_can_parsers(CP)
  controller = CarController({}, CP)

  assert CP.flags & SubaruFlags.D_PLATFORM
  assert CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.D_PLATFORM
  assert CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.STOP_START_BUTTON
  assert CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.FIXED_ANGLE_LIMITS
  assert CanBus.main_for_cp(CP) == CanBus.alt
  assert CanBus.angle_for_cp(CP) == CanBus.main
  assert parsers[Bus.pt].bus == CanBus.alt
  assert parsers[Bus.cam].bus == CanBus.camera
  assert parsers[Bus.alt].bus == CanBus.alt
  assert parsers[Bus.main].bus == CanBus.main
  assert controller.angle_bus == CanBus.main
  assert controller.status_bus == CanBus.main
  assert CP.lateralSmoothSeconds == pytest.approx(0.4)


@pytest.mark.parametrize("platform", [CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_LEGACY_2025])
def test_stop_start_inputs_are_captured_for_supported_models(platform):
  CP = CarInterface.get_non_essential_params(platform)
  car_state = CarState(CP, None)
  parsers = car_state.get_can_parsers(CP)
  raw_dashlights = bytes.fromhex("13031407875a8100")
  parsers[Bus.alt].vl["Dashlights"]["COUNTER"] = 6
  parsers[Bus.alt].vl["Dashlights"]["STOP_START"] = 0
  parsers[Bus.alt].vl["Engine_Stop_Start"]["STOP_START_STATE"] = 3
  parsers[Bus.alt].vl_raw["Dashlights"] = raw_dashlights

  car_state.update(parsers, SimpleNamespace(subaru_sng=False))

  assert car_state.dashlights_msg["COUNTER"] == 6
  assert car_state.dashlights_dat == raw_dashlights
  assert car_state.stop_start_state == 3


@pytest.mark.parametrize("platform, expected_bus, start_frame", [
  (CAR.SUBARU_OUTBACK_2023, CanBus.alt, 101),
  (CAR.SUBARU_LEGACY_2025, CanBus.alt, 401),
])
def test_stop_start_request_is_bounded_and_uses_live_dashlights(platform, expected_bus, start_frame):
  CP = CarInterface.get_non_essential_params(platform)
  controller = CarController({}, CP)
  controller.frame = start_frame

  class TestActuators:
    steeringAngleDeg = 0.0

    def as_builder(self):
      return SimpleNamespace(steeringAngleDeg=self.steeringAngleDeg)

  CC = SimpleNamespace(
    enabled=False,
    latActive=False,
    longActive=False,
    actuators=TestActuators(),
    hudControl=SimpleNamespace(leadVisible=False),
    cruiseControl=SimpleNamespace(cancel=False),
  )
  CS = SimpleNamespace(
    canValid=True,
    dashlights_msg={"COUNTER": 6, "STOP_START": 0},
    dashlights_dat=bytes.fromhex("13061407875a8100"),
    stop_start_state=0,
    out=SimpleNamespace(
      standstill=True,
      gearShifter=structs.CarState.GearShifter.park,
    ),
  )
  toggles = SimpleNamespace(subaru_stop_start_off=True, subaru_sng=False)

  _, can_sends = controller.update(CC, CS, 0, toggles)
  stop_start_msgs = [msg for msg in can_sends if msg[0] == 0x390]
  assert len(stop_start_msgs) == 1
  assert stop_start_msgs[0][2] == expected_bus
  assert stop_start_msgs[0][1] == bytes.fromhex("57071407875ac100")
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("Dashlights", 0)], expected_bus)
  parser.update([(expected_bus, [stop_start_msgs[0]])])
  assert parser.vl["Dashlights"]["STOP_START"] == 1
  assert parser.vl["Dashlights"]["COUNTER"] == 7

  controller.frame = 103
  CS.stop_start_state = 3
  _, can_sends = controller.update(CC, CS, 0, toggles)
  assert not any(msg[0] == 0x390 for msg in can_sends)
  assert controller.stop_start_acknowledged


def test_legacy_2025_uses_gen2_angle_bus_layout():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_LEGACY_2025)
  parsers = CarState.get_can_parsers(CP)
  controller = CarController({}, CP)

  assert not (CP.flags & SubaruFlags.D_PLATFORM)
  assert not (CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.D_PLATFORM)
  assert not (CP.flags & SubaruFlags.D_PLATFORM_CAMERA)
  assert not (CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.D_PLATFORM_CAMERA)
  assert CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.FIXED_ANGLE_LIMITS
  assert CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.STOP_START_BUTTON
  assert CanBus.main_for_cp(CP) == CanBus.main
  assert CanBus.angle_for_cp(CP) == CanBus.main
  assert parsers[Bus.pt].bus == CanBus.main
  assert parsers[Bus.cam].bus == CanBus.camera
  assert parsers[Bus.alt].bus == CanBus.alt
  assert Bus.main not in parsers
  assert controller.angle_bus == CanBus.main
  assert controller.status_bus == CanBus.main


def test_legacy_2025_uses_validated_angle_request_limits():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_LEGACY_2025)
  controller = CarController({}, CP)
  CC = SimpleNamespace(
    enabled=False,
    latActive=False,
    actuators=SimpleNamespace(steeringAngleDeg=-73.05),
  )
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=2.4,
    steeringAngleDeg=-75.27,
    steeringRateDeg=0.0,
    steeringPressed=False,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))

  controller.lateral_angle(CC, CS)
  CC.latActive = True
  msg = controller.lateral_angle(CC, CS)
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)
  parser.update([(1, [msg])])

  assert parser.can_valid
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(-73.05)

  CS.out.standstill = True
  msg = controller.lateral_angle(CC, CS)
  parser.update([(2, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)


def test_legacy_2025_engagement_continues_from_last_sent_angle():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_LEGACY_2025)
  controller = CarController({}, CP)
  CC = SimpleNamespace(
    enabled=False,
    latActive=False,
    actuators=SimpleNamespace(steeringAngleDeg=3.17),
  )
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=13.9,
    steeringAngleDeg=-0.14,
    steeringRateDeg=0.0,
    steeringPressed=False,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(-0.14)

  # CarState can advance between the last inactive command and the first active one.
  # Continue from the command panda accepted rather than skipping ahead to the newer sample.
  CS.out.steeringAngleDeg = -0.09
  CC.latActive = True
  msg = controller.lateral_angle(CC, CS)
  parser.update([(2, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(0.47)


def test_legacy_2025_reengages_immediately_after_manual_steering_stops():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_LEGACY_2025)
  controller = CarController({}, CP)
  CC = SimpleNamespace(
    enabled=True,
    latActive=True,
    actuators=SimpleNamespace(steeringAngleDeg=-100.0),
  )
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=6.2,
    steeringAngleDeg=-121.55,
    steeringRateDeg=350.0,
    steeringTorque=250.0,
    steeringPressed=True,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])

  msg = controller.lateral_angle(CC, CS)
  parser.update([(2, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)

  CS.out.steeringPressed = False
  CS.out.steeringTorque = 0.0
  CS.out.steeringAngleDeg = -113.78
  CS.out.steeringRateDeg = 0.0
  msg = controller.lateral_angle(CC, CS)
  parser.update([(3, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)

  CS.out.steeringRateDeg = 0.0
  msg = controller.lateral_angle(CC, CS)
  parser.update([(4, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
  assert -113.78 < parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] < -100.0


def test_legacy_2025_manual_handoff_reentry_uses_normal_angle_limits():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_LEGACY_2025)
  controller = CarController({}, CP)
  CC = SimpleNamespace(
    enabled=False,
    latActive=True,
    actuators=SimpleNamespace(steeringAngleDeg=-20.0),
  )
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=3.7,
    steeringAngleDeg=2.5,
    steeringRateDeg=-45.0,
    steeringTorque=250.0,
    steeringPressed=True,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])

  msg = controller.lateral_angle(CC, CS)
  parser.update([(2, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0

  CS.out.steeringPressed = False
  CS.out.steeringTorque = 0.0
  CS.out.steeringRateDeg = 0.0
  msg = controller.lateral_angle(CC, CS)
  parser.update([(3, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0

  msg = controller.lateral_angle(CC, CS)
  parser.update([(4, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
  first_reentry_angle = parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"]
  assert CC.actuators.steeringAngleDeg < first_reentry_angle < CS.out.steeringAngleDeg

  reentry_angles = []
  for i in range(6):
    msg = controller.lateral_angle(CC, CS)
    parser.update([(20 + i, [msg])])
    reentry_angles.append(parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"])

  assert all(reentry_angles[i] >= reentry_angles[i + 1] for i in range(len(reentry_angles) - 1))
  assert reentry_angles[-1] > CC.actuators.steeringAngleDeg


def test_ascent_2023_uses_gen2_angle_bus_layout():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  parsers = CarState.get_can_parsers(CP)
  controller = CarController({}, CP)

  assert not (CP.flags & SubaruFlags.D_PLATFORM)
  assert not (CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.D_PLATFORM)
  assert not (CP.flags & SubaruFlags.D_PLATFORM_CAMERA)
  assert not (CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.D_PLATFORM_CAMERA)
  assert CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.FIXED_ANGLE_LIMITS
  assert CanBus.main_for_cp(CP) == CanBus.main
  assert CanBus.angle_for_cp(CP) == CanBus.main
  assert parsers[Bus.pt].bus == CanBus.main
  assert parsers[Bus.cam].bus == CanBus.camera
  assert parsers[Bus.alt].bus == CanBus.alt
  assert Bus.main not in parsers
  assert controller.angle_bus == CanBus.main
  assert controller.status_bus == CanBus.main


def test_ascent_steering_rate_retains_last_can_sample():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  car_state = CarState(CP, None)
  parsers = car_state.get_can_parsers(CP)
  toggles = SimpleNamespace(subaru_sng=False)

  parsers[Bus.pt].vl["Steering_2"]["Steering_Angle"] = 1.0
  parsers[Bus.pt].vl["Steering_2"]["COUNTER"] = 1
  car_state.update(parsers, toggles)

  parsers[Bus.pt].vl["Steering_2"]["Steering_Angle"] = 2.0
  parsers[Bus.pt].vl["Steering_2"]["COUNTER"] = 2
  state, _ = car_state.update(parsers, toggles)
  assert state.steeringRateDeg == pytest.approx(50.0)

  state, _ = car_state.update(parsers, toggles)
  assert state.steeringRateDeg == pytest.approx(50.0)


def test_other_angle_platforms_keep_existing_bus_layout():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
  parsers = CarState.get_can_parsers(CP)

  assert not (CP.flags & SubaruFlags.D_PLATFORM)
  assert not (CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.D_PLATFORM)
  assert parsers[Bus.pt].bus == CanBus.main
  assert parsers[Bus.cam].bus == CanBus.camera
  assert parsers[Bus.alt].bus == CanBus.alt


def test_angle_controller_tracks_driver_override():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
  controller = CarController({}, CP)
  CC = SimpleNamespace(latActive=True, actuators=SimpleNamespace(steeringAngleDeg=15.0))
  CS = SimpleNamespace(out=SimpleNamespace(vEgoRaw=15.0, steeringAngleDeg=2.0, steeringTorque=175.0))

  msg = controller.lateral_angle(CC, CS)

  assert not controller.driver_override

  msg = controller.lateral_angle(CC, CS)

  assert controller.driver_override
  assert controller.p.STEER_OVERRIDE_TORQUE_HIGH == 150
  assert controller.p.STEER_OVERRIDE_TORQUE_LOW == 100
  assert controller.apply_steer_last == CS.out.steeringAngleDeg
  assert msg[0] == 0x124

  CS.out.steeringTorque = 125.0
  controller.lateral_angle(CC, CS)
  assert controller.driver_override

  CS.out.steeringTorque = 75.0
  controller.lateral_angle(CC, CS)
  assert not controller.driver_override


def test_angle_controller_blocks_low_speed_mads_engagement():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
  controller = CarController({}, CP)
  CC = SimpleNamespace(
    enabled=False,
    latActive=True,
    actuators=SimpleNamespace(steeringAngleDeg=15.0),
  )
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=0.3,
    steeringAngleDeg=80.0,
    steeringTorque=0.0,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])

  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)

  CS.out.vEgoRaw = 1.0
  CS.out.steeringAngleDeg = 130.0
  msg = controller.lateral_angle(CC, CS)
  parser.update([(2, [msg])])

  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)

  CS.out.steeringAngleDeg = 0.0
  msg = controller.lateral_angle(CC, CS)
  parser.update([(3, [msg])])

  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1


@pytest.mark.parametrize("platform", (CAR.SUBARU_ASCENT_2023, CAR.SUBARU_OUTBACK_2023))
def test_angle_controller_uses_fixed_angle_rate_limits(platform):
  CP = CarInterface.get_non_essential_params(platform)
  controller = CarController({}, CP)
  CC = SimpleNamespace(enabled=True, latActive=True, actuators=SimpleNamespace(steeringAngleDeg=-14.88))
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=21.66,
    steeringAngleDeg=-25.77,
    steeringRateDeg=0.0,
    steeringTorque=-250.0,
    steeringPressed=False,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
  assert CS.out.steeringAngleDeg < parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] < -25.0


def test_ascent_angle_controller_reengages_immediately_after_manual_steering_stops():
  platform = CAR.SUBARU_ASCENT_2023
  CP = CarInterface.get_non_essential_params(platform)
  controller = CarController({}, CP)
  CC = SimpleNamespace(enabled=True, latActive=True, actuators=SimpleNamespace(steeringAngleDeg=-10.0))
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=21.66,
    steeringAngleDeg=-25.06,
    steeringRateDeg=35.0,
    steeringTorque=-250.0,
    steeringPressed=True,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])

  msg = controller.lateral_angle(CC, CS)
  parser.update([(2, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)

  CS.out.steeringPressed = False
  CS.out.steeringTorque = 0.0
  CS.out.steeringAngleDeg = -17.91
  CS.out.steeringRateDeg = 0.0
  msg = controller.lateral_angle(CC, CS)
  parser.update([(3, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(4, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
  assert CS.out.steeringAngleDeg < parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] < CC.actuators.steeringAngleDeg


def test_ascent_reentry_rate_uses_last_transmitted_angle():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  controller = CarController({}, CP)
  controller.angle_handoff_active = True
  CC = SimpleNamespace(enabled=True, latActive=True, actuators=SimpleNamespace(steeringAngleDeg=-1.45))
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=30.3, steeringAngleDeg=0.78, steeringRateDeg=-1.5, steeringTorque=56.0,
    gearShifter=structs.CarState.GearShifter.drive, standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  parser.update([(1, [controller.lateral_angle(CC, CS)])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(0.78)

  CS.out.steeringAngleDeg = 0.74
  CS.out.steeringRateDeg = -1.99
  parser.update([(2, [controller.lateral_angle(CC, CS)])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(0.53, abs=0.01)


def test_ascent_angle_controller_waits_for_parking_lot_safety_envelope():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  controller = CarController({}, CP)
  CC = SimpleNamespace(enabled=False, latActive=True, actuators=SimpleNamespace(steeringAngleDeg=-206.12))
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=1.57,
    steeringAngleDeg=-260.44,
    steeringRateDeg=96.0,
    steeringTorque=7.0,
    steeringPressed=False,
    cruiseState=SimpleNamespace(available=True),
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)

  CS.out.steeringAngleDeg = -100.0
  CS.out.steeringRateDeg = 0.0
  for frame in range(2, _ASCENT_AOL_ARM_FRAMES + 1):
    msg = controller.lateral_angle(CC, CS)
    parser.update([(frame, [msg])])
    assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0

  msg = controller.lateral_angle(CC, CS)
  parser.update([(_ASCENT_AOL_ARM_FRAMES + 1, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1

  CS.out.gearShifter = structs.CarState.GearShifter.reverse
  msg = controller.lateral_angle(CC, CS)
  parser.update([(_ASCENT_AOL_ARM_FRAMES + 2, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(CS.out.steeringAngleDeg)


def test_ascent_aol_does_not_arm_before_cruise_main_is_available():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  controller = CarController({}, CP)
  CC = SimpleNamespace(enabled=False, latActive=True, actuators=SimpleNamespace(steeringAngleDeg=5.0))
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=10.0,
    steeringAngleDeg=0.0,
    steeringRateDeg=0.0,
    steeringTorque=0.0,
    steeringPressed=False,
    cruiseState=SimpleNamespace(available=False),
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  for frame in range(_ASCENT_AOL_ARM_FRAMES):
    msg = controller.lateral_angle(CC, CS)
    parser.update([(frame + 1, [msg])])

  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert controller.ascent_aol_arm_frames == 0

  CS.out.cruiseState.available = True
  msg = controller.lateral_angle(CC, CS)
  parser.update([(_ASCENT_AOL_ARM_FRAMES + 1, [msg])])
  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 0
  assert controller.ascent_aol_arm_frames == 1


def test_ascent_angle_controller_does_not_delay_normal_engagement():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  controller = CarController({}, CP)
  CC = SimpleNamespace(enabled=True, latActive=True, actuators=SimpleNamespace(steeringAngleDeg=5.0))
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=10.0,
    steeringAngleDeg=0.0,
    steeringRateDeg=0.0,
    steeringTorque=0.0,
    steeringPressed=False,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  msg = controller.lateral_angle(CC, CS)
  parser.update([(1, [msg])])

  assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1


def test_lkas_hud_state_uses_angle_request_state():
  update_source = inspect.getsource(CarController.update)

  assert "CS.es_lkas_state_msg, self._lkas_status_active(CC), hud_control.visualAlert" in update_source
  assert "create_es_lkas_state(self.packer, self.frame // 10, CS.es_lkas_state_msg, CC.enabled" not in update_source


@pytest.mark.parametrize(("enabled", "expected"), ((False, 0), (True, 1)))
def test_lkas_hud_active_bit_follows_lateral_state(enabled, expected):
  dbc = DBC[CAR.SUBARU_LEGACY_2025][Bus.pt]
  packer = CANPacker(dbc)
  parser = CANParser(dbc, [("ES_LKAS_State", 0)], CanBus.main)
  stock_lkas_state = defaultdict(int, {"LKAS_ACTIVE": 1})

  msg = subarucan.create_es_lkas_state(
    packer, 0, stock_lkas_state, enabled, 0, False, False, False, False, CanBus.main,
  )
  parser.update([(1, [msg])])

  assert parser.can_valid
  assert parser.vl["ES_LKAS_State"]["LKAS_ACTIVE"] == expected


def test_outback_manual_steering_keeps_cooperative_angle_request():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_OUTBACK_2023)
  controller = CarController({}, CP)
  CC = SimpleNamespace(
    enabled=False,
    latActive=True,
    actuators=SimpleNamespace(steeringAngleDeg=-225.0),
  )
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=0.9,
    steeringAngleDeg=-57.0,
    steeringRateDeg=0.0,
    steeringTorque=0.0,
    steeringPressed=True,
    gearShifter=structs.CarState.GearShifter.drive,
    standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)

  for frame, steering_torque in enumerate((-79.0, -81.0, -170.0, -250.0, -250.0, 79.0, 81.0, 170.0, 250.0, 250.0), start=1):
    CS.out.steeringRateDeg = 0.0 if frame == 1 else -45.0
    CS.out.steeringTorque = steering_torque
    CS.out.steeringPressed = abs(steering_torque) > 80.0
    msg = controller.lateral_angle(CC, CS)
    parser.update([(frame, [msg])])

    assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"] == 1
    assert CC.actuators.steeringAngleDeg < parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] < CS.out.steeringAngleDeg
    assert controller._lkas_status_active(CC)


def test_outback_waits_for_manual_turn_to_settle_before_reentry():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_OUTBACK_2023)
  controller = CarController({}, CP)
  CC = SimpleNamespace(enabled=False, latActive=True, actuators=SimpleNamespace(steeringAngleDeg=-80.0))
  CS = SimpleNamespace(out=SimpleNamespace(
    vEgoRaw=7.3, steeringAngleDeg=-121.47, steeringRateDeg=126.5,
    gearShifter=structs.CarState.GearShifter.drive, standstill=False,
  ))
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_LKAS_ANGLE", 0)], CanBus.main)
  for frame, (angle, rate, active) in enumerate([
    (-121.47, 126.5, False), (-117.96, 122.5, False), (-88.65, 112.0, False),
    (-0.24, 0.0, True),
  ], start=1):
    CS.out.steeringAngleDeg = angle
    CS.out.steeringRateDeg = rate
    parser.update([(frame, [controller.lateral_angle(CC, CS)])])
    assert bool(parser.vl["ES_LKAS_ANGLE"]["LKAS_Request"]) == active
    if not active:
      assert parser.vl["ES_LKAS_ANGLE"]["LKAS_Output"] == pytest.approx(angle, abs=0.01)


def test_ascent_hud_waits_for_angle_request():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  controller = CarController({}, CP)
  CC = SimpleNamespace(latActive=True)

  assert not controller._lkas_status_active(CC)
  controller.angle_lkas_active = True
  assert controller._lkas_status_active(CC)


@pytest.mark.parametrize("platform", list(CAR))
def test_secondary_lkas_alert_is_detected_only_on_ascent(platform):
  fingerprint = gen_empty_fingerprint()
  fingerprint[CanBus.camera][0x3C4] = 8
  CP = CarInterface.get_params(platform, fingerprint, [], False, False, False, None)
  expected = platform == CAR.SUBARU_ASCENT_2023
  assert bool(CP.flags & SubaruFlags.ASCENT_LKAS_ALERT) == expected
  assert bool(CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.ASCENT_LKAS_ALERT) == expected
  assert (0x3C4 in CarState.get_can_parsers(CP)[Bus.cam].addresses) == expected


@pytest.mark.parametrize("bus,length", [(0, 8), (1, 8), (2, 4), (2, 16)])
def test_ascent_secondary_alert_requires_native_camera_frame(bus, length):
  fingerprint = gen_empty_fingerprint()
  fingerprint[bus][0x3C4] = length
  CP = CarInterface.get_params(CAR.SUBARU_ASCENT_2023, fingerprint, [], False, False, False, None)
  assert not CP.flags & SubaruFlags.ASCENT_LKAS_ALERT
  assert not CP.safetyConfigs[0].safetyParam & SubaruSafetyFlags.ASCENT_LKAS_ALERT


@pytest.mark.parametrize("active,visual_alert,masked", [
  (True, structs.CarControl.HUDControl.VisualAlert.none, True),
  (False, structs.CarControl.HUDControl.VisualAlert.none, False),
  (True, structs.CarControl.HUDControl.VisualAlert.steerRequired, False),
  (True, structs.CarControl.HUDControl.VisualAlert.fcw, True),
])
def test_ascent_secondary_hands_on_payload_is_filtered_only_during_active_lateral(active, visual_alert, masked):
  raw = bytes.fromhex("ce02000104000000")
  address, dat, bus = subarucan.create_ascent_lkas_alert(raw, active, visual_alert)
  assert address == 0x3C4
  assert bus == CanBus.main
  assert dat[1] == raw[1]
  assert dat[0] == ((address & 0xff) + (address >> 8) + sum(dat[1:])) & 0xff
  assert dat[2:] == (bytes(6) if masked else raw[2:])


@pytest.mark.parametrize("payload", [
  "000000000000", "000000110000", "000000100000", "1e0030000000",
  "000704000000", "000000010000", "1b0002000000", "000002000000",
  "0e0000000000", "190000100000", "1c0704000000", "190000110000",
  "190000000000", "190000010000", "010104000000", "000604000000",
])
def test_ascent_secondary_alert_preserves_other_alerts_and_opaque_data(payload):
  raw = bytearray(bytes.fromhex("0000" + payload))
  raw[0] = (0xc4 + 3 + sum(raw[1:])) & 0xff
  assert subarucan.create_ascent_lkas_alert(bytes(raw), True, structs.CarControl.HUDControl.VisualAlert.none)[1] == raw


@pytest.mark.parametrize("raw", [b"", bytes(7), bytes(9), bytes.fromhex("0002000104000000")])
def test_ascent_secondary_alert_does_not_transmit_invalid_data(raw):
  assert subarucan.create_ascent_lkas_alert(raw, True, structs.CarControl.HUDControl.VisualAlert.none) is None


def test_ascent_secondary_alert_uses_fresh_camera_counter_and_actual_angle_request():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  CP.flags |= SubaruFlags.ASCENT_LKAS_ALERT.value
  controller = CarController({}, CP)
  CC = SimpleNamespace(latActive=True, hudControl=SimpleNamespace(visualAlert=structs.CarControl.HUDControl.VisualAlert.none))
  CS = SimpleNamespace(es_lkas_alert_dat=bytes.fromhex("ce02000104000000"))
  assert controller._ascent_lkas_alert(CC, CS)[1] == CS.es_lkas_alert_dat
  assert controller._ascent_lkas_alert(CC, CS) is None

  controller.angle_lkas_active = True
  for counter in (3, 15, 0):
    dat = bytearray(CS.es_lkas_alert_dat)
    dat[1] = counter
    dat[0] = (0xc4 + 3 + sum(dat[1:])) & 0xff
    CS.es_lkas_alert_dat = bytes(dat)
    msg = controller._ascent_lkas_alert(CC, CS)
    assert msg[1][1] == counter
    assert msg[1][2:] == bytes(6)
    assert controller._ascent_lkas_alert(CC, CS) is None


@pytest.mark.parametrize("platform", [CAR.SUBARU_ASCENT, CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_LEGACY_2025, CAR.SUBARU_CROSSTREK_2025])
def test_secondary_alert_controller_does_not_touch_other_subarus(platform):
  CP = CarInterface.get_non_essential_params(platform)
  CP.flags |= SubaruFlags.ASCENT_LKAS_ALERT.value
  controller = CarController({}, CP)
  assert controller._ascent_lkas_alert(None, None) is None


@pytest.mark.parametrize("platform", [CAR.SUBARU_ASCENT_2023, CAR.SUBARU_OUTBACK_2023, CAR.SUBARU_LEGACY_2025,
                                      CAR.SUBARU_CROSSTREK_2025, CAR.SUBARU_ASCENT])
def test_stock_cruise_cancel_fresh_frame_gate_is_ascent_angle_only(platform):
  CP = CarInterface.get_non_essential_params(platform)
  controller = CarController({}, CP)
  CC = structs.CarControl()
  CC.cruiseControl.cancel = True
  CS = SimpleNamespace(
    out=structs.CarState(),
    es_distance_msg=defaultdict(int, COUNTER=11),
    es_dashstatus_msg=defaultdict(int),
    es_lkas_state_msg=defaultdict(int),
    es_infotainment_msg=defaultdict(int),
  )
  toggles = SimpleNamespace(subaru_sng=False)
  cancel_messages = []
  for frame in range(5):
    _, sends = controller.update(CC.as_reader(), CS, frame * 10_000_000, toggles)
    cancel_messages.extend(msg for msg in sends if msg[0] == 0x221)

  assert len(cancel_messages) == (1 if platform == CAR.SUBARU_ASCENT_2023 else 5)
  assert all(msg[2] == (CanBus.alt if CP.flags & SubaruFlags.GLOBAL_GEN2 else CanBus.main) for msg in cancel_messages)
  parser = CANParser(DBC[platform][Bus.pt], [("ES_Distance", 0)], cancel_messages[0][2])
  parser.update([(1, [cancel_messages[0]])])
  assert parser.vl["ES_Distance"]["COUNTER"] == 12
  assert parser.vl["ES_Distance"]["Cruise_Cancel"] == 1
  assert parser.vl["ES_Distance"]["Cruise_Throttle"] == 1818


def test_ascent_cancel_uses_fresh_stock_frames_and_handles_counter_rollover():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_ASCENT_2023)
  controller = CarController({}, CP)
  CC = structs.CarControl()
  CC.latActive = True
  CS = SimpleNamespace(
    out=structs.CarState(vEgoRaw=24.04, steeringAngleDeg=2.93, gearShifter="drive"),
    es_distance_msg=defaultdict(int, COUNTER=14),
    es_dashstatus_msg=defaultdict(int),
    es_lkas_state_msg=defaultdict(int),
    es_infotainment_msg=defaultdict(int),
  )
  CS.out.cruiseState.available = True
  toggles = SimpleNamespace(subaru_sng=False)
  parser = CANParser(DBC[CP.carFingerprint][Bus.pt], [("ES_Distance", 0)], CanBus.alt)
  cancel_counters = []
  for frame, (stock_counter, cancel) in enumerate([
    (14, False), (14, True), (15, True), (15, True), (0, True), (0, True),
    (1, False), (1, True), (2, True),
  ]):
    CS.es_distance_msg["COUNTER"] = stock_counter
    CC.cruiseControl.cancel = cancel
    _, sends = controller.update(CC.as_reader(), CS, frame * 10_000_000, toggles)
    cancel_messages = [msg for msg in sends if msg[0] == 0x221]
    assert len(cancel_messages) <= 1
    if cancel_messages:
      parser.update([(frame + 1, cancel_messages)])
      cancel_counters.append(parser.vl["ES_Distance"]["COUNTER"])
      assert parser.vl["ES_Distance"]["Cruise_Cancel"] == 1
      assert parser.vl["ES_Distance"]["Cruise_Throttle"] == 1818

  assert cancel_counters == [0, 1, 3]


def test_other_angle_cars_keep_lateral_status_behavior():
  CP = CarInterface.get_non_essential_params(CAR.SUBARU_CROSSTREK_2025)
  controller = CarController({}, CP)
  controller.angle_lkas_active = False

  assert controller._lkas_status_active(SimpleNamespace(latActive=True))
