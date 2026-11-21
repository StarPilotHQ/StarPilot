"""Persistent unit token; current session/calibration authority remains separate.

A real VIN takes precedence. Otherwise a registered device can own a profile
for its exact firmware-bearing CarParams. Callers bind the full typed CP,
settings and explicit Auto Learn reset generation. This is a device/configuration
identity, never a fabricated VIN or proof that two identical cars are different.
Moving that device between identical cars requires an explicit Auto Learn reset.

Binding concept reviewed against squilliam2/openpilot@2fc06420063de69ac8c62d2b3375a6f8f2c6c95b.
"""
# Binding approach reviewed against squilliam2/openpilot@2fc06420063de69ac8c62d2b3375a6f8f2c6c95b.
import re

from opendbc.car.vin import VIN_UNKNOWN, is_valid_vin

REGISTRATION_KEY = 'DongleId'


def known_vin(cp):
  value = str(cp.carVin)
  return value if is_valid_vin(value) and value != VIN_UNKNOWN else None


def identity_keys(cp):
  return () if known_vin(cp) is not None else (REGISTRATION_KEY,)


def persistent_unit(cp, values):
  vin = known_vin(cp)
  if vin is not None:
    return vin
  device = values.get(REGISTRATION_KEY)
  if (type(device) is not bytes or re.fullmatch(rb'[0-9a-f]{16}', device) is None or
      not any(bytes(fw.fwVersion) for fw in getattr(cp, 'carFw', ()))):
    return None  # Preserve isolated drive review; absent identity grants no saved profile.
  return ('registered_device_ecu_configuration_v1', device.decode('ascii'))
