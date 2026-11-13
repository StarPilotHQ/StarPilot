"""Actual policy callbacks and MPU write monitor; Cortex register IO is modeled."""
import ctypes
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope='module', params=[False, True], ids=['DEBUG', 'RELEASE'])
def native_template(tmp_path_factory, request):
  if not sys.platform.startswith('linux'):
    pytest.skip('The native safety-state linker section requires ELF')
  directory = tmp_path_factory.mktemp('mpu-aol')
  comms = (ROOT / 'panda/board/main_comms.h').read_text()
  matches = [re.search(rf'    case 0x{value}:.*?(?=    //|    case)', comms, re.DOTALL) for value in ('d5', 'f5')]
  assert all(match is not None for match in matches)
  cases = ''.join(match[0] for match in matches if match is not None)
  main = (ROOT / 'panda/board/main.c').read_text()
  warning = main[main.index('    bool warning_engaged ='):main.index('    if (is_car_safety_mode(current_safety_mode) && warning_engaged)')]
  health = (ROOT / 'panda/board/health.h').read_text()
  health = health[health.index('#define AOL_SAFETY_PROTOCOL_MAGIC'):health.index('} aol_safety_health_t;') + len('} aol_safety_health_t;')]
  cmsis = (ROOT / 'panda/board/stm32h7/inc/core_cm7.h').read_text()
  masks = '\n'.join(line for line in cmsis.splitlines() if re.match(r'#define MPU_(RASR|CTRL|TYPE|RBAR)_', line))
  api = (ROOT / 'panda/board/stm32h7/inc/mpu_armv7.h').read_text()
  masks += '\n' + '\n'.join(line for line in api.splitlines() if re.match(r'#define ARM_MPU_(AP_|REGION_SIZE_)', line))
  code = r'''
#include "opendbc/safety/tests/libsafety/safety.c"
#define STM32H7
#define ENTER_CRITICAL() (++critical_depth)
#define EXIT_CRITICAL() (--critical_depth)
unsigned critical_depth;
struct { uint32_t TYPE, CTRL, RNR, RBAR, RASR; } mpu;
#define MPU (&mpu)
void __DMB(void) {}
void __DSB(void) {}
void __ISB(void) {}
void register_set(volatile uint32_t *reg, uint32_t value, uint32_t mask) { *reg = value & mask; }
#define PERMANENT_FAULTS 0U
#define FAULT_MPU (1UL << 28)
#define FAULT_REGISTER_DIVERGENT (1UL << 18)
void fault_occurred_value(uint32_t fault, uint32_t value) { (void)fault; (void)value; }
uint32_t _stack_guard;
''' + masks + '\n#include "panda/board/stm32h7/llmpu.h"\n' + health + r'''
void test_usb(uint16_t request, uint16_t param1) {
 struct { uint16_t param1; } packet = {param1}, *req = &packet;
 unsigned char resp[64];
 unsigned resp_len = 0U;
 switch (request) {
''' + cases + r'''
 default: abort();
 }
 (void)resp; (void)resp_len;
}
void test_warning(void) {
''' + warning + r'''
 (void)warning_engaged;
}
// A supported policy callback may revoke controls on a host withdrawal.
void test_host_withdrawal(uint8_t mask) { (void)mask; controls_allowed = false; }
void test_run(unsigned context) {
 init_tests();
 set_alternative_experience(32);
 if (set_safety_hooks(6, 3) != 0) abort();
 controls_allowed = true;
 mpu_init();
 if (context == 0U) test_usb(0xd5, 0);
 else if (context == 1U) test_warning();
 else if (context == 2U) {
   AolSafetyPolicy policy = *aol_policy;
   policy.host_request = test_host_withdrawal;
   aol_policy = &policy;
   test_usb(0xf5, 0);
 } else controls_allowed = false;
 // The next normal safety entry detects any write outside the protected scope.
 mpu_safety_open();
 mpu_safety_close();
}
bool test_changed(void) { return mpu_safety_changed; }
unsigned test_depth(void) { return critical_depth + mpu_safety_depth; }
'''
  source = directory / 'mpu.c'
  source.write_text(code)
  linker = directory / 'safety.ld'
  linker.write_text('''SECTIONS { .test_safety ALIGN(64) : {
_ssafety = .;
*(.data.controls_allowed .bss.controls_allowed)
*(.data.current_safety_mode .bss.current_safety_mode)
*(.data.current_safety_param .bss.current_safety_param)
*(.data.current_hooks .bss.current_hooks)
*(.data.current_safety_config .bss.current_safety_config)
. = _ssafety + 64;
} } INSERT BEFORE .data;
''')
  library = directory / 'mpu.so'
  subprocess.run(['cc', '-shared', '-fPIC', '-fdata-sections', '-std=gnu11',
                  '-Wl,-Bsymbolic', '-Wl,-T,' + str(linker),
                  *([] if request.param else ['-DALLOW_DEBUG']), '-I', str(ROOT),
                  '-I', str(ROOT / 'opendbc_repo'), str(source), '-o', str(library)], check=True, timeout=60)
  return library


@pytest.mark.parametrize('context', [0, 1, 2, 3], ids=['status', 'warning', 'host-request', 'illegal-write'])
def test_aol_callbacks_preserve_mpu_detection(native_template, tmp_path, context):
  library = tmp_path / 'mpu.so'
  shutil.copyfile(native_template, library)
  native = ctypes.CDLL(str(library))
  native.test_run.argtypes = [ctypes.c_uint]
  native.test_changed.restype = native.get_controls_allowed.restype = ctypes.c_bool
  native.test_run(context)
  assert not native.get_controls_allowed()
  assert native.test_changed() is (context == 3)
  assert native.test_depth() == 0
