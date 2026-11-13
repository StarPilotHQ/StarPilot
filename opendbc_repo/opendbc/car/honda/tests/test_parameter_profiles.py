import numpy as np
import pytest

from opendbc.can import CANPacker
from opendbc.can.dbc import DBC as CANDBC
from opendbc.can.parser import CANParser
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.honda import hondacan
from opendbc.car.honda.carcontroller import CarController
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.parameter_profiles import forecast_should_stop, stopping_decel_rate
from opendbc.car.honda.values import CAR, DBC, HondaFlags

PROFILES = {
  CAR.HONDA_CITY_7G: None,
  CAR.HONDA_HRV_3G: None,
  CAR.ACURA_RDX_3G: ([0, 4095], [0, 4095]),
  CAR.ACURA_RDX_3G_MMR: ([0, 3840], [0, 3840]),
  CAR.HONDA_PILOT: ([0, 4096], [0, 4096]),
  CAR.HONDA_PILOT_4G: ([0, 4096], [0, 4096]),
  CAR.ACURA_MDX_4G: ([0, 2560, 4209], [0, 2560, 9150]),
  CAR.ACURA_MDX_4G_MMR: ([0, 2560, 4920], [0, 2560, 12000]),
  CAR.ACURA_TLX_2G_MMR: ([0, 4096], [0, 4096]),
  CAR.HONDA_PASSPORT_4G: ([0, 2560, 5120], [0, 2560, 12789]),
}


def factory(candidate, alpha=False):
  fp = gen_empty_fingerprint()
  initial = CarInterface.get_params(candidate, fp, [], alpha, False, False)
  messages = CANDBC(DBC[candidate][Bus.pt]).name_to_msg
  message = messages.get("GEARBOX_AUTO") or messages.get("GEARBOX_CVT")
  if message is not None:
    fp[hondacan.CanBus(initial).pt][message.address] = message.size
  return CarInterface.get_params(candidate, fp, [], alpha, False, False)


@pytest.mark.parametrize("candidate", PROFILES)
def test_original_factory_and_actual_controller_mapping(candidate):
  cp = factory(candidate)
  controller = CarController(DBC[candidate], cp)
  mapping = PROFILES[candidate]
  if mapping is not None:
    bp, values = mapping
    assert controller.params.STEER_MAX == bp[-1]
    for torque in np.linspace(-bp[-1], bp[-1], 101):
      expected = np.sign(torque) * np.interp(abs(torque), bp, values)
      actual = np.interp(torque, controller.params.STEER_LOOKUP, controller.params.STEER_LOOKUP_V)
      assert actual == pytest.approx(expected, abs=1e-9)
  if candidate in (CAR.HONDA_PILOT, CAR.HONDA_PILOT_4G, CAR.ACURA_TLX_2G_MMR):
    assert cp.lateralTuning.which() == "pid"
    assert cp.lateralTuning.pid.kf == pytest.approx(0.00006)
    assert list(cp.lateralTuning.pid.kpBP) == [0, 10]
    assert list(cp.lateralTuning.pid.kpV) == pytest.approx([0.05, 0.5])
    assert list(cp.lateralTuning.pid.kiBP) == [0, 10]
    assert list(cp.lateralTuning.pid.kiV) == pytest.approx([0.0125, 0.125])
  if candidate == CAR.HONDA_HRV_3G:
    assert cp.longitudinalActuatorDelay == pytest.approx(0.4)
  if candidate in (CAR.ACURA_RDX_3G_MMR, CAR.HONDA_PILOT_4G, CAR.ACURA_MDX_4G_MMR):
    assert cp.steerActuatorDelay == pytest.approx(0.1)
  if candidate == CAR.ACURA_RDX_3G_MMR:
    assert cp.minSteerSpeed == pytest.approx(70 / 3.6)


@pytest.mark.parametrize("active", (False, True))
def test_mdx_tja_actual_wire(active):
  cp = factory(CAR.ACURA_MDX_4G)
  assert cp.flags & HondaFlags.BOSCH_TJA_CONTROL
  packer = CANPacker(DBC[CAR.ACURA_MDX_4G][Bus.pt])
  buses = hondacan.CanBus(cp)
  address, data, bus = hondacan.create_steering_control(packer, buses, 100, active, True)
  assert address == 0xE4 and len(data) == 5 and bus == buses.lkas
  parser = CANParser(DBC[CAR.ACURA_MDX_4G][Bus.pt], [("STEERING_CONTROL", 100)], bus)
  parser.update([(1, [(address, data, bus)])])
  assert parser.vl["STEERING_CONTROL"]["STEER_DOWN_TO_ZERO"] == int(active)
  assert parser.vl["STEERING_CONTROL"]["STEER_TORQUE"] == (100 if active else 0)


