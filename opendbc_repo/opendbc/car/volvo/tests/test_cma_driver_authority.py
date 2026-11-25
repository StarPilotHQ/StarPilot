"""Parsed driver input drives the LCA torque-authority envelope through safety."""
import pytest
from opendbc.car.volvo.helpers import LcaAuthorityEnvelope
from opendbc.car.volvo.values import CarControllerParams as P
from types import SimpleNamespace
from opendbc.car.volvo.carcontroller import CarController
from opendbc.car.volvo.interface import CarInterface
from opendbc.car.volvo.values import CAR, DBC
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.car.volvo.tests.test_cma import _Actuators, _state


DT = 0.01  # the envelope runs once per 100 Hz controller frame


REBUILD_STEP = P.LCA_AUTH_REBUILD_RATE * DT


def _run(env, torque, frames):
  for _ in range(frames):
    pos, neg = env.update(torque)
  return pos, neg


def _saturated():
  env = LcaAuthorityEnvelope()
  frames_to_saturate = int(P.LCA_AUTH_MAX / REBUILD_STEP) + 2
  assert _run(env, 0.0, frames_to_saturate) == (P.LCA_AUTH_MAX, -P.LCA_AUTH_MAX)
  return env


def test_rebuilds_from_zero_to_saturation():
  env = LcaAuthorityEnvelope()
  pos, neg = _run(env, 0.0, 100)
  assert pos == pytest.approx(100 * REBUILD_STEP)
  assert neg == pytest.approx(-100 * REBUILD_STEP)
  _saturated()


@pytest.mark.parametrize("torque", [20.0, -20.0])
def test_sustained_push_yields_toward_driver_and_holds_counter_arm(torque):
  # Well above the override threshold: the yield arm (the side the driver pushes
  # toward) collapses to YIELD_MIN, the counter arm holds at the plateau.
  env = _saturated()
  pos, neg = _run(env, torque, 500)
  if torque > 0:  # pushing right yields the + arm
    assert pos == pytest.approx(P.LCA_AUTH_YIELD_MIN)
    assert neg == pytest.approx(-P.LCA_AUTH_PLATEAU_COUNTER)
  else:
    assert pos == pytest.approx(P.LCA_AUTH_PLATEAU_COUNTER)
    assert neg == pytest.approx(-P.LCA_AUTH_YIELD_MIN)


def test_release_rebuilds_at_rebuild_rate():
  env = _saturated()
  pos0, neg0 = _run(env, 20.0, 500)
  pos, neg = _run(env, 0.0, 10)
  assert pos == pytest.approx(pos0 + 10 * REBUILD_STEP)
  assert neg == pytest.approx(neg0 - 10 * REBUILD_STEP)


def test_reset_starts_over_from_zero():
  env = _saturated()
  _run(env, 20.0, 500)
  env.reset()
  assert (env.pos, env.neg) == (0.0, 0.0)
  pos, neg = env.update(0.0)
  assert pos == pytest.approx(REBUILD_STEP)
  assert neg == pytest.approx(-REBUILD_STEP)


@pytest.mark.parametrize("fingerprint", [CAR.POLESTAR_2, CAR.VOLVO_XC40_RECHARGE])
@pytest.mark.parametrize("driver_torque", [20.0, -20.0, 128.0, -127.0])
def test_override_lca_stream_passes_safety_and_recovers(fingerprint, driver_torque):
  cp = CarInterface.get_non_essential_params(fingerprint)
  controller = CarController(DBC[cp.carFingerprint], cp)
  cs = _state()
  cc = SimpleNamespace(latActive=True, actuators=_Actuators())
  safety = libsafety_py.libsafety
  config = cp.safetyConfigs[0]
  assert safety.set_safety_hooks(config.safetyModel.raw, config.safetyParam) == 0
  safety.init_tests()
  safety.set_controls_allowed(True)

  for active, torque in [(True, 0), (True, driver_torque), (True, -driver_torque),
                        (True, 0), (False, 0), (True, 0)]:
    cc.latActive = active
    cs.out.steeringTorque = torque
    for _ in range(350):
      _, messages = controller.update(cc, cs, 0)
      lca_msgs = [msg for msg in messages if msg[0] == 0x58]
      if active:
        address, data, bus = lca_msgs[0]
        assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (
          active, torque, controller.frame, controller.lca_auth.pos, controller.lca_auth.neg)
      else:
        # Inactive: the controller leaves LCA to panda's forwarding (volvo_fwd_hook),
        # it must not also TX a substitute.
        assert not lca_msgs
    if active and torque == 0:
      assert controller.lca_auth.pos == 614
      assert controller.lca_auth.neg == -614
    elif active:
      assert min(abs(controller.lca_auth.pos), abs(controller.lca_auth.neg)) == 0
