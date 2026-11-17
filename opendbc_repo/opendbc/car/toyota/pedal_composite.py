"""Independent pedal and smartDSU receivers for the RAV4 hybrid.

The exact parent configuration, parser, source leases and tunes are retained.
A valid pedal source supplements that parent; it cannot replace it.
"""

from opendbc.car import structs
from opendbc.car.toyota.config_cache import cached_config
from opendbc.car.toyota.values import CAR, ToyotaFlags

PARENT_WORDS = {
  0xE449: 0x9849,
  0xE649: 0x9A49,
  0x3049: 0x1049,
  0x3249: 0x1249,
  0x3449: 0x1449,
  0x3649: 0x1649,
  0xA042: 0x8042,
  0xA049: 0x8049,
  0xA242: 0x8242,
  0xA249: 0x8249,
  0xA449: 0x8449,
  0xA649: 0x8649,
  0xA849: 0x8849,
  0xA949: 0x8949,
  0xA958: 0x8958,
  0xA964: 0x8964,
  0xAA49: 0x8A49,
  0xAB49: 0x8B49,
  0xAB58: 0x8B58,
  0xAB64: 0x8B64,
  0xB049: 0x9049,
  0xB149: 0x9149,
  0xB158: 0x9158,
  0xB164: 0x9164,
  0xB249: 0x9249,
  0xB349: 0x9349,
  0xB358: 0x9358,
  0xB364: 0x9364,
  0xB449: 0x9449,
  0xB549: 0x9549,
  0xB558: 0x9558,
  0xB564: 0x9564,
  0xB649: 0x9649,
  0xB749: 0x9749,
  0xB758: 0x9758,
  0xB764: 0x9764,
  0xB842: 0x9842,
  0xB849: 0x9849,
  0xBA42: 0x9A42,
  0xBA49: 0x9A49,
  0xE042: 0xC042,
  0xE049: 0xC049,
  0xE242: 0xC242,
  0xE249: 0xC249,
  0xE84D: 0xC84D,
  0xE949: 0xC949,
  0xEA4D: 0xCA4D,
  0xEB49: 0xCB49,
  0xF049: 0xD049,
  0xF04D: 0xD04D,
  0xF149: 0xD149,
  0xF249: 0xD249,
  0xF24D: 0xD24D,
  0xF349: 0xD349,
  0xF44D: 0xD44D,
  0xF549: 0xD549,
  0xF64D: 0xD64D,
  0xF749: 0xD749,
}


def marked(cp):
  return bool(
    cp is not None
    and cp.brand == 'toyota'
    and cp.flags & ToyotaFlags.GAS_INTERCEPTOR
    and len(cp.safetyConfigs) == 1
    and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota
    and cp.safetyConfigs[0].safetyParam in PARENT_WORDS
    and (cp.safetyConfigs[0].safetyParam not in (0xE449, 0xE649) or
         cp.carFingerprint == CAR.TOYOTA_PRIUS_RETROFIT and cp.flags & ToyotaFlags.HYBRID)
  )


def hold_base_cp(cp):
  """Project an eligible, exactly requested hold setting onto its existing owner."""
  from opendbc.car.toyota.values import TOYOTA_AUTO_HOLD_CARS, uses_toyota_auto_hold_aeb
  if cp is None or not cp.flags & ToyotaFlags.AUTO_BRAKE_HOLD:
    return cp
  permission = 256 if uses_toyota_auto_hold_aeb(cp) else 128
  if (cp.brand != 'toyota' or cp.carFingerprint not in TOYOTA_AUTO_HOLD_CARS or
      not cp.openpilotLongitudinalControl or cp.secOcRequired or cp.notCar or cp.passive or cp.dashcamOnly or
      cp.alternativeExperience not in (permission, permission | 32)):
    return cp
  parent = cp.as_reader().as_builder() if hasattr(cp, 'as_reader') else cp.as_builder()
  parent.flags &= ~int(ToyotaFlags.AUTO_BRAKE_HOLD)
  parent.alternativeExperience &= ~(128 | 256)
  return parent


def parent_cp(cp):
  cp = hold_base_cp(cp)
  if not marked(cp):
    return cp
  parent = cp.as_reader().as_builder() if hasattr(cp, 'as_reader') else cp.as_builder()
  parent.flags &= ~int(ToyotaFlags.GAS_INTERCEPTOR)
  parent.safetyConfigs[0].safetyParam = PARENT_WORDS[cp.safetyConfigs[0].safetyParam]
  return parent


def parent_modules():
  from opendbc.car.toyota import dsu_bypass, prius_longitudinal, advert_filter, rav4h_filter, radar_filter
  return (dsu_bypass, prius_longitudinal, advert_filter, rav4h_filter, radar_filter)


@cached_config
def configured(cp):
  return marked(cp) and not cp.secOcRequired and any(module.configured(parent_cp(cp)) for module in parent_modules())


