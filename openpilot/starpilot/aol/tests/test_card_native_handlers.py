from itertools import product

import pytest

from opendbc.car.ford import aol as ford
from opendbc.car.honda import stock_aol as honda
from opendbc.car.hyundai import canfd_angle_aol as hyundai
from opendbc.car.mazda import stock_aol as mazda
from opendbc.car.rivian import stock_aol as rivian
from opendbc.car.tesla.preap import aol as preap
from opendbc.car.volkswagen import stock_aol as volkswagen_observer
from openpilot.starpilot.car.nissan import aol as nissan
from openpilot.starpilot.car.volkswagen import aol as volkswagen_restriction
from openpilot.starpilot.car.volvo import aol as volvo
from openpilot.starpilot.aol.card_native import native_handlers


class _Card:
  honda_lookups = 0

  def honda_observer(self):
    raise AssertionError("Selection must not call the handler")

  @property
  def aol_honda_native_observation(self):
    self.honda_lookups += 1
    return self.honda_observer


@pytest.mark.parametrize('admitted', product((False, True), repeat=8))
def test_native_handlers_keep_card_priority_and_lazy_honda_binding(admitted):
  card = _Card()
  observations = (volvo.native_observation, rivian.native_observation, volkswagen_observer.native_observation, nissan.native_observation,
                  preap.native_observation, mazda.native_observation, card.honda_observer, ford.native_observation,
                  hyundai.native_observation)
  restrictions = (volvo.temporary_restriction, rivian.temporary_restriction, volkswagen_restriction.temporary_restriction, nissan.temporary_restriction,
                  preap.temporary_restriction, mazda.temporary_restriction, honda.temporary_restriction, ford.temporary_restriction,
                  hyundai.temporary_restriction)
  expected = next((i for i, flag in enumerate(admitted) if flag), len(admitted))
  names = ('volvo_aol', 'rivian_aol', 'volkswagen_aol', 'nissan_aol', 'preap_aol', 'mazda_aol', 'honda_aol', 'ford_aol')
  selected = native_handlers(card, **dict(zip(names, admitted, strict=True)))
  assert selected == (observations[expected], restrictions[expected])
  assert card.honda_lookups == int(expected == 6)
