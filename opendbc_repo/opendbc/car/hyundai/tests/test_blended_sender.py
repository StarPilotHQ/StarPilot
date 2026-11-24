"""Whole candidate sender contract under scripted diagnostic ACKs, never physical takeover."""

import unittest
from unittest.mock import patch

from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.blended_disable_ecu import confirm_disable_ecu
from opendbc.car.hyundai.blended_longitudinal import BlendedLongitudinalOwner, BlendedLongitudinalController, candidate_from_stock
from opendbc.car.hyundai.hyundaican import hyundai_checksum
from opendbc.car.hyundai.hyundaicanfd import CanBus
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.tests.test_palisade_2023 import feed
from opendbc.car.hyundai.values import CAR, HyundaiFlags


class PositiveQuery:
  def __init__(self, *args, **kwargs):
    self.address = args[3][0]
    assert (args[4], args[5]) in (([b'\x11\x01'], [b'']), ([b'\x10\x03'], [b'\x50\x03']), ([b'\x28\x03\x01'], [b'\x68\x03']))

  def get_data(self, timeout):
    return {self.address: b''}


def selected_interface(hdaii, fca=False):
  fingerprint = gen_empty_fingerprint()
  if hdaii:
    fingerprint[2][0x50] = 16
  if fca:
    fingerprint[2][0x38D] = 8
  stock = CarInterface.get_params(CAR.HYUNDAI_PALISADE_2023, fingerprint, [], False, False, False)
  cp = candidate_from_stock(stock, alpha_requested=True, native_qualified=True)
  assert bool(cp.flags & HyundaiFlags.USE_FCA) == fca
  ci = CarInterface(cp)
  for tick in range(16):
    feed(ci, tick)
  owner = BlendedLongitudinalOwner(cp, CanBus(cp), confirm_disable_ecu)
  with patch('opendbc.car.hyundai.blended_disable_ecu.IsoTpParallelQuery', PositiveQuery), patch('opendbc.car.hyundai.blended_disable_ecu.time.sleep'):
    result = owner.begin(list, lambda frames: None, admission=True, unpublished=True)
  assert result.admissible and owner.active()
  ci.CC.blended_longitudinal = BlendedLongitudinalController(owner, ci.CC.packer, ci.CC.CAN)
  # Original steady-loop cadence; startup handoff is independently qualified.
  ci.CC.blended_longitudinal.normal_sent = True
  return ci


def decode(ci, address, data, name):
  signal = ci.CC.packer.dbc.addr_to_msg[address].sigs[name]
  assert signal.is_little_endian
  value = (int.from_bytes(data, 'little') >> signal.start_bit) & ((1 << signal.size) - 1)
  if signal.is_signed and value & (1 << (signal.size - 1)):
    value -= 1 << signal.size
  return value * signal.factor + signal.offset


