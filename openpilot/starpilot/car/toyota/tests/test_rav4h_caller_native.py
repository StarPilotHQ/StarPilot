"""Actual current RAV4H factory/Card/SelfdriveD/Controls/CI/native join.

Synthetic boundaries: firmware acquisition, CAN/perception/process producers and
USB/axis transport. No CP/CS fields, native permission, health/frequency flags, or initialized/ControlsReady
are overwritten. Default nominal/physical/OFF cases use elapsed host time. Faults
and explicit input_clock cases use a shared 100Hz input clock and native test timer;
production freshness deadlines are unchanged. Neither mode establishes
installed-hardware scheduling or timing acceptance.
"""

import json
import hashlib
import ctypes
import gc
import time
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from opendbc.can import CANPacker
from opendbc.car import Bus, car_helpers, gen_empty_fingerprint, structs
from opendbc.car.fw_versions import match_fw_to_car
from opendbc.car.toyota.fingerprints import FW_VERSIONS
from opendbc.car.toyota.values import CAR, DBC, ToyotaFlags
from opendbc.car.toyota.tests.test_auto_hold import decode
from opendbc.safety.tests.libsafety import libsafety_py
from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car import card as card_module
from openpilot.selfdrive.controls.controlsd import Controls
from openpilot.selfdrive.selfdrived import selfdrived as sd_module
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety
from openpilot.starpilot.lateral.tests.test_lane_centering import model

OUT = Path(tempfile.mkdtemp(prefix='rav4h-caller-traces-'))
SYNTHETIC_LAST = {}
NATIVE_LAST = {}


@pytest.fixture(scope='module', params=(False, True), ids=('DEBUG', 'RELEASE'), autouse=True)
def source_matched_native(request):
  previous = libsafety_py.__dict__.get('libpath')
  libsafety_py.load(libsafety_py._build_libsafety(release=request.param))
  try:
    yield
  finally:
    if previous is not None:
      libsafety_py.load(previous)


@pytest.fixture(autouse=True)
def daemon_gc_scope():
  # All three production main functions call config_realtime_process, which disables cyclic GC.
  enabled = gc.isenabled()
  gc.disable()
  try:
    yield
  finally:
    if enabled:
      gc.enable()


def event(name, now, size=None):
  return messaging.new_message(name, size, valid=True, logMonoTime=now)


def deliver(sm, messages):
  admitted = [
    m.as_reader() if hasattr(m, 'as_reader') else m for m in messages if m.which() in sm.services and int(sm.logMonoTime[m.which()]) != int(m.logMonoTime)
  ]
  sm.update_msgs(time.monotonic(), admitted)


def firmware(candidate=CAR.TOYOTA_RAV4H):
  # Manual Matrix has no acquired firmware catalog; keep its fixed selection explicit.
  if candidate == CAR.TOYOTA_MATRIX_RETROFIT:
    return []
  return [
    structs.CarParams.CarFw(ecu=e, address=a, subAddress=s or 0, fwVersion=v[0], brand='toyota', bus=0)
    for (e, a, s), v in FW_VERSIONS[candidate].items()
  ]


def physical(packer, *, cruise, main=True, gear=0, brake=False, gas=False, eps=1, sdsu=0, stopped=False):
  frames = [
    packer.make_can_msg(name, 0, {})
    for name in ('VSC1S07', 'BODY_CONTROL_STATE_2', 'ESP_CONTROL', 'PCM_CRUISE_SM', 'BLINKERS_STATE', 'PRE_COLLISION', 'LIGHT_STALK')
  ]
  frames += [
    packer.make_can_msg('BODY_CONTROL_STATE', 0, {'SEATBELT_DRIVER_UNLATCHED': 0}),
    packer.make_can_msg('BRAKE_MODULE', 0, {'BRAKE_PRESSED': brake}),
    packer.make_can_msg('GEAR_PACKET', 0, {'GEAR': gear}),
    packer.make_can_msg('WHEEL_SPEEDS', 0, {f'WHEEL_SPEED_{w}': 0.0 if stopped else 72.0 for w in ('FL', 'FR', 'RL', 'RR')}),
    packer.make_can_msg('PCM_CRUISE', 0, {'CRUISE_ACTIVE': cruise, 'GAS_RELEASED': not gas, 'CRUISE_STATE': 7 if cruise and stopped else 8 if cruise else 0}),
    packer.make_can_msg('PCM_CRUISE_2', 0, {'MAIN_ON': main, 'SET_SPEED': 90}),
    packer.make_can_msg('STEER_ANGLE_SENSOR', 0, {}),
    packer.make_can_msg('STEER_TORQUE_SENSOR', 0, {}),
    packer.make_can_msg('EPS_STATUS', 0, {'LKA_STATE': eps}),
    packer.make_can_msg('LKAS_HUD', 2, {}),
    packer.make_can_msg('RSA1', 2, {}),
  ]
  if sdsu is not None:
    frames.append(packer.make_can_msg('SDSU', 0, {'STATE': sdsu}))
  return frames


