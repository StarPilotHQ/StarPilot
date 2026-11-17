"""Normal-bus discovery for explicitly selected Ford CAN-FD vehicles."""
import time

from opendbc.car.ford.values import CAR, FordFlags


def normal_bus0_queries():
  # Verify both configured and effective requests before omitting the OBD sweep.
  # Essential queries use normal bus0 and its exact auxiliary Panda bus4 copy.
  # As-built remains bus0; bus1 logging cannot provide unique evidence.
  from opendbc.car import fw_versions
  configured = fw_versions.FW_QUERY_CONFIGS['ford'].requests
  effective = [request for brand, _, request in fw_versions.REQUESTS if brand == 'ford']
  if not configured or not effective:
    return False

  def signature(request):
    return (tuple(request.request), tuple(request.response), request.rx_offset, frozenset(request.whitelist_ecus))
  for requests in (configured, effective):
    normal = {signature(request) for request in requests if request.bus == 0}
    essential = {signature(request) for request in requests if request.bus == 0 and not request.logging}
    auxiliary = {signature(request) for request in requests if request.bus == 4 and not request.logging}
    if not normal or not essential or auxiliary != essential:
      return False
    for request in requests:
      if request.bus == 0:
        continue
      if request.bus == 4 and not request.logging and signature(request) in essential:
        continue
      if request.bus == 1 and request.logging and signature(request) in normal:
        continue
      return False
  return True


def normal_bus0_selection(candidate):
  return bool(candidate in CAR and CAR(candidate).config.flags & FordFlags.CANFD and
              not CAR(candidate).config.flags & FordFlags.LKA_STEERING and normal_bus0_queries())


# Normal harness PT/chassis and stock-camera messages. This selects only a
# diagnostic query family; live VIN, ECU responses and fingerprinting still
# determine the vehicle and its control configuration.
NORMAL_BUS0_LAYOUT = {(0, 0x82): 8, (0, 0x165): 8, (0, 0x204): 8,
                      (0, 0x217): 8, (2, 0x3CA): 8, (2, 0x3D6): 8}


def normal_bus_query_brand(can_recv, hyundai_probe, *, gm_probe=None, volvo_probe=None, tesla_probe=None, clock=time.monotonic):
  # Share the existing bounded passive HKG read, rather than consuming a second
  # startup window or enabling the OBD mux to find out whether this is a Ford.
  received = {}
  gm_received = {}
  gm_malformed = False
  malformed = False
  reads = 0
  deadline = clock() + .12

  def observe(**kwargs):
    nonlocal malformed, gm_malformed, reads
    reads += 1
    packets = can_recv(**kwargs)
    for packet in packets:
      for frame in packet:
        key = (frame.src, frame.address)
        if gm_probe is not None or volvo_probe is not None or tesla_probe is not None:
          bus = gm_received.setdefault(frame.src, {})
          length = len(frame.dat)
          gm_malformed |= frame.address in bus and bus[frame.address] != length
          bus[frame.address] = length
        if key in NORMAL_BUS0_LAYOUT:
          length = len(frame.dat)
          malformed |= length != NORMAL_BUS0_LAYOUT[key]
          received[key] = length
    return packets

  if hyundai_probe(observe):
    return 'hyundai'
  # Rejecting HKG or Ford does not reject GM. Finish the shared passive window
  # within its original budget even when an early frame excludes Ford.
  while not (not malformed and received == NORMAL_BUS0_LAYOUT) and reads < 24 and clock() < deadline:
    if malformed and ((gm_probe is None and volvo_probe is None and tesla_probe is None) or gm_malformed):
      break
    observe(wait_for_one=True)
  if not malformed and received == NORMAL_BUS0_LAYOUT and normal_bus0_queries():
    return 'ford'
  if gm_probe is not None and not gm_malformed and gm_probe(gm_received):
    return 'gm'
  if volvo_probe is not None and not gm_malformed and volvo_probe(gm_received):
    return 'volvo'
  if tesla_probe is not None and not gm_malformed and tesla_probe(gm_received):
    return 'tesla'
  return None
