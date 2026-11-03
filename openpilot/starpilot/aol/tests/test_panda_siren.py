"""Host heartbeat -> native USB handlers -> safety RX -> actual 8 Hz siren tick.

Hardware IO is modeled. The compiled tick, timer/interrupt health checks, fault
recording and USB cases are extracted verbatim from production sources, and
permissions come from real Toyota CAN RX hooks.
"""
import ctypes
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.test_toyota_secoc_aol import checksum

ROOT = Path(__file__).resolve().parents[4]


def usb_case(source, request):
  match = re.search(rf'    case 0x{request}:.*?(?=    //|    case)', source, re.S)
  assert match is not None
  return match[0]


@pytest.fixture(scope='module', params=[False, True], ids=['DEBUG', 'RELEASE'])
def native_template(tmp_path_factory, request):
  tmp_path = tmp_path_factory.mktemp('siren-native')
  main = (ROOT / 'panda/board/main.c').read_text()
  tick = main[main.index('// go into SILENT'):main.index('\nint main(void)')]
  comms = (ROOT / 'panda/board/main_comms.h').read_text()
  cases = ''.join(usb_case(comms, r) for r in ('f3', 'f5', 'f6'))
  drivers = (ROOT / 'panda/board/drivers/drivers.h').read_text()
  declarations = drivers[drivers.index('typedef struct interrupt'):drivers.index('// ******************** registers')]
  utils = (ROOT / 'panda/board/utils.h').read_text()
  elapsed = re.search(r'uint32_t get_ts_elapsed\(.*?\n\}', utils, re.S)
  assert elapsed is not None
  timers = (ROOT / 'panda/board/drivers/timers.h').read_text()
  timer_check = timers[timers.index('bool timer_check('):timers.index('\nvoid interrupt_timer_init(')]
  interrupts = (ROOT / 'panda/board/drivers/interrupts.h').read_text()
  interrupts = interrupts[interrupts.index('static void unused_interrupt_handler'):]
  sys = (ROOT / 'panda/board/sys/sys.h').read_text()
  faults = sys[sys.index('#define FAULT_STATUS_NONE'):sys.index('// ******************** power_saving')]
  fault_impl = (ROOT / 'panda/board/sys/faults.h').read_text()
  faults += fault_impl[fault_impl.index('uint8_t fault_status'):]
  code = r'''
#include "opendbc/safety/tests/libsafety/safety.c"
uint32_t heartbeat_counter, heartbeat_tick_counter, uptime_cnt, ignition_can_cnt, wake_on_can_cnt;
uint32_t voltage = 12000U;
bool heartbeat_lost, heartbeat_disabled, siren_enabled, ignition_can, wake_on_can, power_save_enabled;
bool ignition_line = true, siren_output;
struct { uint32_t SR; } timer, interrupt_timer;
#define TICK_TIMER (&timer)
#define INTERRUPT_TIMER (&interrupt_timer)
// STM32F413 register/IRQ layout; only hardware access is modeled.
#define TIM_SR_UIF 1U
#define NUM_INTERRUPTS 102U
#define TICK_TIMER_IRQ 24U
#define INTERRUPT_TIMER_IRQ 54U
typedef uint32_t IRQn_Type;
struct { uint32_t ISER[8]; uint8_t IP[240]; } nvic;
#define NVIC (&nvic)
#define ENTER_CRITICAL() ((void)0)
#define EXIT_CRITICAL() ((void)0)
uint32_t current_irq;
uint32_t __get_IPSR(void) { return current_irq + 16U; }
uint32_t __CLZ(uint32_t value) { return value ? __builtin_clz(value) : 32U; }
uint32_t __RBIT(uint32_t value) {
 uint32_t reversed = 0U;
 for (uint32_t i = 0U; i < 32U; i++) { reversed = (reversed << 1U) | (value & 1U); value >>= 1U; }
 return reversed;
}
void NVIC_EnableIRQ(IRQn_Type irq) { NVIC->ISER[irq >> 5U] |= 1U << (irq & 31U); }
void NVIC_DisableIRQ(IRQn_Type irq) { NVIC->ISER[irq >> 5U] &= ~(1U << (irq & 31U)); }
// Register setup is hardware IO; the timer ISR and its health checks below are real.
void interrupt_timer_init(void) {}
void fault_safe_state(void) { abort(); }
void print(const char *s) { (void)s; }
void puth(uint32_t n) { (void)n; }
''' + declarations + faults + elapsed[0] + '\n' + timer_check + interrupts + r'''
#define HARNESS_STATUS_NC 0U
#define HARNESS_STATUS_FLIPPED 2U
#define LED_GREEN 0U
#define LED_BLUE 1U
struct { uint8_t status; } harness;
void noop(void) {}
uint32_t read_voltage(void) { return voltage; }
void set_siren(bool enabled) { siren_output = enabled; }
void set_ir_power(uint8_t value) { (void)value; }
bool read_som_gpio(void) { return true; }
struct { uint32_t (*read_voltage_mV)(void); void (*set_siren)(bool);
 void (*set_ir_power)(uint8_t); bool (*read_som_gpio)(void); } board = {
 read_voltage, set_siren, set_ir_power, read_som_gpio}, *current_board = &board;
#define fan_tick noop
#define harness_tick noop
#define simple_watchdog_kick noop
#define sound_tick noop
#define can_init_all noop
#define check_registers noop
void can_set_orientation(bool flipped) { (void)flipped; }
void led_set(uint8_t color, bool enabled) { (void)color; (void)enabled; }
void bootkick_tick(bool started, bool recent, bool wake) { (void)started; (void)recent; (void)wake; }
void fan_set_power(uint8_t power) { (void)power; }
bool panda_ignition_line(void) { return ignition_line; }
bool is_car_safety_mode(uint16_t mode) {
 return mode != SAFETY_SILENT && mode != SAFETY_NOOUTPUT && mode != SAFETY_ALLOUTPUT && mode != SAFETY_ELM327;
}
void set_safety_mode(uint16_t mode, uint16_t param) { set_safety_hooks(mode, param); }
void set_power_save_state(bool enabled) { power_save_enabled = enabled; }
''' + tick + r'''
void test_usb(uint16_t request, uint16_t param1) {
 struct { uint16_t param1; } packet = {param1}, *req = &packet;
 switch (request) {
''' + cases + r'''
 default: abort();
 }
}
bool test_heartbeat_lost(void) { return heartbeat_lost; }
void test_native_init(void) {
 init_interrupts(true);
 REGISTER_INTERRUPT(TICK_TIMER_IRQ, tick_handler, 10U, FAULT_INTERRUPT_RATE_TICK)
 interrupts[TICK_TIMER_IRQ].min_call_rate = 4U;
 REGISTER_INTERRUPT(INTERRUPT_TIMER_IRQ, interrupt_timer_handler, 2U, FAULT_INTERRUPT_RATE_INTERRUPTS)
 nvic_enable(TICK_TIMER_IRQ);
 nvic_enable(INTERRUPT_TIMER_IRQ);
}
uint32_t test_faults(void) { return faults; }
void test_tick(bool powered, bool started) {
 voltage = powered ? 12000U : 5000U;
 ignition_line = started;
 // Model the independent 1 Hz interrupt timer alongside the 8 Hz tick.
 static uint32_t ticks = 0U;
 if ((ticks++ % 8U) == 0U) {
   interrupt_timer.SR = TIM_SR_UIF;
   current_irq = INTERRUPT_TIMER_IRQ;
   handle_interrupt(INTERRUPT_TIMER_IRQ);
 }
 timer.SR = TIM_SR_UIF;
 current_irq = TICK_TIMER_IRQ;
 handle_interrupt(TICK_TIMER_IRQ);
}
bool test_siren(void) { return siren_output; }
'''
  path = tmp_path / 'siren.c'
  path.write_text(code)
  lib = tmp_path / 'siren.so'
  subprocess.run(['cc', '-shared', '-fPIC', '-std=gnu11', '-Wall', '-Werror',
                  *([] if request.param else ['-DALLOW_DEBUG']), '-I', str(ROOT / 'opendbc_repo'),
                  str(path), '-o', str(lib)], check=True)
  return lib


