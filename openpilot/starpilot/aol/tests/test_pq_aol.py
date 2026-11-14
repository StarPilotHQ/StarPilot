import pytest
from opendbc.car import structs, gen_empty_fingerprint
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from opendbc.car.volkswagen.pq_aol import qualified
from openpilot.starpilot.car.volkswagen.aol import policy_for, VolkswagenStockCardIntent, native_accepts_cp
from openpilot.starpilot.aol.intent import AolSettings
from openpilot.starpilot.aol.vehicle import native_matches_cp
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.can import CANPacker
from opendbc.safety.tests.common import make_msg



def cp_for(alpha=False, release=False, offset=0):
  fp = gen_empty_fingerprint()
  for b in range(8):
    fp.setdefault(b, {})
  fp[offset][0x440] = 8
  fp[offset + 1][0x1A0] = 8
  return CarInterface.get_params(CAR.VOLKSWAGEN_SHARAN_MK2, fp, [], alpha, release, False)


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('offset', (0, 4))
def test_real_factory_and_marked_admission(alpha, offset):
  cp = cp_for(alpha, offset=offset)
  assert qualified(cp)
  p = policy_for(cp)
  assert p.intent_supported and p.runtime_supported
  cp.alternativeExperience |= p.alternative_experience_addition
  cp.safetyConfigs[-1].safetyParam |= p.safety_param_addition
  assert cp.safetyConfigs[-1].safetyParam == 8 | int(alpha)
  assert qualified(cp, marked_only=True)
  assert native_matches_cp(cp, 21, 8 | int(alpha))
  assert not native_matches_cp(cp, 15, 8 | int(alpha))
  assert cp.openpilotLongitudinalControl == alpha
  assert cp.pcmCruise != alpha
  assert not qualified(cp_for(alpha, release=True))
  for field, value in [
    ('minSteerSpeed', 0),
    ('dashcamOnly', True),
    ('passive', True),
    ('notCar', True),
    ('networkLocation', structs.CarParams.NetworkLocation.fwdCamera),
    ('transmissionType', structs.CarParams.TransmissionType.manual),
    ('flags', 1 << 20),
    ('alternativeExperience', 33),
    ('carFingerprint', CAR.VOLKSWAGEN_GOLF_MK7),
  ]:
    bad = structs.CarParams(**cp.to_dict())
    setattr(bad, field, value)
    assert not qualified(bad), (field, value)


@pytest.mark.parametrize('enabled', (False, True))
def test_host_default_main_waits_for_floor_and_respects_off(enabled):
  cp = cp_for()
  cp.alternativeExperience = 32
  cp.safetyConfigs[-1].safetyParam = 8
  settings = AolSettings(enabled, 5.0, 0, 0, (0, 0, 0), (0, 0, 0))
  owner = VolkswagenStockCardIntent(cp, settings)
  cs = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive)
  cs.cruiseState.available = True
  for t in range(30):
    owner.update(cs, now_ns=1_000_000_000 + t * 10_000_000, fault_active=False)
  assert not owner.allowed_latch
  cs.vEgo = 20.0
  cs.standstill = False
  owner.update(cs, now_ns=1_400_000_000, fault_active=False)
  assert owner.allowed_latch == enabled


class Native:
  def __init__(self, alpha=False, experience=32, param=None):
    self.s = libsafety_py.libsafety
    self.packer = CANPacker('vw_pq')
    self.tick = 0
    self.alpha = alpha
    self.s.init_tests()
    self.s.set_alternative_experience(experience)
    assert self.s.set_safety_hooks(21, 8 | int(alpha) if param is None else param) == 0
    self.s.set_aol_test_heartbeat(True)

  def msg(self, name, fields=None, bus=0):
    a, d, b = self.packer.make_can_msg(name, bus, fields or {})
    return make_msg(b, a, dat=d)

  def feed(self, *, speed=72, gear=5, eps=3, main=True, brake=False, set_button=False, cancel=False, cruise=False, skip=(), request=3):
    self.tick += 1
    self.s.set_timer(1_000_000 + self.tick * 10_000)
    rows = [
      ('Getriebe_1', {'GE1_Wahl_Pos': gear, 'COUNTER': self.tick % 16}),
      ('Lenkhilfe_2', {'LH2_Sta_HCA': eps, 'COUNTER': self.tick % 16}),
      ('Lenkhilfe_3', {'COUNTER': self.tick % 16}),
      ('Bremse_1', {'BR1_Rad_kmh': speed}),
      ('Motor_2', {'MO2_Sta_GRA': int(cruise), 'MO2_BLS': int(brake)}),
      ('Motor_3', {}),
      ('Motor_5', {'MO5_GRA_Hauptsch': int(main)}),
      ('GRA_Neu', {'GRA_Hauptschalt': int(main), 'GRA_Neu_Setzen': int(set_button), 'GRA_Abbrechen': int(cancel), 'COUNTER': self.tick % 16}),
    ]
    for n, f in rows:
      if n not in skip:
        assert self.s.safety_rx_hook(self.msg(n, f)), n
    self.s.safety_tick()
    self.s.aol_set_host_request(request)
    return self.s.aol_get_permission_mask()

  def prime(self, **kw):
    for _ in range(15):
      p = self.feed(**kw)
    return p

  def steer(self, torque=4, active=True):
    return self.msg('HCA_1', {'HCA_Status': 7 if active else 3, 'LM_Offset': abs(torque), 'LM_OffSign': int(torque < 0), 'Vib_Freq': 16})


