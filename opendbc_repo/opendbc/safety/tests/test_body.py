#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import unittest

from opendbc.safety.tests.libsafety import libsafety_py

from opendbc.car.structs import CarParams
import opendbc.safety.tests.common as common


class TestBody(common.SafetyTest):
  DBC = "comma_body"
  SAFETY_MODEL = CarParams.SafetyModel.body

  TX_MSGS = [[0x250, 0], [0x251, 0],
             [0x1, 0], [0x1, 1], [0x1, 2], [0x1, 3]]
  FWD_BUS_LOOKUP = {}

  def _motors_data_msg(self, speed_l, speed_r):
    values = {"SPEED_L": speed_l, "SPEED_R": speed_r}
    return self.packer.make_can_msg_safety("MOTORS_DATA", 0, values)

  def _torque_cmd_msg(self, torque_l, torque_r):
    values = {"TORQUE_L": torque_l, "TORQUE_R": torque_r}
    return self.packer.make_can_msg_safety("TORQUE_CMD", 0, values)

  def test_rx_hook(self):
    self.assertFalse(self.safety.get_controls_allowed())

    # controls allowed when we get MOTORS_DATA message
    self.assertTrue(self._rx(self._torque_cmd_msg(0, 0)))
    self.assertFalse(self.safety.get_controls_allowed())

    self.assertTrue(self._rx(self._motors_data_msg(0, 0)))
    self.assertTrue(self.safety.get_controls_allowed())

    self._reset_safety_hooks()
    for _ in range(16):
      self.assertTrue(self._rx(self._motors_data_msg(0, 0)))

    msg = self._motors_data_msg(0, 0)
    msg[0].data[7] ^= 0xff
    self.assertFalse(self._rx(msg))

    self._reset_safety_hooks()
    for _ in range(16):
      self.assertTrue(self._rx(self._motors_data_msg(0, 0)))

    msg = self._motors_data_msg(0, 0)
    for _ in range(common.MAX_WRONG_COUNTERS + 1):
      valid = self._rx(msg)
    self.assertFalse(valid)

  def test_rx_callback_finishes(self):
    try:
      mutant = self.safety.mutation_get_active_mutant()
    except AttributeError:
      mutant = None
    script = """import sys, unittest
from opendbc.safety.tests.libsafety import libsafety_py
libsafety_py.load(sys.argv[1])
if sys.argv[2] != 'None':
  libsafety_py.libsafety.mutation_set_active_mutant(int(sys.argv[2]))
suite = unittest.TestLoader().loadTestsFromName('opendbc.safety.tests.test_body.TestBody.test_rx_hook')
print('NATIVE_BODY_CALLBACK_READY', flush=True)
result = unittest.TextTestRunner().run(suite)
raise SystemExit(2 if result.errors or result.testsRun != 1 or result.skipped else 1 if result.failures else 0)
"""
    try:
      result = subprocess.run([sys.executable, '-c', script, libsafety_py.libpath, str(mutant)],
                              cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
      output = exc.stdout or b''
      if isinstance(output, bytes):
        output = output.decode(errors='replace')
      if 'NATIVE_BODY_CALLBACK_READY' not in output:
        raise RuntimeError('Native callback child did not reach its test') from exc
      self.fail('Native CRC/RX callback did not finish within ten seconds')
    if 'NATIVE_BODY_CALLBACK_READY' not in result.stdout or result.returncode not in (0, 1):
      raise RuntimeError(f'Native callback child infrastructure failure ({result.returncode}): {result.stdout}{result.stderr}')
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_tx_hook(self):
    self.assertFalse(self._tx(self._torque_cmd_msg(0, 0)))
    self.safety.set_controls_allowed(True)
    self.assertTrue(self._tx(self._torque_cmd_msg(0, 0)))

  def test_can_flasher(self):
    # CAN flasher always allowed
    self.safety.set_controls_allowed(False)
    self.assertTrue(self._tx(common.make_msg(0, 0x1, 8)))

    # 0xdeadfaceU allowed for CAN flashing mode
    self.assertTrue(self._tx(common.make_msg(0, 0x250, dat=b'\xce\xfa\xad\xde\x1e\x0b\xb0\x0a')))
    self.assertFalse(self._tx(common.make_msg(0, 0x250, dat=b'\xcf\xfa\xad\xde\x1e\x0b\xb0\x0a')))  # wrong signature
    self.assertFalse(self._tx(common.make_msg(0, 0x250, dat=b'\xce\xfa\xad\xde\x1e\x0b\xb0')))  # not correct data/len
    self.assertFalse(self._tx(common.make_msg(0, 0x251, dat=b'\xce\xfa\xad\xde\x1e\x0b\xb0\x0a')))  # wrong address


if __name__ == "__main__":
  unittest.main()
