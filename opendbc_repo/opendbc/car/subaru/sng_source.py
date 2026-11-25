"""Retain raw sources only after the real CANParser accepted their current sample."""

from opendbc.car import Bus

NAMES = {0x40: 'Throttle', 0x139: 'Brake_Pedal'}


def capture(interface, packets):
  state = interface.CS
  parser = interface.can_parsers[Bus.pt]
  if not hasattr(state, 'sng_sources'):
    state.sng_sources = {}
    state.sng_source_valid = {}
  for timestamp, frames in packets:
    for address, data, bus in frames:
      if address not in NAMES or bus != 0 or len(data) != 8:
        continue
      name = NAMES[address]
      accepted = parser.vl[name]
      source_ns = int(parser.ts_nanos[name]['COUNTER'])
      previous = state.sng_sources.get(address)
      if (
        timestamp == source_ns
        and source_ns > 0
        and int(accepted['COUNTER']) == data[1] & 15
        and data[0] == ((address & 255) + (address >> 8) + sum(data[1:])) & 255
        and (previous is None or source_ns > previous[0] and data[1] & 15 != previous[1][1] & 15)
      ):
        state.sng_source_valid[address] = previous is not None and data[1] & 15 != previous[1][1] & 15
        state.sng_sources[address] = (source_ns, bytes(data))
