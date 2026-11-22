from opendbc.car.honda.hondacan import honda_checksum


# Classic Bosch: PT on bus 1 and the stock ACC camera on bus 2. This selects
# diagnostic wiring only; fresh firmware and CAN fingerprinting own identity.
NORMAL_BUS_LAYOUT = {(1, 0x158): 8, (1, 0x17C): 8, (1, 0x18F): 7,
                     (1, 0x296): 4, (2, 0xE4): 5, (2, 0x400): 4}


# Nidec uses two SCM layouts and two EPS status lengths on normal bus 0.
NIDEC_BUS0_COMMON = {(0, 0x158): 8, (0, 0x17C): 8}
NIDEC_BUS0_LAYOUTS = (
  *({**NIDEC_BUS0_COMMON, (0, 0x18F): eps_length, (0, 0x1A6): 8, (0, 0x156): 6} for eps_length in (6, 7)),
  {**NIDEC_BUS0_COMMON, (0, 0x18F): 7, (0, 0x296): 4, (0, 0x326): 8},
)


def normal_bus_topology(packets, *, layout=NORMAL_BUS_LAYOUT):
  observed = set()
  for packet in packets:
    for frame in packet:
      key = (frame.src, frame.address)
      if key in layout:
        if (len(frame.dat) != layout[key] or
            honda_checksum(frame.address, None, frame.dat) != frame.dat[-1] & 0xF):
          return False
        observed.add(key)
  return observed == layout.keys()


def nidec_bus0_topology(packets):
  return any(normal_bus_topology(packets, layout=layout) for layout in NIDEC_BUS0_LAYOUTS)
