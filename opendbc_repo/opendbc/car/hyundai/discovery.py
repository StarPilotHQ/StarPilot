import time
from itertools import product


NORMAL_BUS_LAYOUT = {(2, 0x110): 32, (2, 0x362): 32, (1, 0x35): 32,
                     (1, 0xEA): 24, (1, 0xA0): 24, (1, 0x1A0): 32,
                     (1, 0x1CF): 8, (0, 0x3A5): 24}


# Classic HKG steering, chassis and stock SCC are already visible on normal
# harness wiring. This selects a diagnostic query family, never a vehicle or
# control profile. Fresh firmware and CAN fingerprinting still own identity.
CLASSIC_NORMAL_BUS_LAYOUT = {(0, 0x251): 8, (0, 0x386): 8, (0, 0x394): 8,
                            (0, 0x4F1): 4, (2, 0x340): 8, (0, 0x420): 8, (0, 0x421): 8}


HDA1_NORMAL_BUS_LAYOUT = {(0, 0x120): 32, (2, 0x120): 32, (0, 0x35): 32, (0, 0xEA): 24,
                         (0, 0xA0): 24, (0, 0x1A0): 32, (0, 0x1CF): 8, (0, 0x130): 16}


HDA2_NORMAL_BUS_LAYOUT = {(2, 0x50): 16, (2, 0x2A4): 24, (1, 0x35): 32, (1, 0xEA): 24,
                         (1, 0xA0): 24, (1, 0x1A0): 32, (1, 0x1CF): 8, (1, 0x130): 16}


QUIET_CAMERA_NORMAL_BUS_LAYOUT = {(0, 0xEA): 24, (0, 0xA0): 24, (0, 0x1A0): 32, (0, 0x60): 32,
                                  (0, 0x35): 32, (0, 0x1CF): 8, (0, 0x130): 16,
                                  **{(1, address): 32 for address in range(0x210, 0x220)}}


def _canfd_variant_layouts():
  layouts = []
  originals = (NORMAL_BUS_LAYOUT, HDA1_NORMAL_BUS_LAYOUT, HDA2_NORMAL_BUS_LAYOUT)
  for base in (*originals, QUIET_CAMERA_NORMAL_BUS_LAYOUT):
    bus = next(bus for bus, address in base if address == 0xEA)
    gears = ((0x130, 16), (0x40, 32), (0x70, 32)) if (bus, 0x130) in base else ((None, 0),)
    for accelerator, (buttons, length), (gear, gear_length) in product(
        (0x35, 0x100, 0x105), ((0x1CF, 8), (0x1AA, 16)), gears):
      layout = {key: value for key, value in base.items() if key not in ((bus, 0x35), (bus, 0x1CF), (bus, 0x130))}
      layout[(bus, accelerator)] = 32
      layout[(bus, buttons)] = length
      if gear is not None:
        layout[(bus, gear)] = gear_length
      if layout not in originals:
        layouts.append(layout)
  return tuple(layouts)


# Same camera/chassis proof, with the DBC-defined fuel, button and shifter sources.
# This selects only query wiring; fresh VIN/FW/CAN still determine vehicle identity.
CANFD_VARIANT_LAYOUTS = _canfd_variant_layouts()


def normal_bus_topology(can_recv, *, clock=time.monotonic):
  deadline = clock() + .12
  layouts = (NORMAL_BUS_LAYOUT, CLASSIC_NORMAL_BUS_LAYOUT)
  observed = [{} for _ in layouts]
  original_invalid = False
  hda2_observed = {}
  hda2_invalid = False
  hda1_observed = {}
  hda1_invalid = False
  variants_observed = [{} for _ in CANFD_VARIANT_LAYOUTS]
  variants_invalid = [False for _ in CANFD_VARIANT_LAYOUTS]
  for _ in range(24):
    if clock() >= deadline:
      break
    for packet in can_recv(wait_for_one=True):
      for frame in packet:
        key = (frame.src, frame.address)
        for layout, received in zip(layouts, observed, strict=True):
          if key in layout:
            length = len(frame.dat)
            if length != layout[key]:
              # Standard HDA2 reuses the classic MDPS identifier with 16 bytes.
              if key != (0, 0x251) or length != 16:
                return False
              original_invalid = True
            received[key] = length
        for index, layout in enumerate(CANFD_VARIANT_LAYOUTS):
          if key in layout:
            if len(frame.dat) != layout[key]:
              variants_invalid[index] = True
            else:
              variants_observed[index][key] = len(frame.dat)
        if key in HDA2_NORMAL_BUS_LAYOUT:
          if len(frame.dat) != HDA2_NORMAL_BUS_LAYOUT[key]:
            hda2_invalid = True
          else:
            hda2_observed[key] = len(frame.dat)
        if key in HDA1_NORMAL_BUS_LAYOUT:
          if len(frame.dat) != HDA1_NORMAL_BUS_LAYOUT[key]:
            hda1_invalid = True
          else:
            hda1_observed[key] = len(frame.dat)
    # Other families reuse HDA2 identifiers with different packet lengths.
    if (not hda2_invalid and hda2_observed == HDA2_NORMAL_BUS_LAYOUT or
        not original_invalid and (any(received == layout for layout, received in zip(layouts, observed, strict=True)) or
                                  not hda1_invalid and hda1_observed == HDA1_NORMAL_BUS_LAYOUT)):
      return True
    if any(not invalid and received == layout and (not original_invalid or (2, 0x50) in layout)
           for layout, received, invalid in zip(CANFD_VARIANT_LAYOUTS, variants_observed, variants_invalid, strict=True)):
      return True
  return False
