"""Full production callers for every distinct parent parser/controller topology."""
import pytest
from opendbc.car.toyota import dsu_bypass, pedal
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_pedal_parent_configurations import sources
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope
PROFILES = (
  ('TOYOTA_RAV4H', '2FF8', 'catalog'),
  ('TOYOTA_CAMRY', '2FF8', 'catalog'),
  ('LEXUS_GS_F', '2FF8', 'catalog'),
  ('TOYOTA_PRIUS', '2FF8', 'catalog'),
  ('TOYOTA_RAV4_TSS2', '2FF4', 'catalog'),
  ('TOYOTA_COROLLA', '2FF4', 'catalog'),
  ('TOYOTA_RAV4_TSS2_2022', '2AA8', 'catalog'),
  ('TOYOTA_RAV4_TSS2', '2AA8', 'catalog'),
  ('TOYOTA_PRIUS', '2FF8', 'EPS73_8965B47070'),
  ('TOYOTA_PRIUS_RETROFIT', '2FF4', 'EPS73_8965B47070'),
  ('TOYOTA_CAMRY', 'camera343_or_4CB8', 'catalog'),
  ('LEXUS_GS_F', 'camera343_or_4CB8', 'catalog'),
  ('TOYOTA_RAV4_TSS2_2023', '2FF8', 'catalog'),
  ('TOYOTA_RAV4_TSS2_2023', '2AA8', 'catalog'),
)


@pytest.mark.parametrize('name,topology,rack', PROFILES)
@pytest.mark.parametrize('stock', (False, True))
def test_nominal_parent_pedal_full_callers(name, topology, rack, stock, host_heartbeat_bridge):
  run(name, topology, rack, stock, host_heartbeat_bridge)


def run(name, topology, rack, stock, bridge, kind='nominal', *, mirror=False):
  identity = getattr(CAR, name)
  fp, fw = sources(identity, topology, rack)
  fp[0][0x201] = 6
  if mirror:
    fp[2][0x201] = 6
  cp = CarInterface.get_params(identity, fp, fw, True, False, False)
  assert pedal.configured(cp) and cp.openpilotLongitudinalControl
  long_word = cp.safetyConfigs[0].safetyParam
  tick = 0
  pedal_sent = False

  def physical(packer, **fields):
    nonlocal tick
    tick += 1
    if kind == 'hold' and 260 <= tick < 430:
      fields.update(stopped=True, brake=tick < 400, cruise=False)
    if kind == 'hold' and 430 <= tick < 445:
      fields.update(gas=True, cruise=False)
    parent_drop = kind == 'parent_drop' and 260 <= tick < 311
    pedal_drop = kind == 'pedal_drop' and 260 <= tick < 311
    if topology != '2FF8' or parent_drop:
      fields['sdsu'] = None
    frames = helpers.physical(packer, **fields)
    if identity in dsu_bypass.ALT_MAIN_CARS:
      frames += [packer.make_can_msg('DSU_CRUISE', 0, {'MAIN_ON': fields.get('main', True), 'SET_SPEED': 90}),
                 packer.make_can_msg('PCM_CRUISE_ALT', 0, {'UI_SET_SPEED': 90})]
    if topology == '2FF4':
      frames.append((0x2FF, bytes.fromhex('00000409'), 0))
    if topology == '2AA8':
      frames.append((0x2AA, bytes(8), 0))
    if topology in ('2AA8', '2FF4', 'camera343_or_4CB8', 'standalone2016'):
      frames += [packer.make_can_msg('ACC_CONTROL', 2, {'ACC_TYPE': 1}), packer.make_can_msg('PRE_COLLISION', 2, {})]
    if topology == '2FF8':
      frames.append(packer.make_can_msg('PRE_COLLISION', 2, {}))
    if kind == 'hold' or topology == 'standalone2016':
      frames.append(packer.make_can_msg('PRE_COLLISION', 2, {}))
      frames.append(packer.make_can_msg('PRE_COLLISION_2', 2, {}))
    frames.append(packer.make_can_msg('PCS_HUD', 0 if cp.flags & ToyotaFlags.RADAR_ACC else 2, {}))
    if identity == CAR.TOYOTA_RAV4_TSS2_2023:
      frames = [frame for frame in frames if frame[0] not in (0x260, 0x262)]
      frames += [packer.make_can_msg('STEER_TORQUE_SENSOR', 0, {'STEER_ANGLE': .1, 'STEER_ANGLE_INITIALIZING': 0}),
                 packer.make_can_msg('EPS_STATUS', 0, {'LKA_STATE': fields.get('eps', 1), 'LTA_STATE': fields.get('eps', 1)})]
    frames = [frame for frame in frames if frame[0] != 0xAA]
    frames.append(packer.make_can_msg('WHEEL_SPEEDS', 0, {
      'WHEEL_SPEED_' + wheel: 0.0 if fields.get('stopped') else 18.0 for wheel in ('FL', 'FR', 'RL', 'RR')}))
    a, d, b = packer.make_can_msg('GAS_SENSOR', 0, {
      'INTERCEPTOR_GAS': 806 if fields.get('gas') else 0,
      'INTERCEPTOR_GAS2': 806 if fields.get('gas') else 0, 'COUNTER_PEDAL': tick & 15, 'STATE': 0})
    data = bytearray(d)
    data[-1] = pedal.checksum(data)
    if not pedal_drop:
      frames.append((a, bytes(data), b))
    if mirror:
      frames.append((a, bytes(data), 2))
    if parent_drop and topology != '2FF8':
      frames = [frame for frame in frames if not (frame[0] == 0x262 and frame[2] == 0)]
    return frames

  def tx_check(tick, frame, cp, safety):
    nonlocal pedal_sent
    if frame.address == 0x200:
      data = bytes(frame.dat)
      assert len(data) == 6 and data[-1] == pedal.checksum(data)
      if data[4] & 128:
        assert not stock and safety.aol_get_permission_mask() & 2
        assert not safety.get_gas_pressed_prev() and not safety.get_brake_pressed_prev()
        pedal_sent = True
      else:
        assert data[:4] == bytes(4)
    return True

  def observe(tick, host, cs, cc, safety, native):
    if kind == 'hold':
      assert host.CP.flags & ToyotaFlags.AUTO_BRAKE_HOLD
      if 380 <= tick < 425:
        assert host.CI.CC.brake_hold_active, (tick, cs.to_dict())
      if 431 <= tick < 445:
        assert not host.CI.CC.brake_hold_active
    if kind.endswith('_drop') and 301 <= tick < 308:
      assert not cc.latActive and not cc.longActive
      assert safety.aol_get_permission_mask() == 0

  scenario = 'main_lease' if kind.endswith('_drop') else 'nominal' if kind == 'ordinary' else kind
  helpers.run_case(
    stock, scenario, candidate=identity, bridge=bridge, input_clock=True, alpha_long=True,
    sdsu_words=(long_word, long_word | 0x200), firmware_override=fw,
    fingerprint_frames=[(a, bytes(n), bus) for bus, messages in fp.items() for a, n in messages.items()],
    physical_frames=physical, native_tx_expected=tx_check, brake_stop_check_start=355,
    angle=identity == CAR.TOYOTA_RAV4_TSS2_2023,
    manual_selection=rack == 'EPS73_8965B47070', manual_pedal=rack == 'EPS73_8965B47070',
    auto_hold=kind == 'hold', aol=kind != 'ordinary', on_sample=observe, filter_advert=topology == '2FF4',
    bypass_owner=topology == 'camera343_or_4CB8',
  )
  assert pedal_sent == (not stock)


