# Legacy Mazda powertrain and camera traffic selects diagnostic wiring only.
# Identification still requires the normal live firmware/CAN match.
NORMAL_BUS0_LAYOUT = {(0, 0x09D), (0, 0x165), (0, 0x202),
                      (0, 0x21C), (0, 0x240), (2, 0x243)}


def normal_bus0_topology(packets):
  seen = set()
  for packet in packets:
    for frame in packet:
      key = (frame.src, frame.address)
      if key in NORMAL_BUS0_LAYOUT:
        if len(frame.dat) != 8:
          return False
        seen.add(key)
  return seen == NORMAL_BUS0_LAYOUT
