"""Tesla scroll speed sync: one gated right-wheel scroll frame (0x3C2 on bus 1) for the Model 3/Y."""
import random
import re
import unittest
from functools import cache
from pathlib import Path

from opendbc.car.structs import CarParams
from opendbc.car.tesla.screen_button import SYNC_WORDS
from opendbc.car.tesla.speed_sync import neutral_wheel, scroll_request
from opendbc.car.tesla.values import TeslaSafetyFlags
from opendbc.safety.tests import test_tesla
from opendbc.safety.tests import test_tesla_ordinary_aol as ordinary
from opendbc.safety.tests import test_tesla_screen_button as screen
from opendbc.safety.tests.common import CANPackerSafety, make_msg
from opendbc.safety.tests.libsafety import libsafety_py

TESLA = CarParams.SafetyModel.tesla
NO_OUTPUT = CarParams.SafetyModel.noOutput
SYNC = int(TeslaSafetyFlags.SCROLL_SPEED_SYNC)
WHEEL = 0x3C2

# The bit rides only on a word with LONG_CONTROL: base, screen AOL and ordinary AOL,
# each with and without cooperative steering.
BASES = (1, 257, 513, 769, 1537, 1793, 2049, 2305, 3073, 3329)
WORDS = tuple(SYNC | base for base in BASES)
STOCK_WORDS = (0, 256, 512, 768, 1536, 1792, 2048, 2304, 3072, 3328)

# VCLEFT_switchStatus, index 1: every tilt and press neutral, no scroll on either wheel.
NEUTRAL = bytes.fromhex('2955000000000000')
# The same neutral frame with every bit the panda does not interpret set.
NEUTRAL_BUSY = bytes.fromhex('AD55C0C0F0FFFFFF')
STEPS = (1, -1, 5, -5)
TICKS = tuple(ticks & 0x3F for ticks in STEPS)

# safety_test_tesla_sync_state()
ENABLED, SEEN, DI_SEEN, DI_VALID, NEUTRAL_SEEN, BLOCKED, PENDING, DRIVE, OCCUPANT, ACTIVE, SENT = (1 << bit for bit in range(11))
READY = ENABLED | SEEN | DI_SEEN | DI_VALID | NEUTRAL_SEEN | DRIVE | OCCUPANT | ACTIVE
TOKENS = 30

CAR = {'state': 2, 'speed': 120, 'units': 0, 'autopark': 0, 'gear': 4, 'door': False, 'belt': True, 'brake': False,
       'standstill': False, 'aeb': 0, 'stock_type': 0, 'kph': 80., 'esp_delta': 0., 'speed_valid': 1, 'angle': 0., 'hands': 0,
       'eps': 1, 'eps_error': 0, 'torque': 0., 'pedal': 0.}
# What a drive can do to each of them: the driver, the stock systems and the car's own faults.
EVENTS = {'state': (0, 1, 1, 3, 4, 4, 5, 6, 7), 'speed': (0, 2, 3, 121, 300, 507, 508), 'units': (1,), 'autopark': (1, 3, 4, 9),
          'gear': (0, 1, 2, 3, 7), 'door': (True,), 'belt': (False,), 'brake': (True,), 'standstill': (True,), 'aeb': (1, 2, 3),
          'stock_type': (1, 2, 3), 'kph': (0., 5., 120.), 'esp_delta': (12.,), 'speed_valid': (0,), 'angle': (5., -10., 30.),
          'hands': (1, 2, 3), 'eps': (0, 2, 3, 5), 'eps_error': (9,), 'torque': (1., -3., 5.5, -6.), 'pedal': (20.,)}


def is_debug(lib):
  debug = lib.set_safety_hooks(CarParams.SafetyModel.allOutput, 0) == 0
  lib.set_safety_hooks(NO_OUTPUT, 0)
  return debug


@cache
def release_safety():
  default = libsafety_py.libsafety
  library = libsafety_py.ffi.dlopen(libsafety_py._build_libsafety(release=True))
  assert libsafety_py.libsafety is default
  return library


