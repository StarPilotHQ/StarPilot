"""Conservative GM query-family and legacy CAN alias discovery."""
from opendbc.car.gm.values import CAR, CanBus
from opendbc.car.fingerprints import all_legacy_fingerprint_cars, eliminate_incompatible_cars
from opendbc.car.can_definitions import CanData
from opendbc.car.vin import is_valid_vin


def automatic_trax_candidate(candidate, fingerprint, candidates, vin):
  # This recorded 2025 Trax shares its CAN layout with Equinox and Silverado.
  compatible = set(candidates.get(CanBus.POWERTRAIN, ()))
  if (candidate is not None or CAR.CHEVROLET_TRAX not in compatible or
      not compatible <= {CAR.CHEVROLET_TRAX, CAR.CHEVROLET_EQUINOX, CAR.CHEVROLET_SILVERADO}):
    return candidate
  if (not isinstance(vin, str) or not is_valid_vin(vin) or
      not vin.startswith('KL77L') or vin[5] not in 'FGHJK' or
      vin[6] != 'E' or vin[7] not in '2P' or vin[9:11] != 'SC'):
    return candidate
  pt = fingerprint.get(CanBus.POWERTRAIN, {})
  if any(pt.get(address) != size for address, size in
         ((0xBE, 6), (0xD3, 2), (0x130, 3), (0x1C4, 8), (0x1E1, 7))):
    return candidate
  return CAR.CHEVROLET_TRAX


def automatic_volt_candidate(candidate, fingerprint, candidates):
  # Never replace an identified vehicle, firmware result, or explicit choice.
  # Only the known gateway/CC CAN collision can enter this refinement.
  if candidate is not None or set(candidates.get(CanBus.POWERTRAIN, ())) != {
      CAR.CHEVROLET_VOLT, CAR.CHEVROLET_VOLT_CC}:
    return candidate
  pt = fingerprint.get(CanBus.POWERTRAIN, {})
  camera = fingerprint.get(CanBus.CAMERA, {})
  required = {0xD3: 2, 0x130: 1, 0x17D: 6, 0x210: 4, 0x236: 5,
              0x184: 8, 0x34A: 5, 0x1C4: 8, 0xC9: 8, 0x1E1: 7, 0xF1: 6}
  # This recorded F1-brake installation mirrors PT on buses 0 and 2. A live
  # camera, interceptor, SASCM, radar bus, or another DLC/layout remains outside
  # this rule and retains normal discovery/manual configuration ownership.
  if (not pt or pt != camera or
      any(messages for bus, messages in fingerprint.items() if bus not in (CanBus.POWERTRAIN, CanBus.CAMERA)) or
      any(pt.get(address) != size for address, size in required.items()) or
      any(address in pt for address in (0xBE, 0x201, 0x2FF, 0x320))):
    return candidate
  # CAN topology cannot establish ECU programming. Keep automatic selection
  # conventional; a flashed direct-long installation selects plain Volt OBD
  # explicitly and opts into alpha long. Never override that manual selection.
  return CAR.CHEVROLET_VOLT_CC


# Frequent normal-bus frames observed on mirrored pedal installations. This is
# only diagnostic query-family evidence, never a vehicle/control fingerprint.
PEDAL_NORMAL_BUS_LAYOUT = {0xD3: 2, 0xBE: 6, 0xC9: 8, 0x1C4: 8,
                           0xF1: 6, 0x1E1: 7, 0x201: 6}


def pedal_normal_bus0_topology(fingerprint):
  if any(messages for bus, messages in fingerprint.items() if bus not in (0, 2)):
    return False
  return all(fingerprint.get(bus, {}).get(address) == size
             for bus in (0, 2) for address, size in PEDAL_NORMAL_BUS_LAYOUT.items())


def normal_bus0_topology(fingerprint):
  """Recognize the GM query family without choosing a vehicle or actuator owner."""
  if pedal_normal_bus0_topology(fingerprint):
    return True
  pt = {address: size for address, size in fingerprint.get(CanBus.POWERTRAIN, {}).items()
        if address < 0x800 and address not in (0x7DF, 0x7E0, 0x7E8)}
  if len(pt) < 8:
    return False
  # Common Global A driver/steering state is shared by gateway, ASCM, SDGM,
  # camera, CC and interceptor installations. Camera presence and pedal presence
  # are deliberately not query-family requirements. Both ECM DLCs are in GM's
  # existing legacy inventory; they do not select a parser or control profile.
  if (pt.get(0xC9) in (6, 8) and pt.get(0x184) == 8 and
      pt.get(0x1C4) == 8 and pt.get(0x1E1) == 7):
    return True
  # Older GM architectures use different state messages. Reuse the existing
  # all-brand CAN inventory only when every surviving identity belongs to GM.
  candidates = all_legacy_fingerprint_cars()
  for address, size in pt.items():
    candidates = eliminate_incompatible_cars(CanData(address, bytes(size), CanBus.POWERTRAIN), candidates)
    if not candidates:
      return False
  return bool(candidates) and all(candidate in CAR for candidate in candidates)