@pytest.fixture
def native(tmp_path, native_template):
  # A separate load path gives each case pristine static timer/native state.
  lib = tmp_path / 'siren.so'
  shutil.copyfile(native_template, lib)
  safety = libsafety_py.ffi.dlopen(str(lib))
  io = ctypes.CDLL(str(lib))
  io.test_usb.argtypes = [ctypes.c_uint16, ctypes.c_uint16]
  io.test_tick.argtypes = [ctypes.c_bool, ctypes.c_bool]
  io.test_siren.restype = ctypes.c_bool
  io.test_heartbeat_lost.restype = ctypes.c_bool
  io.test_faults.restype = ctypes.c_uint32
  io.test_native_init()
  yield safety, io


@pytest.fixture(scope='module')
def host(tmp_path_factory):
  """Compile the actual pandad engaged decision and Panda USB serialization."""
  tmp = tmp_path_factory.mktemp('host-heartbeat')
  pandad = (ROOT / 'openpilot/selfdrive/pandad/pandad.cc').read_text()
  decision_match = re.search(r'      if \(aol_write_ok\) \{.*?\n      \}', pandad, re.S)
  assert decision_match is not None
  decision = decision_match[0]
  panda = (ROOT / 'openpilot/selfdrive/pandad/panda.cc').read_text()
  send_match = re.search(r'void Panda::send_heartbeat\(bool engaged\) \{.*?\n\}', panda, re.S)
  assert send_match is not None
  send = send_match[0]
  code = r'''
#include "openpilot/starpilot/car/toyota/aol_policy.h"
uint16_t usb_request, usb_value;
const AolProfileRegistry registry{&TOYOTA_AOL_PROFILE, 1U};
AolAxisNegotiator negotiator(registry);
AolWritePlan pending;
aol_safety_health_t snapshot;
struct Handle { int control_write(uint16_t r, uint16_t v, uint16_t unused) {
 (void)unused; usb_request = r; usb_value = v; return 0; }} storage, *handle = &storage;
''' + send.replace('Panda::', '') + r'''
extern "C" void host_reset(void) { negotiator = AolAxisNegotiator(registry); }
extern "C" uint64_t host_heartbeat(uint16_t word, uint8_t native_request, uint8_t native_permission,
 uint64_t now, uint8_t mask, bool retain, bool engaged, bool aol_write_ok) {
 snapshot = {AOL_SAFETY_PROTOCOL_MAGIC, AOL_SAFETY_PROTOCOL_VERSION, native_request,
             native_permission, 2U, word, 1U};
 AolAxisInput axis{true, true, "siren-session", now, now + 100000000ULL, now,
                   (mask & 1U) != 0U, (mask & 2U) != 0U, retain};
 auto plan = negotiator.prepare(snapshot, axis, now, 2U);
 pending = plan;
''' + decision + r'''
 send_heartbeat(engaged);
 return (uint64_t(plan.request_mask) << 32) | (uint32_t(usb_request) << 16) | usb_value;
}
extern "C" void host_complete(uint8_t native_request, uint8_t native_permission) {
 snapshot.request_mask = native_request;
 snapshot.permission_mask = native_permission;
 negotiator.complete(pending, true, snapshot);
}
'''
  source = tmp / 'host.cc'
  source.write_text(code)
  lib = tmp / 'host.so'
  subprocess.run(['c++', '-shared', '-fPIC', '-std=c++17', '-Wall', '-Werror', '-I', str(ROOT), '-I', str(ROOT / 'openpilot'),
                  str(source), '-o', str(lib)], check=True)
  dll = ctypes.CDLL(str(lib))
  dll.host_heartbeat.argtypes = [ctypes.c_uint16, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint64,
                                 ctypes.c_uint8, ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
  dll.host_heartbeat.restype = ctypes.c_uint64
  dll.host_complete.argtypes = [ctypes.c_uint8, ctypes.c_uint8]
  return dll


class Loop:
  def __init__(self, native, host, word):
    self.safety, self.io = native
    self.host = host
    self.word = word
    self.host.host_reset()
    self.time = 1_000_000
    self.safety.init_tests()
    self.safety.set_alternative_experience(32)
    assert self.safety.set_safety_hooks(2, word) == 0

  def rx(self, addr, data, crc=False):
    if crc:
      data = checksum(addr, data)
    assert self.safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, 0, data))

  def step(self, *, gear='drive', cruise=False, main=True, mask=1, retain=True,
           powered=True, started=True, heartbeat=True, ordinary=False, no_output=False):
    # Synthetic CAN at 5 ms, host negotiation/heartbeat at the actual 10 Hz,
    # and the production timer IRQ at 8 Hz.
    for _ in range(25):
      self.time += 5000
      self.safety.set_timer(self.time)
      if heartbeat and (self.time - 1_005_000) % 100_000 == 0:
        packed = self.host.host_heartbeat(self.word, self.safety.aol_get_request_mask(),
                                          self.safety.aol_get_permission_mask(), self.time * 1000,
                                          mask, retain, ordinary, True)
        self.io.test_usb(0xf5, packed >> 32)
        self.io.test_usb((packed >> 16) & 0xffff, packed & 0xffff)
        self.host.host_complete(self.safety.aol_get_request_mask(), self.safety.aol_get_permission_mask())
      self.rx(0xAA, bytes([0x1A, 0x6F] * 4))
      self.rx(0x260, bytes(8), True)
      self.rx(0x1D2, bytes([16 | (32 if cruise else 0)]) + bytes(7), True)
      self.rx(0x226, bytes(8))
      self.rx(0x1D3, bytes([0, 128 if main else 0]) + bytes(6), True)
      self.rx(0x620, bytes(8))
      self.rx(0x262, bytes(3) + bytes([2]) + bytes(4), True)
      self.rx(0x3BC, bytes([0, {'drive': 0, 'park': 32, 'reverse': 16}[gear]]) + bytes(6))
    if no_output:
      assert self.safety.set_safety_hooks(19, 0) == 0
    self.io.test_tick(powered, started)
    assert self.io.test_faults() == 0
    return self.io.test_siren()


