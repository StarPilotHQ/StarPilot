"""Passive Tesla query-wiring selection; live firmware still owns vehicle identity."""

HW1_NORMAL_BUS0_LAYOUT = {(0, 0x108): 8, (0, 0x155): 8, (0, 0x20A): 8,
                         (0, 0x368): 8, (0, 0x370): 8, (2, 0x488): 4, (2, 0x2B9): 8}

# Modern party-bus layout scopes live queries without granting control.
MODERN_NORMAL_BUS0_LAYOUT = {(0, 0x118): 8, (0, 0x145): 8, (0, 0x155): 8,
                            (0, 0x257): 8, (0, 0x311): 7, (0, 0x370): 8,
                            (2, 0x488): 4, (2, 0x2B9): 8}


def hw1_normal_bus0_topology(fingerprint):
  try:
    return all(fingerprint.get(bus, {}).get(address) == length
               for (bus, address), length in HW1_NORMAL_BUS0_LAYOUT.items())
  except (AttributeError, TypeError, ValueError):
    return False


def normal_bus0_topology(fingerprint):
  if hw1_normal_bus0_topology(fingerprint):
    return True
  try:
    return all(fingerprint.get(bus, {}).get(address) == length
               for (bus, address), length in MODERN_NORMAL_BUS0_LAYOUT.items())
  except (AttributeError, TypeError, ValueError):
    return False
