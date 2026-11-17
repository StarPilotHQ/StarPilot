"""Donor legacy EPS73 Prius DSU+pedal, with independent primary RX leases."""
import pytest
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota import pedal, pedal_composite, prius_legacy_pedal as owner
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.aol import qualified
from openpilot.starpilot.car.toyota.tests.test_dsu_bypass import acquired
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope


def sources():
  fp = gen_empty_fingerprint()
  fp[0] = {0x201: 6, 0x343: 8, 0x4CB: 8, 0x1D3: 8, 0x262: 8}
  fp[2] = {0x201: 6, 0x343: 8, 0x4CB: 8}
  return fp, acquired(CAR.TOYOTA_PRIUS_RETROFIT)


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('mirror', (False, True))
def test_exact_legacy_factory(stock, mirror):
  fp, fw = sources()
  if not mirror:
    del fp[2][0x201]
  cp = CarInterface.get_params(CAR.TOYOTA_PRIUS_RETROFIT, fp, fw, False, False, False)
  assert owner.hardware_supported(CAR.TOYOTA_PRIUS_RETROFIT, fp, fw)
  assert cp.openpilotLongitudinalControl and not cp.dashcamOnly
  assert cp.safetyConfigs[0].safetyParam == owner.LONG_WORD and pedal_composite.configured(cp)
  if stock:
    assert pedal_composite.prepare_stock(cp)
  assert cp.openpilotLongitudinalControl != stock
  assert cp.safetyConfigs[0].safetyParam == (owner.STOCK_WORD if stock else owner.LONG_WORD)
  assert qualified(cp)


@pytest.mark.parametrize('bad', ('auto', 'rack', 'missing_camera', 'firmware_bus', 'no_pedal', 'bus1_pedal',
                                 'short_pedal', 'missing_main', 'native_acc_missing', 'filter', 'twoaa'))
def test_legacy_identity_refusals(bad):
  fp, fw = sources()
  candidate = CAR.TOYOTA_PRIUS_RETROFIT
  if bad == 'auto':
    candidate = CAR.TOYOTA_PRIUS
  elif bad == 'rack':
    next(f for f in fw if f.ecu == structs.CarParams.Ecu.eps).fwVersion = b'unknown'
  elif bad == 'missing_camera':
    fw = [f for f in fw if f.ecu != structs.CarParams.Ecu.fwdCamera]
  elif bad == 'firmware_bus':
    next(f for f in fw if f.ecu == structs.CarParams.Ecu.eps).bus = 1
  elif bad == 'no_pedal':
    del fp[0][0x201]
  elif bad == 'bus1_pedal':
    fp[1][0x201] = 6
  elif bad == 'short_pedal':
    fp[2][0x201] = 5
  elif bad == 'missing_main':
    del fp[0][0x1D3]
  elif bad == 'native_acc_missing':
    del fp[0][0x343]
  elif bad == 'filter':
    fp[0][0x2FF] = 4
  else:
    fp[0][0x2AA] = 8
  assert not owner.hardware_supported(candidate, fp, fw)


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('fault', ('none', 'pedal_drop', 'parent_drop', 'bad_mirror'))
def test_legacy_actual_callers(stock, fault, host_heartbeat_bridge):
  fp, fw = sources()
  tick = 0
  neutral = 0
  active_pedal = False

  def physical(packer, **fields):
    nonlocal tick
    tick += 1
    fields['sdsu'] = None
    frames = helpers.physical(packer, **fields)
    frames += [packer.make_can_msg('ACC_CONTROL', 0, {'ACC_TYPE': 1}),
               packer.make_can_msg('ACC_CONTROL', 2, {'ACC_TYPE': 1}),
               (0x4CB, bytes(8), 0), (0x4CB, bytes(8), 2),
               packer.make_can_msg('PRE_COLLISION', 2, {}), packer.make_can_msg('PCS_HUD', 2, {})]
    frames = [frame for frame in frames if frame[0] != 0xAA]
    frames.append(packer.make_can_msg('WHEEL_SPEEDS', 0, {
      'WHEEL_SPEED_' + wheel: 0. if fields.get('stopped') else 18. for wheel in ('FL', 'FR', 'RL', 'RR')}))
    state = 5 if neutral < 3 else 0
    a, data, bus = packer.make_can_msg('GAS_SENSOR', 0, {
      'INTERCEPTOR_GAS': 806 if fields.get('gas') else 0,
      'INTERCEPTOR_GAS2': 806 if fields.get('gas') else 0,
      'COUNTER_PEDAL': tick & 15, 'STATE': state})
    data = bytearray(data)
    data[-1] = pedal.checksum(data)
    if not (fault == 'pedal_drop' and 260 <= tick < 311):
      frames.append((a, bytes(data), bus))
    mirror = bytes(data[:-1]) + bytes([data[-1] ^ 1]) if fault == 'bad_mirror' and tick == 260 else bytes(data)
    frames.append((a, mirror, 2))
    if fault == 'parent_drop' and 260 <= tick < 311:
      frames = [f for f in frames if not (f[0] == 0x262 and f[2] == 0)]
    return frames

  def tx(tick, frame, cp, safety):
    nonlocal neutral, active_pedal
    if frame.address == 0x200:
      data = bytes(frame.dat)
      if not data[4] & 128:
        assert data[:4] == bytes(4)
        neutral += 1
      else:
        active_pedal = True
        assert not stock and safety.aol_get_permission_mask() & 2
    return True

  def observe(tick, host, cs, cc, safety, native):
    assert host.CP.safetyConfigs[0].safetyParam == (owner.STOCK_WORD if stock else owner.LONG_WORD)
    if fault != 'none' and 301 <= tick < 308:
      assert not cc.latActive and not cc.longActive and safety.aol_get_permission_mask() == 0
    if fault == 'bad_mirror' and tick >= 310:
      assert not cc.latActive and not cc.longActive

  def rx(address, data, bus, expected, cp):
    if address == 0x201 and bus == 0 and data[4] >> 4:
      return False
    return expected

  scenario = 'identity_refusal' if fault == 'bad_mirror' else 'main_lease' if fault != 'none' else 'nominal'
  helpers.run_case(stock, scenario, candidate=CAR.TOYOTA_PRIUS_RETROFIT,
    bridge=host_heartbeat_bridge, input_clock=True, sdsu_words=(owner.LONG_WORD, owner.STOCK_WORD),
    firmware_override=fw, fingerprint_frames=[(a, bytes(n), b) for b, frames in fp.items() for a, n in frames.items()],
    manual_selection=True, manual_pedal=True, bypass_owner=True, physical_frames=physical,
    native_tx_expected=tx, native_rx_expected=rx, on_sample=observe, brake_stop_check_start=355)
  assert neutral >= 3
  if fault == 'none':
    assert active_pedal != stock
