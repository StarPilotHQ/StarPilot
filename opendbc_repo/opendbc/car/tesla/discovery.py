"""Passive AP1 query-wiring selection; live firmware still owns vehicle identity."""

HW1_NORMAL_BUS0_LAYOUT = {(0, 0x108): 8, (0, 0x155): 8, (0, 0x20A): 8,
                         (0, 0x368): 8, (0, 0x370): 8, (2, 0x488): 4, (2, 0x2B9): 8}


def hw1_normal_bus0_topology(fingerprint):
  try:
    return all(fingerprint.get(bus, {}).get(address) == length
               for (bus, address), length in HW1_NORMAL_BUS0_LAYOUT.items())
  except (AttributeError, TypeError, ValueError):
    return False
