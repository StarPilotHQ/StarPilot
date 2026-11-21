"""Inactive or stalled openpilot hands every replaced address back to the stock frames.

openpilot sends nothing while lateral is inactive, and panda only blocks a stock frame
while openpilot's replacement for that address is recent (volvo_cma.h VOLVO_REPLACED_MSGS).
"""
from types import SimpleNamespace
import pytest
from opendbc.car import structs
from opendbc.car.volvo.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.volvo.carcontroller import CarController
from opendbc.car.volvo.helpers import checksum_lca_5_message
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.tests.test_cma import CMA, REPLACED, STOCK_BUS, _Actuators, _state, interface, packet, sources


@pytest.mark.parametrize('identity', CMA)
def test_stock_frames_resume_when_openpilot_stops_sending(identity):
  """The panda timing fallback: a replaced address is only blocked while openpilot's
  replacement is recent, so stock frames flow again if openpilot disengages or stalls."""
  cp, ci = interface(identity)
  lib = libsafety_py.libsafety
  assert lib.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  lib.init_tests()
  for address, bus in STOCK_BUS.items():
    assert lib.safety_fwd_hook(bus, address) != -1, hex(address)

  cc = structs.CarControl.new_message()
  cc.latActive = True
  sent = set()
  for frame in range(4):
    now = 1_000_000_000 + frame * 10_000_000
    lib.set_timer(frame * 10000)
    incoming = sources(identity, frame=frame)
    for msg in incoming:
      lib.safety_rx_hook(packet(msg))
    ci.update([(now, incoming)])
    _, output = ci.apply(cc.as_reader(), now)
    for msg in output:
      assert lib.safety_tx_hook(packet(msg))
      sent.add(msg[0])
  assert sent == {address for address, _ in REPLACED}
  for address in sent:
    assert lib.safety_fwd_hook(STOCK_BUS[address], address) == -1, hex(address)

  # openpilot goes quiet; after the longest replacement timeout every stock frame forwards again.
  lib.set_timer(30000 + 100001)
  for address in sent:
    assert lib.safety_fwd_hook(STOCK_BUS[address], address) != -1, hex(address)


@pytest.mark.parametrize("fingerprint", [CAR.POLESTAR_2, CAR.VOLVO_XC40_RECHARGE])
def test_controller_leaves_lca5_to_panda_forwarding_when_inactive(fingerprint):
  # While inactive, the controller must not TX an LCA_5 substitute at all: panda only
  # blocks the stock frame while openpilot is recently sending one (see
  # volvo_fwd_hook), so the real stock frame flows through untouched.
  cp = CarInterface.get_non_essential_params(fingerprint)
  controller = CarController(DBC[cp.carFingerprint], cp)
  cs = _state()
  cc = SimpleNamespace(latActive=False, actuators=_Actuators())

  for _ in range(4):
    _, can_sends = controller.update(cc, cs, 0)
    assert not any(msg[0] == 0x67 for msg in can_sends)

  # Once active, the stock counter is passed straight through (LCA_5 is 50 Hz, so
  # step until it's sent)
  cs.msg_lca_5["COUNTER"] = 7
  cc.latActive = True
  sent = []
  for _ in range(2):
    _, can_sends = controller.update(cc, cs, 0)
    sent += [msg for msg in can_sends if msg[0] == 0x67]
  assert len(sent) == 1
  data = sent[0][1]
  assert data[3] >> 4 == 7
  assert data[2] == checksum_lca_5_message(data[0], data[1], data[3], data[4], data[5])
