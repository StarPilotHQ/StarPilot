"""Parity between the panda TI2 health model and GEN2 CarState.

Panda (mazda.h + mazda_stock_aol.h) and CarState.update_gen2 each decide, from
the same 0x24B TI_FEEDBACK stream, whether a TI2 condition is a fault the driver
must re-arm after, or a pause the always-on-lateral (AOL) session survives. If
the two disagree, AOL breaks in one of two ways:

  * Panda withholds lateral permission while CarState reports no steering
    restriction. The host then judges the denial a native rejection
    (starpilot/aol/vehicle.py native_latch_rejected, stamped with the sample's
    observedMonoTime) and drops its arming, so the driver must press MAIN again.
  * CarState reports only a temporary restriction while panda has revoked its
    AOL token. The host keeps believing it is armed, but panda never grants
    lateral again until a new physical gesture.

This test feeds identical frames with identical timestamps to libsafety and to a
real CarState. On every tick carrying a 0x24B frame it requires the two health
models to agree (proven health; lateral paused) and CarState's reported faults
to be consistent with panda. It also models the host arming rules and requires
the host's final armed state to equal panda's final lateral permission.

Host model (mirrors starpilot/aol/intent.py and starpilot/car/mazda/aol.py):
  * arming succeeds only if CarState reports no permanent fault;
  * a permanent fault disarms (intent.py update(): _fault_inhibit);
  * a panda denial disarms unless CarState reports a steering fault when it is
    judged. A native sample may be judged up to SAFETY_MAX_AGE_NS after it was
    observed, so a denial seen within that window counts as well.
"""
import unittest

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.mazda.carstate import CarState
from opendbc.car.mazda.values import CAR, TI2_HEALTH, MazdaSafetyFlags
from opendbc.can.dbc import DBC
from opendbc.safety.tests.common import CANPackerSafety
from opendbc.safety.tests.libsafety import libsafety_py

GEN2 = int(MazdaSafetyFlags.GEN2)
STEP_US = 10_000
# starpilot/aol/runtime.py SAFETY_MAX_AGE_NS: oldest native sample still judged.
SAFETY_MAX_AGE_NS = 200_000_000
RUN = (3, 3, 3, 3)
CLEAN = (0, 0, 0, 0)
ALL_02 = (2, 2, 2, 2)
# Long enough for 10 clean frames, the CarState release grace, and SAFETY_MAX_AGE_NS.
TAIL_STEPS = 80


def ti(viol=CLEAN, states=RUN, n=1):
  return [('ti', viol, states)] * n


def gap(steps):
  return [('gap', None, None)] * steps


def arm(viol=CLEAN, states=RUN):
  return [('arm', viol, states)]


def seconds(s):
  return round(s * 1_000_000 / STEP_US)