def native_events(host, safety, now, session):
  prior = NATIVE_LAST.get(session)
  if prior is not None and now - prior[0] < 100_000_000:
    return prior[1]
  cfg = host.CP.safetyConfigs[0]
  request, allowed = safety.aol_get_request_mask(), safety.aol_get_permission_mask()
  slot = PandaSlot(
    0,
    'rav4h-test',
    cfg.safetyModel.raw,
    cfg.safetyParam,
    host.CP.alternativeExperience,
    bool(safety.get_controls_allowed()),
    not bool(safety.safety_config_valid()),
  )
  state = SafetyState(
    1,
    True,
    now,
    now + 200_000_000,
    cfg.safetyModel.raw,
    cfg.safetyParam,
    bool(allowed & 1),
    bool(allowed & 2),
    bool(request & 1),
    bool(request & 2),
    slot.hardwareSerial,
    session,
    sourcePandaStatesMonoTime=now,
    pandaInventory=(slot,),
  )
  wire = event('aolSafetyWire', now, 0)
  wire.aolSafetyWire = encode_safety(state)
  pandas = event('pandaStates', now, 1)
  p = pandas.pandaStates[0]
  p.safetyModel, p.safetyParam, p.alternativeExperience = cfg.safetyModel, cfg.safetyParam, host.CP.alternativeExperience
  p.controlsAllowed, p.safetyRxChecksInvalid = slot.controlsAllowed, slot.safetyRxChecksInvalid
  p.ignitionLine = True
  NATIVE_LAST[session] = (now, [wire, pandas])
  return [wire, pandas]


def selfdrive_inputs(sd, state, native, previous, cp, now):
  peers = []
  for name in sd.sm.services:
    if name in ('alertDebug', 'lateralManeuverPlan', 'laneChangeAssistWire', 'userBookmark'):
      continue
    if name in state:
      peers.append(state[name])
      continue
    if name in ('aolSafetyWire', 'pandaStates'):
      peers.append(next(e for e in native if e.which() == name))
      continue
    freq = SERVICE_LIST[name].frequency
    key = (id(sd.sm), name)
    if freq > 0 and now - SYNTHETIC_LAST.get(key, 0) < int(1e9 / freq):
      continue
    SYNTHETIC_LAST[key] = now
    e = event(name, now)
    v = getattr(e, name)
    if name == 'extrinsicsCalibration':
      v.calStatus, v.rpyCalib = 'calibrated', [0.0, 0.0, 0.0]
    elif name == 'vehicleParameters':
      v.stiffnessFactor, v.steerRatio, v.valid = 1.0, cp.steerRatio, True
    elif name == 'deviceMotion':
      v.inputsOK = v.posenetOK = True
      v.velocityDevice.x = state['carState'].carState.vEgo
    elif name == 'controlsState':
      v.lateralControlState.init('angleState')
    elif name == 'carControl':
      v.from_dict(previous.to_dict())
    elif name == 'modelV2':
      v.from_dict(model().to_dict())
    elif name == 'deviceState':
      v.freeSpacePercent = 50.0
      v.started = True
    elif name == 'longitudinalPlan':
      v.aTarget = 0.8
    elif name == 'lateralDelay':
      v.lateralDelay = 0.1
    peers.append(e)
  return peers


