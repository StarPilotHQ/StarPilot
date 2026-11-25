"""Light driver contact gives one brief authority dip; override latching has hysteresis."""
import pytest
from opendbc.car.volvo.values import CarControllerParams as P
from opendbc.car.volvo.tests.test_cma_driver_authority import DT, REBUILD_STEP, _run, _saturated


def test_light_contact_gives_brief_dip_without_latching():
  # Resting hands: a step to a torque above LIGHT_THRESH but below the override
  # threshold. Authority dips for LIGHT_HOLD_FRAMES, then rebuilds and doesn't
  # re-arm while the torque stays constant.
  light = P.LCA_AUTH_OVERRIDE_ENTER - 1
  assert P.LCA_AUTH_LIGHT_THRESH < light < P.LCA_AUTH_OVERRIDE_ENTER
  step = P.LCA_AUTH_COLLAPSE_RATE * max(1.0, light / P.LCA_AUTH_OVERRIDE_ENTER) * DT
  dipped = P.LCA_AUTH_MAX - P.LCA_AUTH_LIGHT_HOLD_FRAMES * step
  assert dipped > P.LCA_AUTH_SPLIT  # stays in the symmetric phase for this check

  env = _saturated()
  pos, neg = _run(env, light, P.LCA_AUTH_LIGHT_HOLD_FRAMES)
  assert pos == pytest.approx(dipped)
  assert neg == pytest.approx(-dipped)

  prev = pos
  for _ in range(100):
    pos, neg = env.update(light)
    assert pos > prev  # rebuilding, never dipping again
    assert neg == pytest.approx(-pos)
    prev = pos
  assert pos == pytest.approx(dipped + 100 * REBUILD_STEP)


def test_override_latch_hysteresis():
  between = (P.LCA_AUTH_OVERRIDE_ENTER + P.LCA_AUTH_OVERRIDE_EXIT) / 2
  assert P.LCA_AUTH_OVERRIDE_EXIT < between < P.LCA_AUTH_OVERRIDE_ENTER

  env = _saturated()
  _run(env, P.LCA_AUTH_OVERRIDE_ENTER + 1, 50)  # enter override

  # Dropping between EXIT and ENTER stays latched: no rebuild toward saturation.
  for _ in range(100):
    pos, neg = env.update(between)
    assert pos <= P.LCA_AUTH_YIELD_BASE
    assert neg == pytest.approx(-P.LCA_AUTH_PLATEAU_COUNTER)

  # Below EXIT the latch releases and both arms rebuild.
  pos_latched, neg_latched = pos, neg
  below = P.LCA_AUTH_OVERRIDE_EXIT - 1
  assert below < P.LCA_AUTH_LIGHT_THRESH  # so this isn't also a light-contact edge
  pos, neg = _run(env, below, 50)
  assert pos == pytest.approx(pos_latched + 50 * REBUILD_STEP)
  assert neg == pytest.approx(neg_latched - 50 * REBUILD_STEP)
