import unittest

from opendbc.car import structs
from openpilot.starpilot.aol.intent import AolSettings, AOL_TOGGLE
from openpilot.starpilot.car.ford.aol import FordCardIntent


class TestFordAolIntent(unittest.TestCase):
  def intent(self, lkas=0):
    return FordCardIntent(None, AolSettings(True, 0.0, lkas, 0, (0, 0, 0), (0, 0, 0)))

  def frame(self, intent, *, main, button=None, valid=True, gear=None, temporary=False, now=1):
    cs = structs.CarState(canValid=valid, steerFaultTemporary=temporary,
                          gearShifter=gear or structs.CarState.GearShifter.drive)
    cs.cruiseState.available = main
    if button is not None:
      cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.lkas, pressed=button)]
    intent.update(cs.as_reader(), now_ns=now)
    return cs

  def test_main_pulse_releases_before_tja_toggle(self):
    intent = self.intent()
    self.frame(intent, main=False)
    self.frame(intent, main=True, now=2)
    self.assertFalse(intent.allowed_latch)  # Prior MAIN-off feedback still owns this frame.
    self.frame(intent, main=True, now=3)
    self.assertTrue(intent.allowed_latch)
    self.frame(intent, main=True, now=4)
    self.assertFalse(intent._main_held)
    self.frame(intent, main=True, button=True, now=5)
    self.assertFalse(intent.allowed_latch)
    self.frame(intent, main=True, button=False, now=6)
    self.frame(intent, main=True, button=True, now=7)
    self.assertTrue(intent.allowed_latch)

  def test_lkas_managed_with_main_already_on(self):
    intent = self.intent(AOL_TOGGLE)
    self.frame(intent, main=True)
    self.assertFalse(intent.allowed_latch)
    self.frame(intent, main=True, button=True, now=2)
    self.assertTrue(intent.allowed_latch)

  def test_temporary_state_retains_intent_but_rx_loss_does_not(self):
    intent = self.intent()
    self.frame(intent, main=False)
    self.frame(intent, main=True, now=2)
    self.frame(intent, main=True, now=3)
    reverse = self.frame(intent, main=True, gear=structs.CarState.GearShifter.reverse, now=4)
    self.assertTrue(intent.allowed_latch)
    self.assertFalse(intent.output(reverse.as_reader())[0])
    self.frame(intent, main=True, temporary=True, now=5)
    self.assertTrue(intent.allowed_latch)
    self.frame(intent, main=True, valid=False, now=6)
    self.assertFalse(intent.allowed_latch)
    self.frame(intent, main=True, now=7)
    self.assertFalse(intent.allowed_latch)


  def test_unassigned_cancel_preserves_but_never_arms_lateral(self):
    intent = self.intent(AOL_TOGGLE)
    state = self.frame(intent, main=True)
    state.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
    intent.update(state.as_reader(), now_ns=2)
    self.assertFalse(intent.allowed_latch)
    self.frame(intent, main=True, button=True, now=3)
    self.assertTrue(intent.allowed_latch)
    intent.update(state.as_reader(), now_ns=4)
    self.assertTrue(intent.allowed_latch)
    intent.update(state.as_reader(), now_ns=5, native_rejection_ns=5)
    self.assertFalse(intent.allowed_latch)

  def test_configured_cancel_keeps_existing_lateral_withdrawal(self):
    from dataclasses import replace
    intent = self.intent(AOL_TOGGLE)
    intent.settings = replace(intent.settings, cancel_actions=(1, 0, 0))
    self.frame(intent, main=True)
    self.frame(intent, main=True, button=True, now=2)
    self.assertTrue(intent.allowed_latch)
    state = self.frame(intent, main=True, button=False, now=3)
    state.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)]
    intent.update(state.as_reader(), now_ns=4)
    self.assertFalse(intent.allowed_latch)

  def test_cancel_exception_requires_current_granted_same_owner_lateral(self):
    from dataclasses import replace
    from opendbc.car.ford.values import CAR, FordFlags
    from openpilot.starpilot.aol.runtime import AxisDecision
    from openpilot.starpilot.aol.wire import IntentState, PandaSlot, SafetyState
    from openpilot.starpilot.car.ford.aol import physical_cancel_lateral_allowed
    now = 1_000_000_000
    cp = structs.CarParams(brand='ford', carFingerprint=CAR.FORD_MUSTANG_MACH_E_MK1,
      flags=int(FordFlags.CANFD), pcmCruise=True, openpilotLongitudinalControl=True,
      alternativeExperience=32, steerControlType=structs.CarParams.SteerControlType.angle,
      transmissionType=structs.CarParams.TransmissionType.automatic,
      safetyConfigs=[structs.CarParams.SafetyConfig(safetyModel=structs.CarParams.SafetyModel.ford, safetyParam=3)])
    state = structs.CarState(canValid=True, gearShifter=structs.CarState.GearShifter.drive,
      buttonEvents=[structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.cancel, pressed=True)])
    state.cruiseState.available = True
    settings = AolSettings(True, 0., AOL_TOGGLE, 0, (0, 0, 0), (0, 0, 0))
    intent = IntentState('card', 1, now, now, now + 200_000_000, True, False, False, True, True)
    previous = AxisDecision(True, True, True, True, True)
    slot = PandaSlot(0, 'panda', int(structs.CarParams.SafetyModel.ford), 3, 32, True, False)
    native = SafetyState(1, True, now, now + 200_000_000, slot.safetyModel, 3, True, False,
      True, True, 'panda', 'axis', now, (slot,))
    def allowed(**changes):
      args = {'settings': settings, 'previous': previous, 'source_ns': now, 'now_ns': now, 'axis_session_id': 'axis'}
      value = changes.pop('native', native)
      driver = changes.pop('intent', intent)
      args.update(changes)
      return physical_cancel_lateral_allowed(cp, state.as_reader(), driver, value, **args)
    self.assertTrue(allowed())
    for value in (None, replace(native, lateralAllowed=False), replace(native, compatible=False),
                  replace(native, validUntilMonoTime=now - 1), replace(native, axisSessionId='other'),
                  replace(native, safetyParam=147), replace(native, pandaSerial='other'),
                  replace(native, pandaInventory=(replace(slot, faults=1),)),
                  replace(native, pandaInventory=(replace(slot, heartbeatLost=True),)),
                  replace(native, pandaInventory=(replace(slot, safetyRxChecksInvalid=True),))):
      with self.subTest(native=value):
        self.assertFalse(allowed(native=value))
    self.assertFalse(allowed(intent=replace(intent, allowedLatch=False)))
    self.assertFalse(allowed(previous=AxisDecision()))
    self.assertFalse(allowed(source_ns=now - 1))
    self.assertFalse(allowed(settings=replace(settings, cancel_actions=(1, 0, 0))))
