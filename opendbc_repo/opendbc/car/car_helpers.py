import os
import time

from opendbc.car import gen_empty_fingerprint, fw_versions
from opendbc.car.can_definitions import CanRecvCallable, CanSendCallable
from opendbc.car.carlog import carlog
from opendbc.car.structs import CarParams, CarParamsT
from opendbc.car.fingerprints import eliminate_incompatible_cars, all_legacy_fingerprint_cars
from opendbc.car.fw_versions import (ObdCallback, get_fw_versions, get_fw_versions_ordered,
                                     get_present_ecus, match_fw_to_car)
from opendbc.car.mock.values import CAR as MOCK
from opendbc.car.gm.pedal_capability import automatic_bolt_candidate
from opendbc.car.gm.discovery import automatic_trax_candidate, automatic_volt_candidate, normal_bus0_topology as gm_normal_bus0_topology
from opendbc.car.ford.discovery import (normal_bus0_selection as ford_normal_bus0_selection,
                                       normal_bus0_queries as ford_normal_bus0_queries, normal_bus_query_brand)
from opendbc.car.volvo.discovery import normal_bus_topology as volvo_normal_bus_topology
from opendbc.car.hyundai.discovery import normal_bus_topology
from opendbc.car.honda.discovery import normal_bus_topology as honda_normal_bus_topology
from opendbc.car.tesla.discovery import normal_bus0_topology as tesla_normal_bus0_topology
from opendbc.car.values import BRANDS
from opendbc.car.vin import get_vin, is_valid_vin, VIN_UNKNOWN

FRAME_FINGERPRINT = 100  # 1s


def load_interfaces(brand_names):
  ret = {}
  for brand_name in brand_names:
    path = f'opendbc.car.{brand_name}'
    CarInterface = __import__(path + '.interface', fromlist=['CarInterface']).CarInterface
    for model_name in brand_names[brand_name]:
      ret[model_name] = CarInterface
  return ret


def _get_interface_names() -> dict[str, list[str]]:
  # returns a dict of brand name and its respective models
  brand_names = {}
  for brand in BRANDS:
    brand_name = brand.__module__.split('.')[-2]
    brand_names[brand_name] = [model.value for model in brand]

  return brand_names


# imports from directory opendbc/car/<name>/
interface_names = _get_interface_names()
interfaces = load_interfaces(interface_names)


def can_fingerprint(can_recv: CanRecvCallable, *, vin: str = VIN_UNKNOWN,
                    camera_cruise_messages: set[bytes] | None = None) -> tuple[str | None, dict[int, dict]]:
  finger = gen_empty_fingerprint()
  candidate_cars = {i: all_legacy_fingerprint_cars() for i in [0, 1]}  # attempt fingerprint on both bus 0 and 1
  frame = 0
  car_fingerprint = None
  done = False
  if camera_cruise_messages is None:
    camera_cruise_messages = set()

  while not done:
    # can_recv(wait_for_one=True) may return zero or multiple packets, so we increment frame for each one we receive
    can_packets = can_recv(wait_for_one=True)
    for can_packet in can_packets:
      for can in can_packet:
        # Stock camera only: host echoes and malformed DLCs are not capability evidence.
        if can.src == 2 and can.address == 0x370 and len(can.dat) == 6:
          camera_cruise_messages.add(bytes(can.dat))
        # The fingerprint dict is generated for all buses, this way the car interface
        # can use it to detect a (valid) multipanda setup and initialize accordingly
        if can.src < 128:
          if can.src not in finger:
            finger[can.src] = {}
          finger[can.src][can.address] = len(can.dat)

        for b in candidate_cars:
          # Ignore extended messages and VIN query response.
          if can.src == b and can.address < 0x800 and can.address not in (0x7df, 0x7e0, 0x7e8):
            candidate_cars[b] = eliminate_incompatible_cars(can, candidate_cars[b])

      # if we only have one car choice and the time since we got our first
      # message has elapsed, exit
      for b in candidate_cars:
        if len(candidate_cars[b]) == 1 and frame > FRAME_FINGERPRINT:
          # fingerprint done
          car_fingerprint = candidate_cars[b][0]

      # bail if no cars left or we've been waiting for more than 2s
      failed = (all(len(cc) == 0 for cc in candidate_cars.values()) and frame > FRAME_FINGERPRINT) or frame > 200
      succeeded = car_fingerprint is not None
      done = failed or succeeded

      frame += 1

  car_fingerprint = automatic_volt_candidate(car_fingerprint, finger, candidate_cars)
  car_fingerprint = automatic_trax_candidate(car_fingerprint, finger, candidate_cars, vin)
  return automatic_bolt_candidate(car_fingerprint, finger, camera_cruise_messages, vin), finger


