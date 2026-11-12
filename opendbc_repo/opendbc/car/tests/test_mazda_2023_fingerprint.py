from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.mazda.values import CAR
from opendbc.car.structs import CarParams


def test_2023_mazda3_captured_firmware():
  """The six ECUs captured from the 2023 Mazda3 BP must uniquely identify GEN2."""
  ecu = CarParams.Ecu
  captured = [
    (ecu.engine, 0x7e0, b'PXNA-188K2-F', 12),
    (ecu.transmission, 0x7e1, b'PXLS-21PS1-A', 12),
    (ecu.fwdCamera, 0x706, b'DFR5-67WK2-E', 12),
    (ecu.eps, 0x730, b'BCKA-3216X-E', 12),
    (ecu.abs, 0x760, b'BHCB-4300F-', 13),
    (ecu.fwdRadar, 0x764, b'B0N2-67XK2-E', 12),
  ]
  live = []
  for kind, address, value, padding in captured:
    fw = CarParams.CarFw.new_message()
    fw.ecu = kind
    fw.address = address
    fw.brand = 'mazda'
    fw.bus = 0
    fw.obdMultiplexing = True
    fw.fwVersion = value + b'\x00' * padding
    live.append(fw)

  exact, matches = match_fw_to_car(live, '', log=False)
  assert exact
  assert matches == {CAR.MAZDA_3_2019}
