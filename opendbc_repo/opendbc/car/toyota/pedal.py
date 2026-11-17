"""Dom pedal protocol for qualified TSS2 camera and radar longitudinal owners."""
import numpy as np
from opendbc.can import CANParser
from opendbc.car.toyota.toyotacan import toyota_checksum
from opendbc.car import structs, CanData
from opendbc.car.toyota.values import CAR, ToyotaFlags, MIN_ACC_SPEED, PEDAL_TRANSITION

LONG_WORD = 0x2049
STOCK_WORD = 0x2249
CAMERA_PEDAL_CARS = frozenset((
  CAR.TOYOTA_ALPHARD_TSS2,
  CAR.TOYOTA_AVALON_TSS2,
  CAR.TOYOTA_CAMRY_TSS2,
  CAR.TOYOTA_COROLLA_TSS2,
  CAR.TOYOTA_HIGHLANDER_TSS2,
  CAR.TOYOTA_PRIUS_TSS2,
  CAR.TOYOTA_RAV4_TSS2,
  CAR.TOYOTA_MIRAI,
  CAR.LEXUS_ES_TSS2,
  CAR.LEXUS_NX_TSS2,
  CAR.LEXUS_LC_TSS2,
  CAR.LEXUS_RX_TSS2,
  CAR.LEXUS_IS_TSS2,
  CAR.LEXUS_RC_TSS2,
))


RADAR_PEDAL_CARS = frozenset((CAR.TOYOTA_CHR_TSS2, CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023))
ANGLE_PEDAL_CARS = frozenset((CAR.TOYOTA_RAV4_TSS2_2023,))

def long_word(candidate):
  return 0x2449 if candidate in ANGLE_PEDAL_CARS else LONG_WORD


def checksum(data):
  crc = 0xFF
  for value in reversed(data[:-1]):
    crc ^= value
    for _ in range(8):
      crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
  return crc


def primary_pedal_hardware(fingerprint):
  # StarPilot enrolls the bus0 pedal. A six-byte camera copy is informational;
  # it cannot replace that primary source or enroll a second pedal bus.
  return (fingerprint.get(0, {}).get(0x201) == 6 and
          all(bus in (0, 2) and frames[0x201] == 6 for bus, frames in fingerprint.items() if 0x201 in frames))


def hardware_supported(candidate, fingerprint, car_fw):
  if candidate not in CAMERA_PEDAL_CARS | RADAR_PEDAL_CARS or not primary_pedal_hardware(fingerprint):
    return False
  if any(addr in messages for messages in fingerprint.values() for addr in (0x2FF, 0x2AA)):
    return False
  from opendbc.car.toyota.fingerprints import FW_VERSIONS
  known = FW_VERSIONS[candidate]
  for ecu in (structs.CarParams.Ecu.eps, structs.CarParams.Ecu.fwdCamera):
    observed = [fw for fw in car_fw if fw.ecu == ecu]
    if not observed or any(fw.logging or fw.bus != 0 or fw.brand != 'toyota' or
        bytes(fw.fwVersion) not in known.get((ecu, fw.address, fw.subAddress or None), ()) for fw in observed):
      return False
  return True


def configured(cp):
  from opendbc.car.toyota.pedal_composite import hold_base_cp
  cp = hold_base_cp(cp)
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.configured(cp):
    return True
  if cp is None or cp.brand != 'toyota' or cp.carFingerprint not in CAMERA_PEDAL_CARS | RADAR_PEDAL_CARS or (
      cp.notCar or cp.passive or cp.dashcamOnly or not cp.pcmCruise or
      cp.secOcRequired or len(cp.safetyConfigs) != 1):
    return False
  required = ToyotaFlags.TSS2 | ToyotaFlags.NO_DSU | ToyotaFlags.RAISED_ACCEL_LIMIT | ToyotaFlags.GAS_INTERCEPTOR
  allowed = required | ToyotaFlags.HYBRID | ToyotaFlags.HAS_BSM
  if cp.carFingerprint in RADAR_PEDAL_CARS:
    required |= ToyotaFlags.RADAR_ACC
    if cp.openpilotLongitudinalControl:
      required |= ToyotaFlags.DISABLE_RADAR
    allowed |= required
  angle = cp.carFingerprint in ANGLE_PEDAL_CARS
  if angle:
    required |= ToyotaFlags.ANGLE_CONTROL
    allowed |= ToyotaFlags.ANGLE_CONTROL
  if cp.carFingerprint in (CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023):
    allowed |= ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_FUSED
  if cp.flags & ToyotaFlags.ARS510_FUSED and not cp.flags & ToyotaFlags.ARS510_RADAR:
    return False
  expected_steer = structs.CarParams.SteerControlType.angle if angle else structs.CarParams.SteerControlType.torque
  if cp.steerControlType != expected_steer:
    return False
  config = cp.safetyConfigs[0]
  return (cp.flags & required == required and not int(cp.flags) & ~int(allowed) and
          config.safetyModel == structs.CarParams.SafetyModel.toyota and
          config.safetyParam == (long_word(cp.carFingerprint) | (0 if cp.openpilotLongitudinalControl else 0x200)))