def _gm_fw_uses_normal_bus0() -> bool:
  configured = fw_versions.FW_QUERY_CONFIGS['gm'].requests
  effective = [request for brand, _, request in fw_versions.REQUESTS if brand == 'gm']
  return bool(configured) and bool(effective) and all(request.bus == 0 for request in (*configured, *effective))


def _tesla_fw_uses_normal_bus0() -> bool:
  configured = fw_versions.FW_QUERY_CONFIGS['tesla'].requests
  effective = [request for brand, _, request in fw_versions.REQUESTS if brand == 'tesla']
  return bool(configured) and bool(effective) and all(request.bus == 0 for request in (*configured, *effective))


# **** for use live only ****
def fingerprint(can_recv: CanRecvCallable, can_send: CanSendCallable, set_obd_multiplexing: ObdCallback,
                cached_params: CarParamsT | None, *, known_gm_bus0: bool = False, known_ford_bus0: bool = False,
                known_tesla_bus0: bool = False
                ) -> tuple[str | None, dict, str, list[CarParams.CarFw], CarParams.FingerprintSource, bool]:
  if known_ford_bus0 and (known_gm_bus0 or not ford_normal_bus0_queries()):
    raise ValueError('Ford discovery topology no longer uses normal bus 0')
  if known_gm_bus0 and not _gm_fw_uses_normal_bus0():
    raise ValueError('GM discovery topology no longer uses normal bus 0')
  if known_tesla_bus0 and (known_gm_bus0 or known_ford_bus0 or not _tesla_fw_uses_normal_bus0()):
    raise ValueError('Tesla discovery topology no longer uses normal bus 0')
  fixed_fingerprint = os.environ.get('FINGERPRINT', "")
  skip_fw_query = os.environ.get('SKIP_FW_QUERY', False)
  disable_fw_cache = os.environ.get('DISABLE_FW_CACHE', False)
  ecu_rx_addrs = set()

  normal_bus_packets = []

  def observe_normal_bus(**kwargs):
    packets = can_recv(**kwargs)
    normal_bus_packets.extend(packets)
    return packets

  start_time = time.monotonic()
  if not skip_fw_query:
    if cached_params is not None and cached_params.brand != "mock" and len(cached_params.carFw) > 0 and \
       (cached_params.carVin != VIN_UNKNOWN or os.environ.get("REPLAY")) and not disable_fw_cache:
      carlog.warning("Using cached CarParams")
      vin_rx_addr, vin_rx_bus, vin = -1, -1, cached_params.carVin
      car_fw = list(cached_params.carFw)
      cached = True
    else:
      carlog.warning("Getting VIN & FW versions")
      if known_gm_bus0:
        # An explicit registered GM selection already identifies the query family.
        # Current GM requests use normal bus 0. Keep live VIN/FW collection and
        # the subsequent live CAN layout; scope queries to its declared normal bus.
        set_obd_multiplexing(False)
        # Preserve the sendcan subscriber connection warmup of the normal OBD path.
        warmup_remaining = 0.1 - (time.monotonic() - start_time)
        if warmup_remaining > 0:
          time.sleep(warmup_remaining)
        vin_rx_addr, vin_rx_bus, vin = get_vin(can_recv, can_send, (0,))
        car_fw = get_fw_versions(can_recv, can_send, set_obd_multiplexing, query_brand='gm')
        ecu_rx_addrs = {(fw.responseAddress, fw.subAddress or None, fw.bus) for fw in car_fw if fw.responseAddress > 0}
      elif known_ford_bus0:
        # A registered CAN-FD selection identifies Ford's query family, not live
        # ECU authority. Keep normal CAN2 wiring during discovery: the unrelated
        # all-brand OBD mux scan can produce a CAN2 error/interrupt storm.
        set_obd_multiplexing(False)
        warmup_remaining = 0.1 - (time.monotonic() - start_time)
        if warmup_remaining > 0:
          time.sleep(warmup_remaining)
        vin_rx_addr, vin_rx_bus, vin = get_vin(can_recv, can_send, (0,))
        car_fw = get_fw_versions(can_recv, can_send, set_obd_multiplexing,
                                 query_brand='ford', normal_bus_only=True)
        ecu_rx_addrs = {(fw.responseAddress, fw.subAddress or None, fw.bus) for fw in car_fw if fw.responseAddress > 0}
      elif known_tesla_bus0:
        # A selected Tesla needs only its declared bus0 queries, not an all-brand OBD sweep.
        set_obd_multiplexing(False)
        warmup_remaining = 0.1 - (time.monotonic() - start_time)
        if warmup_remaining > 0:
          time.sleep(warmup_remaining)
        vin_rx_addr, vin_rx_bus, vin = get_vin(can_recv, can_send, (0,))
        car_fw = get_fw_versions(can_recv, can_send, set_obd_multiplexing, query_brand='tesla')
        ecu_rx_addrs = {(fw.responseAddress, fw.subAddress or None, fw.bus) for fw in car_fw if fw.responseAddress > 0}
      elif (normal_brand := normal_bus_query_brand(
          observe_normal_bus, normal_bus_topology,
          gm_probe=gm_normal_bus0_topology if _gm_fw_uses_normal_bus0() else None,
          volvo_probe=volvo_normal_bus_topology if not fw_versions.FW_QUERY_CONFIGS['volvo'].requests else None,
          tesla_probe=tesla_normal_bus0_topology if _tesla_fw_uses_normal_bus0() else None) or
          ('honda' if honda_normal_bus_topology(normal_bus_packets) else None)) is not None:

        set_obd_multiplexing(False)
        warmup_remaining = 0.1 - (time.monotonic() - start_time)
        if warmup_remaining > 0:
          time.sleep(warmup_remaining)
        vin_buses = (0,) if normal_brand in ('ford', 'gm', 'volvo', 'tesla') else (0, 1)
        vin_rx_addr, vin_rx_bus, vin = get_vin(can_recv, can_send, vin_buses)
        car_fw = get_fw_versions(can_recv, can_send, set_obd_multiplexing,
                                 query_brand=normal_brand, normal_bus_only=True)
        ecu_rx_addrs = {(fw.responseAddress, fw.subAddress or None, fw.bus) for fw in car_fw if fw.responseAddress > 0}
      else:
        # enable OBD multiplexing for VIN query
        # NOTE: this takes ~0.1s and is relied on to allow sendcan subscriber to connect in time
        set_obd_multiplexing(True)
        # VIN query only reliably works through OBDII
        vin_rx_addr, vin_rx_bus, vin = get_vin(can_recv, can_send, (0, 1))
        ecu_rx_addrs = get_present_ecus(can_recv, can_send, set_obd_multiplexing)
        car_fw = get_fw_versions_ordered(can_recv, can_send, set_obd_multiplexing, vin, ecu_rx_addrs)
      cached = False

    exact_fw_match, fw_candidates = match_fw_to_car(car_fw, vin)
  else:
    vin_rx_addr, vin_rx_bus, vin = -1, -1, VIN_UNKNOWN
    exact_fw_match, fw_candidates, car_fw = True, set(), []
    cached = False

  if not is_valid_vin(vin):
    carlog.error({"event": "Malformed VIN", "vin": vin})
    vin = VIN_UNKNOWN
  carlog.warning("VIN %s", vin)

  # disable OBD multiplexing for CAN fingerprinting and potential ECU knockouts
  set_obd_multiplexing(False)

  fw_query_time = time.monotonic() - start_time

  # CAN fingerprint
  # drain CAN socket so we get the latest messages
  can_recv()
  camera_cruise_messages: set[bytes] = set()
  car_fingerprint, finger = can_fingerprint(can_recv, vin=vin, camera_cruise_messages=camera_cruise_messages)

  exact_match = True
  source = CarParams.FingerprintSource.can

  # If FW query returns exactly 1 candidate, use it
  if len(fw_candidates) == 1:
    car_fingerprint = list(fw_candidates)[0]
    source = CarParams.FingerprintSource.fw
    exact_match = exact_fw_match

  # Firmware catalogues can use the same broad Bolt aliases as legacy CAN.
  # Refine after both automatic sources, before the explicit override below.
  if not fixed_fingerprint:
    car_fingerprint = automatic_bolt_candidate(car_fingerprint, finger, camera_cruise_messages, vin)

  if fixed_fingerprint:
    car_fingerprint = fixed_fingerprint
    source = CarParams.FingerprintSource.fixed

  carlog.error({"event": "fingerprinted", "car_fingerprint": str(car_fingerprint), "source": source, "fuzzy": not exact_match,
                "cached": cached, "fw_count": len(car_fw), "ecu_responses": list(ecu_rx_addrs), "vin_rx_addr": vin_rx_addr,
                "vin_rx_bus": vin_rx_bus, "fingerprints": repr(finger), "fw_query_time": fw_query_time})

  return car_fingerprint, finger, vin, car_fw, source, exact_match


