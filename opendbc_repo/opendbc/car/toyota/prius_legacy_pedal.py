"""StarPilot's manual EPS73 Prius DSU/pedal receiver, without a 2FF lease.

The donor enrolls this legacy path from camera DSU traffic and a pedal. Keep a
separate exact native identity; primary pedal, MAIN, EPS and camera ACC remain
independent current sources. A camera copy of the pedal grants nothing.
"""
from opendbc.car import structs
from opendbc.car.toyota.values import CAR

LONG_WORD = 0xE449
STOCK_WORD = 0xE649
WORDS = frozenset((LONG_WORD, STOCK_WORD))


def hardware_supported(candidate, fingerprint, car_fw):
  from opendbc.car.toyota.pedal import primary_pedal_hardware
  if (candidate != CAR.TOYOTA_PRIUS_RETROFIT or
      any(address in messages for messages in fingerprint.values() for address in (0x2FF, 0x2AA)) or
      not primary_pedal_hardware(fingerprint) or
      any(fingerprint.get(bus, {}).get(address) != size for bus in (0, 2)
          for address, size in ((0x343, 8), (0x4CB, 8))) or
      any(fingerprint.get(0, {}).get(address) != 8 for address in (0x1D3, 0x262))):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS
  known = FW_VERSIONS[CAR.TOYOTA_PRIUS]
  for ecu in (structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera, structs.CarParams.Ecu.fwdRadar):
    rows = [fw for fw in car_fw if fw.ecu == ecu]
    if not rows:
      return False
    for fw in rows:
      expected = ((b'8965B47070' + bytes(6),) if ecu == structs.CarParams.Ecu.eps else
                  known.get((ecu, fw.address, fw.subAddress or None), ()))
      if (fw.logging or fw.bus != 0 or fw.brand != 'toyota' or bytes(fw.fwVersion) not in expected or
          ecu == structs.CarParams.Ecu.eps and (fw.address != 0x7A1 or fw.subAddress)):
        return False
  return True