class TestBlendedSender(unittest.TestCase):
  def test_whole_controller_order_cadence_crc_and_hda2_mirror(self):
    for hdaii, fca in ((False, False), (True, False), (True, True)):
      with self.subTest(hdaii=hdaii, fca=fca):
        ci = selected_interface(hdaii, fca)
        cp = ci.CP
        self.assertEqual(ci.can_parsers[Bus.pt].bus, int(hdaii))
        command = structs.CarControl()
        command.enabled = command.latActive = command.longActive = True
        command.actuators.torque = 0.5
        command.actuators.accel = 3.5
        command.actuators.longControlState = structs.CarControl.Actuators.LongControlState.pid
        command.hudControl.setSpeed = 25.0
        for frame in range(32):
          tick = 16 + frame
          camera = 16 + frame // 5 if frame % 5 == 0 else False
          feed(ci, tick, camera=camera)
          actuators, messages = ci.apply(command.as_reader(), 1_000_000_000 + tick * 10_000_000)
          wanted = []
          if frame % 100 == 0:
            wanted.append((0x730 if hdaii else 0x7D0, int(hdaii)))
          wanted.extend([(0x50, 0), (0x340, 1)] if hdaii else [(0x340, 0), (0x364, 0)])
          if hdaii and frame % 5 == 0:
            wanted.append((0x2A4, 0))
          if hdaii:
            wanted.append((0x51, 0))
          if frame % 2 == 0:
            wanted.append((0x363, int(hdaii)))
          if frame % 5 == 0:
            wanted.extend((address, int(hdaii)) for address in ((0x398, 0x399, 0x39A, 0x39B, 0x39C) if hdaii else (0x398,)))
          if hdaii and frame % 20 == 0:
            wanted.append((0x43A, 1))
          if frame % 2 == 0:
            wanted.extend((address, int(hdaii)) for address in (0x420, 0x421, 0x389))
            if fca:
              wanted.append((0x38D, 1))
          if hdaii and frame % 5 == 0:
            wanted.append((0x485, 1))
          self.assertEqual([(address, bus) for address, _, bus in messages], wanted)
          packets = {address: data for address, data, _ in messages}
          if hdaii and frame % 5 == 0:
            self.assertEqual(decode(ci, 0x2A4, packets[0x2A4], 'COUNTER'), camera)
          for address, data, _ in messages:
            if address not in (0x730, 0x7D0, 0x50, 0x51, 0x2A4):
              self.assertEqual(data[0], hyundai_checksum(data[1:8]), hex(address))
          self.assertAlmostEqual(actuators.accel, 3.5)
          if hdaii:
            self.assertNotIn(0x364, packets)
            self.assertEqual(decode(ci, 0x50, packets[0x50], 'TORQUE_REQUEST'), actuators.torqueOutputCan)
            self.assertEqual(decode(ci, 0x340, packets[0x340], 'CR_Lkas_StrToqReq'), actuators.torqueOutputCan)
            self.assertEqual(decode(ci, 0x50, packets[0x50], 'STEER_REQ'), decode(ci, 0x340, packets[0x340], 'CF_Lkas_ActToi'))
            self.assertEqual(decode(ci, 0x340, packets[0x340], 'CF_Lkas_MsgCount'), frame % 15)
          if frame % 2 == 0:
            self.assertAlmostEqual(decode(ci, 0x420, packets[0x420], 'aReqRaw'), 3.5)
          self.assertFalse(cp.passive)

  def test_hda2_mirror_fault_suppression_and_disengage_icon_boundary(self):
    ci = selected_interface(True)
    command = structs.CarControl()
    command.enabled = command.latActive = True
    command.actuators.torque = 0.5
    ci.CS.out = ci.CS.out.as_reader().as_builder()
    ci.CS.out.steeringAngleDeg = 85.0
    ci.CS.out = ci.CS.out.as_reader()
    requests = []
    for frame in range(94):
      _, messages = ci.apply(command.as_reader(), 2_000_000_000 + frame * 10_000_000)
      packets = {address: data for address, data, _ in messages}
      primary = decode(ci, 0x50, packets[0x50], 'STEER_REQ')
      mirror = decode(ci, 0x340, packets[0x340], 'CF_Lkas_ActToi')
      self.assertEqual(primary, mirror)
      requests.append(bool(primary))
    self.assertEqual(requests[88:92], [True, False, False, True])
    command.enabled = command.latActive = False
    for frame in range(94, 195):
      _, messages = ci.apply(command.as_reader(), 2_000_000_000 + frame * 10_000_000)
      packets = {address: data for address, data, _ in messages}
      expected = 3 if frame - 93 < 100 else 1
      self.assertEqual(decode(ci, 0x50, packets[0x50], 'LKA_ICON'), expected)
      self.assertEqual(decode(ci, 0x340, packets[0x340], 'CF_Lkas_FcwOpt_USM'), 2 if expected == 3 else 1)

  def test_actual_mixed_topology_metadata_correct_bus_and_wrong_bus(self):
    for hdaii in (False, True):
      for observation_bus in (0, 1, 2):
        with self.subTest(hdaii=hdaii, observation_bus=observation_bus):
          fingerprint = gen_empty_fingerprint()
          if hdaii:
            fingerprint[2][0x50] = 16
          fingerprint[observation_bus][0x38D] = 8
          fingerprint[observation_bus][0x58B] = 8
          stock = CarInterface.get_params(CAR.HYUNDAI_PALISADE_2023, fingerprint, [], False, False, False)
          expected_fca = observation_bus in (int(hdaii), 2)
          self.assertEqual(bool(stock.flags & HyundaiFlags.USE_FCA), expected_fca)
          self.assertEqual(stock.deprecated.enableBsm, observation_bus == int(hdaii))
          candidate = candidate_from_stock(stock, alpha_requested=True, native_qualified=True)
          if not hdaii and expected_fca:
            self.assertIsNone(candidate)
          else:
            self.assertIsNotNone(candidate)
            self.assertEqual(bool(candidate.flags & HyundaiFlags.USE_FCA), expected_fca)
            self.assertEqual(candidate.deprecated.enableBsm, stock.deprecated.enableBsm)

  def test_mixed_standstill_metadata_retains_original_bus_zero(self):
    for hdaii in (False, True):
      for observation_bus in (0, 1):
        with self.subTest(hdaii=hdaii, observation_bus=observation_bus):
          fingerprint = gen_empty_fingerprint()
          if hdaii:
            fingerprint[2][0x50] = 16
          fingerprint[observation_bus][0x2AA] = 8
          stock = CarInterface.get_params(CAR.HYUNDAI_PALISADE_2023, fingerprint, [], False, False, False)
          self.assertEqual(stock.steerAtStandstill, observation_bus == 0)
          candidate = candidate_from_stock(stock, alpha_requested=True, native_qualified=True)
          self.assertEqual(candidate.steerAtStandstill, stock.steerAtStandstill)
          # Exercise the reached flag clear with a real mutable CP, not a fake field bag.
          stock.flags |= HyundaiFlags.MIN_STEER_32_MPH.value
          stock.minSteerSpeed = 10.0
          CarInterface._get_params(stock, CAR.HYUNDAI_PALISADE_2023, fingerprint, [], False, False, False)
          self.assertEqual(bool(stock.flags & HyundaiFlags.MIN_STEER_32_MPH), observation_bus != 0)
          self.assertEqual(stock.minSteerSpeed, 0.0 if observation_bus == 0 else 10.0)
