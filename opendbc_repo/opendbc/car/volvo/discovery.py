"""Recognize recorded Polestar normal wiring before the generic OBD scan."""

# Mirrored PT/camera and independent front-ECM sources select diagnostic wiring
# only. Live CAN fingerprinting still owns vehicle and control identity.
NORMAL_BUS_LAYOUT = {(bus, address): 8 for bus in (0, 2)
                     for address in (0x15, 0x16, 0x17, 0x45, 0x55, 0x58)}
NORMAL_BUS_LAYOUT.update({(1, address): 8 for address in (0x23, 0x35, 0x70, 0x116)})


def normal_bus_topology(fingerprint):
  return all(fingerprint.get(bus, {}).get(address) == size
             for (bus, address), size in NORMAL_BUS_LAYOUT.items())
