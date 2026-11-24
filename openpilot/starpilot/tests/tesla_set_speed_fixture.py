"""Tesla Model 3/Y car parameters and the saved switch for the set-speed and car-control tests."""
from pathlib import Path

from opendbc.car import gen_empty_fingerprint
from opendbc.car.tesla.interface import CarInterface
from opendbc.car.tesla.speed_sync import prepare
from opendbc.car.tesla.values import CAR, CANBUS

SWITCH = 'TeslaSpeedSync'


def tesla(candidate=CAR.TESLA_MODEL_3, *, cable=True, long=True, selected=False):
  """Car parameters built by the real car interface. cable: the CAN add-on cable was detected when the car started.
  long: StarPilot longitudinal control. selected: startup admitted the scroll frame (it only lands on an eligible car);
  whether the comma then owns the set speed follows the saved switch."""
  fingerprint = gen_empty_fingerprint()
  fingerprint[CANBUS.autopilot_party][0x489] = 8
  if cable:
    fingerprint[CANBUS.vehicle][0x3DF] = 8
  return prepare(CarInterface.get_params(candidate, fingerprint, [], long, False, False), selected)


def save(params, key, value):
  """Write a saved setting as its file, as the settings store leaves it: True/False, raw bytes, or None to remove."""
  path = Path(params.get_param_path(key))
  if value is None:
    path.unlink(missing_ok=True)
  else:
    path.write_bytes(value if isinstance(value, bytes) else b'1' if value else b'0')


def switch(params, on=True):
  """Save Comma can set speed on Tesla dashboard."""
  save(params, SWITCH, on)
  return params
