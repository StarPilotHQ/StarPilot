"""Current Ford Alpha gas-override LAT evidence; no LONG authority or lease writes."""

from opendbc.car import structs
from opendbc.car.ford.aol import qualified
from openpilot.cereal import log
from openpilot.starpilot.aol.runtime import AXIS_MAX_AGE_NS
from openpilot.starpilot.aol.transport_pause import cp_identity, independent_lateral_health, _healthy_transport_native


def retained_gas_lateral_native(feedback, sm, cp, cs, axis, now_ns, *, latched, producer):
  """Return only independently granted LAT from a current typed gas proof."""
  try:
    if (
      not qualified(cp, marked_only=True)
      or not cp.openpilotLongitudinalControl
      or not latched
      or feedback.failed
      or feedback.paused
      or feedback.pending_event
      or feedback.awaiting
      or feedback.retention_started_ns
      or feedback.retention_pending
      or feedback.retention_qualified
      or feedback.source_retention_started_ns
      or feedback.cp != cp_identity(cp)
      or not cs.gasPressed
      or cs.brakePressed
      or cs.regenBraking
      or cs.steerFaultTemporary
      or cs.steerFaultPermanent
      or cs.vehicleSensorsInvalid
      or cs.accFaulted
      or cs.steeringDisengage
      or cs.doorOpen
      or cs.seatbeltUnlatched
      or cs.buttonEvents
      or not cs.canValid
      or cs.canTimeout
      or cs.gearShifter != structs.CarState.GearShifter.drive
      or not cs.cruiseState.available
      or not cs.cruiseState.enabled
      or not feedback.session
      or not feedback.lateral_inventory
      or feedback.lateral_inventory != feedback.healthy_inventory
      or feedback.lateral_only_baseline
    ):
      return None
    previous = feedback.signature
    stamp = int(sm.logMonoTime['aolAxisState'])
    if (
      not producer
      or producer != feedback.availability_producer
      or str(axis.intentProducerSessionId) != producer
      or feedback.availability_epoch is None
      or axis.intentIntegrityEpoch != feedback.availability_epoch
      or str(axis.sessionId) != feedback.session
      or str(axis.faultSessionId) != feedback.session
      or not axis.qualified
      or str(axis.faultReason) != 'none'
      or str(axis.mode) != 'lateralOnly'
      or not axis.nativeAcknowledged
      or not axis.desiredLateral
      or not axis.lateralActive
      or not axis.desiredLongitudinal
      or axis.longitudinalActive
      or not sm.seen['aolAxisState']
      or not sm.valid['aolAxisState']
      or not sm.alive['aolAxisState']
      or not 0 < axis.sourceCarStateMonoTime == stamp == axis.observedMonoTime <= now_ns <= axis.validUntilMonoTime
      or now_ns - stamp > AXIS_MAX_AGE_NS
      or axis.validUntilMonoTime - stamp > AXIS_MAX_AGE_NS
      or previous is None
      or previous[0] != feedback.session
      or previous[7] != feedback.session
      or previous[5] != 'none'
      or not previous[9]
      or not previous[11]
      or not previous[12]
      or not previous[13]
      or previous[14] != producer
      or previous[15] != feedback.availability_epoch
      or not 0 < previous[8] == previous[2] == previous[3] <= now_ns <= previous[4]
      or now_ns - previous[8] > AXIS_MAX_AGE_NS
      or not 0 < feedback.lateral_source_ns <= feedback.lateral_baseline_ns <= now_ns
      or now_ns - feedback.lateral_source_ns > AXIS_MAX_AGE_NS
      or axis.faultEventMonoTime < previous[6]
      or axis.sourceCarStateMonoTime < previous[8]
      or axis.sequence <= feedback.sequence
    ):
      return None
    event_ns = int(sm.logMonoTime['onroadEvents'])
    events = sm['onroadEvents']
    if (
      not sm.seen['onroadEvents']
      or not sm.valid['onroadEvents']
      or not sm.alive['onroadEvents']
      or not 0 < event_ns == axis.faultEventMonoTime <= stamp
      or now_ns - event_ns > 1_500_000_000
      or len(events) != 1
      or events[0].name != log.OnroadEvent.EventName.gasPressedOverride
      or not events[0].overrideLongitudinal
      or events[0].immediateDisable
      or events[0].softDisable
      or events[0].userDisable
    ):
      return None
    native = independent_lateral_health(sm, cp, cs, feedback.session, now_ns)
    if (
      native is None
      or not native.pandaInventory
      or not native.sourcePandaStatesMonoTime
      or not native.requestedLongitudinal
      or native.longitudinalAllowed
      or native.observedMonoTime < feedback.lateral_native_ns
      or (native.pandaSerial, tuple(p.hardwareSerial for p in native.pandaInventory)) != feedback.lateral_inventory
      or not all(bool(p.controlsAllowed) == (p.safetyModel == int(structs.CarParams.SafetyModel.ford))
                 for p in native.pandaInventory)
      or not all(bool(p.controlsAllowed) == (c.safetyModel == structs.CarParams.SafetyModel.ford)
                 for p, c in zip(sm['pandaStates'], cp.safetyConfigs, strict=True))
    ):
      return None
    return native
  except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
    return None


def healthy_gas_transport_native(sm, cp, cs, session, now_ns):
  """Health for bounded intent retention; denied LONG never supplies permission."""
  if (not qualified(cp, marked_only=True) or not cp.openpilotLongitudinalControl or
      not cs.gasPressed or cs.brakePressed or cs.regenBraking or not cs.cruiseState.available or
      cs.doorOpen or cs.seatbeltUnlatched or cs.vehicleSensorsInvalid or cs.steeringDisengage or
      cs.gearShifter != structs.CarState.GearShifter.drive):
    return None
  return _healthy_transport_native(sm, cp, cs, session, now_ns, require_longitudinal=False)
