"""Every Galaxy speed/distance slider (shown in its own units or converted by Galaxy speed / distance units) uses a
clean step and round range ends, and a value picked on it saves and then shows exactly that value again. Picking a
round end saves the setting's own limit. Runs every settings page for a Tesla Model 3 and a Toyota, both comma units,
and Galaxy units Same as comma / Metric / Imperial."""
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import math
import tempfile

import pytest

from opendbc.car import gen_empty_fingerprint
from opendbc.car.car_helpers import interfaces
from opendbc.car.tesla.values import CAR as TESLA
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.params import Params
from openpilot.starpilot.galaxy import slider_units
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, PAGES, SettingsChanged, SettingsGateway, SettingsUnavailable


def _gateway(params, platform, metric):
  cp = interfaces[platform].get_params(platform, gen_empty_fingerprint(), [], False, False, False)
  cp.openpilotLongitudinalControl = True
  context = SimpleNamespace(value=AuthorityContext(True, cp, cp.to_bytes(), metric=metric))
  context.sample = lambda: context.value
  return SettingsGateway(params, cast(ContextSource, context), clock=lambda: 100.0)


def _sliders(gateway, name, session, generation):
  """Available speed/distance sliders on a page, with each setting's own row (unconverted) for comparison."""
  try:
    page = gateway.page(name, session, generation)
    native = {row.label: row for row in gateway._base_state(name, gateway._context(name)).rows}
  except (SettingsUnavailable, SettingsChanged):
    return None, []
  found = []
  for index, row in enumerate(page["rows"]):
    own = native.get(row["label"]) or next((r for r in native.values() if r.key and r.unit in slider_units.SI and
                                                 slider_units.convert_text(r.label, r.unit, row["unit"]) == row["label"]), None)
    if (own is not None and row["step"] and row["available"] and row["action"] and row["unit"] in slider_units.SI and
        _numeric(row["value"]) and slider_units.category(own) is not None):
      found.append((index, row, own))
  return page, found


def _numeric(value):
  try:
    return math.isfinite(float(value))
  except (TypeError, ValueError):
    return False


def _on(value, step):
  return math.isclose(value / step, round(value / step), rel_tol=0, abs_tol=1e-6)


def _round_end(value, step, own_unit_end):
  return math.isclose(value, slider_units.sensible(value, step), abs_tol=1e-6) or own_unit_end


UNITS = ((None, None), (b"1", b"1"), (b"2", b"2"))
CASES = [(platform, metric, speed, distance) for platform in (TESLA.TESLA_MODEL_3, TOYOTA.TOYOTA_COROLLA_TSS2)
         for metric in (False, True) for speed, distance in UNITS]


@pytest.mark.parametrize("platform,metric,speed,distance", CASES)
def test_every_slider_is_clean_and_round_trips(platform, metric, speed, distance):
  with tempfile.TemporaryDirectory() as folder:
    params = Params(folder)
    params.put_bool("IsMetric", metric, block=True)
    for key, raw in (("GalaxySpeedUnits", speed), ("GalaxySmallDistanceUnits", distance)):
      if raw is not None:
        Path(params.get_param_path(key)).write_bytes(raw)
    gateway = _gateway(params, platform, metric)
    session, generation = "units-session", b"generation"
    checked = []
    try:
      for name in sorted(PAGES):
        _, sliders = _sliders(gateway, name, session, generation)
        for _, row, own in sliders:
          label, step = row["label"], float(row["step"])
          low, high, value = float(row["minimum"]), float(row["maximum"]), float(row["value"])
          assert any(math.isclose(step, n) for n in slider_units.NICE_STEPS) or math.isclose(step, own.step), (name, label, step)
          assert _on(low, step) and _on(high, step) and _on(value, step), (name, label, low, high, value, step)
          assert low <= value <= high, (name, label, low, value, high)
          same_units = row["unit"] == own.unit
          assert _round_end(low, step, same_units and math.isclose(low, own.minimum)), (name, label, "low", low)
          assert _round_end(high, step, same_units and math.isclose(high, own.maximum)), (name, label, "high", high)
          middle = round(round((low + high) / 2 / step) * step, 6)
          for pick in [p for p in dict.fromkeys((high, middle, low)) if not math.isclose(p, value)]:
            page, now = _sliders(gateway, name, session, generation)
            index = next(i for i, r, _ in now if r["label"] == label)
            intent = gateway.preview(page["view"], index, 0, session, generation, value=pick)["intent"]
            assert gateway.confirm(intent, session, generation), (name, label, pick)
            _, again = _sliders(gateway, name, session, generation)
            shown, saved = next((r, o) for _, r, o in again if r["label"] == label)
            assert math.isclose(float(shown["value"]), pick, abs_tol=1e-6), (name, label, row["unit"], pick, shown["value"])
            if math.isclose(pick, high):
              assert math.isclose(float(saved.value), own.maximum, abs_tol=own.step * 1e-3), (name, label, "top end saves the maximum", saved.value)
            if math.isclose(pick, low):
              assert math.isclose(float(saved.value), own.minimum, abs_tol=own.step * 1e-3), (name, label, "bottom end saves the minimum", saved.value)
            checked.append((name, label, row["unit"], f"{low:g}-{high:g} step {step:g}", pick))
    finally:
      gateway.close()
    assert checked, "no sliders were checked"
    print(f"\n{platform} metric={metric} speed={speed} distance={distance}: {len(checked)} saves")
    for item in checked:
      print("  ", item)