@pytest.mark.parametrize("candidate", (CAR.HONDA_CIVIC, CAR.HONDA_CIVIC_BOSCH, CAR.HONDA_CITY_7G))
def test_admitted_honda_longitudinal_ramp(candidate):
  from openpilot.starpilot.longitudinal.extension import create_extension
  cp = factory(candidate, True)
  assert cp.openpilotLongitudinalControl
  assert stopping_decel_rate(cp) == 0.3
  extension = create_extension(cp)
  assert extension is not None and extension.stopping_decel_rate == 0.3
  for field in ("passive", "dashcamOnly", "notCar"):
    negative = cp.as_reader().as_builder()
    setattr(negative, field, True)
    assert stopping_decel_rate(negative) is None
  cp.brand = "toyota"
  assert stopping_decel_rate(cp) is None
  cp.brand = "honda"
  cp.openpilotLongitudinalControl = False
  assert stopping_decel_rate(cp) is None


def test_city_two_horizon_forecast_boundaries():
  from openpilot.starpilot.longitudinal.vehicle_policy import forecast_should_stop as reached_forecast
  cp = factory(CAR.HONDA_CITY_7G, True)
  times = [0, 0.5, 1.5, 3]
  for speeds, expected in (([1.9] * 4, True), ([2.0] * 4, False), ([1, 1, 2.1, 3], False)):
    assert forecast_should_stop(cp, speeds, times, 0.5) is expected
    assert reached_forecast(cp, speeds, times, 0.5, not expected) is expected
  cp.openpilotLongitudinalControl = False
  assert forecast_should_stop(cp, [0] * 4, times, 0.5) is None
  assert reached_forecast(cp, [0] * 4, times, 0.5, False) is False


@pytest.mark.parametrize("candidate", [identity for identity, mapping in PROFILES.items() if mapping is not None])
def test_reached_controller_emits_original_steering_map(candidate):
  from opendbc.car import structs
  cp = factory(candidate)
  ci = CarInterface(cp)
  ci.update([])
  command = structs.CarControl(enabled=True, latActive=True, longActive=False)
  command.actuators.torque = 0.6
  parser = CANParser(DBC[candidate][Bus.pt], [("STEERING_CONTROL", 100)], ci.CC.CAN.lkas)
  mapping = PROFILES[candidate]
  assert mapping is not None
  bp, values = mapping
  limited = 0.0
  for tick in range(25):
    _, frames = ci.apply(command.as_reader(), 1_000_000_000 + tick * 10_000_000)
    steering = [frame for frame in frames if frame[0] in (0xE4, 0x194)]
    assert len(steering) == 1
    parser.update([(1_000_000_000 + tick * 10_000_000, steering)])
    limited = min(0.6, limited + 0.03)
    expected = int(-np.interp(limited * bp[-1], bp, values))
    assert parser.vl["STEERING_CONTROL"]["STEER_TORQUE"] == expected


@pytest.mark.parametrize("alpha", (False, True))
def test_crv_standard_eps_crawl_feedback_preserves_high_speed_and_wire_limits(alpha):
  from openpilot.common.pid import PIDController
  cp = factory(CAR.HONDA_CRV_5G, alpha)
  tune = cp.lateralTuning.pid
  pid = PIDController((tune.kpBP, tune.kpV), (tune.kiBP, tune.kiV), pos_limit=1., neg_limit=-1.)
  # Small alternating near-stop errors must not demand full alternating torque.
  for speed in (0., 1., 3., 5.):
    outputs = [pid.update(error, speed=speed, freeze_integrator=True) for error in (1.5, -1.5)]
    assert max(abs(value) for value in outputs) <= .75 * (.64 * 1.5)
    assert outputs[0] == pytest.approx(-outputs[1])
  for speed in (10., 20., 35.):
    pid.speed = speed
    assert pid.k_p == pytest.approx(.64)
    assert pid.k_i == pytest.approx(.192)
  assert cp.lateralTuning.pid.kf == pytest.approx(.00006)
  controller = CarController(DBC[CAR.HONDA_CRV_5G], cp)
  assert controller.params.STEER_MAX == 3840
  assert controller.params.STEER_LOOKUP == [-3840, 0, 3840]
  assert controller.params.STEER_LOOKUP_V == [-3840, 0, 3840]
