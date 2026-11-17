from dataclasses import dataclass

from opendbc.car.hyundai.values import CAR


@dataclass(frozen=True)
class WheelSettingsPolicy:
  settings_supported: bool = True
  fixed_cruise_buttons: bool = False
  explicit_latch: bool = True
  distance_pause_only: bool = True
  paddle_pause: bool = True
  runtime_supported: bool = False
  lkas_button_supported: bool = True
  auxiliary_lkas_supported: bool = False


def supports_ioniq6_configuration(cp) -> bool:
  return bool(cp is not None and cp.brand == 'hyundai' and cp.carFingerprint == CAR.HYUNDAI_IONIQ_6 and
              not cp.notCar and not cp.passive and not cp.dashcamOnly)


def supports_long_configuration(cp) -> bool:
  return bool(cp is not None and not cp.notCar and not cp.passive and not cp.dashcamOnly and
              (supports_ioniq6_configuration(cp) or cp.alphaLongitudinalAvailable or cp.openpilotLongitudinalControl))


def configuration_wheel_policy(cp) -> WheelSettingsPolicy | None:
  return WheelSettingsPolicy() if supports_ioniq6_configuration(cp) else None


def configuration_media_supported(cp) -> bool:
  return (supports_ioniq6_configuration(cp) or
          bool(cp is not None and cp.brand == 'hyundai' and cp.carFingerprint == CAR.KIA_EV6 and
               not cp.notCar and not cp.passive and not cp.dashcamOnly))
