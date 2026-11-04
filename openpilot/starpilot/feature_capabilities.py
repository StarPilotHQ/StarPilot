"""Generic optional-feature admission from finalized native CarParams.

Admission creates a host; the host still checks live axis and source authority.
PCM cruise describes engagement/set-speed integration, not who controls acceleration.
"""


def _native_configuration_valid(cp, *, longitudinal: bool) -> bool:
  """Keep native configuration checks separate from global feature availability.

  GM's finalized owners encode the physical topology and longitudinal owner in
  correlated safety words, flags and vehicle identity. A control boolean alone
  cannot validate that contract. Reuse its native validators instead of a
  feature launch allowlist or a second, weaker copy of those safety rules.
  """
  if getattr(cp, 'brand', None) == 'gm':
    from opendbc.car.gm.feature_capabilities import display_supported, longitudinal_supported
    return longitudinal_supported(cp) if longitudinal else display_supported(cp)
  return True


def display_supported(cp) -> bool:
  return bool(cp is not None and getattr(cp, 'carFingerprint', '') and
              getattr(cp, 'passive', None) is False and
              getattr(cp, 'dashcamOnly', None) is False and
              getattr(cp, 'notCar', None) is False and
              _native_configuration_valid(cp, longitudinal=False))


def longitudinal_supported(cp) -> bool:
  return bool(display_supported(cp) and getattr(cp, 'openpilotLongitudinalControl', None) is True and
              _native_configuration_valid(cp, longitudinal=True))


def lateral_supported(cp) -> bool:
  """Generic curvature shaping follows steering capability, not cruise ownership.

  Creating the optional preference host does not grant steering permission.
  Controls retains its current axis, source, fault and final curvature checks.
  GM additionally keeps its existing finalized physical-configuration checks.
  """
  from opendbc.car.structs import CarParams

  try:
    if (cp is None or not cp.carFingerprint or getattr(cp, 'passive', None) is not False or
        getattr(cp, 'dashcamOnly', None) is not False or getattr(cp, 'notCar', None) is not False or
        (getattr(cp, 'secOcRequired', False) and not getattr(cp, 'secOcKeyAvailable', False)) or
        cp.steerControlType not in (CarParams.SteerControlType.torque, CarParams.SteerControlType.angle,
                                    CarParams.SteerControlType.curvature) or
        not any(config.safetyModel not in (CarParams.SafetyModel.silent, CarParams.SafetyModel.noOutput,
                                          CarParams.SafetyModel.allOutput) for config in cp.safetyConfigs)):
      return False
    if cp.brand == 'gm':
      from openpilot.starpilot.lateral.bolt_policy import supported_cp as bolt_supported_cp
      from opendbc.car.gm.lateral import lane_centering_supported
      return bool(bolt_supported_cp(cp) or lane_centering_supported(cp))
    return True
  except (AttributeError, IndexError, TypeError, ValueError):
    return False


def vehicle_preferences_supported(cp) -> bool:
  """Admit the settings page through each vehicle feature's native contract."""
  if cp is None:
    return False
  if cp.brand == 'chrysler':
    from opendbc.car.chrysler.brake_hold import brake_hold_supported
    return brake_hold_supported(cp)
  if cp.brand == 'toyota':
    from opendbc.car.toyota.interface import toyota_auto_hold_supported
    return toyota_auto_hold_supported(cp)
  return False
