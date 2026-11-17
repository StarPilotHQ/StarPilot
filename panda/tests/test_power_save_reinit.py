"""Compile production power-save/CAN-init bodies against observable fake hardware."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def function(source, signature):
  start = source.index(signature)
  opening = source.index('{', start)
  depth = 1
  end = opening + 1
  while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
  return source[start:end]


class TestPowerSaveReinit(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.temp = tempfile.TemporaryDirectory()
    cls.addClassCleanup(cls.temp.cleanup)
    power = (ROOT / 'panda/board/sys/power_saving.h').read_text()
    cls.libs = {}
    for driver in ('bxcan', 'fdcan'):
      for hkg in (False, True):
        init = function((ROOT / f'panda/board/drivers/{driver}.h').read_text(), 'bool can_init(uint8_t can_number)')
        source = r'''
#include <stdint.h>
#include <stdbool.h>
#define HARNESS_STATUS_FLIPPED 1U
#define REGISTER_INTERRUPT(...)
#define CANIF_FROM_CAN_NUM(n) cans[n]
typedef struct { unsigned index; uint32_t IR; uint32_t TXFQS; } CAN_TypeDef;
typedef CAN_TypeDef FDCAN_GlobalTypeDef;
static CAN_TypeDef controllers[3] = {{0U, 0U, 1U}, {1U, 0U, 1U}, {2U, 0U, 1U}};
static CAN_TypeDef *cans[3] = {&controllers[0], &controllers[1], &controllers[2]};
static struct { unsigned status; } harness;
static bool irq[3], transceivers[4];
static unsigned faults, errors, prints, bad_process;
static bool power_save_enabled;
static void print(const char *text) { (void)text; prints++; }
static void llcan_irq_enable(const CAN_TypeDef *controller) { irq[controller->index] = true; }
static void llcan_irq_disable(const CAN_TypeDef *controller) { irq[controller->index] = false; }
static bool llcan_init(const CAN_TypeDef *controller) { llcan_irq_enable(controller); return true; }
static bool can_set_speed(uint8_t number) { (void)number; return true; }
static void enable_transceiver(uint8_t number, bool enabled) { transceivers[number - 1U] = enabled; }
static void set_ir_power(uint8_t power) { (void)power; }
static struct { void (*enable_can_transceiver)(uint8_t, bool); void (*set_ir_power)(uint8_t); }
  board = {enable_transceiver, set_ir_power}, *current_board = &board;
static void process_can(uint8_t number) {
  unsigned camera = harness.status == HARNESS_STATUS_FLIPPED ? 0U : 2U;
  if ((number == camera) && irq[camera] && !transceivers[camera]) bad_process++;
}
'''
        source += function(power, 'void enable_can_transceivers(bool enabled)') + '\n'
        source += function(power, 'void set_power_save_state(bool enable)') + '\n' + init
        source += r'''
void reset(bool flipped, bool saving) {
  harness.status = flipped ? HARNESS_STATUS_FLIPPED : 0U;
  for (unsigned i = 0U; i < 3U; i++) irq[i] = true;
  for (unsigned i = 0U; i < 4U; i++) transceivers[i] = true;
  power_save_enabled = false;
  faults = errors = prints = bad_process = 0U;
  set_power_save_state(saving);
}
void cold_reset(void) { reset(false, false); for (unsigned i = 0U; i < 3U; i++) irq[i] = false; }
void reinit(unsigned controller) { (void)can_init((uint8_t)controller); }
void save(bool enabled) { set_power_save_state(enabled); }
void flip(void) { harness.status ^= HARNESS_STATUS_FLIPPED; }
void awake_offroad_traffic(void) {
  unsigned camera = harness.status == HARNESS_STATUS_FLIPPED ? 0U : 2U;
  if (irq[camera] && !transceivers[camera]) { errors += 21000U; faults |= 16U; }
}
void latch_fault(void) { faults |= 16U; }
bool irq_enabled(unsigned i) { return irq[i]; }
bool transceiver_enabled(unsigned i) { return transceivers[i]; }
bool saving(void) { return power_save_enabled; }
unsigned fault_mask(void) { return faults; }
unsigned error_count(void) { return errors; }
unsigned print_count(void) { return prints; }
unsigned process_before_restore(void) { return bad_process; }
'''
        if driver == 'fdcan':
          source += r"""
#define FDCAN_TX_FIFO_EL_CNT 1UL
#define FDCAN_TXFQS_TFFL 0x3FU
#define UNUSED(value) ((void)(value))
static uint32_t clock_us;
static struct { unsigned can_core_reset_cnt; unsigned total_tx_lost_cnt; } can_health[3];
static uint32_t microsecond_timer_get(void) { return clock_us; }
static uint32_t get_ts_elapsed(uint32_t now, uint32_t then) { return now - then; }
"""
          source += function((ROOT / 'panda/board/stm32h7/llfdcan.h').read_text(),
                             'void llcan_clear_send(FDCAN_GlobalTypeDef *FDCANx)') + '\n'
          source += function((ROOT / 'panda/board/drivers/fdcan.h').read_text(),
                             'void can_clear_send(FDCAN_GlobalTypeDef *FDCANx, uint8_t can_number)') + '\n'
          source += r"""
