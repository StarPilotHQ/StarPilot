"""Synthetic acquisition and perception around the real Subaru production callers."""

import os
from contextlib import ExitStack, contextmanager
import json
from unittest.mock import patch

from opendbc.can import CANPacker
from opendbc.car import structs
from opendbc.car import car_helpers
from opendbc.car.fw_versions import match_fw_to_car
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.selfdrive.car.card import Car
from openpilot.selfdrive.selfdrived import selfdrived as selfdrived_module
from openpilot.starpilot.lateral.tests.test_lane_runtime import feed
from openpilot.starpilot.vehicle_selection import encode


CONTROLS_PERCEPTION_SERVICES = frozenset(('deviceState', 'vehicleParameters', 'lateralDelay', 'modelV2'))
CONTROLS_OTHER_OWNERS = frozenset(('carState', 'carOutput', 'selfdriveState', 'onroadEvents', 'longitudinalPlan', 'aolAxisState', 'aolSafetyWire'))


def feed_perception_only(controls, timestamp, tick, **kwargs):
  """Use shared perception fixtures without submitting synthetic actual-caller publications."""
  pending, observed = [], []

  def capture(cur_time, events):
    for event in events:
      service = event.which()
      assert service in CONTROLS_PERCEPTION_SERVICES | CONTROLS_OTHER_OWNERS, service
      delivered = service in CONTROLS_PERCEPTION_SERVICES
      observed.append({'service': service, 'source_ns': int(event.logMonoTime), 'valid': bool(event.valid), 'delivered': delivered})
      if delivered:
        pending.append(event)

  with patch.object(controls.sm, 'update_msgs', side_effect=capture):
    feed(controls, timestamp, tick, **kwargs)
  controls.sm.update_msgs(timestamp / 1e9, pending)
  return {'observed': observed, 'delivered': [event.which() for event in pending]}


def firmware_records(candidate, versions, brand):
  return [
    structs.CarParams.CarFw(ecu=ecu, address=address, subAddress=0 if sub is None else sub, fwVersion=values[0], brand=brand)
    for (ecu, address, sub), values in versions[candidate].items()
  ]


@contextmanager
def actual_factory(candidate, fingerprint, fw, vin, *, manual, alpha=False, release=False, aol=True, preferences=None, firmware_candidate=None):
  with (
    OpenpilotPrefix(),
    patch.object(selfdrived_module, 'SIMULATION', True),
    patch.object(selfdrived_module, 'REPLAY', False),
    patch('openpilot.selfdrive.car.card.REPLAY', False),
    patch.dict(os.environ, {'SIMULATION': '1', 'AOL_REPLAY_RUNTIME': '0', 'SKIP_FW_QUERY': '', 'FINGERPRINT': '', 'DISABLE_FW_CACHE': '1'}),
  ):
    os.environ.pop('REPLAY', None)
    params = Params()
    for key, value in [
      ('OpenpilotEnabledToggle', True),
      ('AlphaLongitudinalEnabled', alpha),
      ('IsReleaseBranch', release),
      ('AlwaysOnLateral', aol),
      ('SafeMode', False),
      ('IsOffroad', False),
      ('ControlsReady', False),
    ]:
      params.put_bool(key, value, block=True)
    for key, value in (preferences or {}).items():
      if type(value) is bool:
        params.put_bool(key, value, block=True)
      else:
        params.put(key, value, block=True)
    params.put('VehicleSelection', json.loads(encode(str(candidate) if manual else None)), block=True)
    _, matches = match_fw_to_car(fw, vin, log=False)
    if not manual:
      assert matches == {firmware_candidate or candidate}, (candidate, matches)
    with ExitStack() as io:
      io.enter_context(patch('openpilot.selfdrive.car.card.messaging.recv_one_retry', return_value=messaging.new_message('can', 1)))
      io.enter_context(patch('openpilot.selfdrive.car.card.can_comm_callbacks', return_value=(lambda *a, **kw: [], lambda *a, **kw: None)))
      io.enter_context(patch('openpilot.selfdrive.car.card.obd_callback', return_value=lambda _: None))
      io.enter_context(patch('openpilot.selfdrive.car.card.messaging.recv_one_or_none', return_value=None))
      io.enter_context(patch.object(car_helpers, 'get_vin', return_value=(-1, -1, vin)))
      io.enter_context(patch.object(car_helpers, 'get_present_ecus', return_value=set()))
      io.enter_context(patch.object(car_helpers, 'get_fw_versions_ordered', return_value=fw))
      # Acquisition boundary only: retain production fingerprint FW matcher and get_car.
      io.enter_context(patch.object(car_helpers, 'can_fingerprint', return_value=(None, fingerprint)))
      io.enter_context(patch.object(car_helpers, 'normal_bus_topology', wraps=car_helpers.normal_bus_topology))
      card = Car()
    cp = card.CP
    assert cp.carFingerprint == candidate, (cp.carFingerprint, candidate)
    assert cp.fingerprintSource == (structs.CarParams.FingerprintSource.fixed if manual else structs.CarParams.FingerprintSource.fw)
    with structs.CarParams.from_bytes(params.get('CarParams')) as published:
      assert published.to_dict() == cp.to_dict()
    if aol:
      assert card.aol_qualified and card.aol_card_intent is not None
    assert cp.alternativeExperience == (32 if aol else 0)
    yield card, params, matches


class CompleteCan:
  """Pack every subscribed physical source; counters/checksums use real packer."""

  def __init__(self, ci):
    self.ci = ci
    ci.update([])
    self.packers = {bus: CANPacker(parser.dbc.name) for bus, parser in ci.can_parsers.items()}

  def frames(self, values, *, tick, slow=(), omit=()):
    result = []
    for logical_bus, parser in self.ci.can_parsers.items():
      for address in sorted(parser.addresses):
        name = parser.dbc.addr_to_msg[address].name
        if (logical_bus, name) in omit or (name in slow and tick % 5):
          continue
        intended = values.get(name, {})
        assert intended.keys() <= parser.dbc.name_to_msg[name].sigs.keys(), (name, intended)
        result.append(self.packers[logical_bus].make_can_msg(name, parser.bus, intended))
    return result