@pytest.mark.parametrize('word', [73, 585, 4169, 4681], ids=['RAV4-OP', 'RAV4-stock', 'Prius-OP', 'Prius-stock'])
@pytest.mark.parametrize('gear', ['park', 'reverse'])
def test_armed_pause_shutdown_quiet(native, host, word, gear):
  loop = Loop(native, host, word)
  # Active AOL, followed by an indefinitely armed physical pause. Include stale
  # PCM active to ensure native gear denial wins over ordinary enabled state.
  loop.step(cruise=True)
  assert loop.safety.aol_get_permission_mask() & 1
  for _ in range(48):
    assert not loop.step(gear=gear, cruise=True, mask=0, ordinary=True)
    assert loop.safety.aol_get_permission_mask() == 0
  assert loop.safety.get_controls_allowed()
  assert not any(loop.step(gear=gear, mask=0, heartbeat=False, powered=False, started=False) for _ in range(24))


@pytest.mark.parametrize('word', [73, 585, 4169, 4681])
@pytest.mark.parametrize('loss', ['power', 'heartbeat', 'park', 'noOutput'])
def test_last_active_loss_still_warns(native, host, word, loss):
  loop = Loop(native, host, word)
  # First nine ticks inactive put the active boundary away from the 1 Hz sample.
  for _ in range(9):
    loop.step(gear='park', mask=0)
  loop.step(cruise=True, mask=3)
  assert loop.safety.aol_get_permission_mask() & 1
  alarms = []
  for _ in range(24 if loss == 'heartbeat' else 8):
    alarms.append(loop.step(gear='park' if loss == 'park' else 'drive', mask=0,
                            heartbeat=False, powered=loss == 'heartbeat', started=False,
                            no_output=loss == 'noOutput'))
  assert any(alarms)