# Each scenario starts from a freshly initialised panda and CarState.
SCENARIOS = {
  # Normal AOL session with no TI2 event.
  'clean_session': ti(n=15) + arm() + ti(n=50),
  # 0x02 events on an armed session.
  'pause_all_cpus': ti(n=15) + arm() + ti(ALL_02, n=seconds(2)),
  'pause_some_cpus': ti(n=15) + arm() + ti((0, 2, 0, 2), n=seconds(1)),
  'pause_18s_like_captures': ti(n=15) + arm() + ti(ALL_02, n=seconds(18)),
  'pause_twice_with_full_recovery': ti(n=15) + arm() + ti(ALL_02, n=100) + ti(n=40) + ti(ALL_02, n=100),
  'pause_then_short_recovery_then_pause': ti(n=15) + arm() + ti(ALL_02, n=100) + ti(n=3) + ti(ALL_02, n=100),
  'pause_one_clean_frame_short_of_recovery': ti(n=15) + arm() + ti(ALL_02, n=50) + ti(n=9) + ti(ALL_02, n=50),
  'pause_outlasts_cap': ti(n=15) + arm() + ti(ALL_02, n=seconds(31)),
  'pause_flicker_outlasts_cap': (ti(n=15) + arm() + ti(ALL_02, n=seconds(25)) + ti(n=3) +
                                 ti(ALL_02, n=seconds(6))),
  # 0x02 that is not a pause.
  'mixed_codes': ti(n=15) + arm() + ti((2, 1, 0, 0), n=20),
  'pause_with_cpu_not_in_run': ti(n=15) + arm() + ti(ALL_02, (3, 3, 2, 3), n=20),
  'real_fault_then_one_clean_then_02': ti(n=15) + arm() + ti((1, 0, 0, 0)) + ti() + ti(ALL_02, n=50),
  'real_fault_then_recovery_then_02': ti(n=15) + arm() + ti((1, 0, 0, 0)) + ti(n=12) + ti(ALL_02, n=50),
  'powerup_13_then_02': ti(n=15) + arm() + ti((0x13,) * 4, n=5) + ti(n=3) + ti(ALL_02, n=50),
  'state_only_fault_then_02': ti(n=15) + arm() + ti(CLEAN, (2, 2, 2, 2), n=5) + ti(ALL_02, n=50),
  'feedback_gap_then_02': ti(n=15) + arm() + gap(30) + ti(ALL_02, n=50),
  'short_feedback_gap': ti(n=15) + arm() + gap(10) + ti(n=20),
  'feedback_gap_at_the_timeout': ti(n=15) + arm() + gap(19) + ti(n=20),
  'feedback_gap_just_past_the_timeout': ti(n=15) + arm() + gap(20) + ti(n=20),
  'state_only_fault_then_recovery': ti(n=15) + arm() + ti(CLEAN, (2, 3, 3, 3)) + ti(n=20),
  # Startup: TI2 has not yet proven health when 0x02 arrives.
  'startup_first_frame_02_arm_during': ti(ALL_02, n=30) + arm(ALL_02) + ti(ALL_02, n=30),
  'startup_9_clean_then_02_arm_during': ti(n=9) + ti(ALL_02, n=30) + arm(ALL_02) + ti(ALL_02, n=30),
  'startup_02_then_recovery_then_arm': ti(ALL_02, n=30) + ti(n=15) + arm() + ti(n=20),
}

# Whether the AOL session is still usable once the TI2 is clean again.
SURVIVES = {
  'clean_session': True,
  'pause_all_cpus': True,
  'pause_some_cpus': True,
  'pause_18s_like_captures': True,
  'pause_twice_with_full_recovery': True,
  'pause_then_short_recovery_then_pause': True,
  'pause_one_clean_frame_short_of_recovery': True,
  'pause_outlasts_cap': False,
  'pause_flicker_outlasts_cap': False,
  'mixed_codes': False,
  'pause_with_cpu_not_in_run': False,
  'real_fault_then_one_clean_then_02': False,
  'real_fault_then_recovery_then_02': False,
  'powerup_13_then_02': False,
  'state_only_fault_then_02': False,
  'feedback_gap_then_02': False,
  'short_feedback_gap': True,
  'feedback_gap_at_the_timeout': True,
  'feedback_gap_just_past_the_timeout': False,
  'state_only_fault_then_recovery': False,
  'startup_first_frame_02_arm_during': False,
  'startup_9_clean_then_02_arm_during': False,
  'startup_02_then_recovery_then_arm': True,
}
assert SURVIVES.keys() == SCENARIOS.keys()