class SyncFixture:
  WORD = SYNC | 1
  EXPERIENCE = 0

  def setUp(self):
    self.lib = libsafety_py.libsafety
    self.packer = CANPackerSafety('tesla_model3_party')
    self.start(self.WORD)

  def tearDown(self):
    self.lib.set_aol_test_heartbeat(False)
    self.lib.init_tests()
    self.lib.set_alternative_experience(0)
    self.lib.set_safety_hooks(NO_OUTPUT, 0)

  def start(self, word, experience=None, ready=True, now=5_000_000):
    self.lib.init_tests()
    self.lib.set_alternative_experience(self.EXPERIENCE if experience is None else experience)
    self.now = now
    self.lib.set_timer(self.now & 0xFFFFFFFF)
    self.assertEqual(self.lib.set_safety_hooks(TESLA, word), 0, word)
    self.lib.set_aol_test_heartbeat(True)
    self.car = dict(CAR)
    self.packer.counters.clear()
    self.counter = 0
    self.template = NEUTRAL
    if ready:
      self.ready()

  def ready(self):
    """Engaged on the Tesla's own cruise with a current neutral wheel frame: a request must be admitted from here."""
    for _ in range(3):
      self.step(us=0)

  def advance(self, us):
    self.now += us
    self.lib.set_timer(self.now & 0xFFFFFFFF)

  def rx(self, name, bus, values):
    return bool(self.lib.safety_rx_hook(self.packer.make_can_msg_safety(name, bus, values)))

  def di(self, counter=None, **changes):
    """DI_state with its counter under test control. The selected speed is in half display units, as the panda keeps it."""
    self.car.update(changes)
    self.counter = (self.counter + 1) % 16 if counter is None else counter
    car = self.car
    return self.rx('DI_state', 0, {'DI_cruiseState': car['state'], 'DI_autoparkState': car['autopark'],
                                   'DI_digitalSpeed': car['speed'] * .5, 'DI_speedUnits': car['units'],
                                   'DI_locStatusCounter': self.counter})

  def feed(self, skip=(), check=True, **changes):
    """Every required message once, as the car sends them. Returns what the panda made of each."""
    self.car.update(changes)
    car = self.car
    frames = (
      ('DAS_control', 2, {'DAS_aebEvent': car['aeb']}),
      ('DAS_steeringControl', 2, {'DAS_steeringControlType': car['stock_type']}),
      ('DI_speed', 0, {'DI_vehicleSpeed': car['kph']}),
      ('ESP_B', 0, {'ESP_vehicleSpeed': car['kph'] + car['esp_delta'], 'ESP_wheelSpeedsQF': car['speed_valid'],
                    'ESP_vehicleStandstillSts': int(car['standstill'])}),
      ('EPAS3S_sysStatus', 0, {'EPAS3S_internalSAS': car['angle'], 'EPAS3S_eacStatus': car['eps'], 'EPAS3S_eacErrorCode': car['eps_error'],
                               'EPAS3S_handsOnLevel': car['hands'], 'EPAS3S_torsionBarTorque': car['torque']}),
      ('DI_systemStatus', 0, {'DI_gear': car['gear'], 'DI_accelPedalPos': car['pedal']}),
      ('ESP_status', 0, {'ESP_driverBrakeApply': 2 if car['brake'] else 1}),
      ('UI_warning', 0, {'anyDoorOpen': int(car['door']), 'buckleStatus': int(car['belt'])}),
    )
    results = [self.rx(name, bus, values) for name, bus, values in frames if name not in skip]
    if 'DI_state' not in skip:
      results.append(self.di())
    if check:
      self.assertTrue(all(results), results)
    self.lib.safety_tick()
    return results

  def wheel(self, data=None, bus=1, address=WHEEL):
    """A wheel frame from the car on the vehicle bus. The panda copies a neutral one as its template."""
    self.lib.safety_rx_hook(make_msg(bus, address, dat=self.template if data is None else bytes(data)))

  def step(self, us=20_000, skip=(), wheel=True, **changes):
    """One cycle of car traffic: the required messages, then a new neutral wheel frame."""
    self.advance(us)
    self.feed(skip, **changes)
    if wheel:
      self.wheel()

  def answer(self, ticks=1, us=20_000):
    """The Tesla moves its selected speed, which releases the outstanding request."""
    self.step(us=us, speed=self.car['speed'] + 2 * ticks)

  def frame(self, ticks=1, template=None):
    data = bytearray(self.template if template is None else template)
    data[3] = (data[3] & 0xC0) | (ticks & 0x3F)
    return bytes(data)

  def tx(self, ticks=1, data=None, bus=1):
    data = self.frame(ticks) if data is None else bytes(data)
    return bool(self.lib.safety_tx_hook(make_msg(bus, WHEEL, dat=data)))

  def hook(self, ticks=1):
    """The Tesla TX hook alone, without the shared relay and allow-list checks around it."""
    return bool(self.lib.safety_test_selected_tx(make_msg(1, WHEEL, dat=self.frame(ticks))))

  def state(self, mask=0x7FF):
    return self.lib.safety_test_tesla_sync_state() & mask

  def tokens(self):
    return self.lib.safety_test_tesla_sync_state() >> 16

  def force_controls(self):
    self.lib.set_controls_allowed(True)


class TestTeslaSpeedSyncWords(unittest.TestCase):
  def setUp(self):
    self.lib = libsafety_py.libsafety
    self.debug = is_debug(self.lib)

  def tearDown(self):
    self.lib.set_safety_hooks(NO_OUTPUT, 0)

  def test_only_the_ten_exact_words_carry_the_bit(self):
    self.assertEqual(frozenset(WORDS), SYNC_WORDS, 'the car port selects from the same ten words')
    for word in range(0x10000):
      if word & SYNC:
        valid = self.debug and word in WORDS
        self.assertEqual(self.lib.set_safety_hooks(TESLA, word), 0 if valid else -1, word)
        self.assertEqual(self.lib.get_current_safety_mode(), TESLA if valid else NO_OUTPUT, word)
        self.assertEqual(self.lib.get_current_safety_param(), word if valid else 0, word)
        if valid:
          self.assertEqual(self.lib.safety_test_tesla_sync_state(), ENABLED | (TOKENS << 16), word)

  def test_near_miss_words_select_no_output(self):
    near = [SYNC, 2 * SYNC, SYNC | 16, SYNC | 17, SYNC | 273, SYNC | 1025, SYNC | 1281, SYNC | 2561, SYNC | 3585]
    near += [SYNC | word for word in STOCK_WORDS]  # no LONG_CONTROL
    near += [word | extra for word in WORDS for extra in (2, 4, 8, 16, 32, 64, 128, 8192, 16384, 32768)]
    near += [word ^ flag for word in WORDS for flag in (1, 512, 1024, 2048) if word ^ flag not in WORDS]
    for word in near:
      self.assertNotIn(word, WORDS)
      self.assertEqual(self.lib.set_safety_hooks(TESLA, word), -1, word)
      self.assertEqual(self.lib.get_current_safety_mode(), NO_OUTPUT, word)
      self.lib.set_controls_allowed(True)
      self.assertFalse(self.lib.safety_tx_hook(make_msg(1, WHEEL, dat=NEUTRAL[:3] + b'\x01' + NEUTRAL[4:])), word)

  def test_every_base_word_is_a_long_control_word_valid_on_its_own(self):
    for base in BASES:
      self.assertTrue(base & TeslaSafetyFlags.LONG_CONTROL)
      self.assertEqual(self.lib.set_safety_hooks(TESLA, base), 0 if self.debug else (0 if base in (1, 257) else -1), base)
      self.assertFalse(self.lib.safety_test_tesla_sync_state() & ENABLED, base)


