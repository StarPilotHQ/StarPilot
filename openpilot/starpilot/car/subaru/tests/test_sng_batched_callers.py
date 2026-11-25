"""SNG hold pulse through the real interface/controller callers, with batched CAN delivery.

The full-stack caller harness (test_sng_callers) delivers exactly one frame per
message per tick, so the stored Throttle counter always advances by exactly one
and the 2026-11-24 standstill failure is unrepresentable in it. Here the
100 Hz Throttle source is delivered pandad-style: every fifth tick the packet
carries two Throttle frames, the CANParser accepts both, and capture() can only
store the newest -- the stored counter jumps by 2. The StopAndGo ready clock
must still reach 51 consecutive frames and emit the 3 kph hold pulse.
"""

import time as time_module
from types import SimpleNamespace
from unittest.mock import patch

from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.subaru.interface import CarInterface
from opendbc.car.subaru.sng import prepare, selected
from opendbc.car.subaru.values import CAR
from openpilot.cereal import messaging
from openpilot.starpilot.car.subaru.sng_caller import update as sng_caller_update
from openpilot.starpilot.car.subaru.tests.caller_helpers import CompleteCan


_real_clock_gettime_ns = time_module.clock_gettime_ns
_ADDED_BOOTTIME = not hasattr(time_module, 'CLOCK_BOOTTIME')
if _ADDED_BOOTTIME:
  time_module.CLOCK_BOOTTIME = 7  # Linux value; absent on macOS
_BOOTTIME = time_module.CLOCK_BOOTTIME
_virtual = {'ns': 0}


def _fake_clock_gettime_ns(clk):
  if clk == _BOOTTIME:
    return _virtual['ns']
  return _real_clock_gettime_ns(clk)


class FakeSubMaster:
  """The three publications sng_caller gates on, always seen/valid/alive/fresh."""

  SERVICES = ('carControl', 'selfdriveState', 'longitudinalPlan')

  def __init__(self):
    self.seen = dict.fromkeys(self.SERVICES, True)
    self.valid = dict.fromkeys(self.SERVICES, True)
    self.alive = dict.fromkeys(self.SERVICES, True)
    self.logMonoTime = dict.fromkeys(self.SERVICES, 0)
    self.recv_time = dict.fromkeys(self.SERVICES, 0.0)

  def update(self, now_ns):
    for name in self.SERVICES:
      self.logMonoTime[name] = now_ns
      self.recv_time[name] = (now_ns - 1000) / 1e9


def test_sng_hold_pulse_with_batched_throttle_delivery():
  cp = CarInterface.get_params(CAR.SUBARU_IMPREZA_2020, gen_empty_fingerprint(), [], False, False, False)
  prepare(cp, True)
  assert selected(cp)
  try:
    with patch.object(time_module, 'clock_gettime_ns', _fake_clock_gettime_ns):
      ci = CarInterface(cp)
      stream = CompleteCan(ci)
      sm = FakeSubMaster()
      card = SimpleNamespace(CI=ci, CP=cp, sm=sm)
      # A capnp reader, as production card passes to CI.apply (CC.update calls
      # actuators.as_builder(), which the opendbc structs wrapper lacks).
      control_msg = messaging.new_message('carControl', valid=True)
      control_msg.carControl.enabled = True
      control_msg.carControl.hudControl.leadVisible = True
      cc = messaging.log_from_bytes(control_msg.to_bytes()).carControl
      values = {
        "Steering_Torque": {"Steer_Warning": 0},
        "Steering_2": {"Steering_Angle": 0},
        "Wheel_Speeds": dict.fromkeys(("FL", "FR", "RL", "RR"), 0),
        "Throttle": {"Signal1": 9, "Engine_RPM": 1234, "Neutral": 1, "Signal3": 7, "Off_Accel": 3},
        "Brake_Pedal": {"Brake_Pedal": 0, "Signal1": 7, "Signal2": 31, "Signal3": 1, "Signal4": 0xABCD},
        "ES_Distance": {"Car_Follow": 1, "Close_Distance": 2.0},
        "Brake_Status": {"Brake": 0},
        "CruiseControl": {"Cruise_On": 1, "Cruise_Activated": 1},
        "ES_Status": {"Cruise_Activated": 1},
        "ES_DashStatus": {"Cruise_On": 1, "Cruise_Set_Speed": 60},
        "Transmission": {"Gear": 121},
        "Cruise_Buttons": {"Resume": 0, "Signal1": 0x12345, "Signal2": 0x12345},
      }
      pt_bus = ci.can_parsers[Bus.pt].bus
      base_ns = 1_000_000_000_000
      pulses = []
      throttle_taps = []
      ready_ticks = []
      max_duration = 0
      for tick in range(200):
        now = base_ns + tick * 10_000_000
        _virtual['ns'] = now
        frames = []
        if tick % 5 == 4:
          # Packed first, so this is the older of the two Throttle frames the
          # parser accepts from this tick's packet; frames() packs the newer.
          frames.append(stream.packers[Bus.pt].make_can_msg('Throttle', pt_bus, values["Throttle"]))
        frames.extend(stream.frames(values, tick=tick, omit=((Bus.pt, 'Brake_Pedal'),) if tick % 2 else ()))
        ci.update([(now, frames)])
        assert ci.CS.out.standstill
        sm.update(now)
        sng_caller_update(card, ci.CS.out, cc, now)
        _, sends = ci.apply(cc, now)
        if tick >= 20:
          ready_ticks.append(bool(ci.CS.sng_lead_ready))
        max_duration = max(max_duration, ci.CC.frame - ci.CC.sng_owner.last_standstill_frame)
        for address, data, _bus in sends:
          if address == 0x139 and data[2] == 53 and (data[3] & 15) == 0:
            pulses.append(tick)
          if address == 0x40 and data[4] == 5:
            throttle_taps.append(tick)
      # The batched ticks must not flap the lead-ready health signal...
      assert sum(ready_ticks) >= 0.95 * len(ready_ticks), ready_ticks
      # ...the standstill clock must run past the 51 consecutive frames the
      # hold pulse requires (it peaked at 24 in the 2026-11-24 incident)...
      assert max_duration > 50, max_duration
      # ...and the spoofed 3 kph Brake_Pedal hold pulse must reach the wire,
      # on roughly the designed 0.55 s cadence (two pulses in 2 s).
      assert len(pulses) >= 2, pulses
      # The departure tap must not fire: Close_Distance never enters 3.0-4.5 m.
      assert not throttle_taps, throttle_taps
  finally:
    if _ADDED_BOOTTIME:
      del time_module.CLOCK_BOOTTIME
