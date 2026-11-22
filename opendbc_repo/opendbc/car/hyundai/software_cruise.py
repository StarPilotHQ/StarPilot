"""Conventional cruise button control for exact classic Hyundai profiles."""

from opendbc.car.hyundai.non_scc_aol import SOFTWARE_CRUISE, qualified


def eligible(cp):
  return cp is not None and qualified(cp)


def selected(cp):
  return (cp is not None and cp.brand == 'hyundai' and len(cp.safetyConfigs) == 1 and
          bool(cp.safetyConfigs[0].safetyParam & SOFTWARE_CRUISE) and eligible(cp))


def prepare(cp, enabled):
  if enabled and eligible(cp):
    cp.safetyConfigs[0].safetyParam |= SOFTWARE_CRUISE
  return cp
