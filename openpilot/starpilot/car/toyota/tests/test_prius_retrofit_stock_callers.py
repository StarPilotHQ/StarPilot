"""Manual EPS73 Prius through the current factory, callers, C++ peer and native.

Manual selection, acquired firmware, CAN/perception and USB are input fixtures.
These tests do not establish physical vehicle or scheduling acceptance.
"""
import struct

import pytest
from opendbc.car import structs
from opendbc.car.toyota.values import CAR, ToyotaFlags
from openpilot.starpilot.car.toyota.tests import test_rav4h_caller_native as helpers
from openpilot.starpilot.car.toyota.tests.test_toyota_cpp_transport import host_heartbeat_bridge as host_heartbeat_bridge

source_matched_native = helpers.source_matched_native
daemon_gc_scope = helpers.daemon_gc_scope


@pytest.mark.parametrize('scenario', ('startup-main', 'physical', 'saved-long-off', 'ordinary-cruise', 'mirrored-pedal'))
def test_actual_prius_retrofit_stock_lifecycle(scenario, host_heartbeat_bridge):
  def physical(packer, **fields):
    fields['sdsu'] = None
    if scenario == 'startup-main':
      fields['cruise'] = False
    frames = helpers.physical(packer, **fields)
    frames.append(packer.make_can_msg('ACC_CONTROL', 0, {}))
    if scenario == 'mirrored-pedal':
      frames.extend(((0x4CB, bytes(8), 0), (0x201, bytes.fromhex('01f003dc5dd6'), 0)))
      frames.extend((address, data, 2) for address, data, bus in list(frames) if bus == 0)
    assert len(next(data for address, data, bus in frames if address == 0x262 and bus == 0)) == 8
    assert len(next(data for address, data, bus in frames if address == 0x1D3 and bus == 0)) == 8
    return frames

  def on_state(tick, host, cs):
    cp = host.CP
    assert cp.carFingerprint == CAR.TOYOTA_PRIUS_RETROFIT
    assert cp.safetyConfigs[0].safetyParam == 585 and cp.flags == int(ToyotaFlags.HYBRID)
    assert cp.pcmCruise and not cp.openpilotLongitudinalControl
    assert cp.steerActuatorDelay == struct.unpack('<f', struct.pack('<f', .14))[0]
    assert cp.lateralTuning.torque.steeringAngleDeadzoneDeg == struct.unpack('<f', struct.pack('<f', .3))[0]

  def observe(tick, host, cs, cc, safety, native):
    assert not cc.longActive and not safety.aol_get_permission_mask() & 2
    if scenario == 'physical':
      if tick == 265:
        assert cs.gasPressed and not cs.brakePressed
        assert cc.latActive and safety.aol_get_permission_mask() == 1
      if tick == 355:
        assert cs.brakePressed and cs.standstill
        assert cc.latActive and safety.aol_get_permission_mask() == 1
      if tick == 415:
        assert cs.gearShifter == structs.CarState.GearShifter.neutral
        assert not cc.latActive and not cc.longActive and safety.aol_get_permission_mask() == 0
      if tick == 425:
        assert cs.gearShifter == structs.CarState.GearShifter.reverse
        assert not cc.latActive and not cc.longActive and safety.aol_get_permission_mask() == 0
      if tick == 505:
        assert cs.gearShifter == structs.CarState.GearShifter.drive
        assert cc.latActive and safety.aol_get_permission_mask() == 1
    if scenario == 'startup-main' and tick >= 220:
      assert cs.cruiseState.available and not cs.cruiseState.enabled
      assert host.aol_card_intent.allowed_latch and cc.latActive
      assert safety.aol_get_permission_mask() == 1
    if scenario == 'ordinary-cruise' and tick >= 220:
      assert not host.aol_card_intent.settings.enabled
      assert not host.aol_card_intent.allowed_latch
      assert cs.cruiseState.enabled and cc.enabled and cc.latActive
    if scenario == 'saved-long-off' and tick >= 310:
      assert not host.CP.openpilotLongitudinalControl and not cc.longActive
      assert host.CP.safetyConfigs[0].safetyParam == 585

  fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.eps, address=0x7A1,
                              fwVersion=b'8965B47070' + bytes(6), brand='toyota', bus=0)]
  actual_scenario = 'physical' if scenario == 'physical' else 'off' if scenario == 'saved-long-off' else 'nominal'
  helpers.run_case(True, actual_scenario, candidate=CAR.TOYOTA_PRIUS_RETROFIT,
                   bridge=host_heartbeat_bridge, input_clock=True, aol=scenario != 'ordinary-cruise',
                   sdsu_words=(585, 585), firmware_override=fw, fingerprint_frames=([(address, data, bus) for bus in (0, 2) for address, data in
                     ((0x343, bytes(8)), (0x4CB, bytes(8)), (0x201, bytes.fromhex('01f003dc5dd6')))]
                     if scenario == 'mirrored-pedal' else []), manual_selection=True,
                   physical_frames=physical, on_state=on_state, on_sample=observe)