@pytest.mark.parametrize('word', [73, 585, 4169, 4681])
def test_last_active_window_expires_without_disarming(native, host, word):
  loop = Loop(native, host, word)
  loop.step(cruise=True)
  assert loop.safety.aol_get_permission_mask() & 1
  for _ in range(40):
    assert not loop.step(gear='park', mask=0)
  assert not any(loop.step(gear='park', mask=0, heartbeat=False, powered=False, started=False) for _ in range(24))


@pytest.mark.parametrize('word,mask', [(73, 1), (73, 2), (73, 3), (585, 1), (4169, 2), (4681, 1)])
@pytest.mark.parametrize('paused_ticks,expected', [(0, True), (36, True), (40, False)])
def test_exact_last_authority_window(native, host, word, mask, paused_ticks, expected):
  loop = Loop(native, host, word)
  loop.step(gear='park', mask=0)
  loop.step(cruise=True, mask=mask)
  assert loop.safety.aol_get_permission_mask() == mask
  for _ in range(paused_ticks):
    assert not loop.step(gear='park', mask=0)
  alarms = [loop.step(gear='park', mask=0, heartbeat=False, powered=False, started=False) for _ in range(8)]
  assert any(alarms) == expected


@pytest.mark.parametrize('word,mask,gear', [(73, 0, 'drive'), (585, 2, 'drive'), (73, 3, 'park'), (585, 3, 'reverse')])
def test_never_active_armed_or_denied_request(native, host, word, mask, gear):
  loop = Loop(native, host, word)
  for _ in range(48):
    assert not loop.step(gear=gear, mask=mask, cruise=True, ordinary=True)
    assert loop.safety.aol_get_permission_mask() == 0
  assert not any(loop.step(gear=gear, heartbeat=False, mask=0, powered=False, started=False) for _ in range(24))