class TestTeslaSpeedSyncDisabled(SyncFixture, unittest.TestCase):
  WORD = 1

  def setUp(self):
    self.debug = is_debug(libsafety_py.libsafety)
    super().setUp()

  def scroll_frames_refused(self):
    self.force_controls()
    for ticks in STEPS:
      for bus in range(3):
        self.assertFalse(self.tx(ticks, bus=bus))
      self.assertFalse(self.hook(ticks))

  def test_without_the_bit_no_wheel_frame_is_admitted_or_tracked(self):
    words = STOCK_WORDS + (BASES if self.debug else ())
    for word in words:
      for experience in (0, 32):
        with self.subTest(word=word, experience=experience):
          self.start(word, experience)
          for template in (NEUTRAL, NEUTRAL_BUSY):
            self.template = template
            self.step()
            self.assertTrue(self.lib.get_controls_allowed())
            self.assertEqual(self.state(), 0)
            self.assertEqual(self.tokens(), TOKENS)
            self.scroll_frames_refused()
            self.assertEqual(self.state(), 0)
            self.assertEqual(self.tokens(), TOKENS)

  def test_release_firmware_refuses_the_bit_and_the_wheel_frame(self):
    release = release_safety()
    self.assertFalse(is_debug(release))
    for word in range(0x10000):
      if word & SYNC:
        self.assertEqual(release.set_safety_hooks(TESLA, word), -1, word)
        self.assertEqual(release.get_current_safety_mode(), NO_OUTPUT, word)
    native = TestTeslaSpeedSyncDisabled(methodName='test_without_the_bit_no_wheel_frame_is_admitted_or_tracked')
    native.lib = release
    native.packer = CANPackerSafety('tesla_model3_party')
    native.debug = False
    try:
      native.test_without_the_bit_no_wheel_frame_is_admitted_or_tracked()
      for word in BASES:
        self.assertEqual(release.set_safety_hooks(TESLA, word), 0 if word in (1, 257) else -1, word)
        release.set_controls_allowed(True)
        self.assertFalse(release.safety_tx_hook(make_msg(1, WHEEL, dat=native.frame())), word)
    finally:
      native.tearDown()
    self.assertIsNot(libsafety_py.libsafety, release)

  def test_other_modes_never_send_the_wheel_frame_after_a_sync_session(self):
    if not self.debug:
      self.skipTest('RELEASE firmware refuses the sync words')
    for mode, word in ((TESLA, 1), (TESLA, 0), (TESLA, 17), (CarParams.SafetyModel.teslaPreap, 0), (NO_OUTPUT, 0),
                       (CarParams.SafetyModel.silent, 0), (CarParams.SafetyModel.hondaBosch, 0)):
      with self.subTest(mode=mode, word=word):
        self.start(SYNC | 1)
        self.assertTrue(self.tx())
        self.answer()
        self.assertEqual(self.lib.set_safety_hooks(mode, word), 0)
        self.force_controls()
        for ticks in STEPS:
          for bus in range(3):
            self.assertFalse(self.tx(ticks, bus=bus))


