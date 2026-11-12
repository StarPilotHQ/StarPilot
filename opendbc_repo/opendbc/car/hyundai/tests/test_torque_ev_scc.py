from types import SimpleNamespace

import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.carcontroller import CarController
from opendbc.car.hyundai.canfd_lead import CANFDLeadObservation, select_lead
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.torque_ev_startup import candidate
from opendbc.car.hyundai.torque_ev_scc import eligible, options, fallback_lead
from opendbc.car.hyundai import torque_ev_scc
from opendbc.car.hyundai.canfd_camera_lead import CameraLeadObservation
from opendbc.car.hyundai.values import CAR, DBC


@pytest.fixture(params=[(car, alternate) for car in (CAR.HYUNDAI_IONIQ_5, CAR.HYUNDAI_KONA_EV_2ND_GEN) for alternate in (False, True)])
def cp(request):
  car, alternate = request.param
  fingerprint = gen_empty_fingerprint()
  fingerprint[2].update({0x110: 32, 0x362: 32} if alternate else {0x50: 16, 0x2A4: 24})
  fingerprint[1].update({0x1CF: 8, 0x1AA: 16, 0x35: 32, 0x175: 24, 0xA0: 24, 0xEA: 24, 0x1BA: 24, 0x1E5: 16, 0x36A: 16})
  stock = CarInterface.get_params(car, fingerprint, [], False, False, False)
  return candidate(stock, requested=True, is_release=False)


@pytest.mark.parametrize(
  "enabled,override,available,pid,visible",
  [(False, False, False, False, False), (True, False, True, True, False), (True, True, True, False, True), (True, False, False, False, True)],
)
def test_actual_controller_scc_fields_and_cadence(cp, enabled, override, available, pid, visible):
  assert eligible(cp)
  controller = CarController(DBC[cp.carFingerprint], cp)
  selection = select_lead(CANFDLeadObservation(visible, 26.0, -2.0, 1), None, hud_visible=False, observed_ns=2, epoch_floor_ns=0)
  calls = []

  def selected(camera, hud, now):
    calls.append((camera, hud, now))
    return selection

  controller.torque_ev_lead_inputs = SimpleNamespace(update=selected)
  parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [("SCC_CONTROL", 50)], 1)
  command = structs.CarControl()
  command.enabled = enabled
  command.cruiseControl.override = override
  command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid if pid else structs.CarControl.Actuators.LongControlState.off
  state = SimpleNamespace(
    out=SimpleNamespace(cruiseState=SimpleNamespace(available=available), gearShifter=structs.CarState.GearShifter.drive),
    lfa_block_msg={**{f"BYTE{i}": 0 for i in range(3, 32)}, "COUNTER": 0},
    torque_ev_camera_lead=object(),
  )
  stamps = []
  for frame in range(6):
    controller.frame = frame
    packets = controller.create_canfd_msgs(False, 0, 60.0, 0.8, False, command.hudControl, state, command, (frame + 1) * 10_000_000)
    scc = [packet for packet in packets if packet[0] == 0x1A0]
    assert len(scc) == int(frame % 2 == 0)
    if scc:
      stamps.append(scc[0][1][2])
      parser.update([((frame + 1) * 10_000_000, scc)])
      values = parser.vl["SCC_CONTROL"]
      assert values["MainMode_ACC"] == int(available)
      assert values["ACCMode"] == (0 if not enabled else 2 if override else 1)
      assert values["aReqValue"] == pytest.approx(0.0 if not enabled or override else 0.8, abs=0.01)
      assert values["aReqRaw"] == pytest.approx(0.0 if not enabled or override else 0.8, abs=0.01)
      assert values["JerkLowerLimit"] == 5.0
      assert values["JerkUpperLimit"] == (3.0 if pid else 1.0)
      assert values["ACC_ObjDist"] == (26.0 if visible else 0.0)
      assert values["OBJ_STATUS"] == (0 if not enabled or not visible else 1 if override else 2)
  assert len(calls) == 3
  assert stamps == [(stamps[0] + offset) % 256 for offset in range(3)]


def test_lead_priority_and_expiry():
  radar = CANFDLeadObservation(True, 26.0, -2.0, 2)
  camera = CANFDLeadObservation(True, 12.0, -1.0, 2)

  def choose(radar_value, camera_value, now=3, hud=False):
    return select_lead(radar_value, camera_value, hud_visible=hud, observed_ns=now, epoch_floor_ns=1)

  assert choose(radar, camera).distance == 26.0
  assert choose(None, camera).distance == 12.0
  assert not choose(None, camera, 300_000_003).visible
  assert choose(None, camera, 300_000_003, True).distance == 20.0
  assert not choose(None, None).visible


