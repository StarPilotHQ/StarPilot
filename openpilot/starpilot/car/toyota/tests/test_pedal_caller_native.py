"""Actual Corolla/RAV4 camera-long pedal factory/Card/SelfdriveD/Controls/CI/native join.

Synthetic boundaries: firmware acquisition, CAN/perception/process producers and
USB/axis transport. No CP/CS fields, native permission, health/frequency flags, or initialized/ControlsReady
are overwritten. CI uses one explicit shared input clock plus the native test timer. The retained
qualification packet separately records elapsed-host-time runs and a correct
fail-safe withdrawal after a 45 ms scheduling gap. Neither establishes
installed-hardware scheduling or timing acceptance.
"""

import json
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
from opendbc.car.toyota.values import CAR, DBC
from opendbc.car.toyota import pedal
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

OUT = Path(tempfile.mkdtemp(prefix='toyota-pedal-caller-traces-'))
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


def firmware(candidate=CAR.TOYOTA_COROLLA_TSS2):
  # Manual Matrix has no acquired firmware catalog; keep its fixed selection explicit.
  if candidate == CAR.TOYOTA_MATRIX_RETROFIT:
    return []
  return [
    structs.CarParams.CarFw(ecu=e, address=a, subAddress=s or 0, fwVersion=v[0], brand='toyota', bus=0)
    for (e, a, s), v in FW_VERSIONS[candidate].items()
  ]


def physical(packer, *, dsu_main=False, cruise, main=True, gear=0, brake=False, gas=False, eps=1, sdsu=0, stopped=False):
  frames = [
    packer.make_can_msg(name, 0, {})
    for name in ('VSC1S07', 'BODY_CONTROL_STATE_2', 'ESP_CONTROL', 'BLINKERS_STATE', 'PRE_COLLISION', 'LIGHT_STALK')
  ]
  frames += [
    packer.make_can_msg('BODY_CONTROL_STATE', 0, {'SEATBELT_DRIVER_UNLATCHED': 0}),
    packer.make_can_msg('BRAKE_MODULE', 0, {'BRAKE_PRESSED': brake}),
    packer.make_can_msg('GEAR_PACKET', 0, {'GEAR': gear}),
    packer.make_can_msg('WHEEL_SPEEDS', 0, {f'WHEEL_SPEED_{w}': 0.0 if stopped else 72.0 if dsu_main else 18.0 for w in ('FL', 'FR', 'RL', 'RR')}),
    packer.make_can_msg('PCM_CRUISE', 0, {'CRUISE_ACTIVE': cruise, 'GAS_RELEASED': not gas, 'CRUISE_STATE': 7 if cruise and stopped else 8 if cruise else 0}),
    packer.make_can_msg('PCM_CRUISE_2', 0, {'MAIN_ON': main, 'SET_SPEED': 90}),
    packer.make_can_msg('STEER_ANGLE_SENSOR', 0, {}),
    packer.make_can_msg('STEER_TORQUE_SENSOR', 0, {}),
    packer.make_can_msg('EPS_STATUS', 0, {'LKA_STATE': eps}),
    packer.make_can_msg('LKAS_HUD', 2, {}),
    packer.make_can_msg('RSA1', 2, {}),
  ]
  if dsu_main:
    frames += [packer.make_can_msg('PCM_CRUISE_ALT', 0, {}), packer.make_can_msg('DSU_CRUISE', 0, {'MAIN_ON': main, 'SET_SPEED': 90})]
    # This source must not revive or withdraw DSU-owned MAIN.
    frames = [f for f in frames if f[0] != 0x1D3] + [packer.make_can_msg('PCM_CRUISE_2', 0, {'MAIN_ON': not main})]
  else:
    frames.append(packer.make_can_msg('PCM_CRUISE_SM', 0, {}))
  frames += [packer.make_can_msg('ACC_CONTROL', 2, {'ACC_TYPE': 1}), packer.make_can_msg('PCS_HUD', 2, {}), packer.make_can_msg('PRE_COLLISION', 2, {})]
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


def control_inputs(controls, published, state, sd_events, native, now, planned_curvature=0.):
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
      elif name == 'lateralManeuverPlan' and (controls.CP.carFingerprint == CAR.LEXUS_IS or planned_curvature):
        v.desiredCurvature = planned_curvature or .00005  # Synthetic plan exercises nonzero encoded steering.
      elif name == 'vehicleParameters':
        v.stiffnessFactor, v.steerRatio = 1.0, controls.CP.steerRatio
      elif name == 'longitudinalPlan':
        v.aTarget = 0.8
      elif name == 'lateralDelay':
        v.lateralDelay = 0.1
      values.append(e)
  return values