class TestMazdaGen2TI2Parity(unittest.TestCase):
  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.safety.init_tests()
    self.safety.set_alternative_experience(32)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.mazda, GEN2)
    self.time = 1_000_000
    self.safety.set_timer(self.time)
    self.safety.set_aol_test_heartbeat(True)
    self.packer = CANPackerSafety('mazda_2019')
    self.dbc = DBC('mazda_2019')
    self.host_packer = CANPacker('mazda_2019')
    # Cause of panda's current unhealthy period: 'fault' (violation or feedback
    # gap), 'state' (non-RUN CPU state only), or None.
    self.unhealthy_cause = None
    self.feedback_first = getattr(self, 'feedback_first', False)

    self.CP = structs.CarParams(carFingerprint=CAR.MAZDA_3_2019, brand="mazda", flags=GEN2)
    self.car_state = CarState(self.CP)
    self.parsers = CarState.get_can_parsers(self.CP)

  def tearDown(self):
    self.safety.init_tests()
    self.safety.set_alternative_experience(0)
    self.safety.set_safety_hooks(structs.CarParams.SafetyModel.noOutput, 0)

  def _panda_packet(self, name, fields, bus):
    self.assertFalse(fields.keys() - self.dbc.name_to_msg[name].sigs.keys())
    return self.packer.make_can_msg_safety(name, bus, fields)

  @staticmethod
  def _ti_fields(viol, states):
    fields = {'STEER_TORQUE_SENSOR': 0}
    fields.update({f'CPU_{cpu}_VIOL': viol[cpu] for cpu in range(4)})
    fields.update({f'CPU_{cpu}_STATE': states[cpu] for cpu in range(4)})
    return fields

  def _step(self, kind, viol, states, main=True):
    """One 10 ms tick: the same frames to panda and to CarState."""
    self.time += STEP_US
    self.safety.set_timer(self.time)
    sources = [
      ('CRUZE_STATE', 0, {'CRZ_STATE': int(main)}),
      ('CRZ_BTNS', 0, {'RES': 0, 'SET_M': 0, 'CAN': 0}),
      ('GEAR', 2, {'GEAR': 4}),        # GEN2: 1..6 = D
      ('SPEED', 2, {'SPEED': 80}),      # moving: panda requires raw > 10
      ('ENGINE_DATA', 2, {'PEDAL_GAS': 0}),
      ('BRAKE_PEDAL_SLOW', 0, {'BRAKE_PEDAL_PRESSED': 0}),
    ]
    if kind != 'gap':
      # Feedback first makes the 0x24B frame itself the one that finds a feedback gap; later in the tick,
      # another frame finds it first. Both orders must give the same answers.
      sources.insert(0 if self.feedback_first else 2, ('TI_FEEDBACK', 1, self._ti_fields(viol, states)))
    for name, bus, fields in sources:
      self.safety.safety_rx_hook(self._panda_packet(name, fields, bus))
    self.safety.safety_tick()
    self.safety.aol_set_host_request(1)

    frames = []
    if kind != 'gap':
      frames.append(self.host_packer.make_can_msg('TI_FEEDBACK', 1, self._ti_fields(viol, states)))
    now_ns = self.time * 1000
    for parser in self.parsers.values():
      parser.update([now_ns, frames])
    cs = self.car_state.update_gen2(self.parsers)
    if kind == 'gap':
      self.unhealthy_cause = 'fault'
      # No 0x24B this tick: panda judges staleness on the other frames, the host on every update.
      panda_unhealthy = bool(self.safety.get_mazda_ti_unhealthy())
      host = self.car_state
      host_healthy = host.ti2_clean_frames >= TI2_HEALTH.RECOVERY_FRAMES and not host.ti2_hard_fault
      where = f"gap t={self.time} temp={cs.steerFaultTemporary} perm={cs.steerFaultPermanent}"
      self.assertEqual(host_healthy, not panda_unhealthy, f"proven-health mismatch: {where}")
      if panda_unhealthy and not self.safety.get_mazda_ti_lateral_paused():
        self.assertTrue(cs.steerFaultPermanent, f"panda faulted on silence the host does not report: {where}")
    else:
      self._check_frame_parity(cs, viol, states)
    return cs, now_ns

  def _check_frame_parity(self, cs, viol, states):
    """Per-frame agreement between CarState and panda on a fresh 0x24B frame."""
    host = self.car_state
    panda_unhealthy = bool(self.safety.get_mazda_ti_unhealthy())
    panda_paused = bool(self.safety.get_mazda_ti_lateral_paused())
    host_healthy = host.ti2_clean_frames >= TI2_HEALTH.RECOVERY_FRAMES and not host.ti2_hard_fault
    host_paused = host.ti2_pause or host.ti2_pause_clean_frames < TI2_HEALTH.RECOVERY_FRAMES
    where = f"t={self.time} viol={viol} states={states} temp={cs.steerFaultTemporary} perm={cs.steerFaultPermanent}"
    self.assertEqual(host_healthy, not panda_unhealthy, f"proven-health mismatch: {where}")
    self.assertEqual(host_paused, panda_paused, f"pause mismatch: {where}")
    if panda_paused:
      self.assertTrue(cs.steerFaultTemporary, f"panda withholds lateral without a host restriction: {where}")
    if cs.steerFaultTemporary:
      self.assertFalse(panda_unhealthy, f"host reports a pause panda treats as a fault: {where}")
    if cs.steerFaultPermanent:
      self.assertTrue(panda_unhealthy, f"host reports a fault panda does not: {where}")

    # Until panda has fully recovered from a violation or a feedback gap, the
    # host must keep reporting it, or it would accept a MAIN press panda refuses.
    if not panda_unhealthy:
      self.unhealthy_cause = None
    elif any(viol) and not panda_paused:
      self.unhealthy_cause = 'fault'
    elif self.unhealthy_cause is None and any(state != 3 for state in states):
      self.unhealthy_cause = 'state'
    if self.unhealthy_cause == 'fault':
      self.assertTrue(cs.steerFaultPermanent, f"panda still recovering from a fault the host no longer reports: {where}")

  def _run(self, steps):
    host_armed = False
    last_denial_ns = None
    trace = []
    for index, (kind, viol, states) in enumerate(steps + ti(n=TAIL_STEPS)):
      if kind == 'arm':
        # Physical MAIN gesture, carrying this step's TI frame on both ticks.
        cs, now_ns = self._step('ti', viol, states, main=False)
        cs, now_ns = self._step('ti', viol, states, main=True)
        host_armed = not cs.steerFaultPermanent
        last_denial_ns = None
      else:
        cs, now_ns = self._step(kind, viol, states)
        if cs.steerFaultPermanent:
          host_armed = False
        if host_armed and not (self.safety.aol_get_permission_mask() & 1):
          last_denial_ns = now_ns
        restricted = cs.steerFaultTemporary or cs.steerFaultPermanent
        if (host_armed and not restricted and last_denial_ns is not None and
            now_ns - last_denial_ns <= SAFETY_MAX_AGE_NS):
          host_armed = False  # judged a native rejection
      trace.append((index, kind, viol, states, bool(cs.steerFaultTemporary), bool(cs.steerFaultPermanent),
                    self.safety.aol_get_permission_mask(), host_armed))
    return host_armed, bool(self.safety.aol_get_permission_mask() & 1), trace

  def test_aol_survival_matches_ti2_contract(self):
    # A lone 0x02 (every CPU in RUN, starting from proven health, within the
    # 30 s episode cap) is a pause the AOL session survives. Everything else
    # still needs a new physical gesture.
    for (name, steps), first in ((item, first) for first in (False, True) for item in SCENARIOS.items()):
      with self.subTest(scenario=name, feedback_first=first):
        self.feedback_first = first
        self.setUp()
        host_armed, panda_lateral, trace = self._run(steps)
        tail = '\n'.join(map(str, trace[-5:]))
        self.assertEqual(panda_lateral, SURVIVES[name], f"{name}: panda lateral={panda_lateral}\n{tail}")
        self.assertEqual(host_armed, SURVIVES[name], f"{name}: host armed={host_armed}\n{tail}")

  def test_host_and_panda_agree_on_aol_survival(self):
    for (name, steps), first in ((item, first) for first in (False, True) for item in SCENARIOS.items()):
      with self.subTest(scenario=name, feedback_first=first):
        self.feedback_first = first
        self.setUp()
        host_armed, panda_lateral, trace = self._run(steps)
        tail = '\n'.join(map(str, trace[-5:]))
        self.assertEqual(host_armed, panda_lateral,
                         f"{name}: host armed={host_armed}, panda lateral={panda_lateral}\n{tail}")


if __name__ == "__main__":
  unittest.main()