void recovery(unsigned controller, unsigned elapsed) {
  clock_us += elapsed;
  can_clear_send(cans[controller], (uint8_t)controller);
}
unsigned recovery_count(unsigned controller) { return can_health[controller].can_core_reset_cnt; }
"""
        file = Path(cls.temp.name) / f'{driver}-{hkg}.c'
        lib = file.with_suffix('.so')
        file.write_text(source)
        command = ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', str(file), '-o', str(lib)]
        if hkg:
          command.insert(1, '-DPANDA_HKG_REMOTE_START')
        subprocess.run(command, check=True, capture_output=True)
        loaded = ctypes.CDLL(str(lib))
        for name in ('irq_enabled', 'transceiver_enabled', 'saving'):
          getattr(loaded, name).restype = ctypes.c_bool
        cls.libs[driver, hkg] = loaded

  def check_state(self, lib, flipped, saving, hkg):
    main = 2 if flipped else 0
    hkg_transceiver = 3 if flipped else 1
    for index in range(3):
      self.assertEqual(lib.irq_enabled(index), not saving or index == main or hkg and index == 1)
    for index in range(4):
      self.assertEqual(lib.transceiver_enabled(index), not saving or index == main or hkg and index == hkg_transceiver)
    self.assertEqual(lib.saving(), saving)

  def test_single_and_all_controller_reinit_preserve_power_save(self):
    for (driver, hkg), lib in self.libs.items():
      for flipped in (False, True):
        for saving in (False, True):
          with self.subTest(driver=driver, hkg=hkg, flipped=flipped, saving=saving):
            lib.reset(flipped, saving)
            for _ in range(5):
              # can_init_all, safety changes, bitrate changes, and loopback use
              # these same production controller initializers.
              for controller in range(3):
                lib.reinit(controller)
                lib.awake_offroad_traffic()
                self.check_state(lib, flipped, saving, hkg)
            self.assertEqual(lib.error_count(), 0)
            self.assertEqual(lib.fault_mask(), 0)
            self.assertEqual(lib.process_before_restore(), 0)

  def test_same_state_refresh_harness_flip_and_onroad_wake(self):
    for (driver, hkg), lib in self.libs.items():
      with self.subTest(driver=driver, hkg=hkg):
        lib.reset(False, True)
        before = lib.print_count()
        lib.save(True)
        self.assertEqual(lib.print_count(), before)
        lib.flip()
        for controller in range(3):
          lib.reinit(controller)
        lib.save(True)
        self.check_state(lib, True, True, hkg)
        self.assertEqual(lib.print_count(), before)
        lib.save(False)
        self.check_state(lib, True, False, hkg)
        lib.save(False)
        self.assertEqual(lib.print_count(), before + 1)

  def test_reinitialization_never_clears_a_latched_fault(self):
    for (driver, hkg), lib in self.libs.items():
      with self.subTest(driver=driver, hkg=hkg):
        lib.reset(False, True)
        lib.latch_fault()
        for controller in range(3):
          lib.reinit(controller)
        lib.save(False)
        self.assertEqual(lib.fault_mask(), 16)

  def test_onroad_cold_init_does_not_enable_other_controllers_early(self):
    for (driver, hkg), lib in self.libs.items():
      with self.subTest(driver=driver, hkg=hkg):
        lib.cold_reset()
        for controller in range(3):
          lib.reinit(controller)
          for other in range(3):
            self.assertEqual(lib.irq_enabled(other), other <= controller)

  def test_fdcan_error_recovery_preserves_power_and_latched_faults(self):
    for hkg in (False, True):
      lib = self.libs['fdcan', hkg]
      for flipped in (False, True):
        for saving in (False, True):
          for controller in range(3):
            with self.subTest(hkg=hkg, flipped=flipped, saving=saving, controller=controller):
              lib.reset(flipped, saving)
              lib.latch_fault()
              lib.recovery(controller, 200_000)
              self.check_state(lib, flipped, saving, hkg)
              self.assertEqual(lib.fault_mask(), 16)
              lib.awake_offroad_traffic()
              self.assertEqual(lib.error_count(), 0, 'FDCAN_RECOVERY_POWER_STATE_LOST')

  def test_fdcan_error_recovery_keeps_existing_reset_rate_limit(self):
    for hkg in (False, True):
      lib = self.libs['fdcan', hkg]
      for flipped in (False, True):
        with self.subTest(hkg=hkg, flipped=flipped):
          lib.reset(flipped, True)
          controller = 0 if flipped else 2
          before = lib.recovery_count(controller)
          lib.recovery(controller, 200_000)
          self.assertEqual(lib.recovery_count(controller), before + 1)
          lib.recovery(controller, 100_000)
          self.assertEqual(lib.recovery_count(controller), before + 1)
          lib.recovery(controller, 1)
          self.assertEqual(lib.recovery_count(controller), before + 2)
          self.check_state(lib, flipped, True, hkg)