def run_case(stock, scenario='nominal', *, aol=True, candidate=CAR.TOYOTA_COROLLA_TSS2):
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
      ('DisableOpenpilotLongitudinal', stock),
      ('ToyotaAutoHold', False),
    ):
      params.put_bool(k, v, block=True)
    fw = firmware(candidate)
    exact, found = match_fw_to_car(fw, '', log=False)
    assert exact and found == {candidate}
    fp = gen_empty_fingerprint()
    fp[0][0x201] = 6
    observed = (candidate, fp, '0' * 17, fw, structs.CarParams.FingerprintSource.fw, True)
    with (
      patch.object(car_helpers, 'fingerprint', return_value=observed),
      patch.object(card_module, 'can_comm_callbacks', return_value=(lambda *a: [], lambda x: None)),
      patch.object(card_module.messaging, 'recv_one_retry', return_value=messaging.new_message('can', 1)),
    ):
      host = card_module.Car()
    assert host.CP.openpilotLongitudinalControl == (not stock)
    dsu_main = candidate == CAR.LEXUS_IS
    assert host.CP.safetyConfigs[0].safetyParam == (0x424D if dsu_main else 0x2249 if stock else 0x2049)
    assert host.CP.alternativeExperience == 32
    controls, sd = Controls(), sd_module.SelfdriveD(CP=host.CP)
    assert controls.CP.to_dict() == host.CP.to_dict()
    safety.init_tests()
    safety.set_alternative_experience(host.CP.alternativeExperience)
    assert safety.set_safety_hooks(host.CP.safetyConfigs[0].safetyModel.raw, host.CP.safetyConfigs[0].safetyParam) == 0
    safety.set_aol_test_heartbeat(True)
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
    if dsu_main:
      assert 'wheel:LKASButtonControl' not in rows
    else:
      assert 'Bookmark' in rows['wheel:LKASButtonControl'].choices
      assert 'Toggle AOL' not in rows['wheel:LKASButtonControl'].choices
    assert not any('Cancel' in k for k in rows)
    main = rows['wheel:MainCruiseButtonControl']
    assert main.value == 'Factory cruise / AOL' and not main.available and not main.choices
    assert not wheel.choices('MainCruiseButtonControl')
    assert display_supported(host.CP) and longitudinal_supported(host.CP) == (not stock)
    params.put('CancelButtonControl', 9, block=True)  # Unsupported saved remap cannot steal MAIN/physical Cancel ownership.
    seen_active = False
    seen_pedal = False
    seen_steer = False
    clock = [time.monotonic_ns()]
    # Deterministic input-time vector for the three serially hosted daemon methods.
    # Keep the real-time nominal/physical/OFF receipts separate; no scheduling/road claim here.
    real_clock = time.clock_gettime_ns
    stack.enter_context(patch.object(time, 'monotonic_ns', side_effect=lambda: clock[0]))
    stack.enter_context(patch.object(time, 'monotonic', side_effect=lambda: clock[0] / 1e9))
    stack.enter_context(
      patch.object(time, 'clock_gettime_ns', side_effect=lambda c: clock[0] if c in (time.CLOCK_MONOTONIC, time.CLOCK_BOOTTIME) else real_clock(c))
    )
    try:
      steps = {'nominal': 251, 'physical': 601, 'faults': 801, 'bad_dlc': 801,
               'main_dropout': 801, 'main_bad_crc': 801, 'main_bad_dlc': 801, 'off': 401}
      for tick in range(1, steps[scenario]):
        clock[0] += 10_000_000
        now = time.monotonic_ns()
        safety.set_timer((now // 1000) & 0xFFFFFFFF)
        fields = {'cruise': tick >= 150}
        if scenario == 'physical':
          fields.update(
            gas=250 <= tick < 270,
            brake=340 <= tick < 370,
            stopped=340 <= tick < 370,
            gear=8 if 410 <= tick < 420 else 16 if 420 <= tick < 430 else 0,
            cruise=tick >= 150 and not any(a <= tick < b for a, b in ((280, 290), (340, 370), (460, 470), (520, 540))),
          )
        elif scenario in ('faults', 'bad_dlc', 'main_dropout', 'main_bad_crc', 'main_bad_dlc'):
          fields.update(
            eps=9 if 250 <= tick < 270 else 3 if 320 <= tick < 340 else 1,
            sdsu=2 if 460 <= tick < 470 else None if 630 <= tick < 665 else 0,
            main=not any(a <= tick < b for a, b in ((360, 370), (480, 490), (690, 700))),
            cruise=tick >= 150 and not any(a <= tick < b for a, b in ((360, 390), (480, 510), (690, 730))),
          )
        elif scenario == 'off' and tick == 250:
          params.put_bool('DisableOpenpilotLongitudinal', not stock, block=True)
        if dsu_main and fields.get('sdsu'):
          fields['eps'] = 9
        main_fault = scenario.startswith('main_') and fields.get('sdsu', 0) is None
        if main_fault:
          fields['sdsu'] = 0  # Keep pedal source healthy while only MAIN disappears.
        frames = physical(packer, dsu_main=dsu_main, **fields)
        if scenario.startswith('main_') and (tick % 3 != 0 or main_fault and scenario == 'main_dropout'):
          frames = [f for f in frames if f[0] != 0x1D3]  # Real PCM MAIN cadence, about 33 Hz.
        if main_fault and scenario in ('main_bad_crc', 'main_bad_dlc'):
          changed = []
          for a, d, b in frames:
            if a == 0x1D3:
              d = d[:-1] + bytes([d[-1] ^ 1]) if scenario == 'main_bad_crc' else d[:-1]
            changed.append((a, d, b))
          frames = changed
        if dsu_main and fields.get('sdsu', 0) is None:
          if scenario == 'bad_dlc':
            frames = [(a, d + bytes(1) if a == 0x365 else d, b) for a, d, b in frames]
          else:
            frames = [f for f in frames if f[0] != 0x365]
        if not dsu_main and fields.get('sdsu', 0) is not None:
          sensor = packer.make_can_msg(
            'GAS_SENSOR',
            0,
            {
              'INTERCEPTOR_GAS': 806 if fields.get('gas') else 0,
              'INTERCEPTOR_GAS2': 806 if fields.get('gas') else 0,
              'STATE': int(fields.get('sdsu') or 0),
              'COUNTER_PEDAL': tick & 15,
            },
          )
          a, data, bus = sensor
          data = bytearray(data)
          data[-1] = pedal.checksum(data)
          frames.append((a, bytes(data), bus))
        for a, d, bus in frames:
          accepted = bool(safety.safety_rx_hook(libsafety_py.make_CANPacket(a, bus, d)))
          assert accepted == (not (a == 0x201 and (d[4] >> 4) != 0)), (tick, hex(a), d.hex(), accepted)
        safety.safety_tick()
        native = native_events(host, safety, now, sd.aol_session_id)
        deliver(host.sm, [*native, *prev.values()])
        batch = messaging.log_from_bytes(card_module.can_list_to_can_capnp(frames)).as_builder()
        batch.logMonoTime = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        with patch.object(card_module.messaging, 'drain_sock_raw', return_value=[batch.to_bytes()]), patch.object(host.sm, 'update'):
          cs, radar = host.state_update()
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
        safety.aol_set_host_request(int(wanted.desired_lateral) | int(wanted.desired_longitudinal) << 1)
        native = native_events(host, safety, time.monotonic_ns(), sd.aol_session_id)
        current_sd = dict(prev)
        current_sd.update(sd_events)
        deliver(controls.sm, control_inputs(controls, published, state, current_sd, native, time.monotonic_ns(), .001 if scenario.startswith('main_') else 0.))
        command, lac_log = controls.state_control()
        controls.publish(command, lac_log)
        cc_event = published['carControl']
        cc = cc_event.carControl
        deliver(host.sm, [cc_event, *native, *sd_events.values()])
        host.controls_update(cs, cc)
        outgoing = published.get('sendcan')
        assert outgoing is not None
        if not outgoing.valid:
          assert not outgoing.sendcan and not cs.canValid, ('startup must stay neutral until real CAN health', tick, cs.canValid)
        for f in outgoing.sendcan:
          assert safety.safety_tx_hook(libsafety_py.make_CANPacket(f.address, f.src, bytes(f.dat))), (tick, hex(f.address), bytes(f.dat).hex())
        trace.append(
          {
            'tick': tick,
            'canValid': cs.canValid,
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
          scenario == 'nominal'
          or scenario == 'off'
          and tick < 250
          or scenario == 'physical'
          and any(a <= tick < b for a, b in ((220, 240), (320, 330), (390, 400), (500, 510), (570, 601)))
          or scenario in ('faults', 'bad_dlc', 'main_dropout', 'main_bad_crc', 'main_bad_dlc')
          and any(a <= tick < b for a, b in ((220, 240), (420, 430), (540, 550), (760, 801)))
        )
        if nominal:
          assert cs.canValid and not cs.canTimeout
          assert sd.initialized and host.ci_initialized and params.get_bool('ControlsReady')
          assert sd.sm.all_checks(), ('actual consumer source health failed', trace[-1])
          assert cc.latActive, trace[-1]
          assert cc.longActive == (not stock), trace[-1]
          seen_active = True
        if scenario == 'physical':
          if any(a <= tick < b for a, b in ((260, 266), (350, 366), (445, 456), (530, 536))):
            assert cc.latActive and not cc.longActive, trace[-1]
          if 415 <= tick < 430:
            assert not cc.latActive and not cc.longActive, trace[-1]
        if scenario in ('faults', 'bad_dlc', 'main_dropout', 'main_bad_crc', 'main_bad_dlc'):
          dropout = ((635, 665) if scenario == 'bad_dlc' else (662, 665)) if dsu_main else (650, 661)
          if scenario == 'main_dropout':
            dropout = (662, 665)
          elif scenario.startswith('main_'):
            dropout = (635, 665)
          if (dsu_main and scenario != 'bad_dlc' or scenario == 'main_dropout') and 650 <= tick < 656:
            assert cc.latActive, ('DSU MAIN is still inside its existing 303 ms lease', trace[-1])
          if any(a <= tick < b for a, b in ((260, 269), (330, 339), (465, 469), dropout)):
            assert not cc.latActive and not cc.longActive, trace[-1]
          if 350 <= tick < 356:
            assert cc.latActive and not cc.longActive, ('cleared EPS must not automatically reengage ordinary LONG', trace[-1])
          if 290 <= tick < 300:
            assert cc.latActive, ('temporary steering fault did not recover', trace[-1])
        if scenario == 'off' and tick >= 310:
          if stock:
            assert not host.CP.openpilotLongitudinalControl and not cc.longActive
            assert host.CP.safetyConfigs[0].safetyParam == (0x424D if dsu_main else 0x2249)
          else:
            assert not host.CI.CC.pedal_input.update(time.monotonic_ns())
            for f in outgoing.sendcan:
              if f.address == 0x343:
                acc = decode(host.CP, (f.address, bytes(f.dat), f.src), 'ACC_CONTROL')
                assert acc['ACCEL_CMD'] == 0 and acc['CANCEL_REQ'] and not acc['RELEASE_STANDSTILL']

        for frame in outgoing.sendcan:
          if (dsu_main or scenario.startswith('main_')) and frame.address == 0x2E4:
            steer = decode(host.CP, (frame.address, bytes(frame.dat), frame.src), 'STEERING_LKA')
            if steer['STEER_TORQUE_CMD'] != 0:
              assert cc.latActive and steer['STEER_REQUEST']
              seen_steer = True
          if frame.address == 0x200:
            data = bytes(frame.dat)
            assert len(data) == 6 and data[5] == pedal.checksum(data)
            active = bool(data[4] & 0x80)
            if active:
              assert not stock and cc.longActive and not cs.gasPressed and not cs.brakePressed
              seen_pedal = True
            else:
              assert data[:4] == bytes(4)
        prev = current_sd
      assert seen_active
      assert seen_pedal == (not stock)
      if dsu_main or scenario.startswith('main_'):
        assert seen_steer, "Actual nonzero steering must reach the fresh native TX hook"
    finally:
      (OUT / (str(candidate) + '-actual-' + scenario + ('-stock-trace.json' if stock else '-long-trace.json'))).write_text(json.dumps(trace, indent=2) + '\n')
      safety.set_alternative_experience(0)
      safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)
      host.vehicle_startup.close()


@pytest.fixture(scope='module', params=(CAR.TOYOTA_COROLLA_TSS2, CAR.TOYOTA_RAV4_TSS2))
def pedal_candidate(request):
  return request.param


@pytest.mark.parametrize('stock', (False, True))
def test_actual_matched_factory_card_selfdrived_controls_native(stock, pedal_candidate):
  run_case(stock, candidate=pedal_candidate)


@pytest.mark.parametrize('stock', (False, True))
@pytest.mark.parametrize('scenario', ('physical', 'faults'))
def test_actual_physical_fault_recovery_paths(scenario, stock, pedal_candidate):
  run_case(stock, scenario, candidate=pedal_candidate)


@pytest.mark.parametrize('stock', (False, True))
def test_actual_saved_runtime_long_off_preserves_owner(stock, pedal_candidate):
  run_case(stock, 'off', candidate=pedal_candidate)


@pytest.mark.parametrize('stock', (False, True))
def test_actual_ordinary_cruise_without_aol(stock, pedal_candidate):
  run_case(stock, aol=False, candidate=pedal_candidate)