def hardware_supported(candidate, fingerprint, car_fw):
  from opendbc.car.toyota import prius_legacy_pedal
  if prius_legacy_pedal.hardware_supported(candidate, fingerprint, car_fw):
    return True
  from opendbc.car.toyota import dsu_bypass, rav4h_filter, eps8_sdsu, legacy_sdsu, advert_filter, prius_sdsu, prius_advert_filter, radar_filter
  from opendbc.car.toyota.pedal import primary_pedal_hardware
  if not primary_pedal_hardware(fingerprint):
    return False
  parent = without_pedal(fingerprint)
  for module in (dsu_bypass, prius_advert_filter, prius_sdsu, advert_filter, legacy_sdsu, eps8_sdsu, rav4h_filter):
    if module.hardware_supported(candidate, parent, car_fw):
      return True
  if prius_status_hardware(candidate, parent, car_fw):
    return True
  if not radar_filter.hardware_supported(candidate, parent):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS
  known = FW_VERSIONS[candidate]
  for ecu in (structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera):
    rows = [fw for fw in car_fw if fw.ecu == ecu]
    if not rows or any(fw.logging or fw.bus != 0 or fw.brand != 'toyota' or
                      bytes(fw.fwVersion) not in known.get((ecu, fw.address, fw.subAddress or None), ()) for fw in rows):
      return False
  return True


def prius_status_hardware(candidate, fingerprint, car_fw):
  from opendbc.car.toyota import prius_retrofit_advert
  if (candidate not in prius_retrofit_advert.CARS or fingerprint.get(0, {}).get(0x2FF) != 8 or
      any(0x2FF in frames and (bus not in (0, 2) or frames[0x2FF] != 8) for bus, frames in fingerprint.items()) or
      any(address in frames for frames in fingerprint.values() for address in (0x201, 0x2AA))):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS
  known = FW_VERSIONS[CAR.TOYOTA_PRIUS]
  eps = [fw for fw in car_fw if fw.ecu == structs.CarParams.Ecu.eps]
  if not eps or any(fw.logging or fw.bus != 0 or fw.brand != 'toyota' or fw.address != 0x7A1 or fw.subAddress or
                    bytes(fw.fwVersion) != prius_retrofit_advert.EPS_FIRMWARE for fw in eps):
    return False
  for ecu in (structs.CarParams.Ecu.fwdCamera, structs.CarParams.Ecu.fwdRadar):
    if any(fw.logging or fw.bus != 0 or fw.brand != 'toyota' or
           bytes(fw.fwVersion) not in known.get((ecu, fw.address, fw.subAddress or None), ()) for fw in car_fw if fw.ecu == ecu):
      return False
  return True


def without_pedal(fingerprint):
  return {bus: {address: size for address, size in frames.items() if address != 0x201} for bus, frames in fingerprint.items()}


def composite_word(cp, *, legacy_prius=False):
  if legacy_prius:
    return 0xE449 if cp.openpilotLongitudinalControl else 0xE649
  return cp.safetyConfigs[0].safetyParam | 0x2000


def apply(cp, *, legacy_prius=False):
  if legacy_prius and (cp.carFingerprint != CAR.TOYOTA_PRIUS_RETROFIT or cp.safetyConfigs[0].safetyParam not in (0x9849, 0x9A49)):
    return False
  if not any(module.configured(cp) for module in parent_modules()) or composite_word(cp) not in PARENT_WORDS:
    return False
  cp.safetyConfigs[0].safetyParam = composite_word(cp, legacy_prius=legacy_prius)
  cp.flags |= int(ToyotaFlags.GAS_INTERCEPTOR)
  cp.minEnableSpeed = -1.
  return True


def prepare_stock(cp):
  if not configured(cp):
    return False
  legacy_prius = cp.safetyConfigs[0].safetyParam in (0xE449, 0xE649)
  parent = parent_cp(cp)
  assert any(module.prepare_stock(parent) for module in parent_modules())
  cp.flags = parent.flags | int(ToyotaFlags.GAS_INTERCEPTOR)
  cp.alternativeExperience = parent.alternativeExperience
  cp.openpilotLongitudinalControl = False
  cp.safetyConfigs[0].safetyParam = composite_word(parent, legacy_prius=legacy_prius)
  cp.autoResumeSng = parent.autoResumeSng
  return True


class ParentPedalParser:
  def __init__(self, parent, *, mirrored_pedal=False):
    self.parent = parent
    self.mirrored_pedal = mirrored_pedal

  def __getattr__(self, name):
    return getattr(self.parent, name)

  def update(self, strings, sendcan=False):
    # Only primary pedal RX supplies its lease. A CRC-valid camera copy is
    # ignored; malformed/foreign input still reaches the exact parent.
    from opendbc.car.toyota.pedal import checksum

    def enrolled(frame):
      address, data, bus = frame
      return address == 0x201 and len(data) == 6 and (bus == 0 or
        self.mirrored_pedal and bus == 2 and data[-1] == checksum(data))
    batches = [(stamp, [frame for frame in frames if not enrolled(frame)]) for stamp, frames in strings]
    return self.parent.update(batches, sendcan)
