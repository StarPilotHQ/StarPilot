"""Select Card's existing native AOL observation and restriction handlers."""

from opendbc.car.ford.aol import (native_observation as ford_native_observation,
                                  temporary_restriction as ford_temporary_restriction)
from opendbc.car.honda.stock_aol import temporary_restriction as honda_temporary_restriction
from opendbc.car.hyundai.canfd_angle_aol import native_observation, temporary_restriction
from opendbc.car.mazda.stock_aol import (native_observation as mazda_native_observation,
                                         temporary_restriction as mazda_temporary_restriction)
from opendbc.car.rivian.stock_aol import (native_observation as rivian_native_observation,
                                          temporary_restriction as rivian_temporary_restriction)
from opendbc.car.tesla.preap.aol import (native_observation as preap_native_observation,
                                         temporary_restriction as preap_temporary_restriction)
from opendbc.car.volkswagen.stock_aol import native_observation as volkswagen_native_observation
from openpilot.starpilot.car.nissan.aol import (native_observation as nissan_native_observation,
                                                temporary_restriction as nissan_temporary_restriction)
from openpilot.starpilot.car.volkswagen.aol import temporary_restriction as volkswagen_temporary_restriction


def native_handlers(card, *, rivian_aol: bool, volkswagen_aol: bool, nissan_aol: bool, preap_aol: bool,
                    mazda_aol: bool, honda_aol: bool, ford_aol: bool):
  # The caller determines permission. Honda's bound handler is only read if Honda wins.
  observe = (rivian_native_observation if rivian_aol else
             volkswagen_native_observation if volkswagen_aol else nissan_native_observation if nissan_aol else
             preap_native_observation if preap_aol else mazda_native_observation if mazda_aol else card.aol_honda_native_observation if honda_aol else
             ford_native_observation if ford_aol else native_observation)
  restrict = (rivian_temporary_restriction if rivian_aol else
              volkswagen_temporary_restriction if volkswagen_aol else nissan_temporary_restriction if nissan_aol else
              preap_temporary_restriction if preap_aol else mazda_temporary_restriction if mazda_aol else honda_temporary_restriction if honda_aol else
              ford_temporary_restriction if ford_aol else temporary_restriction)
  return observe, restrict
