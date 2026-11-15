"""Reject contradictory discovery evidence for the scoped Subaru angle identities."""
from opendbc.car.fw_query_definitions import LiveFwVersions, OfflineFwVersions
from opendbc.car.structs import CarParams


SCOPED_CANDIDATES = frozenset(("SUBARU_CROSSTREK_2025", "SUBARU_IMPREZA_2025", "SUBARU_LEGACY_2025", "SUBARU_FORESTER_2022", "SUBARU_OUTBACK_2023"))
REQUIRED_ECUS = frozenset((CarParams.Ecu.abs, CarParams.Ecu.fwdCamera, CarParams.Ecu.engine))


def validate_fw_match(candidate: str, observed: LiveFwVersions, versions: OfflineFwVersions) -> bool:
  if candidate not in SCOPED_CANDIDATES:
    return True
  found = set()
  for (ecu, address, subaddress), allowed in versions.get(candidate, {}).items():
    actual = observed.get((address, subaddress), set())
    if actual:
      if not actual.issubset(allowed):
        return False
      found.add(ecu)
  # EPS can be absent in Gen2 non-OBD queries. Preserve that existing rule,
  # and require the three platform ECUs before accepting exact or fuzzy selection.
  return REQUIRED_ECUS.issubset(found)