def test_exact_profile_denials(cp):
  assert eligible(cp)
  for field, value in [
    ("carFingerprint", CAR.HYUNDAI_IONIQ_6),
    ("openpilotLongitudinalControl", False),
    ("pcmCruise", True),
    ("dashcamOnly", True),
    ("passive", True),
    ("notCar", True),
  ]:
    changed = cp.as_reader().as_builder()
    setattr(changed, field, value)
    assert not eligible(changed)
  assert (
    options(False, structs.CarControl.Actuators.LongControlState.off, select_lead(None, None, hud_visible=False, observed_ns=2, epoch_floor_ns=1))[
      "main_mode_acc"
    ]
    == 0
  )


def test_optional_transport_failure_preserves_hud_and_current_camera(monkeypatch):
  monkeypatch.setattr(torque_ev_scc, "clock_boot_ns", lambda: 400_000_000)

  class Camera:
    def current(self, now, floor):
      assert (now, floor) == (400_000_000, 100_000_000)
      return CameraLeadObservation(390_000_000, True, 12.0, -1.0)

  assert fallback_lead(None, True, 100_000_000).distance == 20.0
  assert fallback_lead(Camera(), True, 100_000_000).distance == 12.0
  assert not fallback_lead(None, False, 100_000_000).visible


def test_actual_controller_cluster_lifecycle(cp):
  controller = CarController(DBC[cp.carFingerprint], cp)
  parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [("LFAHDA_CLUSTER", 0)], 1)
  command = structs.CarControl()
  state = SimpleNamespace(
    out=SimpleNamespace(cruiseState=SimpleNamespace(available=True), gearShifter=structs.CarState.GearShifter.drive),
    lfa_block_msg={**{f"BYTE{i}": 0 for i in range(3, 32)}, "COUNTER": 0},
    torque_ev_camera_lead=None,
  )
  for frame in range(120):
    controller.frame = frame
    command.latActive = 5 <= frame < 10 or frame >= 115
    command.enabled = 105 <= frame < 110
    packets = controller.create_canfd_msgs(False, 0, 60.0, 0.0, False, command.hudControl, state, command, (frame + 1) * 10_000_000)
    cluster = [packet for packet in packets if packet[0] == 0x1E0]
    assert len(cluster) == int(frame % 5 == 0)
    if cluster:
      parser.update([((frame + 1) * 10_000_000, cluster)])
      values = parser.vl["LFAHDA_CLUSTER"]
      expected = 2 if command.latActive else 3 if 10 <= frame < 109 else 0
      assert values["LFA_ICON"] == expected
      assert values["HDA_ICON"] == int(command.enabled)


def test_actual_transport_creation_failure_still_delivers_hud_lead(cp, monkeypatch):
  from openpilot.starpilot.controller_extensions import configure_controller
  from openpilot.starpilot.longitudinal import radar_lead_context

  def unavailable(*args, **kwargs):
    raise OSError("unavailable")

  monkeypatch.setattr(radar_lead_context.messaging, "SubMaster", unavailable)
  controller = CarController(DBC[cp.carFingerprint], cp)
  configure_controller(SimpleNamespace(CP=cp, CC=controller), None)
  assert controller.torque_ev_lead_inputs is None
  command = structs.CarControl()
  command.enabled = True
  command.hudControl.leadVisible = True
  state = SimpleNamespace(
    out=SimpleNamespace(cruiseState=SimpleNamespace(available=True), gearShifter=structs.CarState.GearShifter.drive),
    lfa_block_msg={**{f"BYTE{i}": 0 for i in range(3, 32)}, "COUNTER": 0},
    torque_ev_camera_lead=None,
  )
  packets = controller.create_canfd_msgs(False, 0, 60.0, 0.8, False, command.hudControl, state, command, 10_000_000)
  parser = CANParser(DBC[cp.carFingerprint][Bus.pt], [("SCC_CONTROL", 50)], 1)
  parser.update([(10_000_000, [packet for packet in packets if packet[0] == 0x1A0])])
  assert parser.vl["SCC_CONTROL"]["ACC_ObjDist"] == 20.0
  assert parser.vl["SCC_CONTROL"]["OBJ_STATUS"] == 2
