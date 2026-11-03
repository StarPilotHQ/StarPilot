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
real CarState, models the host arming rules, and requires the host's final
armed state to equal panda's final lateral permission for every scenario.

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
from opendbc.car.mazda.values import CAR, MazdaSafetyFlags
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
  # Startup: TI2 has not yet proven health when 0x02 arrives.
  'startup_first_frame_02_arm_during': ti(ALL_02, n=30) + arm(ALL_02) + ti(ALL_02, n=30),
  'startup_9_clean_then_02_arm_during': ti(n=9) + ti(ALL_02, n=30) + arm(ALL_02) + ti(ALL_02, n=30),
  'startup_02_then_recovery_then_arm': ti(ALL_02, n=30) + ti(n=15) + arm() + ti(n=20),
}


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
      sources.insert(2, ('TI_FEEDBACK', 1, self._ti_fields(viol, states)))
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
    return cs, now_ns

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

  def test_host_and_panda_agree_on_aol_survival(self):
    for name, steps in SCENARIOS.items():
      with self.subTest(scenario=name):
        self.setUp()
        host_armed, panda_lateral, trace = self._run(steps)
        tail = '\n'.join(map(str, trace[-5:]))
        self.assertEqual(host_armed, panda_lateral,
                         f"{name}: host armed={host_armed}, panda lateral={panda_lateral}\n{tail}")


if __name__ == "__main__":
  unittest.main()
