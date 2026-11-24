"""Card side of the Tesla set-speed rule: the saved switch decides who owns SET, and SLC's qualified commands become
software SET.

Only a Model 3/Y selected by opendbc.car.tesla.speed_sync gets a receiver or reads the switch. Every other car has
neither, so nothing here subscribes, reads or writes for it.
"""
import math
from collections import deque

from opendbc.car.tesla.speed_sync import selected
from openpilot.cereal import messaging
from openpilot.common.constants import CV
from openpilot.selfdrive.car.cruise import slc_target_kph
from openpilot.starpilot.car.tesla import set_speed
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.speed_limits import physical_actions
from openpilot.starpilot.speed_limits.runtime_settings import PEDAL_OVERRIDE_KEY, PedalOverride

PEDAL_HOLD = str(int(PedalOverride.SPEED_REACHED)).encode()


class Receiver:
  def __init__(self, sock):
    self.sock = sock
    self.commands = deque(maxlen=8)
    self.session = ''
    self.last_id = 0
    self.pedal_peak_mps = 0.  # the highest speed reached during this accelerator press


def refresh(card) -> None:
  """Follow the saved switch (card's settings thread, once a second). SET changes owner at once: it is the car's own
  set speed on that frame either way, so nothing jumps."""
  if selected(card.CP):
    card.v_cruise_helper.tesla_set_speed = set_speed.active(card.params, card.CP)
    # After an accelerator override: card applies "Hold the speed you reached", because card owns SET here.
    card.v_cruise_helper.tesla_pedal_hold = read_saved(card.params, PEDAL_OVERRIDE_KEY, 1) == (PEDAL_HOLD, True)


def attach(card) -> Receiver | None:
  """Read the switch, and return the command receiver for a car where SLC may write SET, else None."""
  refresh(card)
  if not (card.slc_transport_available and selected(card.CP)):
    return None
  return Receiver(messaging.sub_sock('slcCruiseCommand', conflate=False))


def rule(card) -> bool:
  """SLC writes SET on this car now."""
  return bool(getattr(card, 'slc_set_speed', None) is not None and card.v_cruise_helper.tesla_set_speed)


def pedal_hold(card, CS, now_ns: int) -> bool:
  """After an accelerator override, Hold the speed you reached: on lifting off, the highest speed reached above SET
  becomes SET as the driver's own change, so SLC holds it until the limit changes, as it does a scroll. The other two
  choices return to SET, which is the limit plus offset or the speed the driver scrolled to."""
  receiver, helper = card.slc_set_speed, card.v_cruise_helper
  speed = float(CS.vEgoCluster) if CS.vEgoCluster > 0 else float(CS.vEgo)
  if not (helper.tesla_pedal_hold and helper.slc_pcm and helper.v_cruise_initialized and CS.cruiseState.enabled and
          CS.canValid and not CS.canTimeout and not CS.standstill and math.isfinite(speed)):
    receiver.pedal_peak_mps = 0.
    return False
  if CS.gasPressed:
    receiver.pedal_peak_mps = max(receiver.pedal_peak_mps, speed)
    return False
  peak, receiver.pedal_peak_mps = receiver.pedal_peak_mps, 0.
  previous_kph = float(helper.v_cruise_kph)
  unit = 1. if card.is_metric else CV.MPH_TO_KPH
  # An override of the limit: SLC has an accepted limit in force and the speed reached is above it.
  limit = physical_actions.accepted_target(card.sm, now_ns)
  # Compare as shown: SET moves only when the speed reached rounds to a higher whole unit.
  if (peak <= 0. or limit is None or peak <= limit or
      round(slc_target_kph(peak, card.is_metric) / unit) <= round(previous_kph / unit)):
    return False
  helper.apply_slc_target(peak, card.is_metric)
  card.slc_receipts.append(('driverChange', (previous_kph / 3.6, float(helper.v_cruise_kph / 3.6), 'accel', False),
                            previous_kph / 3.6, float(helper.v_cruise_kph / 3.6), now_ns))
  return True


def apply_commands(card, CS, observation, now_ns: int, *, long_active: bool, after_ns: int = 0) -> None:
  """Write each new qualified command's target into software SET and answer it with a receipt."""
  receiver = getattr(card, 'slc_set_speed', None)
  if receiver is None:
    return
  helper = card.v_cruise_helper
  for _ in range(8):
    message = messaging.recv_one_or_none(receiver.sock)
    if message is None:
      break
    if card.slc_replay and message.valid:
      receiver.commands.append(message)
  if not helper.tesla_set_speed:
    receiver.commands.clear()  # the switch is off: the car owns SET
    receiver.pedal_peak_mps = 0.
    return
  held = pedal_hold(card, CS, now_ns)
  for _ in range(len(receiver.commands)):
    message = receiver.commands.popleft()
    command = message.slcCruiseCommand
    issued, expires = int(command.issuedMonoTime), int(command.expiresMonoTime)
    if issued <= after_ns:
      continue  # issued before the SLC settings last changed
    if now_ns < issued:
      if now_ns <= expires:
        receiver.commands.append(message)
      continue
    state = physical_actions.state_current(card.sm, observation, card.slc_producer_session, now_ns)
    # Wait for the planner state published with this command.
    if (state is None or int(card.sm.logMonoTime['slcState']) < issued) and now_ns < expires:
      receiver.commands.append(message)
      continue
    if state is not None and str(state.sessionId) != receiver.session:
      receiver.session, receiver.last_id = str(state.sessionId), 0
    if not receiver.session:
      receiver.session = str(command.sessionId)
    if str(command.sessionId) != receiver.session or int(command.commandId) <= receiver.last_id:
      continue
    receiver.last_id = int(command.commandId)
    previous_mps = float(helper.v_cruise_kph / 3.6)
    # The driver's own SET change in this frame wins; the planner sees it and decides again.
    driver_input = held or helper.slc_cruise_change is not None or len(CS.buttonEvents) > 0 or any(helper.button_timers.values())
    applicable = physical_actions.command_applicable(
      command, state, observation, card.slc_producer_session, now_ns, previous_mps,
      long_active=long_active, button_event=driver_input)
    if applicable:
      helper.apply_slc_target(float(command.targetMps), card.is_metric)
    card.slc_receipts.append(('commandApplied' if applicable else 'commandRejected', command,
                              previous_mps, float(helper.v_cruise_kph / 3.6), now_ns))


def write_receipt(record, kind: str, detail) -> None:
  """Card's answer to a command; the runtime matches it to the one it issued."""
  if kind in ('commandApplied', 'commandRejected'):
    record.sessionId = str(detail.sessionId)
    record.commandId = int(detail.commandId)
    record.decisionId = int(detail.decisionId)
    record.presentationId = int(detail.presentationId)
