"""Normal-wiring discovery for the observed Ram PT/camera layout."""
from opendbc.car.chrysler.chryslercan import chrysler_checksum


# This chooses diagnostic routing only. Fresh VIN/FW and CAN still own identity.
NORMAL_BUS_LAYOUT = {(0, 0x23): 8, (0, 0x31): 8, (0, 0x83): 8, (2, 0x99): 8}


def normal_bus_topology(packets):
  observed = set()
  for packet in packets:
    for frame in packet:
      key = (frame.src, frame.address)
      if key in NORMAL_BUS_LAYOUT:
        if (len(frame.dat) != NORMAL_BUS_LAYOUT[key] or
            chrysler_checksum(frame.address, None, frame.dat) != frame.dat[-1]):
          return False
        observed.add(key)
  if observed != NORMAL_BUS_LAYOUT.keys():
    return False
  from opendbc.car import fw_versions
  configured = fw_versions.FW_QUERY_CONFIGS['chrysler'].requests
  effective = [request for brand, _, request in fw_versions.REQUESTS if brand == 'chrysler']
  return all(requests and any(request.bus == 0 and not request.logging for request in requests) and
             all(request.bus in (0, 4) for request in requests) for requests in (configured, effective))