def control_inputs(controls, published, state, sd_events, native, now):
  values = []
  for name in controls.sm.services:
    if name in sd_events:
      values.append(sd_events[name])
    elif name == 'carState':
      values.append(state['carState'])
    elif name in ('aolSafetyWire', 'pandaStates'):
      values.append(next(e for e in native if e.which() == name))
    elif name in published:
      values.append(published[name])
    elif name == 'onroadEvents':
      continue  # Retain the actual producer's initial absence until its first publication.
    else:
      freq = SERVICE_LIST[name].frequency
      key = (id(controls.sm), name)
      if freq > 0 and now - SYNTHETIC_LAST.get(key, 0) < int(1e9 / freq):
        continue
      SYNTHETIC_LAST[key] = now
      e = event(name, now)
      v = getattr(e, name)
      if name == 'modelV2':
        v.from_dict(model().to_dict())
      elif name == 'lateralManeuverPlan':
        v.desiredCurvature = .0002 if controls.CP.steerControlType == structs.CarParams.SteerControlType.angle else .00005
      elif name == 'vehicleParameters':
        v.stiffnessFactor, v.steerRatio = 1.0, controls.CP.steerRatio
      elif name == 'longitudinalPlan':
        v.aTarget = 0.8
      elif name == 'lateralDelay':
        v.lateralDelay = 0.1
      values.append(e)
  return values


