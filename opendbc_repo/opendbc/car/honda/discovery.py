from opendbc.car.honda.hondacan import honda_checksum


# Classic Bosch: PT on bus 1 and the stock ACC camera on bus 2. This selects
# diagnostic wiring only; fresh firmware and CAN fingerprinting own identity.
NORMAL_BUS_LAYOUT = {(1, 0x158): 8, (1, 0x17C): 8, (1, 0x18F): 7,
                     (1, 0x296): 4, (2, 0xE4): 5, (2, 0x400): 4}


def normal_bus_topology(packets):
  observed = set()
  for packet in packets:
    for frame in packet:
      key = (frame.src, frame.address)
      if key in NORMAL_BUS_LAYOUT:
        if (len(frame.dat) != NORMAL_BUS_LAYOUT[key] or
            honda_checksum(frame.address, None, frame.dat) != frame.dat[-1] & 0xF):
          return False
        observed.add(key)
  return observed == NORMAL_BUS_LAYOUT.keys()
