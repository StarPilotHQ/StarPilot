"""Dom's rolling stopped-lead guard for admitted automatic MQB Alpha Long."""
from opendbc.car import structs
from opendbc.car.volkswagen.long_aol import qualified
from openpilot.starpilot.car.ford.stopped_lead import RollingStoppedLead


def owner_for(cp, stop_distance):
  if (qualified(cp, marked_only=True) and cp.transmissionType in
      (structs.CarParams.TransmissionType.automatic, structs.CarParams.TransmissionType.direct)):
    return RollingStoppedLead(stop_distance)
  return None