def run_case(stock, scenario='nominal', *, aol=True, candidate=CAR.TOYOTA_RAV4H, bridge=None, input_clock=False, brake_stop_check_start=350,
             sdsu_words=(0x9049, 0x9249), alpha_long=False, disable_setting=None, physical_frames=None, angle=False,
             firmware_override=None, assert_hybrid_release=False, sdsu_mirror=False, on_sample=None, camera_messages=False, filter_advert=False,
             brake_pause_mps=None, native_rx_expected=None, on_state=None, fingerprint_frames=None, manual_selection=False, native_tx_expected=None,
             manual_filter_length=None, manual_bypass=False, bypass_owner=False, manual_pedal=False, auto_hold=False):
  SYNTHETIC_LAST.clear()
  NATIVE_LAST.clear()
  safety = libsafety_py.libsafety
  trace, cc = [], structs.CarControl().as_reader()
  with OpenpilotPrefix(), ExitStack() as stack:
    params = Params()
    for k, v in (
      ('OpenpilotEnabledToggle', True),
      ('SafeMode', False),
      ('AlwaysOnLateral', aol),
      ('DisableOpenpilotLongitudinal', stock if disable_setting is None else disable_setting),
      ('AlphaLongitudinalEnabled', alpha_long),
      ('ToyotaAutoHold', auto_hold),
    ):
      params.put_bool(k, v, block=True)
    if brake_pause_mps is not None:
      params.put('AolBrakePauseSpeedMps', float(brake_pause_mps), block=True)
    fw = firmware(candidate) if firmware_override is None else firmware_override
    if manual_selection:
      if manual_pedal:
        from opendbc.car.toyota import pedal_composite
        assert candidate in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT, CAR.TOYOTA_MATRIX_RETROFIT)
        assert fingerprint_frames is not None
        acquired_fp = gen_empty_fingerprint()
        for address, data, bus in fingerprint_frames:
          acquired_fp[bus][address] = len(data)
        assert acquired_fp[0].get(0x201) == 6
        assert pedal_composite.hardware_supported(candidate, acquired_fp, fw)
      elif manual_bypass:
        assert fingerprint_frames is not None
        assert candidate in (CAR.TOYOTA_MATRIX_RETROFIT, CAR.TOYOTA_PRIUS_RETROFIT) and bypass_owner
        assert any(bus == 2 and address in (0x343, 0x4CB) and len(data) == 8 for address, data, bus in fingerprint_frames)
        assert not any(address in (0x201, 0x2FF, 0x2AA) for address, _, _ in fingerprint_frames)
        if candidate == CAR.TOYOTA_MATRIX_RETROFIT:
          assert not fw and firmware_override == []
        else:
          assert any(f.ecu == structs.CarParams.Ecu.eps and f.address == 0x7A1 and f.bus == 0 and bytes(f.fwVersion) == b'8965B47070' + bytes(6) for f in fw)
      elif manual_filter_length is not None:
        if candidate == CAR.TOYOTA_MATRIX_RETROFIT:
          assert manual_filter_length in (4, 8) and not fw and firmware_override == []
        else:
          assert candidate in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT) and manual_filter_length == 4
          observed_eps = [entry for entry in fw if entry.ecu == structs.CarParams.Ecu.eps]
          assert len(observed_eps) == 1 and observed_eps[0].address == 0x7A1 and observed_eps[0].bus == 0
          assert bytes(observed_eps[0].fwVersion) == b'8965B47070' + bytes(6)
          assert all(entry.brand == 'toyota' and not entry.logging and entry.bus == 0 for entry in fw)
        assert fingerprint_frames == [(0x2FF, bytes(manual_filter_length), 0)]
      else:
        assert stock and (fingerprint_frames == [] or candidate == CAR.TOYOTA_PRIUS_RETROFIT)
        if candidate == CAR.TOYOTA_MATRIX_RETROFIT:
          assert not fw and firmware_override == []
        else:
          assert candidate == CAR.TOYOTA_PRIUS_RETROFIT and len(fw) == 1
          assert fw[0].ecu == structs.CarParams.Ecu.eps and fw[0].address == 0x7A1 and fw[0].bus == 0
          assert bytes(fw[0].fwVersion) == b'8965B47070' + bytes(6)
    else:
      exact, found = match_fw_to_car(fw, '', log=False)
      assert exact and found == {candidate}
    fp = gen_empty_fingerprint()
    if fingerprint_frames is None:
      fp[0][0x2FF] = 4 if filter_advert else 8
    else:
      for address, data, bus in fingerprint_frames:
        fp[bus][address] = len(data)
    if sdsu_mirror:
      fp[2][0x2FF] = 8
    if camera_messages:
      fp[2].update({0x343: 8, 0x4CB: 8})
    source = structs.CarParams.FingerprintSource.fixed if manual_selection else structs.CarParams.FingerprintSource.fw
    observed = (candidate, fp, '0' * 17, fw, source, True)
    with (
      patch.object(car_helpers, 'fingerprint', return_value=observed),
      patch.object(card_module, 'can_comm_callbacks', return_value=(lambda *a: [], lambda x: None)),
      patch.object(card_module.messaging, 'recv_one_retry', return_value=messaging.new_message('can', 1)),
    ):
      host = card_module.Car()
    assert host.CP.openpilotLongitudinalControl == (not stock)
    assert host.CP.safetyConfigs[0].safetyParam == sdsu_words[stock]
    from opendbc.car.toyota.values import uses_toyota_auto_hold_aeb
    assert host.CP.alternativeExperience == (32 | ((256 if uses_toyota_auto_hold_aeb(host.CP) else 128) if auto_hold else 0))
    controls, sd = Controls(), sd_module.SelfdriveD(CP=host.CP)
    assert controls.CP.to_dict() == host.CP.to_dict()
    safety.init_tests()
    safety.set_alternative_experience(host.CP.alternativeExperience)
    assert safety.set_safety_hooks(host.CP.safetyConfigs[0].safetyModel.raw, host.CP.safetyConfigs[0].safetyParam) == 0
    safety.set_aol_test_heartbeat(True)
    native_globals = ctypes.CDLL(libsafety_py.libpath)
    if bridge is not None:
      bridge.reset_host()
    packer = CANPacker(DBC[host.CP.carFingerprint][Bus.pt])
    published, prev = {}, {}

    def capture(name, m):
      if isinstance(m, bytes):
        m = messaging.log_from_bytes(m)
      published[name] = m.as_reader() if hasattr(m, 'as_reader') else m

    stack.enter_context(patch.object(host.pm, 'send', side_effect=capture))
    stack.enter_context(patch.object(controls.pm, 'send', side_effect=capture))
    from openpilot.starpilot.ui.wheel_feature import WheelFeature
    from openpilot.starpilot.feature_capabilities import display_supported, longitudinal_supported

    wheel = WheelFeature(
      SimpleNamespace(
        params=params,
        vehicle_params=lambda: host.CP,
        configuration_vehicle=lambda: False,
        configuration_longitudinal=lambda: host.CP.openpilotLongitudinalControl,
        longitudinal_available=lambda: host.CP.openpilotLongitudinalControl,
        authority=lambda _: True,
      )
    )
    rows = {r.key: r for r in wheel.rows()}
    if candidate == CAR.TOYOTA_RAV4H and not bypass_owner:
      assert 'Bookmark' in rows['wheel:LKASButtonControl'].choices
      assert 'Toggle AOL' not in rows['wheel:LKASButtonControl'].choices
    assert not any('Cancel' in k for k in rows)
    main = rows['wheel:MainCruiseButtonControl']
    assert main.value == 'Factory cruise / AOL' and not main.available and not main.choices
    assert not wheel.choices('MainCruiseButtonControl')
    assert display_supported(host.CP) and longitudinal_supported(host.CP) == (not stock)
    params.put('CancelButtonControl', 9, block=True)  # Unsupported saved remap cannot steal MAIN/physical Cancel ownership.
    seen_active = positive_steering = positive_accel = False
    next_cycle = time.monotonic()
    clock = [time.monotonic_ns()]
    deterministic = input_clock or scenario in ('faults', 'bad_dlc')
    if deterministic:
      # Deterministic input-time vector for the three serially hosted daemon methods.
      # Keep the real-time nominal/physical/OFF receipts separate; no scheduling/road claim here.
      real_clock = time.clock_gettime_ns
      stack.enter_context(patch.object(time, 'monotonic_ns', side_effect=lambda: clock[0]))
      stack.enter_context(patch.object(time, 'monotonic', side_effect=lambda: clock[0] / 1e9))
      stack.enter_context(
        patch.object(time, 'clock_gettime_ns', side_effect=lambda c: clock[0] if c in (time.CLOCK_MONOTONIC, time.CLOCK_BOOTTIME) else real_clock(c))
      )
    try:
      duration = {
        'gas_steering': 401, 'hold': 501, 'nominal': 251, 'physical': 601, 'faults': 801, 'bad_dlc': 801,
        'off': 401, 'cold_low': 251, 'hybrid_stop': 251, 'main_lease': 501, 'identity_refusal': 501,
      }[scenario]
      for tick in range(1, duration):
        if deterministic:
          clock[0] += 10_000_000
        else:
          next_cycle += 0.01
          time.sleep(max(0.0, next_cycle - time.monotonic()))
        now = time.monotonic_ns()
        safety.set_timer((now // 1000) & 0xFFFFFFFF)
        fields = {'cruise': tick >= 150}
        if scenario == 'main_lease':
          fields.update(cruise=tick >= 150 and not 330 <= tick < 360, main=not 340 <= tick < 350)
        if scenario == 'hybrid_stop':
          fields.update(stopped=200 <= tick < 230)
        if scenario == 'cold_low':
          fields.update(cruise=False, stopped=True)
        if scenario == 'physical':
          fields.update(
            gas=250 <= tick < 270,
            brake=340 <= tick < 370,
            stopped=340 <= tick < 370,
            gear=8 if 410 <= tick < 420 else 16 if 420 <= tick < 430 else 0,
            cruise=tick >= 150 and not any(a <= tick < b for a, b in ((280, 290), (340, 370), (460, 470), (520, 540))),
          )
        elif scenario in ('faults', 'bad_dlc'):
          fields.update(
            eps=9 if 250 <= tick < 270 else 3 if 320 <= tick < 340 else 1,
            sdsu=2 if 460 <= tick < 470 else 7 if 560 <= tick < 580 else None if 630 <= tick < 665 else 0,
            main=not any(a <= tick < b for a, b in ((360, 370), (480, 490), (690, 700))),
            cruise=tick >= 150 and not any(a <= tick < b for a, b in ((360, 390), (480, 510), (690, 730))),
          )
        elif scenario == 'off' and tick == 250:
          params.put_bool('DisableOpenpilotLongitudinal', not stock, block=True)
        frames = (physical if physical_frames is None else physical_frames)(packer, **fields)
        if filter_advert:
          advertisements = [f for f in frames if f[0] == 0x2FF and len(f[1]) == 4]
          frames = [f for f in frames if f[0] != 0x2FF]
          frames += advertisements or [(0x2FF, bytes.fromhex('00000409'), 0)]
        if camera_messages:
          frames += [packer.make_can_msg('ACC_CONTROL', 2, {}), (0x4CB, bytes(8), 2)]
          assert safety.safety_fwd_hook(2, 0x343) == 0
          assert safety.safety_fwd_hook(2, 0x4CB) == 0
        if sdsu_mirror:
          # Bus2 remains active even while the required bus0 source disappears.
          # Its conflicting state7 must also never replace bus0 state0.
          frames.append(packer.make_can_msg('SDSU', 2, {'STATE': 0 if scenario in ('faults', 'bad_dlc') else 7}))
        if scenario == 'bad_dlc' and 630 <= tick < 665:
          malformed = packer.make_can_msg('SDSU', 0, {'STATE': 0})
          frames.append((malformed[0], malformed[1][:7], malformed[2]))
        for a, d, bus in frames:
          # USB TX receipts are delivered to host parsers, never native RX.
          if bus >= 128:
            continue
          accepted = bool(safety.safety_rx_hook(libsafety_py.make_CANPacket(a, bus, d)))
          expected = not (a == 0x2FF and bus == 0 and 0 < (d[2] >> 4) < 7)
          if native_rx_expected is not None:
            expected = native_rx_expected(a, d, bus, expected, host.CP)
          assert accepted == expected, (tick, hex(a), d.hex(), accepted, expected)
        safety.safety_tick()
        native = native_events(host, safety, now, sd.aol_session_id)
        deliver(host.sm, [*native, *prev.values()])
        batch = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        batch.logMonoTime = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        with patch.object(card_module.messaging, 'drain_sock_raw', return_value=[batch.to_bytes()]), patch.object(host.sm, 'update'):
          cs, radar = host.state_update()
        if on_state is not None:
          on_state(tick, host, cs)
        host.state_publish(cs, radar)
        state = dict(published)
        peers = selfdrive_inputs(sd, state, native, cc, host.CP, now)
        sd_events = {}
        with (
          patch.object(sd_module.messaging, 'recv_one', return_value=state['carState']),
          patch.object(sd_module.messaging, 'recv_one_or_none', return_value=None),
          patch.object(sd.sm, 'update', side_effect=lambda _, peers=peers: deliver(sd.sm, peers)),
          patch.object(sd.pm, 'send', side_effect=lambda n, m, sd_events=sd_events: sd_events.update({n: m.as_reader()})),
        ):
          sd.step()
        wanted = sd.aol_axis_decision
        if bridge is None:
          safety.aol_set_host_request(int(wanted.desired_lateral) | int(wanted.desired_longitudinal) << 1)
        else:
          axis = sd_events.get('aolAxisState', prev.get('aolAxisState'))
          intent_wire = state.get('aolIntentWire')
          axis_bytes = axis.as_builder().to_bytes() if axis is not None else b''
          intent_bytes = intent_wire.as_builder().to_bytes() if intent_wire is not None else b''
          capability = int(bool(ctypes.c_void_p.in_dll(native_globals, 'aol_policy').value))
          capability |= 2 * int(ctypes.c_bool.in_dll(native_globals, 'aol_optional_release_capable').value)
          model = ctypes.c_uint16.in_dll(native_globals, 'current_safety_mode').value
          word = ctypes.c_uint16.in_dll(native_globals, 'current_safety_param').value
          state_event = sd_events.get('selfdriveState', prev.get('selfdriveState'))
          packed = bridge.plan_host(model, word, safety.aol_get_request_mask(), safety.aol_get_permission_mask(),
            time.monotonic_ns(), axis_bytes, len(axis_bytes), intent_bytes, len(intent_bytes), bool(state_event.selfdriveState.enabled), capability)
          safety.aol_set_host_request(packed & 0xFF)
          safety.set_aol_test_heartbeat(bool(packed & 0x100))
          bridge.complete_host(safety.aol_get_request_mask(), safety.aol_get_permission_mask())
        native = native_events(host, safety, time.monotonic_ns(), sd.aol_session_id)
        current_sd = dict(prev)
        current_sd.update(sd_events)
        deliver(controls.sm, control_inputs(controls, published, state, current_sd, native, time.monotonic_ns()))
        command, lac_log = controls.state_control()
        controls.publish(command, lac_log)
        cc_event = published['carControl']
        cc = cc_event.carControl
        deliver(host.sm, [cc_event, *native, *sd_events.values()])
        # A valid previous publication is not a new producer event. Card may
        # deliberately emit nothing when the current control message is invalid.
        previous_sendcan = published.pop('sendcan', None)
        host.controls_update(cs, cc)
        outgoing = published.get('sendcan')
        if outgoing is None:
          assert not cs.canValid or cs.canTimeout
          assert not host.sm.valid['carControl']
          assert not cc.latActive and not cc.longActive
          assert safety.aol_get_request_mask() == safety.aol_get_permission_mask() == 0
          outgoing_frames = ()
        else:
          outgoing_frames = outgoing.sendcan
          if not outgoing.valid:
            assert not outgoing_frames and not cs.canValid, ('startup must stay neutral until real CAN health', tick, cs.canValid)
        native_tx_accepted = native_tx_rejected = 0
        for f in outgoing_frames:
          before_tx = {
            'nativePermission': int(safety.aol_get_permission_mask()), 'nativeRequest': int(safety.aol_get_request_mask()),
            'controlsAllowed': bool(safety.get_controls_allowed()), 'configValid': bool(safety.safety_config_valid()),
            'heartbeatEngaged': ctypes.c_bool.in_dll(native_globals, 'heartbeat_engaged').value,
            'globals': {name: ctypes.c_int.in_dll(native_globals, name).value for name in
                        ('desired_torque_last', 'rt_torque_last', 'valid_steer_req_count', 'invalid_steer_req_count')},
          }
          expected_tx = True if native_tx_expected is None else native_tx_expected(tick, f, host.CP, safety)
          assert type(expected_tx) is bool
          accepted_tx = bool(safety.safety_tx_hook(libsafety_py.make_CANPacket(f.address, f.src, bytes(f.dat))))
          native_tx_accepted += int(accepted_tx)
          native_tx_rejected += int(not accepted_tx)
          if accepted_tx != expected_tx:
            refusal = {'tick': tick, 'candidate': str(candidate), 'scenario': scenario, 'address': f.address,
                       'data': bytes(f.dat).hex(), 'bus': f.src, 'outgoingValid': bool(outgoing.valid),
                       'CP': host.CP.to_dict(), 'CS': cs.to_dict(), 'CC': cc.to_dict(), 'beforeNativeTX': before_tx,
                       'controllerLastTorque': host.CI.CC.last_torque,
                       'nativeAfterPermission': int(safety.aol_get_permission_mask()),
                       'axis': {'lateral': wanted.desired_lateral, 'longitudinal': wanted.desired_longitudinal},
                       'state': str(sd.state_machine.state), 'fault': sd.aol_fault_reason}
            (OUT / 'matrix-native-tx-refusal.json').write_text(json.dumps(refusal, indent=2, default=str) + '\n')
          assert accepted_tx == expected_tx, (tick, hex(f.address), bytes(f.dat).hex(), accepted_tx, expected_tx)
          if f.address == 0x2E4:
            decoded = decode(host.CP, (f.address, bytes(f.dat), f.src), 'STEERING_LKA')
            positive_steering |= bool(decoded['STEER_REQUEST'] and decoded['STEER_TORQUE_CMD'])
          elif f.address == 0x191 and angle:
            decoded = decode(host.CP, (f.address, bytes(f.dat), f.src), 'STEERING_LTA')
            positive_steering |= bool(decoded['STEER_REQUEST'] and decoded['STEER_ANGLE_CMD'])
          elif f.address == 0x343:
            acc = decode(host.CP, (f.address, bytes(f.dat), f.src), 'ACC_CONTROL')
            positive_accel |= acc['ACCEL_CMD'] > 0
            if assert_hybrid_release and cc.longActive:
              assert host.CP.flags & ToyotaFlags.HYBRID
              assert acc['RELEASE_STANDSTILL'] == 1, ('active hybrid resume behavior changed', tick, acc)
        trace.append(
          {
            'tick': tick,
            'canValid': cs.canValid,
            'freshSendcan': outgoing is not None,
            'actualSendcanFrames': len(outgoing_frames),
            'nativeAcceptedFrames': native_tx_accepted,
            'nativeRejectedFrames': native_tx_rejected,
            'previousSendcanMonoTime': int(previous_sendcan.logMonoTime) if previous_sendcan is not None else 0,
            'previousSendcanSha256': hashlib.sha256(previous_sendcan.as_builder().to_bytes()).hexdigest() if previous_sendcan is not None else '',
            'lat': cc.latActive,
            'long': cc.longActive,
            'sd': str(sd.state_machine.state),
            'events': [str(x) for x in sd.events.names],
            'fault': sd.aol_fault_reason,
            'nativeRequest': int(safety.aol_get_request_mask()),
            'nativePermission': int(safety.aol_get_permission_mask()),
            'axes': [wanted.desired_lateral, wanted.desired_longitudinal],
            'smGood': sd.sm.all_checks(),
            'initialized': sd.initialized,
            'sourceAgeAtEndNs': time.monotonic_ns() - int(state['carState'].logMonoTime),
            'nativeAgeAtEndNs': time.monotonic_ns() - int(native[0].logMonoTime),
            'authorityLost': sd.aol_authority_lost,
            'invalid': [k for k, v in sd.sm.valid.items() if not v],
            'notAlive': [k for k, v in sd.sm.alive.items() if not v],
            'notFreq': [k for k, v in sd.sm.freq_ok.items() if not v],
          }
        )
        nominal = tick >= 220 and (
          scenario == 'gas_steering' and (tick < 301 or tick >= 360)
          or
          scenario in ('nominal', 'cold_low', 'hybrid_stop') or scenario == 'hold' and tick < 250
          or scenario == 'identity_refusal' and tick < 250
          or scenario == 'main_lease' and (tick < 250 or tick >= 480)
          or scenario == 'off'
          and tick < 250
          or scenario == 'physical'
          and any(a <= tick < b for a, b in ((220, 240), (320, 330), (390, 400), (500, 510), (570, 601)))
          or scenario in ('faults', 'bad_dlc')
          and any(a <= tick < b for a, b in ((220, 240), (420, 430), (540, 550), (760, 801)))
        )
        if nominal:
          assert cs.canValid and not cs.canTimeout
          assert sd.initialized and host.ci_initialized and params.get_bool('ControlsReady')
          assert sd.sm.all_checks(), ('actual consumer source health failed', trace[-1])
          assert cc.latActive, trace[-1]
          assert cc.longActive == (not stock and scenario != 'cold_low'), trace[-1]
          seen_active = True
        if scenario == 'identity_refusal' and tick >= 270:
          assert not cc.latActive and not cc.longActive and safety.aol_get_permission_mask() == 0, trace[-1]
        if scenario == 'physical':
          if 340 <= tick < 366:
            assert not cc.longActive, trace[-1]
          if any(a <= tick < b for a, b in ((260, 266), (brake_stop_check_start, 366), (445, 456), (530, 536))):
            assert cc.latActive and not cc.longActive, trace[-1]
          if 415 <= tick < 430:
            assert not cc.latActive and not cc.longActive, trace[-1]
        if scenario in ('faults', 'bad_dlc'):
          if any(a <= tick < b for a, b in ((260, 269), (330, 339), (465, 469), (650, 661))):
            assert not cc.latActive and not cc.longActive, trace[-1]
          if 350 <= tick < 356:
            assert cc.latActive and not cc.longActive, ('cleared EPS must not automatically reengage ordinary LONG', trace[-1])
          if 290 <= tick < 300:
            assert cc.latActive, ('temporary steering fault did not recover', trace[-1])
          if 570 <= tick < 576:
            assert cs.stockAeb and not cs.accFaulted and safety.safety_config_valid()
            # Existing AOL no-entry policy withdraws requested axes during stock AEB.
            assert not cc.latActive and not cc.longActive, trace[-1]
        if scenario == 'off' and tick >= 310:
          if stock:
            assert not host.CP.openpilotLongitudinalControl and not cc.longActive
            assert host.CP.safetyConfigs[0].safetyParam == sdsu_words[1]
          else:
            from opendbc.car.toyota import pedal_composite
            preference = (host.CI.CC.pedal_input if pedal_composite.configured(host.CP) else
              host.CI.CC.bypass_input if bypass_owner else host.CI.CC.prius_filter_input if candidate == CAR.TOYOTA_PRIUS else (
              host.CI.CC.advert_filter_input if filter_advert or fingerprint_frames is not None and any(
                address == 0x2AA for address, _, _ in fingerprint_frames) else host.CI.CC.rav4h_filter_input))
            assert preference is not None and not preference.update(time.monotonic_ns())
            for f in outgoing_frames:
              if f.address == 0x343:
                acc = decode(host.CP, (f.address, bytes(f.dat), f.src), 'ACC_CONTROL')
                assert acc['ACCEL_CMD'] == 0 and acc['CANCEL_REQ'] and not acc['RELEASE_STANDSTILL']

        if on_sample is not None:
          on_sample(tick, host, cs, cc, safety, native)
        prev = current_sd
      assert seen_active
      if scenario != 'cold_low':
        assert positive_steering
      assert positive_accel == (not stock and scenario != 'cold_low')
    finally:
      trace_name = ('actual-' + str(candidate) + '-' + scenario + ('-stock-trace.json' if stock else '-long-trace.json'))
      (OUT / trace_name).write_text(json.dumps(trace, indent=2) + '\n')
      safety.set_alternative_experience(0)
      safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      host.vehicle_startup.close()


@pytest.mark.parametrize('stock', (False, True))
def test_actual_matched_factory_card_selfdrived_controls_native(stock):
  run_case(stock)


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('scenario', ('physical', 'faults', 'bad_dlc'))
def test_actual_physical_fault_recovery_paths(scenario, stock):
  run_case(stock, scenario)


@pytest.mark.parametrize('stock', (False, True))
def test_actual_saved_runtime_long_off_preserves_owner(stock):
  run_case(stock, 'off')


@pytest.mark.parametrize('stock', (False, True))
def test_actual_ordinary_cruise_without_aol(stock):
  run_case(stock, aol=False)