def prepare_stock(cp):
  from opendbc.car.toyota import pedal_composite
  if pedal_composite.marked(cp):
    return pedal_composite.prepare_stock(cp)
  if not configured(cp):
    return False
  cp.openpilotLongitudinalControl = False
  cp.safetyConfigs[0].safetyParam = long_word(cp.carFingerprint) | 0x200
  if cp.carFingerprint in RADAR_PEDAL_CARS:
    cp.flags &= ~int(ToyotaFlags.DISABLE_RADAR)
  cp.autoResumeSng = False
  return True


def command(packer, value, counter):
  active = value > .001
  fields = {'ENABLE': active, 'COUNTER_PEDAL': counter & 15}
  if active:
    fields['GAS_COMMAND'] = fields['GAS_COMMAND2'] = value * 255.
  address, data, bus = packer.make_can_msg('GAS_COMMAND', 0, fields)
  data = bytearray(data)
  # A disabled pedal must contain raw zeros, not a physical-zero offset encoding.
  if not active:
    data[:4] = bytes(4)
  data[-1] = checksum(data)
  return CanData(address, bytes(data), bus)


def amount(cp, cc, cs, accel):
  if not (configured(cp) and cp.openpilotLongitudinalControl and cc.longActive and
          cs.out.canValid and not cs.out.canTimeout and cs.pedal_ready and
          not cs.out.gasPressed and not cs.out.brakePressed):
    return 0.
  if cs.out.standstill:
    return .12 if accel > 0 else 0.
  if cp.carFingerprint == CAR.TOYOTA_RAV4:
    scale = float(np.interp(cs.out.vEgo, [0., 5., MIN_ACC_SPEED, MIN_ACC_SPEED + PEDAL_TRANSITION], [.11, .23, .3, 0.]))
  elif cp.carFingerprint in (CAR.TOYOTA_COROLLA, CAR.TOYOTA_MATRIX_RETROFIT):
    scale = float(np.interp(cs.out.vEgo, [0., MIN_ACC_SPEED, MIN_ACC_SPEED + PEDAL_TRANSITION], [.3, .4, 0.]))
  else:
    scale = float(np.interp(cs.out.vEgo, [0., MIN_ACC_SPEED, MIN_ACC_SPEED + PEDAL_TRANSITION], [.4, .5, 0.]))
  offset = float(np.interp(cs.out.vEgo, [0., 2.3, MIN_ACC_SPEED + PEDAL_TRANSITION], [-.4, 0., .2]))
  return float(np.clip(scale * (accel + offset), 0., .5))


class PedalCANParser(CANParser):
  def __init__(self, *args):
    super().__init__(*args)
    self.pedal_counter = None
    self.pedal_valid = False
    self.main_valid = False

  def update(self, strings, sendcan=False):
    batches = []
    for stamp, frames in strings:
      accepted = []
      for frame in frames:
        address, data, bus = frame
        if address == 0x1D3 and bus == self.bus:
          self.main_valid = len(data) == 8 and data[-1] == toyota_checksum(address, None, bytearray(data))
          if not self.main_valid:
            continue
        if address == 0x201 and bus == self.bus:
          counter = data[4] & 15 if len(data) == 6 else None
          valid = len(data) == 6 and data[-1] == checksum(data)
          if valid:
            valid = self.pedal_counter is None or counter == (self.pedal_counter + 1) & 15
            self.pedal_counter = counter
          self.pedal_valid = valid and data[4] >> 4 == 0
          if not valid:
            continue
        accepted.append(frame)
      batches.append((stamp, accepted))
    return super().update(batches, sendcan)