@pytest.mark.parametrize('kind,name,topology,rack', [
  ('parent_drop', 'TOYOTA_CAMRY', '2FF8', 'catalog'),
  ('parent_drop', 'TOYOTA_COROLLA', '2FF4', 'catalog'),
  ('parent_drop', 'TOYOTA_RAV4_TSS2', '2FF4', 'catalog'),
  ('parent_drop', 'TOYOTA_RAV4_TSS2_2022', '2AA8', 'catalog'),
  ('parent_drop', 'TOYOTA_RAV4_TSS2', '2AA8', 'catalog'),
  ('parent_drop', 'TOYOTA_CAMRY', 'camera343_or_4CB8', 'catalog'),
  ('physical', 'TOYOTA_CAMRY', '2FF8', 'catalog'),
  ('off', 'TOYOTA_CAMRY', '2FF8', 'catalog'),
  ('ordinary', 'TOYOTA_CAMRY', '2FF8', 'catalog'),
  ('pedal_drop', 'TOYOTA_CAMRY', '2FF8', 'catalog'),
])
def test_parent_pedal_physical_withdrawal_and_saved_settings(name, topology, rack, kind, host_heartbeat_bridge):
  run(name, topology, rack, False, host_heartbeat_bridge, kind)


HOLD_PROFILES = (
  ('TOYOTA_RAV4H', '2FF8', 'catalog'),
  ('TOYOTA_RAV4_TSS2', '2FF8', 'catalog'),
  ('TOYOTA_RAV4_TSS2', '2FF4', 'catalog'),
  ('TOYOTA_RAV4_TSS2', '2AA8', 'catalog'),
  ('TOYOTA_COROLLA_TSS2', 'standalone2016', 'catalog'),
  ('TOYOTA_RAV4_TSS2', 'standalone2016', 'catalog'),
  ('TOYOTA_CAMRY_TSS2', '2FF8', 'catalog'),
  ('TOYOTA_RAV4', 'camera343_or_4CB8', 'catalog'),
)


@pytest.mark.parametrize('name,topology,rack', HOLD_PROFILES)
def test_shared_hold_full_callers(name, topology, rack, host_heartbeat_bridge):
  run(name, topology, rack, False, host_heartbeat_bridge, 'hold')