@pytest.mark.parametrize('alpha', (False, True))
def test_native_permission_limits_brake_cancel_and_expiry(alpha):
  n = Native(alpha)
  s = n.s
  assert n.prime() == 1
  assert not s.get_controls_allowed()
  assert s.safety_tx_hook(n.steer())
  assert not s.safety_tx_hook(n.steer(301))
  # A denied command must release ownership; only accepted current nonzero output may block stock.
  assert s.safety_fwd_hook(2, 0xD2) == 0
  n.feed()
  assert s.safety_tx_hook(n.steer())
  assert s.safety_fwd_hook(2, 0xD2) == -1
  assert s.safety_fwd_hook(2, 0x5BE) == 0
  assert s.safety_tx_hook(n.msg('LDW_Status'))
  assert s.safety_fwd_hook(2, 0x5BE) == -1
  assert n.feed(speed=49.99) == 0
  assert s.safety_fwd_hook(2, 0xD2) == 0
  assert n.feed(speed=50) == 1
  if alpha:
    n.feed(set_button=True)
    assert n.feed() == 3
    assert n.feed(cancel=True) == 1
    assert not s.get_controls_allowed()
    assert n.feed() == 1
    n.feed(set_button=True)
    assert n.feed() == 3
    assert n.feed(brake=True) == 1
    assert n.feed() == 1
  assert n.feed(gear=7) == 0
  assert n.feed() == 1
  assert n.feed(eps=4) == 0
  assert n.feed() == 1
  assert n.feed(main=False) == 0
  assert n.feed() == 1
  s.set_timer(1_000_000 + n.tick * 10_000 + 200_001)
  assert s.aol_get_permission_mask() == 0
  assert s.safety_fwd_hook(2, 0xD2) == 0
  assert n.feed() == 0  # expired intent cannot silently re-arm


@pytest.mark.parametrize('source', ('Getriebe_1', 'Lenkhilfe_2', 'Motor_5', 'Bremse_1'))
def test_native_stale_physical_source_revokes(source):
  n = Native()
  assert n.prime() == 1
  for _ in range(32):
    p = n.feed(skip=(source,))
  assert p == 0


@pytest.mark.parametrize('param,experience', [(8, 0), (9, 0), (10, 32), (24, 32), (0, 32), (1, 32)])
def test_unknown_profile_cannot_transmit(param, experience):
  n = Native(experience=experience, param=param)
  n.prime()
  assert n.s.aol_get_permission_mask() == 0
  assert not n.s.safety_tx_hook(n.steer())


@pytest.mark.parametrize('source', ('Lenkhilfe_2', 'Getriebe_1'))
def test_duplicate_optional_counter_revokes(source):
  n = Native()
  assert n.prime() == 1
  fields = {'COUNTER': n.tick % 16}
  fields.update({'LH2_Sta_HCA': 3} if source == 'Lenkhilfe_2' else {'GE1_Wahl_Pos': 5})
  n.s.safety_rx_hook(n.msg(source, fields))
  assert n.s.aol_get_permission_mask() == 0


def test_native_eps_checksum_and_hardware_denial():
  n = Native()
  n.prime()
  m = n.msg('Lenkhilfe_2', {'LH2_Sta_HCA': 3, 'COUNTER': (n.tick + 1) % 16})
  m[0].data[0] ^= 1
  n.s.safety_rx_hook(m)
  assert n.s.aol_get_permission_mask() == 0
  n = Native()
  n.prime()
  n.s.set_aol_test_heartbeat(False)
  assert n.s.aol_get_permission_mask() == 0
  n = Native()
  n.prime()
  n.s.set_relay_malfunction(True)
  assert n.s.aol_get_permission_mask() == 0


@pytest.mark.parametrize('cancel,expire', ((False, False), (True, False), (False, True)))
def test_pending_set_requires_new_request_and_cancel_or_expiry_revokes(cancel, expire):
  n = Native(True)
  n.s.set_aol_test_heartbeat(False)
  assert n.prime() == 0
  n.feed(set_button=True)
  assert n.feed() == 0
  if cancel:
    assert n.feed(cancel=True) == 0
  if expire:
    for _ in range(31):
      assert n.feed() == 0
  n.s.set_aol_test_heartbeat(True)
  assert n.feed() == (0 if cancel or expire else 3)


def test_shared_marker_word_cannot_cross_native_models():
  fp = gen_empty_fingerprint()
  fp[0][0xAD] = 8
  fp[1][0xFD] = 8
  cp = CarInterface.get_params(CAR.VOLKSWAGEN_GOLF_MK7, fp, [], False, False, False)
  cp.alternativeExperience = 32
  cp.safetyConfigs[-1].safetyParam = 8
  assert native_accepts_cp(cp, 15, 8)
  assert not native_accepts_cp(cp, 21, 8)
  assert not native_accepts_cp(cp, 34, 8)


@pytest.mark.parametrize('cancel', (False, True))
def test_set_below_steering_floor_needs_no_second_set_when_speed_rises(cancel):
  n = Native(True)
  assert n.prime(speed=0) == 0
  n.feed(speed=0, set_button=True)
  assert n.feed(speed=0) == 2
  assert not n.s.safety_tx_hook(n.steer())
  if cancel:
    assert n.feed(speed=0, cancel=True) == 0
  # Cancel withdraws LONG from the joined session; eligible AOL retains its intent.
  assert n.feed(speed=72) == (1 if cancel else 3)


def test_brake_held_standstill_set_waits_for_release_and_new_brake_withdraws_long():
  n = Native(True)
  assert n.prime(speed=0, brake=True) == 0
  n.feed(speed=0, brake=True, set_button=True)
  assert n.feed(speed=0, brake=True) == 0
  for _ in range(5):
    assert n.feed(speed=0, brake=True) == 0
  assert n.feed(speed=0) == 2
  assert n.feed(speed=0, brake=True) == 0
  assert n.feed(speed=0) == 0