def get_car(can_recv: CanRecvCallable, can_send: CanSendCallable, set_obd_multiplexing: ObdCallback, alpha_long_allowed: bool,
            is_release: bool, cached_params: CarParamsT | None = None, pre_create_hook=None,
            forced_candidate: str | None = None):
  if forced_candidate is not None and forced_candidate not in interfaces:
    raise ValueError('forced vehicle is not registered')
  if forced_candidate is not None and cached_params is not None and cached_params.carFingerprint != forced_candidate:
    cached_params = None
  explicit_candidate = forced_candidate if forced_candidate is not None else os.environ.get('FINGERPRINT', '')
  known_gm_bus0 = (explicit_candidate in interface_names['gm'] and _gm_fw_uses_normal_bus0())
  known_ford_bus0 = ford_normal_bus0_selection(explicit_candidate)
  known_tesla_bus0 = explicit_candidate in interface_names['tesla'] and _tesla_fw_uses_normal_bus0()
  if known_gm_bus0 and cached_params is not None and (
      cached_params.brand != 'gm' or cached_params.carFingerprint != explicit_candidate):
    cached_params = None
  if known_ford_bus0 and cached_params is not None and (
      cached_params.brand != 'ford' or cached_params.carFingerprint != explicit_candidate):
    cached_params = None
  if known_tesla_bus0 and cached_params is not None and (
      cached_params.brand != 'tesla' or cached_params.carFingerprint != explicit_candidate):
    cached_params = None
  if known_gm_bus0:
    candidate, fingerprints, vin, car_fw, source, exact_match = fingerprint(
      can_recv, can_send, set_obd_multiplexing, cached_params, known_gm_bus0=True)
  elif known_ford_bus0:
    candidate, fingerprints, vin, car_fw, source, exact_match = fingerprint(
      can_recv, can_send, set_obd_multiplexing, cached_params, known_ford_bus0=True)
  elif known_tesla_bus0:
    candidate, fingerprints, vin, car_fw, source, exact_match = fingerprint(
      can_recv, can_send, set_obd_multiplexing, cached_params, known_tesla_bus0=True)
  else:
    candidate, fingerprints, vin, car_fw, source, exact_match = fingerprint(can_recv, can_send, set_obd_multiplexing, cached_params)

  if forced_candidate is not None:
    candidate = forced_candidate
    source = CarParams.FingerprintSource.fixed
    exact_match = True

  if candidate is None:
    carlog.error({"event": "car doesn't match any fingerprints", "fingerprints": repr(fingerprints)})
    candidate = "MOCK"

  if source != CarParams.FingerprintSource.fixed and candidate == 'TOYOTA_PRIUS':
    from opendbc.car.toyota.values import automatic_prius_candidate
    candidate = automatic_prius_candidate(candidate, car_fw)

  CarInterface = interfaces[candidate]
  params_options = {'auto_pedal': False} if source == CarParams.FingerprintSource.fixed and candidate in interface_names['gm'] else {}
  CP: CarParams = CarInterface.get_params(candidate, fingerprints, car_fw, alpha_long_allowed, is_release, docs=False, **params_options)
  CP.carVin = vin
  CP.carFw = car_fw
  CP.fingerprintSource = source
  CP.fuzzyFingerprint = not exact_match

  if pre_create_hook is not None:
    CP = pre_create_hook(CP, candidate, fingerprints, car_fw)

  return interfaces[CP.carFingerprint](CP)


def get_demo_car_params():
  platform = MOCK.MOCK
  CarInterface = interfaces[platform]
  CP = CarInterface.get_non_essential_params(platform)
  return CP
