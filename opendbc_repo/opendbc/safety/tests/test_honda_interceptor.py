from pathlib import Path
import signal
import subprocess
import sys
import unittest

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.honda.nidec_interceptor import create_command, pedal_crc
from opendbc.safety.tests.libsafety import libsafety_py


class TestHondaInterceptor(unittest.TestCase):
  def init(self, word):
    self.safety = libsafety_py.libsafety
    self.assertEqual(self.safety.set_safety_hooks(structs.CarParams.SafetyModel.hondaNidec, word), 0)
    self.safety.init_tests()
    self.safety.set_timer(0)
    self.alt = word in (4, 36)
    self.packer = CANPacker("acura_ilx_2016_can_generated" if self.alt else "honda_civic_touring_2016_can_generated")

  @staticmethod
  def packet(message):
    return libsafety_py.make_CANPacket(message[0], message[2], message[1])

  def rx(self, name, bus, values):
    return self.safety.safety_rx_hook(self.packet(self.packer.make_can_msg(name, bus, values)))

  def sensor(self, level=0, state=0):
    return self.rx("GAS_SENSOR", 0, {"INTERCEPTOR_GAS": level, "INTERCEPTOR_GAS2": level, "STATE": state})

  def feed(self, tick, *, button=0, brake=False, level=0, sensor=True):
    self.safety.set_timer(tick * 10_000)
    self.assertTrue(self.rx("ENGINE_DATA", 0, {"XMISSION_SPEED": 30}))
    self.assertTrue(self.rx("POWERTRAIN_DATA", 0, {"ACC_STATUS": 0, "BRAKE_PRESSED": brake, "PEDAL_GAS": 0}))
    self.assertTrue(self.rx("SCM_BUTTONS", 0, {"CRUISE_BUTTONS": button, **({"MAIN_ON": 1} if self.alt else {})}))
    if not self.alt:
      self.assertTrue(self.rx("SCM_FEEDBACK", 0, {"MAIN_ON": 1}))
    self.assertTrue(self.rx("BRAKE_COMMAND", 2, {"COMPUTER_BRAKE": 0}))
    if sensor:
      self.assertTrue(self.sensor(level))
    self.safety.safety_tick()

  def engage(self, word):
    self.init(word)
    for tick in range(20):
      self.feed(tick)
    self.feed(20, button=3)
    self.feed(21, button=0)
    self.assertTrue(self.safety.get_controls_allowed())

  def command(self, demand=0.5, counter=0):
    return create_command(self.packer, demand, counter)

  def tx(self, message):
    return self.safety.safety_tx_hook(self.packet(message))

  def malformed(self, message, offset, value, *, checksum=True):
    data = bytearray(message[1])
    data[offset] = value
    if checksum:
      data[5] = pedal_crc(data)
    return message[0], bytes(data), message[2]

  def _assert_callbacks_finish(self, words):
    try:
      mutant = libsafety_py.libsafety.mutation_get_active_mutant()
    except AttributeError:
      mutant = None
    script = """import faulthandler, sys
from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car.honda.nidec_interceptor import create_command
from opendbc.safety.tests.libsafety import libsafety_py
faulthandler.enable(all_threads=True)
libsafety_py.load(sys.argv[1])
safety = libsafety_py.libsafety
if sys.argv[2] != 'None':
  safety.mutation_set_active_mutant(int(sys.argv[2]))
word = int(sys.argv[3])
if safety.set_safety_hooks(structs.CarParams.SafetyModel.hondaNidec, word) != 0:
  print('NATIVE_SETUP_ASSERTION', flush=True)
  raise SystemExit(1)
safety.init_tests()
packer = CANPacker('acura_ilx_2016_can_generated' if word in (4, 36) else 'honda_civic_touring_2016_can_generated')
message = packer.make_can_msg('POWERTRAIN_DATA', 0, {'ACC_STATUS': 0}) if word in (0, 4) else create_command(packer, 0.0, 0)
packet = libsafety_py.make_CANPacket(message[0], message[2], message[1])
print('NATIVE_CALLBACK_BEGIN', flush=True)
accepted = safety.safety_rx_hook(packet) if word in (0, 4) else safety.safety_tx_hook(packet)
print('NATIVE_CALLBACK_RETURN', flush=True)
raise SystemExit(0 if accepted else 1)
"""
    for word in words:
      with self.subTest(word=word):
        try:
          result = subprocess.run([sys.executable, '-c', script, libsafety_py.libpath, str(mutant), str(word)],
                                  cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired as exc:
          output = exc.stdout or b''
          if isinstance(output, bytes):
            output = output.decode(errors='replace')
          if 'NATIVE_CALLBACK_BEGIN' not in output:
            raise RuntimeError('Native callback probe did not reach its callback') from exc
          self.fail('Native callback did not finish within ten seconds')
        entered = 'NATIVE_CALLBACK_BEGIN' in result.stdout
        if result.returncode == -signal.SIGSEGV and entered and 'NATIVE_CALLBACK_RETURN' not in result.stdout:
          self.fail('Native callback faulted on a fully allocated valid packet: ' + result.stderr)
        asserted = 'NATIVE_SETUP_ASSERTION' in result.stdout or 'NATIVE_CALLBACK_RETURN' in result.stdout
        if result.returncode not in (0, 1) or not asserted:
          raise RuntimeError(f'Native callback probe infrastructure failure ({result.returncode}): {result.stdout}{result.stderr}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_all_zero_commands_finish_without_native_memory_faults(self):
    self._assert_callbacks_finish((32, 36))

  def test_all_stock_honda_rx_callbacks_finish_without_native_memory_faults(self):
    self._assert_callbacks_finish((0, 4))

  def test_both_tracks_enable_reserved_bits_and_crc_are_required(self):
    for word in (32, 36):
      self.engage(word)
      command = self.command()
      self.assertTrue(self.tx(command))
      for offset in (0, 1, 2, 3):
        self.assertFalse(self.tx(self.malformed(command, offset, command[1][offset] ^ 0x20)))
      self.assertFalse(self.tx(self.malformed(command, 4, command[1][4] | 0x10)))
      self.assertFalse(self.tx(self.malformed(command, 4, command[1][4] & 0x7F)))
      self.assertFalse(self.tx(self.malformed(command, 5, command[1][5] ^ 1, checksum=False)))
      self.assertFalse(self.tx((0x200, command[1], 2)))
      self.assertFalse(self.tx((0x200, command[1][:5], 0)))
      for demand in (0.002, 0.1, 0.5, 1.0):
        self.assertTrue(self.tx(self.command(demand)))

  def test_physical_set_resume_driver_override_expiry_and_zero(self):
    for word in (32, 36):
      self.init(word)
      self.assertTrue(self.tx(self.command(0.0)))
      self.assertFalse(self.tx(self.command()))
      self.engage(word)
      self.assertTrue(self.tx(self.command()))
      self.feed(22, level=493)
      self.assertFalse(self.tx(self.command()))
      self.assertTrue(self.tx(self.command(0.0)))
      self.engage(word)
      self.feed(22, brake=True)
      self.assertFalse(self.tx(self.command()))
      self.engage(word)
      self.sensor(state=1)
      self.assertFalse(self.tx(self.command()))
      self.engage(word)
      for tick in range(22, 50):
        self.feed(tick, sensor=False)
      self.assertFalse(self.tx(self.command()))
      self.assertTrue(self.tx(self.command(0.0)))

  def test_stock_profiles_cannot_send_interceptor_even_zero(self):
    for word in (0, 4):
      self.init(word)
      self.assertFalse(self.tx(self.command(0.0)))
      self.assertFalse(self.tx(self.command()))

  def test_sensor_threshold_and_existing_aeb_forwarding(self):
    for word in (32, 36):
      self.engage(word)
      self.feed(22, level=492)
      self.assertTrue(self.tx(self.command()))
      self.feed(23, level=493)
      self.assertFalse(self.tx(self.command()))
      self.engage(word)
      self.rx("BRAKE_COMMAND", 2, {"COMPUTER_BRAKE": 100, "AEB_REQ_1": 1})
      self.assertFalse(self.safety.safety_fwd_hook(2, 0x1FA))

  def test_counter_bit_does_not_change_enable_or_reserved_bits(self):
    for word in (32, 36):
      self.engage(word)
      for counter in range(16):
        with self.subTest(word=word, counter=counter):
          self.assertTrue(self.tx(self.command(0.0, counter)))
          self.assertTrue(self.tx(self.command(0.5, counter)))

  def test_exact_track_endpoints_and_one_count_tolerance(self):
    for word in (32, 36):
      self.engage(word)
      for first in (328, 329, 330, 1331, 1332, 1333):
        for delta in (-2, -1, 0, 1, 2):
          with self.subTest(word=word, first=first, delta=delta):
            second = 2 * first + delta
            data = bytearray(first.to_bytes(2, 'big') + second.to_bytes(2, 'big') + b'\x80\x00')
            data[5] = pedal_crc(data)
            self.assertEqual(self.tx((0x200, bytes(data), 0)), 329 <= first <= 1332 and abs(delta) <= 1)

  def test_sensor_expires_after_exact_two_hundred_milliseconds(self):
    for word in (32, 36):
      self.engage(word)
      self.safety.set_timer(410_000)
      self.assertTrue(self.tx(self.command()))
      self.safety.set_timer(410_001)
      self.assertFalse(self.tx(self.command()))
      self.assertTrue(self.tx(self.command(0.0)))
