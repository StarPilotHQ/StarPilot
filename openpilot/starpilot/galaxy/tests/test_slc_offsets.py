"""Converted offsets stay readable and editable through the Galaxy settings API."""

from types import SimpleNamespace
from typing import cast

import pytest

from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.galaxy.settings import AuthorityContext, ContextSource, SettingsChanged, SettingsGateway
from openpilot.starpilot.speed_limits import offset_document as od


@pytest.mark.parametrize("metric", (False, True))
def test_converted_offset_slider_uses_whole_steps_in_both_units(tmp_path, metric):
  params = Params(str(tmp_path))
  params.put_bool("IsMetric", metric, block=True)
  cp = CarInterface.get_non_essential_params(CAR.TOYOTA_COROLLA_TSS2)
  cp.openpilotLongitudinalControl, cp.pcmCruise = True, False
  cp_raw = cp.to_bytes()
  context = SimpleNamespace(sample=lambda: AuthorityContext(True, cp, cp_raw, metric=metric))
  gateway = SettingsGateway(params, cast(ContextSource, context))
  try:
    source = 3 * od.MPH_TO_MPS if metric else 5 * od.KPH_TO_MPS
    document = od.OffsetDocument(od.default_document(not metric).bounds_mps, (source,) * 7)
    params.put("SLCOffsetSchedule", od.to_value(document), block=True)
    page = gateway.page("slc", "session", b"generation")
    index = next(i for i, row in enumerate(page["rows"]) if row["label"].endswith("offset"))
    expected = 5 if metric else 3
    assert page["rows"][index]["value"] == str(expected)
    with pytest.raises(SettingsChanged):
      gateway.preview(page["view"], index, 0, "session", b"generation", value=4.83)
    intent = gateway.preview(page["view"], index, 0, "session", b"generation", value=expected + 1)
    assert gateway.confirm(intent["intent"], "session", b"generation")
    factor = od.KPH_TO_MPS if metric else od.MPH_TO_MPS
    assert params.get("SLCOffsetSchedule")["offsets_mps"][0] == pytest.approx((expected + 1) * factor)
    assert gateway.page("slc", "session", b"generation")["rows"][index]["value"] == str(expected + 1)
  finally:
    gateway.close()
