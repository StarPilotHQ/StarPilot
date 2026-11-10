"""Source-only preflight regression: never imports the original/runtime tree."""
import ast
import binascii
from collections import namedtuple
from pathlib import Path
import statistics
import time
import unittest
from dataclasses import dataclass
from enum import Enum, auto
from unittest.mock import patch


def checksum(address, _sig, data):
  # Equivalent XMODEM polynomial and existing Hyundai length XORs.
  return binascii.crc_hqx(bytes(data[2:]) + address.to_bytes(2, 'little'), 0) ^ {8: 0x5F29, 16: 0x041D, 24: 0x819D, 32: 0x9F5B}[len(data)]


SOURCE = Path(__file__).resolve().parents[1] / 'ioniq6_handoff.py'
NAMES = {'_ecan_bus', '_ioniq6_boot_ns', '_qualified_ioniq6_ready', 'HandoffOutcome', 'HandoffResult', '_valid_canfd_crc',
         '_read_window', '_measured_period', 'inspect_ioniq6_long_sources', 'run_ioniq6_handoff'}
tree = ast.parse(SOURCE.read_text())
body = [n for n in tree.body if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in NAMES) or
        (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith('IONIQ6_') for t in n.targets))]
ns = dict(statistics=statistics, time=time, dataclass=dataclass, Enum=Enum, auto=auto, hkg_can_fd_checksum=checksum, __name__=__name__)
body.insert(0, ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0))
exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(SOURCE), 'exec'), ns)
CanData = namedtuple('CanData', 'address dat src')


class Bus:
  def __init__(self, modes):
    self.modes, self.now, self.window, self.index, self.drains, self.flags = modes, 0., -1, -1, 0, []

  @staticmethod
  def frame(address, length, counter=0, src=1, bad_crc=False):
    data = bytearray(length)
    data[2] = counter % 256
    data[:2] = checksum(address, None, data).to_bytes(2, 'little')
    if bad_crc:
      data[0] ^= 1
    return CanData(address, bytes(data), src)

  def recv(self, wait_for_one=False):
    self.flags.append(wait_for_one)
    if self.index in (-1, 13):
      self.window += 1
      self.drains += 1
      self.index = 0
      self.now += .001
      return []
    self.index += 1
    mode = self.modes[min(self.window, len(self.modes) - 1)]
    self.now += .020
    if mode == 'empty':
      return []
    frames = [self.frame(a, length) for a, length in ns['IONIQ6_REQUIRED_RX'].items()
              if not (mode == 'required' and a == 0xEA)]
    frames.append(self.frame(0x1A0, 32, self.index * (2 if mode == 'counter' else 1), bad_crc=mode == 'crc'))
    if mode != 'bsm':
      frames.extend(self.frame(a, length) for a, length in ns['IONIQ6_STOCK_BSM'].items())
    if mode != 'heartbeat':
      frames.append(self.frame(0x100, 24, src=0))
    packet = type('Packet', (list,), {})(frames)
    packet.log_mono_time_ns = int((self.now - (.013 if mode == 'period' and self.index == 5 else 0)) * 1e9)
    return [packet]


class TestRetry(unittest.TestCase):
  def inspect(self, modes):
    bus = Bus(modes)
    with patch.object(time, 'sleep'):
      result = ns['inspect_ioniq6_long_sources'](bus.recv, lambda: bus.now)
    return bus, result

  def test_invalid_then_valid(self):
    bus, result = self.inspect(['period', 'valid'])
    self.assertEqual(result.reason, 'preflight_ready')
    self.assertAlmostEqual(result.source_period, .020)
    self.assertEqual(bus.drains, 2)
    self.assertFalse(any(bus.flags))

  def test_all_invalid_denies_without_uds(self):
    bus, exchanges = Bus(['period']), []
    with patch.object(time, 'sleep'):
      result = ns['run_ioniq6_handoff'](bus.recv, lambda *args: exchanges.append(args), lambda: bus.now)
    self.assertEqual(result.reason, 'preflight_scc_period')
    self.assertIsNone(result.source_period)
    self.assertEqual(bus.drains, 3)
    self.assertEqual(exchanges, [])
    self.assertLess(bus.now, .81)

  def test_crc_counter_health_unchanged(self):
    for mode, reason in [('crc', 'scc_period'), ('counter', 'scc_period'), ('required', 'required_rx'),
                         ('bsm', 'bsm_status'), ('heartbeat', 'radar_heartbeat')]:
      with self.subTest(mode=mode):
        bus, result = self.inspect([mode])
        self.assertIsNone(result.source_period)
        self.assertIn(reason, result.reason)
        self.assertEqual(bus.drains, 3)

  def test_no_cross_window_health_and_latest_failure(self):
    bus, result = self.inspect(['heartbeat', 'bsm', 'required'])
    self.assertEqual(result.reason, 'preflight_required_rx')
    self.assertIsNone(result.source_period)
    self.assertEqual(bus.drains, 3)

  def test_empty_bus_real_clock_is_bounded(self):
    flags = []
    started = time.monotonic()
    result = ns['inspect_ioniq6_long_sources'](lambda wait_for_one=False: flags.append(wait_for_one) or [])
    self.assertIsNone(result.source_period)
    self.assertFalse(any(flags))
    self.assertLess(time.monotonic() - started, 1.25)

  def test_cadence_thresholds_unchanged(self):
    measured = ns['_measured_period']
    good = [(i * .020, i, i * .020) for i in range(8)]
    self.assertAlmostEqual(measured(good), .020)
    for bad in ([(t,c,0.) for t,c,_ in good], [(t,c*2,r) for t,c,r in good],
                [(i*.031,i,i*.031) for i in range(8)], [(i*.011,i,i*.020) for i in range(8)], good[:3]):
      self.assertIsNone(measured(bad))


if __name__ == '__main__':
  unittest.main()
