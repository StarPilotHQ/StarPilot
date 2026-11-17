"""Four-byte CAN-filter advertisements on exact non-radar EPS8 profiles."""

from opendbc.can import CANParser
from opendbc.car import structs
from opendbc.car.config_cache import cached_config
from opendbc.car.toyota.toyotacan import toyota_checksum
from opendbc.car.toyota import eps8_sdsu, radar_filter
from opendbc.car.toyota.values import CAR, ToyotaFlags

CARS = frozenset(eps8_sdsu.CARS - radar_filter.PROFILES.keys())
LONG_WORD = 0x1049
STOCK_WORD = 0x1249


def hardware_supported(candidate, fingerprint, car_fw):
  from opendbc.car.toyota import camera_2aa_filter
  if camera_2aa_filter.hardware_supported(candidate, fingerprint, car_fw):
    return True
  from opendbc.car.toyota import legacy_advert_filter
  if candidate in legacy_advert_filter.PROFILES:
    return legacy_advert_filter.hardware_supported(candidate, fingerprint, car_fw)

  if (
    candidate not in CARS
    or fingerprint.get(0, {}).get(0x2FF) != 4
    or any(address in messages for messages in fingerprint.values() for address in (0x201, 0x2AA))
    or any(bus not in (0, 2) or messages[0x2FF] != 4 for bus, messages in fingerprint.items() if 0x2FF in messages)
  ):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS

  known = FW_VERSIONS[candidate]
  required = {structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera}
  if any(ecu == structs.CarParams.Ecu.fwdRadar for ecu, _, _ in known):
    required.add(structs.CarParams.Ecu.fwdRadar)
  for ecu in required:
    observed = [fw for fw in car_fw if fw.ecu == ecu]
    if not observed or any(
      fw.logging or fw.bus != 0 or fw.brand != "toyota" or bytes(fw.fwVersion) not in known.get((ecu, fw.address, fw.subAddress or None), ()) for fw in observed
    ):
      return False
  return True


def apply(cp, *, camera_twoaa=False):
  if camera_twoaa:
    from opendbc.car.toyota import camera_2aa_filter
    return camera_2aa_filter.apply(cp)
  from opendbc.car.toyota import legacy_advert_filter
  if cp is not None and cp.carFingerprint in legacy_advert_filter.PROFILES:
    return legacy_advert_filter.apply(cp)

  # This is an advertisement, never the eight-byte SDSU status/FD_BUTTON source.
  cp.flags |= int(ToyotaFlags.RADAR_CAN_FILTER)
  cp.safetyConfigs[0].safetyParam = LONG_WORD
  cp.openpilotLongitudinalControl = True
  cp.alphaLongitudinalAvailable = bool(cp.flags & (ToyotaFlags.NO_DSU | ToyotaFlags.RADAR_ACC))


@cached_config
def configured(cp):
  from opendbc.car.toyota.pedal_composite import parent_cp
  cp = parent_cp(cp)
  from opendbc.car.toyota import camera_2aa_filter
  if camera_2aa_filter.configured(cp):
    return True
  from opendbc.car.toyota import legacy_advert_filter
  if cp is not None and cp.carFingerprint in legacy_advert_filter.PROFILES:
    return legacy_advert_filter.configured(cp)

  if (
    cp is None
    or cp.brand != "toyota"
    or cp.carFingerprint not in CARS
    or cp.notCar
    or cp.passive
    or cp.dashcamOnly
    or cp.secOcRequired
    or not cp.pcmCruise
    or len(cp.safetyConfigs) != 1
    or cp.steerControlType != structs.CarParams.SteerControlType.torque
  ):
    return False
  static = CAR(cp.carFingerprint).config.flags
  required = int(static | ToyotaFlags.RADAR_CAN_FILTER | (ToyotaFlags.RAISED_ACCEL_LIMIT if static & ToyotaFlags.TSS2 else 0))
  if cp.flags & required != required or int(cp.flags) & ~int(required | ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM):
    return False
  config = cp.safetyConfigs[0]
  return (
    config.safetyModel == structs.CarParams.SafetyModel.toyota
    and config.safetyParam == (LONG_WORD if cp.openpilotLongitudinalControl else STOCK_WORD)
    and cp.autoResumeSng == bool(static & ToyotaFlags.TSS2 and cp.openpilotLongitudinalControl)
  )


def enabled(cp):
  return configured(cp) and cp.openpilotLongitudinalControl


def prepare_stock(cp):
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.marked(cp):
    return pedal_composite.prepare_stock(cp)
  from opendbc.car.toyota import camera_2aa_filter
  if camera_2aa_filter.configured(cp):
    return camera_2aa_filter.prepare_stock(cp)
  from opendbc.car.toyota import legacy_advert_filter
  if cp is not None and cp.carFingerprint in legacy_advert_filter.PROFILES:
    return legacy_advert_filter.prepare_stock(cp)

  if not configured(cp):
    return False
  cp.openpilotLongitudinalControl = False
  cp.safetyConfigs[0].safetyParam = STOCK_WORD
  cp.autoResumeSng = False
  return True


class AdvertFilterCANParser(CANParser):
  """The ad carries a gap button; only real MAIN/EPS frames supply authority."""

  def __init__(self, *args):
    super().__init__(*args)
    self.main_valid = False
    self.eps_valid = False
    self.advert_distance_button = 0
    self.advert_button_ns = 0

  def update(self, strings, sendcan=False):
    batches = []
    for stamp, frames in strings:
      accepted = []
      for address, data, bus in frames:
        if bus == self.bus:
          if address in (0x1D3, 0x262):
            valid = len(data) == 8 and data[-1] == toyota_checksum(address, None, bytearray(data))
            if address == 0x1D3:
              self.main_valid = valid
            else:
              self.eps_valid = valid
            if not valid:
              continue
          if address == 0x2FF:
            self.advert_button_ns = stamp if len(data) == 4 else 0
            self.advert_distance_button = (data[0] >> 7) if len(data) == 4 else 0
            # Do not decode this advertisement as an eight-byte status lease.
            continue
        accepted.append((address, data, bus))
      batches.append((stamp, accepted))
    return super().update(batches, sendcan)
