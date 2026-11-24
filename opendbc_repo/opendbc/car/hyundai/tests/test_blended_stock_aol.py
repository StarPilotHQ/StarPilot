import time
from unittest.mock import patch

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.hyundai.interface import CarInterface
from opendbc.car.hyundai.values import CAR, DBC
from opendbc.car.hyundai.blended_stock_aol import qualified
from opendbc.safety.tests.test_hyundai import checksum
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE
from openpilot.starpilot.car.hyundai.aol import create_intent


def params(hda2=False, alpha=False):
  fp = gen_empty_fingerprint()
  if hda2:
    fp[2][0x50] = 16
  fp[int(hda2)][0x391] = 8
  return CarInterface.get_params(CAR.HYUNDAI_PALISADE_2023, fp, [], alpha, False, False)


class MixedReceiveStream:
  PT = (
    ('MDPS12', 1),
    ('TCS11', 1),
    ('TCS13', 1),
    ('TCS15', 10),
    ('CLU11', 2),
    ('CLU15', 20),
    ('ESP12', 1),
    ('CGW1', 10),
    ('CGW2', 20),
    ('WHL_SPD11', 1),
    ('SAS11', 1),
    ('EMS12', 1),
    ('EMS16', 1),
    ('LVR12', 1),
    ('SCC11', 2),
    ('SCC12', 2),
    ('BCM_PO_11', 10),
    ('CLU13', 2),
  )

  def __init__(self, ci):
    self.ci = ci
    self.pt = ci.can_parsers[Bus.pt].bus
    self.packer = CANPacker(DBC[ci.CP.carFingerprint][Bus.pt])
    self.count = {}
    self.now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)

  def frames(self, tick, *, lda=False, main=False, cruise=0, cruise_button=0, stock_main=True, scc_raw=-10.23, omit=None, wrong_bus=None):
    inventory = [(name, self.pt, period) for name, period in self.PT]
    inventory += [('CAM_0x2a4', 2, 5)] if self.pt == 1 else [('LKAS11', 2, 1), ('ALERTS_364', 2, 10)]
    result = []
    for name, bus, period in inventory:
      if tick % period or name == omit:
        continue
      count = self.count.get(name, 0)
      self.count[name] = count + 1
      values = {}
      if name == 'EMS16':
        values['AliveCounter'] = count % 4
      elif name == 'MDPS12':
        values.update(CR_Mdps_StrColTq=0, CR_Mdps_OutTq=0)
      elif name == 'TCS13':
        values.update(AliveCounterTCS=count % 8, ACCEnable=0)
      elif name == 'WHL_SPD11':
        values.update(WHL_SPD_FL=72, WHL_SPD_FR=72, WHL_SPD_RL=72, WHL_SPD_RR=72, WHL_SPD_AliveCounter_LSB=count % 4, WHL_SPD_AliveCounter_MSB=(count // 4) % 4)
      elif name == 'CLU11':
        values.update(CF_Clu_AliveCnt1=count % 16, CF_Clu_CruiseSwMain=int(main), CF_Clu_CruiseSwState=cruise_button)
      elif name == 'SCC11':
        values.update(COUNTER=count % 16, aReqRaw=scc_raw)
      elif name == 'SCC12':
        values.update(ACCMode=cruise, MainMode_ACC=int(stock_main), COUNTER=count % 16)
      elif name == 'CGW1':
        values['CF_Gway_DrvSeatBeltSw'] = 1
      elif name == 'LVR12':
        values['CF_Lvr_Gear'] = 5
      elif name == 'BCM_PO_11':
        values['LDA_BTN'] = int(lda)
      assert set(values) <= set(self.packer.dbc.name_to_msg[name].sigs), (name, set(values) - set(self.packer.dbc.name_to_msg[name].sigs))
      frame = self.packer.make_can_msg(name, (1 - self.pt) if name == wrong_bus else bus, values)
      if frame[0] in (0x260, 0x394, 0x386):
        frame = checksum(frame)
      result.append(frame)
    return result


@pytest.mark.parametrize('hda2', [False, True])
@pytest.mark.parametrize('alpha', [False, True])
def test_exact_stock_profile_advertises_hdai_without_selecting_long(hda2, alpha):
  cp = params(hda2, alpha)
  assert qualified(cp)
  assert cp.alternativeExperience == 0
  assert cp.alphaLongitudinalAvailable
  assert not cp.openpilotLongitudinalControl
  assert cp.safetyConfigs[0].safetyParam == (0x2010 if hda2 else 0x2000)
  cp.alternativeExperience = 32
  assert qualified(cp, marked_only=True)
  cp.safetyConfigs[0].safetyParam |= 4
  assert not qualified(cp)


@pytest.mark.parametrize('hda2', (False, True))
def test_independent_stream_physical_latch_native_tx_and_handover(hda2):
  cp = params(hda2)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  owner = create_intent(cp, AolSettings(True, 0.0, AOL_TOGGLE, AOL_TOGGLE, (0, 0, 0), (0, 0, 0)))
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.init_tests()
  rows = []
  for tick in range(100):
    now = stream.now + tick * 10_000_000
    frames = stream.frames(tick, lda=20 <= tick < 30, omit='SCC12' if tick >= 65 else None)
    safety.set_timer((now // 1000) & 0xFFFFFFFF)
    for address, data, bus in frames:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.set_aol_test_heartbeat(True)
    safety.safety_tick()
    with patch('time.clock_gettime_ns', return_value=now):
      cs = ci.update([(now, frames)])
    owner.update(cs, now_ns=now, fault_active=not cs.canValid)
    safety.aol_set_host_request(int(owner.output(cs)[0]))
    permission = safety.aol_get_permission_mask()
    command = structs.CarControl(enabled=False, latActive=bool(permission & 1), longActive=False)
    command.actuators.torque = 0.005 if command.latActive else 0.0
    _, tx = ci.apply(command.as_reader(), now)
    for address, data, bus in tx:
      assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data))
    assert not any(address in (0x420, 0x421) for address, _, _ in tx)
    rows.append((cs.canValid, permission, safety.safety_fwd_hook(2, 0x50 if hda2 else 0x340)))
  assert all(valid for valid, _, _ in rows[12:65])
  assert all(permission == 0 for _, permission, _ in rows[:20])
  assert any(permission == 1 and fwd == -1 for _, permission, fwd in rows[24:60])
  assert all(permission == 0 and fwd == 0 for _, permission, fwd in rows[88:])


@pytest.mark.parametrize('hda2', [False, True])
@pytest.mark.parametrize('aol', [False, True])
@pytest.mark.parametrize('disabled', [False, True])
def test_actual_card_publication_and_controls_stock_only(hda2, aol, disabled, monkeypatch):
  import gc
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  from openpilot.selfdrive.controls.controlsd import Controls
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from openpilot.cereal import messaging

  monkeypatch.setenv('SIMULATION', '1')
  monkeypatch.setenv('REPLAY', '1')
  monkeypatch.setenv('AOL_REPLAY_RUNTIME', '0')
  with OpenpilotPrefix():
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlphaLongitudinalEnabled', True, block=True)
    saved.put_bool('DisableOpenpilotLongitudinal', disabled, block=True)
    saved.put_bool('AlwaysOnLateral', aol, block=True)
    saved.put('LKASButtonControl', AOL_TOGGLE, block=True)
    saved.put('MainCruiseButtonControl', AOL_TOGGLE, block=True)
    cp = params(hda2, alpha=True)
    subscriber = messaging.sub_sock('carParams', timeout=100, conflate=True)
    ci = CarInterface(cp)
    card = Car(CI=ci, RI=RadarInterface(cp))
    assert not card.CP.openpilotLongitudinalControl
    assert card.CP.alphaLongitudinalAvailable
    assert card.CP.pcmCruise and card.CP.safetyConfigs[0].safetyParam == (0x2010 if hda2 else 0x2000)
    assert card.CP.alternativeExperience == (32 if aol else 0)
    expected = card.CP.to_dict()
    with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
      assert published.to_dict() == expected
    event = None
    for _ in range(10):
      card.car_params_published = False
      card.state_publish(ci.update([]), None)
      event = messaging.recv_one(subscriber)
      if event is not None:
        break
      time.sleep(0.01)
    assert event is not None and event.valid and event.carParams.to_dict() == expected
    controls = Controls()
    assert controls.CP.to_dict() == expected
    del controls, card, ci, subscriber
    gc.collect()


@pytest.mark.parametrize('hda2', (False, True))
@pytest.mark.parametrize('missing', ['EMS16', 'WHL_SPD11', 'TCS13', 'MDPS12', 'CLU11', 'SCC11', 'SCC12'])
def test_required_literal_source_absence_cannot_authorize(hda2, missing):
  cp = params(hda2)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.init_tests()
  for tick in range(50):
    now = stream.now + tick * 10_000_000
    frames = stream.frames(tick, lda=20 <= tick < 30, omit=missing)
    safety.set_timer((now // 1000) & 0xFFFFFFFF)
    for address, data, bus in frames:
      safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.set_aol_test_heartbeat(True)
    safety.safety_tick()
    safety.aol_set_host_request(1)
    with patch('time.clock_gettime_ns', return_value=now):
      state = ci.update([(now, frames)])
    assert safety.aol_get_permission_mask() == 0
  assert not state.canValid


@pytest.mark.parametrize('hda2', (False, True))
def test_native_held_startup_and_wrong_bus_are_not_fresh_gestures(hda2):
  for wrong_bus in (None, 'BCM_PO_11'):
    cp = params(hda2)
    cp.alternativeExperience = 32
    ci = CarInterface(cp)
    stream = MixedReceiveStream(ci)
    safety = libsafety_py.libsafety
    safety.set_alternative_experience(32)
    assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
    safety.init_tests()
    for tick in range(50):
      now = stream.now + tick * 10_000_000
      frames = stream.frames(tick, lda=True if wrong_bus is None else 20 <= tick < 30, wrong_bus=wrong_bus)
      safety.set_timer((now // 1000) & 0xFFFFFFFF)
      for address, data, bus in frames:
        safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      safety.set_aol_test_heartbeat(True)
      safety.safety_tick()
      safety.aol_set_host_request(1)
      assert safety.aol_get_permission_mask() == 0


@pytest.mark.parametrize('hda2', (False, True))
def test_ae0_physical_tracker_is_inactive_until_final_ae32(hda2):
  cp = params(hda2)
  ci = CarInterface(cp)
  sources = ci.CS.forte_lkas_sources
  assert not sources.active
  stream = MixedReceiveStream(ci)
  for tick in range(40):
    sources.update([(stream.now + tick * 10_000_000, stream.frames(tick, lda=tick >= 20))])
  assert not sources.held
  assert not sources.edges
  cp.alternativeExperience = 32
  assert sources.active


@pytest.mark.parametrize('hda2', (False, True))
@pytest.mark.parametrize('corruption', ('crc', 'replay', 'wrong_bus', 'inactive'))
def test_real_controller_forwarding_requires_valid_replacement(hda2, corruption):
  cp = params(hda2)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.init_tests()
  for tick in range(40):
    now = stream.now + tick * 10_000_000
    frames = stream.frames(tick, lda=20 <= tick < 30)
    safety.set_timer((now // 1000) & 0xFFFFFFFF)
    for address, data, bus in frames:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.set_aol_test_heartbeat(True)
    safety.safety_tick()
    with patch('time.clock_gettime_ns', return_value=now):
      cs = ci.update([(now, frames)])
  assert cs.canValid
  safety.aol_set_host_request(1)
  assert safety.aol_get_permission_mask() == 1
  command = structs.CarControl(latActive=corruption != 'inactive', longActive=False)
  command.actuators.torque = 0.005 if command.latActive else 0.0
  _, packets = ci.apply(command.as_reader(), now)
  address = 0x50 if hda2 else 0x340
  steering = next(frame for frame in packets if frame[0] == address)
  addr, data, bus = steering
  if corruption == 'replay':
    assert safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))
    assert safety.safety_fwd_hook(2, addr) == -1
    assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))
  elif corruption == 'crc':
    bad = bytearray(data)
    bad[0] ^= 1
    assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, bytes(bad)))
  elif corruption == 'wrong_bus':
    assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, 1, data))
  else:
    assert safety.safety_fwd_hook(2, addr) == 0
    if hda2:
      assert ((data[6] & 0x1F) << 7 | data[5] >> 1) == 1024
    assert safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))
    if hda2:
      assert safety.safety_fwd_hook(2, addr) == -1
      assert safety.safety_fwd_hook(2, 0x2A4) == 0
      safety.set_timer(((now // 1000) + 200_001) & 0xFFFFFFFF)
  assert safety.safety_fwd_hook(2, addr) == 0


@pytest.mark.parametrize('hda2', (False, True))
def test_real_controller_crc_fault_revokes_token_then_fresh_gesture_recovers(hda2):
  cp = params(hda2)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.init_tests()
  address = 0x50 if hda2 else 0x340
  accepted_before = recovered = False
  for tick in range(140):
    now = stream.now + tick * 10_000_000
    safety.set_timer((now // 1000) & 0xFFFFFFFF)
    frames = stream.frames(tick, lda=40 <= tick < 50 or 100 <= tick < 110)
    for addr, data, bus in frames:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(addr, bus, data))
    safety.set_aol_test_heartbeat(True)
    safety.safety_tick()
    with patch('time.clock_gettime_ns', return_value=now):
      cs = ci.update([(now, frames)])
    if tick >= 20:
      assert cs.canValid
    request = 45 <= tick < 80 or tick >= 105
    safety.aol_set_host_request(int(request))
    permission = safety.aol_get_permission_mask()
    command = structs.CarControl(latActive=request, longActive=False)
    command.actuators.torque = 0.005 if request else 0.0
    _, emitted = ci.apply(command.as_reader(), now)
    steering = next(frame for frame in emitted if frame[0] == address)
    addr, data, bus = steering
    if tick == 60:
      assert accepted_before
      bad = bytearray(data)
      bad[0] ^= 1
      assert not safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, bytes(bad)))
      assert safety.aol_get_permission_mask() == 0
      assert safety.safety_fwd_hook(2, addr) == 0
    else:
      accepted = safety.safety_tx_hook(libsafety_py.make_CANPacket(addr, bus, data))
      if 50 <= tick < 60:
        assert permission == 1 and accepted
        accepted_before = True
      if 61 <= tick < 80:
        assert permission == 0 and not accepted
        assert safety.safety_fwd_hook(2, addr) == 0
      if 80 <= tick < 100:
        assert permission == 0
        if hda2:
          assert accepted and ((data[6] & 0x1F) << 7 | data[5] >> 1) == 1024
          assert safety.safety_fwd_hook(2, 0x2A4) == 0
        assert safety.safety_fwd_hook(2, addr) == (-1 if hda2 else 0)
      if tick >= 115:
        assert permission == 1 and accepted
        assert safety.safety_fwd_hook(2, addr) == -1
        recovered = True
  assert recovered


@pytest.mark.parametrize('hda2', (False, True))
@pytest.mark.parametrize('experience', (0, 32))
@pytest.mark.parametrize('cruise', (0, 1, 2))
def test_blended_native_main_uses_scc12_not_acceleration_payload(hda2, experience, cruise):
  cp = params(hda2)
  cp.alternativeExperience = experience
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(experience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.init_tests()
  tick = 0
  for stock_main, raw in ((False, -10.23), (False, -10.22), (True, -10.23), (True, -10.22), (False, -10.22)):
    for _ in range(20):
      now = stream.now + tick * 10_000_000
      safety.set_timer((now // 1000) & 0xFFFFFFFF)
      frames = stream.frames(tick, cruise=cruise, stock_main=stock_main, scc_raw=raw)
      for address, data, bus in frames:
        assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
      safety.safety_tick()
      with patch('time.clock_gettime_ns', return_value=now):
        cs = ci.update([(now, frames)])
      tick += 1
    assert cs.canValid
    assert cs.cruiseState.available == stock_main
    assert bool(safety.get_acc_main_on()) == cs.cruiseState.available
    assert cs.cruiseState.enabled == (cruise != 0)
    assert not safety.get_controls_allowed()


@pytest.mark.parametrize('experience', (0, 32))
def test_hda2_independent_axis_uses_physical_stock_owner(experience):
  from openpilot.starpilot.car.hyundai.aol import policy_for, native_accepts_cp

  cp = params(True)
  cp.alternativeExperience = experience
  assert qualified(cp) and policy_for(cp).intent_supported
  assert native_accepts_cp(cp, cp.safetyConfigs[0].safetyModel.raw, 0x2010) == (experience == 32)
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(experience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, 0x2010) == 0
  safety.init_tests()
  for tick in range(60):
    now = stream.now + tick * 10_000_000
    safety.set_timer((now // 1000) & 0xFFFFFFFF)
    frames = stream.frames(tick, lda=40 <= tick < 50)
    for address, data, bus in frames:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.set_aol_test_heartbeat(True)
    safety.safety_tick()
    safety.aol_set_host_request(1 if tick >= 40 else 0)
    assert safety.aol_get_permission_mask() == (1 if experience == 32 and tick >= 40 else 0)
    with patch('time.clock_gettime_ns', return_value=now):
      cs = ci.update([(now, frames)])
    if tick >= 20:
      assert cs.canValid
  assert not cp.openpilotLongitudinalControl and cp.pcmCruise


@pytest.mark.parametrize('experience', (0, 32))
def test_hda1_ordinary_stock_cruise_with_unused_independent_latch(experience):
  cp = params(False)
  cp.alternativeExperience = experience
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(experience)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam) == 0
  safety.init_tests()
  rows = []
  for tick in range(80):
    now = stream.now + tick * 10_000_000
    cruise = 1 if 16 <= tick < 38 else 2 if 38 <= tick < 60 else 0
    frames = stream.frames(tick, cruise=cruise, cruise_button=2 if tick == 12 else 0)
    safety.set_timer((now // 1000) & 0xffffffff)
    for address, data, bus in frames:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.set_aol_test_heartbeat(True)
    safety.safety_tick()
    with patch('time.clock_gettime_ns', return_value=now):
      state = ci.update([(now, frames)])
    assert not any(event.pressed and event.type == structs.CarState.ButtonEvent.Type.lkas for event in state.buttonEvents)
    safety.aol_set_host_request(int(state.cruiseState.enabled))
    assert safety.aol_get_permission_mask() == (int(state.cruiseState.enabled) if experience == 32 else 0)
    command = structs.CarControl(enabled=state.cruiseState.enabled, latActive=state.cruiseState.enabled, longActive=False)
    command.actuators.torque = .005 if command.latActive else 0.
    _, packets = ci.apply(command.as_reader(), now)
    for address, data, bus in packets:
      assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (tick, address)
    assert not any(address in (0x420, 0x421) for address, _, _ in packets)
    rows.append((state.canValid, state.cruiseState.enabled, bool(safety.get_controls_allowed()), safety.safety_fwd_hook(2, 0x340)))
  assert all(valid for valid, _, _, _ in rows[12:])
  assert all(not enabled and not allowed for _, enabled, allowed, _ in rows[:12])
  assert all(enabled and allowed and forwarding == -1 for _, enabled, allowed, forwarding in rows[18:60])
  assert all(not enabled and not allowed for _, enabled, allowed, _ in rows[62:])
  if experience == 32:
    assert all(forwarding == 0 for _, _, _, forwarding in rows[:12] + rows[62:])
  else:
    assert all(forwarding == -1 for _, _, _, forwarding in rows)


@pytest.mark.parametrize('enabled', [False, True])
def test_actual_alpha_card_final_composition_and_restart_transport(enabled, monkeypatch):
  import gc
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car.card import Car
  from opendbc.car.hyundai.radar_interface import RadarInterface
  from opendbc.car.hyundai.blended_longitudinal import BlendedStartup, candidate_from_stock, Phase, Outcome, TakeoverResult
  from openpilot.starpilot.car.hyundai.aol import policy_for
  monkeypatch.setenv('SIMULATION', '1')
  monkeypatch.setenv('REPLAY', '1')
  monkeypatch.setenv('AOL_REPLAY_RUNTIME', '0')
  with OpenpilotPrefix():
    saved = Params()
    saved.put_bool('OpenpilotEnabledToggle', True, block=True)
    saved.put_bool('AlwaysOnLateral', enabled, block=True)
    saved.put('LKASButtonControl', AOL_TOGGLE, block=True)
    stock = params()
    cp = candidate_from_stock(stock, alpha_requested=True, native_qualified=True)
    owner = BlendedStartup(stock, cp, (lambda *a: None, lambda *a: None))
    # This constructor regression supplies a prepared diagnostic result; actual
    # matched UDS and full physical/native chain remain separate lifecycle tests.
    owner.owner.phase = Phase.OWNED
    owner.owner.result = TakeoverResult(Outcome.OWNED)
    owner.source_floor_ns = 1
    ci = CarInterface(cp)
    card = Car(CI=ci, RI=RadarInterface(cp), startup_owner=owner)
    assert card.aol_replay == enabled and card.aol_qualified == enabled
    if enabled:
      assert 'aolSafetyWire' in card.sm.services
      assert 'aolIntentWire' in card.pm.sock
    assert card.CP.safetyConfigs[0].safetyParam == 0x2004
    assert card.CP.alternativeExperience == (32 if enabled else 0) and owner.prepared_for(card.CP)
    assert card.CP.openpilotLongitudinalControl and not card.CP.pcmCruise
    assert card.aol_card_intent is None or not card.aol_card_intent.allowed_latch
    with structs.CarParams.from_bytes(saved.get('CarParams')) as published:
      assert policy_for(published).full_axis_runtime_required == enabled
      assert published.to_dict() == card.CP.to_dict()
    del card, ci
    gc.collect()


def test_mixed_canfd_checksum_registration_is_address_scoped():
  from opendbc.car.hyundai.hyundaicanfd import hkg_can_fd_checksum
  from opendbc.car.hyundai.blended_longitudinal_can import create_blended_adrv_messages
  from opendbc.car.hyundai.hyundaicanfd import CanBus
  packer = CANPacker(DBC[CAR.HYUNDAI_PALISADE_2023][Bus.pt])
  for name in ('LKAS', 'CAM_0x2a4', 'ADRV_0x51'):
    address, data, _ = packer.make_can_msg(name, 0, {})
    assert int.from_bytes(data[:2], 'little') == hkg_can_fd_checksum(address, None, bytearray(data))
    assert packer.dbc.name_to_msg[name].sigs['CHECKSUM'].calc_checksum is not None
  for name in ('SCC11', 'SCC12', 'SCC14', 'LFAHDA_MFC'):
    assert packer.dbc.name_to_msg[name].sigs['CHECKSUM'].calc_checksum is None
  cp = params(hda2=True)
  for counter in range(258):
    frame = create_blended_adrv_messages(packer, CanBus(cp), counter)[0]
    assert frame[0] == 0x51 and frame[2] == 0
    assert frame[1][3:] == bytes(29)
    # A prior packer call consumed initial0; the same original named-counter law wraps255→0.
    assert frame[1][2] == (counter + 1) & 255


def test_hda2_pt_recovery_before_next_camera_preserves_copy_without_authority():
  cp = params(True)
  cp.alternativeExperience = 32
  ci = CarInterface(cp)
  stream = MixedReceiveStream(ci)
  safety = libsafety_py.libsafety
  safety.set_alternative_experience(32)
  assert safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, 0x2010) == 0
  safety.init_tests()
  saw_withdrawal = saw_recovery_copy = False
  for tick in range(105):
    now = stream.now + tick * 10_000_000
    frames = stream.frames(tick, omit='SCC12' if 60 <= tick < 90 else None)
    if 80 <= tick < 100:
      frames = [frame for frame in frames if frame[0] != 0x2A4]
    safety.set_timer((now // 1000) & 0xFFFFFFFF)
    for address, data, bus in frames:
      assert safety.safety_rx_hook(libsafety_py.make_CANPacket(address, bus, data))
    safety.safety_tick()
    safety.set_aol_test_heartbeat(True)
    safety.aol_set_host_request(0)
    assert safety.aol_get_permission_mask() == 0
    with patch('time.clock_gettime_ns', return_value=now):
      state = ci.update([(now, frames)])
    _, outgoing = ci.apply(structs.CarControl().as_reader(), now)
    copies = [frame for frame in outgoing if frame[0] == 0x2A4]
    if tick == 85:
      assert not state.canValid and not copies
      saw_withdrawal = True
    if tick == 90:
      assert state.canValid and copies
      assert not any(frame[0] == 0x2A4 for frame in frames)
      saw_recovery_copy = True
    for address, data, bus in outgoing:
      assert safety.safety_tx_hook(libsafety_py.make_CANPacket(address, bus, data)), (tick, hex(address), data.hex())
  assert saw_withdrawal and saw_recovery_copy