class TestTeslaSpeedSyncChangesNothingElse(SyncFixture, unittest.TestCase):
  """Every eligible Model 3/Y carries the bit, switch on or off: apart from the wheel frame, the panda must behave as without it."""
  WORD = 1
  REQUIRED = ('DAS_control', 'DAS_steeringControl', 'DI_speed', 'ESP_B', 'EPAS3S_sysStatus', 'DI_systemStatus', 'ESP_status',
              'UI_warning', 'DI_state')
  FORWARDED = (0x488, 0x2b9, 0x27d, WHEEL, 0x3DF, 0x257)

  @classmethod
  def setUpClass(cls):
    if not is_debug(libsafety_py.libsafety):
      raise unittest.SkipTest('RELEASE firmware refuses the sync words')

  def observe(self):
    """Everything the panda decides or reports outside the speed sync state itself."""
    lib = self.lib
    return (bool(lib.get_controls_allowed()), lib.aol_get_request_mask(), lib.aol_get_permission_mask(), bool(lib.get_relay_malfunction()),
            bool(lib.get_gas_pressed_prev()), bool(lib.get_brake_pressed_prev()), bool(lib.get_regen_braking_prev()),
            bool(lib.get_steering_disengage_prev()), bool(lib.get_cruise_engaged_prev()), bool(lib.get_vehicle_moving()),
            lib.get_vehicle_speed_min(), lib.get_vehicle_speed_max(), lib.get_angle_meas_min(), lib.get_angle_meas_max(),
            lib.get_desired_angle_last(), lib.get_heartbeat_engaged_mismatches(), bool(lib.safety_config_valid()),
            lib.get_current_safety_mode(), lib.get_current_safety_param() & ~SYNC,
            tuple(lib.safety_fwd_hook(bus, address) for bus in range(3) for address in self.FORWARDED))

  def assert_same(self, rows, plain, context):
    self.assertEqual(len(rows), len(plain), context)
    for step, (row, expected) in enumerate(zip(rows, plain, strict=True)):
      self.assertEqual(row, expected, (step, context))

  def scripted_drive(self, word, experience):
    commands = CANPackerSafety('tesla_model3_party')  # its own counters: the car's frames keep theirs
    script = ({'state': 0}, {'state': 1}, 'press', {'state': 2}, {'brake': True}, {'brake': False}, {'state': 0}, {'state': 1},
              {'state': 2}, 'press', {'door': True}, {'door': False}, {'state': 1}, {'state': 2}, {'gear': 1}, {'gear': 4},
              {'aeb': 1}, {'aeb': 0}, {'state': 1}, {'stock_type': 2}, {'state': 2}, {'stock_type': 0}, {'belt': False},
              {'belt': True}, {'state': 5}, {'state': 0}, {'state': 2}, {'torque': 5.5}, {'torque': 0.}, {'state': 1}, {'state': 2},
              {'hands': 3}, {'hands': 0}, {'state': 1}, {'state': 2}, {'standstill': True}, {'state': 4})
    self.start(word, experience, ready=False)
    rows = []
    for item in script:
      if item == 'press':
        for count in (0, 3):
          self.lib.safety_rx_hook(make_msg(1, 0x3DF, dat=bytes((0, 0, 0, count, 0, 0, 0, 0))))
      else:
        self.step(**item)
      self.lib.aol_set_host_request(3)
      steering = commands.make_can_msg_safety('DAS_steeringControl', 0, {'DAS_steeringAngleRequest': 0, 'DAS_steeringControlType': 1})
      accel = commands.make_can_msg_safety('DAS_control', 0, {'DAS_accelMin': 1, 'DAS_accelMax': 1})
      rows.append((bool(self.lib.safety_tx_hook(steering)), bool(self.lib.safety_tx_hook(accel)), *self.observe()))
    return rows

  def random_drive(self, word, experience, seed, scroll, steps=300):
    """A seeded drive of faults, driver inputs and commands. With `scroll` the host also asks for wheel steps; without it
    the owner's switch is off. Returns every answer and report except the answers to those wheel requests."""
    rng = random.Random(seed)
    commands = CANPackerSafety('tesla_model3_party')
    keys = sorted(EVENTS) + 4 * ['state'] + 2 * ['stock_type', 'units']
    # Mostly neutral wheel frames; also a scroll, a tilt, the other indexes, a short frame and a missing one.
    wheels = 7 * (NEUTRAL,) + 2 * (NEUTRAL_BUSY,) + (NEUTRAL[:3] + b'\x02' + NEUTRAL[4:], b'\x31' + NEUTRAL[1:], b'\x2a' + NEUTRAL[1:],
                                                     bytes(8), NEUTRAL[:6], None)
    self.start(word, experience, ready=False)
    rows, admitted, engage = [], 0, False
    for _ in range(steps):
      roll = rng.random()
      if engage:
        self.car['state'], engage = 2, False
      elif roll < .3:
        key = rng.choice(keys)
        self.car[key] = rng.choice(EVENTS[key])
      elif roll < .5:
        self.car, engage = dict(CAR, state=1), True  # everything normal again; the driver engages on the next cycle
      self.advance(rng.choice((10_000, 20_000, 20_000, 20_000, 40_000, 120_000)))
      if rng.random() < .03:
        self.counter = (self.counter + 1) % 16  # a DI_state frame went missing
      row = self.feed((rng.choice(self.REQUIRED),) if rng.random() < .03 else (), check=False)
      if rng.random() < .3:
        row.append(bool(self.lib.safety_rx_hook(make_msg(1, 0x3DF, dat=bytes((0, 0, 0, rng.choice((0, 3, 3, 1)), 0, 0, 0, 0))))))
      wheel = rng.choice(wheels)
      if wheel is not None:
        row.append(bool(self.lib.safety_rx_hook(make_msg(1, WHEEL, dat=wheel))))
        if neutral_wheel(wheel):
          self.template = wheel
      if rng.random() < .6:
        self.lib.aol_set_host_request(rng.choice((0, 1, 2, 3, 3, 3, 7)))
      heartbeat = rng.random()
      if heartbeat < .03 or heartbeat > .7:
        self.lib.set_aol_test_heartbeat(heartbeat > .7)
      angle = self.car['angle'] + rng.choice((0., 0., .2, -.3, 2., -15.))
      low, high = rng.choice(((0., 0.), (1., 1.), (-1., 1.5), (-2., -1.), (0., 3.6), (-3.6, 0.), (-.5, 0.)))
      for name, values in (
        ('DAS_steeringControl', {'DAS_steeringAngleRequest': angle, 'DAS_steeringControlType': rng.choice((1, 1, 1, 0, 2, 3))}),
        ('DAS_control', {'DAS_setSpeed': 100, 'DAS_accState': rng.choice((4, 4, 13)), 'DAS_aebEvent': rng.choice((0, 0, 0, 0, 1)),
                         'DAS_accelMin': low, 'DAS_accelMax': high}),
        ('APS_eacMonitor', {}),
      ):
        row.append(bool(self.lib.safety_tx_hook(commands.make_can_msg_safety(name, 0, values))))
      ticks = rng.choice((1, 1, 1, -1, 5, -5, 0, 7))
      if rng.random() < .6 and scroll:
        admitted += self.tx(ticks)
      rows.append((*row, *self.observe()))
    return rows, admitted

  def test_scripted_drive_is_identical_with_and_without_the_bit(self):
    for base in BASES:
      for experience in (0, 32):
        with self.subTest(base=base, experience=experience):
          plain = self.scripted_drive(base, experience)
          self.assert_same(self.scripted_drive(SYNC | base, experience), plain, base)
          # The script is a real drive: steering and acceleration are each allowed and refused in it,
          # and a screen or ordinary AOL word with its saved opt-in grants its own permission.
          for column in (0, 1):
            self.assertEqual({row[column] for row in plain}, {False, True})
          self.assertEqual(any(row[4] for row in plain), experience == 32 and base > 257)

  def test_random_drives_are_identical_with_and_without_the_bit(self):
    # From the end of a row: the three command answers, then observe().
    columns = {'steering': -23, 'acceleration': -22, 'eac monitor': -21, 'controls': -20, 'gas': -16, 'brake': -15,
               'steering disengage': -13, 'cruise': -12, 'moving': -11, 'receive health': -4}
    reached = {name: set() for name in columns}
    for index, base in enumerate(BASES):
      for experience in (0, 32):
        with self.subTest(base=base, experience=experience):
          seed = 64 * index + experience
          plain, admitted = self.random_drive(base, experience, seed, scroll=True)
          self.assertEqual(admitted, 0, 'no wheel request without the bit')
          for scroll in (True, False):
            rows, admitted = self.random_drive(SYNC | base, experience, seed, scroll)
            self.assert_same(rows, plain, (base, experience, scroll))
            self.assertEqual(admitted > 0, scroll, 'the wheel request is the only difference')
          for name, column in columns.items():
            reached[name] |= {row[column] for row in plain}
          self.assertEqual(any(row[-18] for row in plain), experience == 32 and base > 257, 'AOL permission')
          self.assertEqual({row[-20] for row in plain}, {False, True})
    # Together the drives reach both answers of every command and report they compare.
    self.assertEqual(reached, {name: {False, True} for name in columns})

  def test_speed_sync_state_is_private_to_its_header(self):
    """Outside its header the feature is five calls in tesla.h and one in safety.h; inside, it assigns only what it
    declares and calls only helpers that change nothing. So no other Tesla decision can depend on the bit."""
    safety = Path(__file__).parents[1]
    uses = {}
    for path in sorted(safety.rglob('*.h')):
      found = re.findall(r'\b(?:tesla_sync_\w+|tesla_extended_param_valid)\b', path.read_text())
      if found and path.name != 'tesla_speed_sync.h':
        uses[path.relative_to(safety).as_posix()] = found
    self.assertEqual(uses, {'modes/tesla.h': ['tesla_sync_rx', 'tesla_sync_optional_rx', 'tesla_sync_tx', 'tesla_sync_base_param',
                                              'tesla_sync_configure'],
                            'safety.h': ['tesla_extended_param_valid']})
    # The request decision reaches only the wheel frame, and it is the hook's last word on it.
    last_word = '  if (msg->addr == 0x3C2U) {\n    tx = tesla_sync_tx(msg, tesla_stock_aeb, tesla_autopark, tesla_stock_lkas);\n  }\n\n  return tx;\n}\n'
    self.assertIn(last_word, (safety / 'modes/tesla.h').read_text())
    code = re.sub(r'//[^\n]*', '', (safety / 'modes/tesla_speed_sync.h').read_text())
    declared = set(re.findall(r'\b(?:bool|int|uint8_t|uint16_t|uint32_t)\s+(\w+)(?:\[\w+\])?\s*[=;]', code))
    assigned = set(re.findall(r'\b([A-Za-z_]\w*)(?:\[\w+\])?\s*(?:(?:[-+*/%&|^]|<<|>>)?=(?!=)|\+\+|--)', code))
    self.assertTrue(assigned)
    self.assertLessEqual(assigned, declared, 'the header assigns only what it declares')
    lasting = set(re.findall(r'\bstatic\s+(?:const\s+)?(?:unsigned\s+)?\w+\s+(\w+)(?:\[\w+\])?\s*[=;]', code))
    self.assertTrue(lasting and all(name.startswith('tesla_sync_') for name in lasting), 'what outlives a call is its own')
    called = set(re.findall(r'\b([A-Za-z_]\w*)\s*\(', code)) - {'if', 'else', 'while', 'for', 'return'}
    self.assertEqual({name for name in called if not name.startswith('tesla_sync_') and name != 'tesla_extended_param_valid'},
                     {'msg_matches', 'GET_LEN', 'GET_BIT', 'GET_FLAG', 'microsecond_timer_get', 'safety_get_ts_elapsed',
                      'aol_rx_healthy', 'tesla_screen_param_valid'})

  def test_upstream_aol_cases_hold_with_the_bit_on_every_long_word(self):
    """Upstream's own screen and ordinary AOL cases, with the bit added to every LONG_CONTROL word they select."""
    selected = set()
    for case_class in (screen.TestTeslaScreenButton, ordinary.TestTeslaOrdinaryAol):
      for name in unittest.TestLoader().getTestCaseNames(case_class):
        with self.subTest(case=case_class.__name__, test=name):
          case = case_class(methodName=name)

          def start(word, case=case):
            word |= SYNC if word & 1 else 0
            screen.TestTeslaScreenButton.start(case, word)
            self.assertEqual(bool(case.lib.safety_test_tesla_sync_state() & ENABLED), bool(word & SYNC))
            selected.add(word)
          case.start = start
          case.setUp()
          try:
            getattr(case, name)()
          finally:
            case.tearDown()
    self.assertLessEqual({SYNC | 513, SYNC | 1537, SYNC | 2049, SYNC | 3073}, selected)


