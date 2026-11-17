import pytest
from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.car.toyota.tests import test_pedal_caller_native as helpers

source_matched_native = helpers.source_matched_native
WORDS = (0x8049, 0x8249, 0x8449, 0x8649)


class Physical:
  def __init__(self, word, experience=32):
    self.native = libsafety_py.libsafety
    self.native.init_tests()
    self.native.set_alternative_experience(experience)
    assert self.native.set_safety_hooks(structs.CarParams.SafetyModel.toyota, word) == 0
    self.native.set_aol_test_heartbeat(True)
    self.word, self.now = word, 1_000_000
    self.packer = CANPacker("toyota_nodsu_pt_generated")

  def tick(self, *, cruise=True, status=0, bad=None, **fields):
    self.now += 10_000
    self.native.set_timer(self.now)
    frames = helpers.physical(self.packer, cruise=cruise, **fields)
    frames = [f for f in frames if f[0] not in (0x260, 0x262)]
    frames += [
      self.packer.make_can_msg("STEER_TORQUE_SENSOR", 0, {"STEER_ANGLE": 0.0573}),
      self.packer.make_can_msg("EPS_STATUS", 0, {"LKA_STATE": fields.get("eps", 1), "LTA_STATE": fields.get("eps", 1)}),
    ]
    if status is not None:
      frames.append(self.packer.make_can_msg("SDSU", 0, {"STATE": status}))
    for address, data, bus in frames:
      if address == 0x262 and bad == "eps_checksum":
        data = data[:-1] + bytes([data[-1] ^ 1])
      if address == 0x262 and bad == "eps_length":
        data = data[:5]
      if address == 0x2FF and bad == "status_length":
        data = data[:7]
      if address == 0x2FF and bad == "status_bus":
        bus = 2
      self.native.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    self.native.safety_tick()
    self.native.aol_set_host_request(3)

  def accel(self, value=0.1, cancel=0):
    a, d, b = self.packer.make_can_msg("ACC_CONTROL", 0, {"ACCEL_CMD": value, "CANCEL_REQ": cancel})
    return self.native.safety_tx_hook(libsafety_py.make_CANPacket(a, b, d))

  def close(self):
    self.native.set_alternative_experience(0)
    self.native.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)


@pytest.mark.parametrize("word", WORDS)
def test_status_lease_state_aeb_and_recovery(word):
  case = Physical(word)
  try:
    assert case.native.aol_get_permission_mask() == 0
    for _ in range(25):
      case.tick(cruise=False, status=None)
    assert case.native.aol_get_permission_mask() == 0
    for _ in range(25):
      case.tick(cruise=False)
    assert case.native.aol_get_permission_mask() == 1
    for _ in range(25):
      case.tick()
    expected = 1 if word & 0x200 else 3
    assert case.native.aol_get_permission_mask() == expected
    assert bool(case.accel()) == (expected == 3)
    case.tick(status=7)
    assert case.native.safety_config_valid()
    assert case.native.aol_get_permission_mask() == 1 and not case.accel()
    case.tick(status=2)
    assert not case.native.safety_config_valid()
    assert case.native.aol_get_permission_mask() == 0 and not case.accel()
    for _ in range(25):
      case.tick(cruise=False)
    for _ in range(25):
      case.tick()
    assert case.native.aol_get_permission_mask() == expected
  finally:
    case.close()


@pytest.mark.parametrize("word", WORDS)
@pytest.mark.parametrize("bad", ["eps_checksum", "eps_length", "status_length", "status_bus"])
def test_malformed_or_wrong_bus_cannot_refresh_required_sources(word, bad):
  case = Physical(word)
  try:
    for _ in range(25):
      case.tick(cruise=False)
    for _ in range(25):
      case.tick()
    assert case.native.aol_get_permission_mask() == (1 if word & 0x200 else 3)
    for _ in range(25):
      case.tick(bad=bad)
    assert case.native.aol_get_permission_mask() == 0 and not case.accel()
  finally:
    case.close()


@pytest.mark.parametrize("word", WORDS)
@pytest.mark.parametrize("fault", ["main", "gear", "eps", "brake", "gas"])
def test_physical_permission_withdrawal(word, fault):
  case = Physical(word)
  try:
    for _ in range(25):
      case.tick(cruise=False)
    for _ in range(25):
      case.tick()
    case.tick(**{"main": {"main": False}, "gear": {"gear": 16}, "eps": {"eps": 9}, "brake": {"brake": True}, "gas": {"gas": True}}[fault])
    assert case.native.aol_get_permission_mask() == (1 if fault in ("brake", "gas") else 0)
    assert not case.accel()
  finally:
    case.close()


@pytest.mark.parametrize("word", WORDS)
def test_ordinary_cruise_and_stock_cancel_only(word):
  case = Physical(word, 0)
  try:
    for _ in range(25):
      case.tick(cruise=False)
    for _ in range(25):
      case.tick()
    assert case.native.get_controls_allowed()
    assert bool(case.accel()) == (not word & 0x200)
    assert case.accel(0, 1)
    for value in (-3.5, 3.5):
      assert bool(case.accel(value)) == (not word & 0x200)
    for value in (-3.501, 3.501):
      assert not case.accel(value)
    for _ in range(25):
      case.tick(status=None)
    assert not case.accel()
  finally:
    case.close()


@pytest.mark.parametrize("word", [0x8048, 0x804A, 0x8149, 0x8248, 0x8448, 0x844A, 0x8648, 0x8749, 0xA049])
def test_unqualified_adjacent_words_have_no_commands(word):
  case = Physical(word)
  try:
    for _ in range(25):
      case.tick(cruise=False)
    for _ in range(25):
      case.tick()
    assert case.native.aol_get_permission_mask() == 0 and not case.accel()
  finally:
    case.close()