def test_no_output_stale_engaged_and_manual_siren(native, host):
  loop = Loop(native, host, 73)
  for _ in range(48):
    assert not loop.step(gear='park', ordinary=True, mask=0, no_output=True)
  assert not any(loop.step(heartbeat=False, powered=False, started=False, mask=0) for _ in range(24))
  loop.io.test_usb(0xf6, 1)
  assert any(loop.step(heartbeat=False, started=False, mask=0) for _ in range(8))


def test_ordinary_non_aol_engagement_still_warns(native, host):
  loop = Loop(native, host, 73)
  loop.safety.set_alternative_experience(0)
  assert loop.safety.set_safety_hooks(2, 73) == 0
  loop.step(cruise=True, ordinary=True, retain=False, mask=0)
  assert loop.safety.get_controls_allowed()
  assert any(loop.step(heartbeat=False, powered=False, started=False, mask=0) for _ in range(8))


@pytest.mark.parametrize('word', [73, 585, 4169, 4681])
def test_driving_heartbeat_timeout_with_normal_voltage(native, host, word):
  loop = Loop(native, host, word)
  loop.step(cruise=True, mask=3)
  assert loop.safety.aol_get_permission_mask() & 1
  alarms = [loop.step(cruise=True, heartbeat=False, powered=True, started=True) for _ in range(48)]
  assert any(alarms)
  assert loop.io.test_heartbeat_lost()
  assert loop.safety.get_current_safety_mode() == 0


@pytest.mark.parametrize('word', [73, 585, 4169, 4681])
def test_armed_pause_drive_resume_restores_warning(native, host, word):
  loop = Loop(native, host, word)
  for _ in range(48):
    assert not loop.step(gear='park', mask=0)
  loop.step(cruise=True, mask=3)
  assert loop.safety.aol_get_permission_mask() == (1 if word in (585, 4681) else 3)
  assert any(loop.step(heartbeat=False, powered=False, started=False) for _ in range(8))


@pytest.mark.parametrize('word', [73, 585])
def test_main_off_workaround_expires_last_activity(native, host, word):
  loop = Loop(native, host, word)
  loop.step(cruise=True, mask=3)
  assert loop.safety.aol_get_permission_mask() & 1
  for _ in range(40):
    assert not loop.step(main=False, cruise=False, mask=0, retain=False)
  assert not any(loop.step(main=False, heartbeat=False, powered=False, started=False) for _ in range(24))


@pytest.mark.parametrize('status', [0, 2], ids=['no-status', 'non-update-status'])
def test_tick_without_update_flag_records_fault(native, status):
  _, io = native
  ctypes.c_uint32.in_dll(io, 'timer').value = status
  io.handle_interrupt(24)  # TIM1_BRK_TIM9_IRQn in the modeled STM32F413.
  assert io.test_faults() == (1 << 1)  # FAULT_UNUSED_INTERRUPT_HANDLED
  assert ctypes.c_uint32.in_dll(io, 'heartbeat_tick_counter').value == 0


@pytest.mark.parametrize('field', ['enabled', 'priority'])
def test_nvic_divergence_records_fault(native, field):
  _, io = native

  class Nvic(ctypes.Structure):
    _fields_ = [('enabled', ctypes.c_uint32 * 8), ('priority', ctypes.c_uint8 * 240)]

  getattr(Nvic.in_dll(io, 'nvic'), field)[0] ^= 1
  io.nvic_check()
  assert io.test_faults() == (1 << 18)  # FAULT_REGISTER_DIVERGENT


def test_missing_interrupt_timer_records_fault(native):
  _, io = native
  io.interrupt_timer_check()
  assert io.test_faults() == 0  # One missed window is allowed for drift.
  io.interrupt_timer_check()
  assert io.test_faults() == (1 << 17)  # FAULT_INTERRUPT_MISSING


def test_bad_timer_period_records_fault(native):
  safety, io = native
  for tick in range(25):
    safety.set_timer(1_000_000 + (tick + 1) * 250_000)  # Half the expected clock rate.
    io.test_tick(True, True)
    if tick < 24:
      assert io.test_faults() == 0  # Two successive bad periods are required.
  assert io.test_faults() == (1 << 29)  # FAULT_TIMER