class TestTeslaLongitudinalSpeedSyncSafety(test_tesla.TestTeslaLongitudinalSafety):
  """Upstream's complete Tesla longitudinal suite with the bit set and no wheel request made: nothing in it may differ."""
  SAFETY_PARAM = TeslaSafetyFlags.LONG_CONTROL | TeslaSafetyFlags.SCROLL_SPEED_SYNC

  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    if not is_debug(libsafety_py.libsafety):
      raise unittest.SkipTest('RELEASE firmware refuses the sync words')

  def setUp(self):
    super().setUp()
    self.assertTrue(self.safety.safety_test_tesla_sync_state() & ENABLED)


class TestTeslaSpeedSync(SyncFixture, unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    if not is_debug(libsafety_py.libsafety):
      raise unittest.SkipTest('RELEASE firmware refuses the sync words')

  def blocked_until(self, fault, clear):
    """One fault refuses the request and spends nothing; clearing it admits the same request."""
    self.start(self.WORD)
    self.step()
    fault()
    self.assertFalse(self.tx())
    self.assertFalse(self.hook())
    self.assertFalse(self.state(PENDING | SENT))
    self.assertEqual(self.tokens(), TOKENS)
    clear()
    self.assertTrue(self.tx())

  # ***** admitted requests *****

  def test_ready_state_admits_one_request_and_records_it(self):
    self.assertEqual(self.state(), READY)
    self.assertEqual(self.tokens(), TOKENS)
    self.assertTrue(self.tx())
    self.assertEqual(self.state(), READY | PENDING | SENT)
    self.assertEqual(self.tokens(), TOKENS - 1)

  def test_every_word_admits_the_four_steps_the_sender_builds(self):
    for word in WORDS:
      for experience in (0, 32):
        for template in (NEUTRAL, NEUTRAL_BUSY):
          with self.subTest(word=word, experience=experience, template=template.hex()):
            self.start(word, experience)
            self.template = template
            for ticks in STEPS:
              self.step()
              address, data, bus = scroll_request(template, ticks)
              self.assertEqual((address, bus, data), (WHEEL, 1, self.frame(ticks)))
              self.assertTrue(self.lib.safety_tx_hook(make_msg(bus, address, dat=data)))
              self.answer(ticks)
            self.assertEqual(self.tokens(), TOKENS - len(STEPS))

  def test_override_state_and_both_units_are_admitted(self):
    for changes in ({'state': 4}, {'units': 1}, {'state': 4, 'units': 1}):
      with self.subTest(changes=changes):
        self.start(self.WORD)
        self.step(**changes)
        self.assertEqual(self.state(), READY)
        self.assertTrue(self.tx())

  def test_first_request_is_admitted_with_a_template_stamped_zero(self):
    self.start(self.WORD, now=0)
    self.assertEqual(self.state(), READY)
    self.assertTrue(self.tx())
    self.step(us=0, speed=self.car['speed'] + 2)
    self.assertFalse(self.state(PENDING))
    self.assertFalse(self.tx(), 'the same wheel frame cannot carry a second request')

  # ***** frame shape *****

  def test_frame_must_copy_the_template_with_one_legal_scroll_count(self):
    for template in (NEUTRAL, NEUTRAL_BUSY):
      with self.subTest(template=template.hex()):
        self.start(self.WORD)
        self.template = template
        for ticks in range(64):
          self.step(us=5_000)
          self.assertEqual(self.tx(ticks), ticks in TICKS, ticks)
          if ticks in TICKS:
            self.answer(us=5_000)
        self.assertEqual(self.tokens(), TOKENS - len(TICKS))
        self.step()
        for bit in range(64):
          if bit not in range(24, 30):
            data = bytearray(self.frame())
            data[bit // 8] ^= 1 << (bit % 8)
            self.assertFalse(self.tx(data=data), bit)
        for bus in (0, 2, 3):
          self.assertFalse(self.tx(bus=bus), bus)
        for length in range(8):
          self.assertFalse(self.tx(data=self.frame()[:length]), length)
        self.assertFalse(self.tx(data=self.frame() + bytes(4)))
        self.assertFalse(self.tx(data=self.template), 'a neutral frame is not a request')
        self.assertEqual(self.tokens(), TOKENS - len(TICKS))
        self.assertFalse(self.state(PENDING))
        self.assertTrue(self.tx())

  def test_frame_built_from_an_older_template_is_refused(self):
    self.template = NEUTRAL_BUSY
    self.step()
    self.assertFalse(self.tx(data=self.frame(template=NEUTRAL)))
    self.assertTrue(self.tx())

  # ***** engagement and vehicle state *****

  def test_engagement_gates(self):
    gates = {
      'controls': (lambda: self.lib.set_controls_allowed(False), self.force_controls),
      'heartbeat': (lambda: self.lib.set_aol_test_heartbeat(False), lambda: self.lib.set_aol_test_heartbeat(True)),
      'relay': (lambda: self.lib.set_relay_malfunction(True), lambda: self.lib.set_relay_malfunction(False)),
      'standstill': (lambda: self.step(standstill=True), lambda: self.step(standstill=False)),
      'stock AEB': (lambda: self.step(aeb=1), lambda: self.step(aeb=0)),
      'door': (lambda: self.step(door=True), lambda: self.step(door=False)),
      'belt': (lambda: self.step(belt=False), lambda: self.step(belt=True)),
    }
    for gear in (0, 1, 2, 3, 5, 6, 7):
      gates[f'gear {gear}'] = (lambda gear=gear: self.step(gear=gear), lambda: self.step(gear=4))
    for name, (fault, clear) in gates.items():
      with self.subTest(gate=name):
        self.blocked_until(fault, clear)
        self.assertTrue(self.lib.get_controls_allowed())

  def test_brake_blocks_even_with_controls_forced(self):
    def brake():
      self.step(brake=True)
      self.assertFalse(self.lib.get_controls_allowed())
      self.force_controls()

    def release():
      self.step(brake=False)
      self.force_controls()
    self.blocked_until(brake, release)

  def test_required_message_health_is_part_of_every_request(self):
    def stale():
      # EPAS3S_sysStatus is a 100 Hz source: older than 100 ms is unhealthy long before it is lagging.
      self.step(us=60_000, skip=('EPAS3S_sysStatus',))
      self.step(us=40_001, skip=('EPAS3S_sysStatus',))
      self.assertTrue(self.lib.get_controls_allowed())
    self.blocked_until(stale, self.step)

  def test_stock_lkas_blocks_while_engaged(self):
    def stock_lkas():
      self.step(state=1)
      self.step(stock_type=2)  # rising edge while not engaged latches stock LKAS
      self.step(state=2)
      self.assertTrue(self.lib.get_controls_allowed())
    self.blocked_until(stock_lkas, lambda: self.step(stock_type=0))

  def test_autopark_blocks_even_with_controls_forced(self):
    def autopark():
      self.step(state=1)
      self.step(autopark=3)
      self.step(state=2)
      self.assertFalse(self.lib.get_controls_allowed())
      self.assertTrue(self.state(ACTIVE))
      self.force_controls()
    self.blocked_until(autopark, lambda: self.step(autopark=0))

  def test_only_enabled_and_override_cruise_states_are_active(self):
    for state in (0, 1, 3, 5, 6, 7):
      with self.subTest(state=state):
        def inactive(state=state):
          self.step(state=state)
          self.assertFalse(self.state(ACTIVE))
          self.force_controls()
        self.blocked_until(inactive, lambda: self.step(state=2))

  def test_selected_speed_bounds(self):
    for refused, admitted in ((2, 3), (508, 507), (0, 4), (511, 506)):
      with self.subTest(refused=refused, admitted=admitted):
        self.blocked_until(lambda refused=refused: self.step(speed=refused), lambda admitted=admitted: self.step(speed=admitted))

  # ***** DI_state tracking *****

  def test_counter_gap_withholds_the_frame_and_the_request(self):
    for gap in (0, 2, 15):
      with self.subTest(gap=gap):
        def skipped(gap=gap):
          self.advance(20_000)
          self.assertTrue(self.di(counter=(self.counter + gap) % 16, speed=2, state=1, units=1))
          self.assertEqual(self.state(), READY & ~DI_VALID, 'a frame out of sequence updates nothing but the counter')
          self.force_controls()  # the shared cruise check has no such counter rule
        self.blocked_until(skipped, lambda: self.step(speed=120, state=2, units=0))
        self.assertEqual(self.state(), READY | PENDING | SENT)

  def test_out_of_sequence_frame_cannot_release_or_block_a_request(self):
    self.assertTrue(self.tx())
    self.advance(20_000)
    self.assertTrue(self.di(counter=(self.counter + 5) % 16, speed=130, units=1))
    self.assertEqual(self.state(), (READY & ~DI_VALID) | PENDING | SENT)
    self.assertTrue(self.di(speed=120, units=0))
    self.assertEqual(self.state(), READY | PENDING | SENT)

  def test_first_frame_of_a_session_is_taken_whatever_its_counter(self):
    self.step()
    self.assertEqual(self.counter, 4)
    self.assertEqual(self.lib.set_safety_hooks(TESLA, self.WORD), 0)
    self.lib.set_aol_test_heartbeat(True)
    self.assertEqual(self.state(), ENABLED)
    self.advance(20_000)
    self.feed(skip=('DI_state',))
    self.assertTrue(self.di(counter=9))
    self.lib.safety_tick()
    self.wheel()
    self.assertEqual(self.state(), READY)
    self.assertTrue(self.tx())

  def test_gear_and_occupant_tracking(self):
    for gear in range(8):
      self.step(gear=gear)
      self.assertEqual(bool(self.state(DRIVE)), gear == 4, gear)
    for door in (False, True):
      for belt in (False, True):
        self.step(door=door, belt=belt)
        self.assertEqual(bool(self.state(OCCUPANT)), belt and not door, (door, belt))
    for state in range(8):
      self.step(state=state)
      self.assertEqual(bool(self.state(ACTIVE)), state in (2, 4), state)

  # ***** wheel frame tracking *****

  def test_wheel_frame_is_needed_before_any_request(self):
    self.start(self.WORD, ready=False)
    for _ in range(3):
      self.step(wheel=False)
    self.assertEqual(self.state(), READY & ~(SEEN | NEUTRAL_SEEN))
    self.assertFalse(self.tx())
    for bus in (0, 2):
      self.wheel(bus=bus)
    for address in (0x3C1, 0x3C3, 0x3DF):
      self.wheel(address=address)
    self.assertEqual(self.state(), READY & ~(SEEN | NEUTRAL_SEEN))
    self.assertFalse(self.tx())
    self.wheel()
    self.assertEqual(self.state(), READY)
    self.assertTrue(self.tx())

  def test_malformed_and_other_index_wheel_frames(self):
    for length in (*range(8), 12, 16):
      with self.subTest(length=length):
        self.blocked_until(lambda length=length: self.wheel(data=(NEUTRAL + bytes(8))[:length]), self.wheel)
    for index in (2, 3):
      with self.subTest(index=index):
        def other(index=index):
          self.wheel(data=bytes((NEUTRAL[0] & 0xFC | index,)) + NEUTRAL[1:])
          self.assertEqual(self.state(), READY & ~(SEEN | NEUTRAL_SEEN))
        self.blocked_until(other, self.wheel)

  def test_index_zero_frame_is_ignored(self):
    self.wheel(data=bytes(8))
    self.wheel(data=bytes((0xFC,)) + bytes(7 * (0xFF,)))
    self.assertEqual(self.state(), READY)
    self.assertTrue(self.tx())
    # It is not a wheel frame: it neither refreshes the template nor counts as a newer one.
    self.advance(20_000)
    self.feed(speed=self.car['speed'] + 2)
    self.wheel(data=bytes(8))
    self.assertFalse(self.state(PENDING))
    self.assertFalse(self.tx())
    self.wheel()
    self.assertTrue(self.tx())

  def test_any_driver_input_on_the_wheel_refuses_until_neutral_again(self):
    checked = (*range(3, 7), *range(8, 22), *range(24, 30), *range(32, 36))
    for bit in range(2, 64):
      with self.subTest(bit=bit):
        self.start(self.WORD)
        self.step()
        data = bytearray(NEUTRAL)
        data[bit // 8] ^= 1 << (bit % 8)
        self.wheel(data=data)
        self.assertEqual(bool(self.state(NEUTRAL_SEEN)), bit not in checked)
        self.assertTrue(self.state(SEEN))
        if bit in checked:
          self.assertFalse(self.tx())
          self.assertFalse(self.tx(data=self.frame(template=data)))
          self.wheel()
          self.assertTrue(self.tx())
        else:
          self.assertFalse(self.tx(), 'the newest neutral frame is the template')
          self.assertTrue(self.tx(data=self.frame(template=data)))

  def test_neutral_matches_the_sender_for_every_two_bit_deviation(self):
    for first in range(2, 64):
      for second in range(first, 64):
        data = bytearray(NEUTRAL)
        data[first // 8] ^= 1 << (first % 8)
        if second != first:
          data[second // 8] ^= 1 << (second % 8)
        self.wheel(data=data)
        self.assertEqual(bool(self.state(NEUTRAL_SEEN)), neutral_wheel(bytes(data)), data.hex())
        self.assertEqual(scroll_request(bytes(data), 1) is not None, neutral_wheel(bytes(data)))

  # ***** freshness *****

  def test_wheel_frame_is_current_for_250ms(self):
    for age, admitted in ((250_000, True), (250_001, False)):
      with self.subTest(age=age):
        self.start(self.WORD)
        self.step()
        self.step(us=200_000, wheel=False)
        self.advance(age - 200_000)
        self.assertEqual(self.tx(), admitted)
        if not admitted:
          self.assertEqual(self.state(), READY)
          self.wheel()
          self.assertTrue(self.tx())

  def test_selected_speed_is_current_for_250ms(self):
    for age, admitted in ((250_000, True), (250_001, False)):
      with self.subTest(age=age):
        self.start(self.WORD)
        self.step()
        self.step(us=200_000, skip=('DI_state',))
        self.advance(age - 200_000)
        self.assertEqual(self.tx(), admitted)
        if not admitted:
          self.assertEqual(self.state(), READY)
          self.assertTrue(self.di())
          self.assertTrue(self.tx())

  # ***** one request at a time *****

  def test_request_stays_open_until_the_selected_speed_moves(self):
    self.assertTrue(self.tx())
    for _ in range(5):
      self.step()
      self.assertTrue(self.state(PENDING))
      self.assertFalse(self.tx())
    self.assertEqual(self.tokens(), TOKENS - 1)
    self.answer(-1)
    self.assertFalse(self.state(PENDING))
    self.assertTrue(self.tx())
    self.assertEqual(self.tokens(), TOKENS - 2)

  def test_unanswered_request_is_released_after_800ms(self):
    self.assertTrue(self.tx())
    for _ in range(8):
      self.step(us=100_000)
    self.assertFalse(self.tx(), 'still open at exactly 800 ms')
    self.assertTrue(self.state(PENDING))
    self.advance(1)
    self.assertTrue(self.tx())
    self.assertEqual(self.tokens(), TOKENS - 2)

  def test_each_request_needs_a_newer_wheel_frame(self):
    self.assertTrue(self.tx())
    self.advance(20_000)
    self.feed(speed=self.car['speed'] + 2)
    self.assertEqual(self.state(), READY | SENT)
    self.assertFalse(self.tx())
    self.advance(1)
    self.wheel()
    self.assertTrue(self.tx())

  def test_cruise_off_or_standby_closes_the_request(self):
    for state in (0, 1):
      with self.subTest(state=state):
        self.start(self.WORD)
        self.assertTrue(self.tx())
        self.step(state=state)
        self.assertEqual(self.state(), READY & ~ACTIVE)
        self.step(state=2)
        self.assertTrue(self.tx())

  def test_units_change_during_a_request_blocks_until_cruise_is_off(self):
    for release in (0, 1):
      for moved in (0, 2):
        with self.subTest(release=release, moved=moved):
          self.start(self.WORD)
          self.assertTrue(self.tx())
          self.step(units=1, speed=self.car['speed'] + moved)
          self.assertEqual(self.state(), READY | BLOCKED | SENT | (0 if moved else PENDING))
          self.answer()
          for state in (2, 3, 4, 6, 7, 5):
            self.step(state=state)
            self.assertTrue(self.state(BLOCKED), state)
          self.step(state=2)
          self.force_controls()
          self.assertFalse(self.tx())
          self.assertEqual(self.tokens(), TOKENS - 1)
          self.step(state=release)
          self.assertFalse(self.state(BLOCKED | PENDING | SENT))
          self.step(state=2)
          self.assertTrue(self.tx())

  def test_units_change_with_no_request_open_does_not_block(self):
    self.assertTrue(self.tx())
    self.answer()
    self.step(units=1)
    self.assertEqual(self.state(), READY | SENT)
    self.assertTrue(self.tx())
    self.step(units=1, speed=self.car['speed'] + 2)
    self.assertEqual(self.state(), READY | SENT)

  # ***** rate limit *****

  def burst(self, count):
    for sent in range(count):
      self.assertTrue(self.tx(), sent)
      self.answer(1 if sent % 2 else -1, us=5_000)

  def test_thirty_requests_then_one_per_second(self):
    self.step(us=10_000_000)  # an idle start earns nothing above the full bucket
    start = self.now
    self.burst(TOKENS)
    self.assertEqual(self.tokens(), 0)
    self.assertFalse(self.tx())
    for second in (1, 2, 3):
      self.step(us=start + second * 1_000_000 - 1 - self.now, speed=self.car['speed'] + 2)
      self.assertFalse(self.tx(), 'one microsecond early')
      self.assertEqual(self.tokens(), 0)
      self.advance(1)
      self.assertTrue(self.tx())
      self.assertEqual(self.tokens(), 0)
    # Five idle seconds earn five requests, not a new burst.
    self.step(us=start + 8_000_000 - self.now, speed=self.car['speed'] + 2)
    self.burst(5)
    self.assertFalse(self.tx())
    self.assertEqual(self.tokens(), 0)
    # However long the pause, the bucket never holds more than thirty.
    self.step(us=600_000_000)
    self.burst(TOKENS)
    self.assertFalse(self.tx())
    self.assertEqual(self.tokens(), 0)

  def test_refused_attempts_do_not_earn_or_spend_tokens(self):
    self.burst(3)
    spent = self.now
    for _ in range(40):
      self.step(us=20_000)
      self.assertFalse(self.tx(0))
      self.assertFalse(self.tx(bus=0))
    self.assertLess(self.now - spent, 1_000_000)
    self.assertEqual(self.tokens(), TOKENS - 3)
    self.assertFalse(self.state(PENDING))

  def test_timer_wraparound_keeps_every_window(self):
    self.start(self.WORD, now=(1 << 32) - 300_000)
    self.assertTrue(self.tx())
    for _ in range(8):
      self.step(us=100_000)
    self.assertGreater(self.now, 1 << 32)
    self.assertFalse(self.tx())
    self.advance(1)
    self.assertTrue(self.tx())
    self.answer()
    self.step(us=200_000, wheel=False)
    self.advance(50_001)
    self.assertFalse(self.tx())
    self.wheel()
    self.assertTrue(self.tx())
    self.assertEqual(self.tokens(), TOKENS - 2, 'three sent, one earned back a second after the first')

  # ***** resets *****

  def test_reselecting_the_mode_starts_cold(self):
    self.assertTrue(self.tx())
    self.step(units=1)
    self.assertEqual(self.state(), READY | PENDING | BLOCKED | SENT)
    self.assertEqual(self.tokens(), TOKENS - 1)
    used = self.frame()
    for word in (self.WORD, SYNC | 3329, 1, self.WORD):
      with self.subTest(word=word):
        self.assertEqual(self.lib.set_safety_hooks(TESLA, word), 0)
        self.assertEqual(self.state(), ENABLED if word & SYNC else 0)
        self.assertEqual(self.tokens(), TOKENS)
        self.lib.set_aol_test_heartbeat(True)
        self.force_controls()
        self.assertFalse(self.tx(data=used), 'nothing from the last session may be sent')
        self.assertFalse(self.hook())
    # A new session needs its own traffic: the required messages, then a wheel frame.
    self.advance(20_000)
    self.feed(units=0)
    self.assertEqual(self.state(), READY & ~(SEEN | NEUTRAL_SEEN))
    self.assertFalse(self.tx(data=used))
    self.wheel()
    self.assertTrue(self.tx())
    self.assertEqual(self.tokens(), TOKENS - 1)

  def test_wheel_frame_is_never_forwarded_or_a_relay_fault(self):
    self.lib.init_tests()  # counts the mode as settled, so relay checks are live
    self.lib.set_timer(self.now & 0xFFFFFFFF)
    for _ in range(5):
      self.step()
    self.assertTrue(self.tx())
    self.assertFalse(self.lib.get_relay_malfunction())
    self.assertEqual([self.lib.safety_fwd_hook(bus, WHEEL) for bus in range(3)], [2, -1, 0])


class TestTeslaSpeedSyncScreenAol(TestTeslaSpeedSync):
  """The same request rules on a screen AOL word with its opt-in saved, as most cars in the field will run."""
  WORD = SYNC | 1793
  EXPERIENCE = 32


class TestTeslaSpeedSyncOrdinaryAol(TestTeslaSpeedSync):
  """The same request rules on an ordinary AOL word with its opt-in saved."""
  WORD = SYNC | 3329
  EXPERIENCE = 32


if __name__ == "__main__":
  unittest.main()
