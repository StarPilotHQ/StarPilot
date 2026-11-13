import math
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openpilot.cereal import log, messaging
from openpilot.starpilot.gps.phone import PhoneGpsPublisher, phone_gps, read_phone_gps, phone_gps_status
from openpilot.starpilot.gps import source
from openpilot.starpilot.gps.source import GPS_SERVICES, PHONE_MAX_AGE_NS, gps_mode, select_location, select_source
from openpilot.starpilot.navigation.runtime import publish_status
from openpilot.starpilot.navigation.status import NavigationStatusSource

NOW = 101_000_000_000
PATH = Path(tempfile.gettempdir()) / "phone_gps.json"
FIX = {"latitude": 40., "longitude": -110., "horizontalAccuracy": 5., "ageMs": 300, "altitude": 100., "speed": 20., "bearing": 90.,
       "bearingAccuracy": 10., "speedAccuracy": 1., "timestamp": 1_790_000_000_000}


@pytest.fixture(autouse=True)
def isolated_handoff(tmp_path, monkeypatch):
  monkeypatch.setitem(globals(), "PATH", tmp_path / "phone_gps.json")


class Messages:
  def __init__(self):
    self.data = {}
    self.valid = dict.fromkeys(GPS_SERVICES, False)
    self.logMonoTime = dict.fromkeys(GPS_SERVICES, 0)
    self.alive = dict.fromkeys(GPS_SERVICES, True)
    self.seen = dict.fromkeys(GPS_SERVICES, True)
    self.recv_time = dict.fromkeys(GPS_SERVICES, 0.)

  def __getitem__(self, key):
    return self.data[key]

  def update(self, _timeout):
    pass

  def send(self, service, message):
    with log.Event.from_bytes(message.to_bytes()) as event:
      value = getattr(event, service).to_dict()
      if service == "starpilotCarState":
        value["gps"] = SimpleNamespace(**getattr(event, service).gps.to_dict())
      if service == "starpilotNavigation":
        value["phoneGps"] = SimpleNamespace(**getattr(event, service).phoneGps.to_dict())
      self.data[service] = SimpleNamespace(**value)
      self.valid[service] = event.valid
      self.logMonoTime[service] = event.logMonoTime
      self.recv_time[service] = event.logMonoTime / 1e9


class Bus:
  def __init__(self, sm, now):
    self.sm, self.now = sm, now

  def send(self, service, message):
    message.logMonoTime = self.now
    self.sm.send(service, message)


def publish(sm, payload=FIX, now=NOW, tmp_path=None):
  """Galaxy hands the fix over; navigationd publishes it inside starpilotNavigation."""
  path = (tmp_path or PATH.parent) / "phone_gps.json"
  result = PhoneGpsPublisher(path=path, mono_clock=lambda: now).publish(dict(payload))
  publish_status(Bus(sm, now), {"status": "idle"}, read_phone_gps(now, path))
  return result


def test_phone_is_used_only_without_another_fix():
  sm = Messages()
  assert publish(sm) == {"ok": True}
  stamp, gps = select_location(sm, NOW)
  assert stamp == NOW - 300_000_000 and gps.longitude == -110. and gps.bearingDeg == 90.

  car = messaging.new_message("starpilotCarState", valid=True, logMonoTime=NOW)
  car.starpilotCarState.gps = {"sourceMonoTime": NOW - 1, "latitude": 41., "longitude": -111., "horizontalAccuracy": 5., "hasFix": True}
  sm.send("starpilotCarState", car)
  assert select_location(sm, NOW)[1].longitude == -111.

  native = messaging.new_message("gpsLocation", valid=True, logMonoTime=NOW - 10_000_000)
  native.gpsLocation = {"source": "qcomdiag", "hasFix": True, "horizontalAccuracy": 5., "longitude": -90., "latitude": 30.}
  sm.send("gpsLocation", native)
  assert select_location(sm, NOW)[1].longitude == -90.


def test_phone_fix_is_held_through_sparse_updates_then_expires():
  sm = Messages()
  publish(sm)
  stamp = NOW - 300_000_000
  assert select_location(sm, NOW + 2_300_000_000) is not None
  assert select_location(sm, stamp + PHONE_MAX_AGE_NS) is not None
  assert select_location(sm, stamp + PHONE_MAX_AGE_NS + 1) is None


def test_navigation_leaves_phone_empty_without_a_fresh_fix(tmp_path):
  sm = Messages()
  publish(sm, tmp_path=tmp_path)
  later = NOW + PHONE_MAX_AGE_NS + 1_000_000_000
  assert read_phone_gps(later, tmp_path / "phone_gps.json") is None
  publish_status(Bus(sm, later), {"status": "idle"}, None)
  assert select_location(sm, later) is None


def test_phone_handoff_ignores_missing_and_damaged_files(tmp_path):
  assert read_phone_gps(NOW, tmp_path / "missing.json") is None
  (tmp_path / "bad.json").write_text("{")
  assert read_phone_gps(NOW, tmp_path / "bad.json") is None


def test_unknown_vertical_accuracy_is_not_used():
  gps = phone_gps({**FIX, "verticalAccuracy": -1}, NOW)
  assert math.isnan(gps["verticalAccuracy"])


@pytest.mark.parametrize("field", ("altitude", "speed", "bearing", "bearingAccuracy", "speedAccuracy"))
def test_partial_fix_preserves_position_with_unknown_measurement(field):
  payload = dict(FIX)
  payload.pop(field)
  gps = phone_gps(payload, NOW)
  assert gps['hasFix'] and gps['latitude'] == FIX['latitude']
  key = {'bearing': 'bearingDeg', 'bearingAccuracy': 'bearingAccuracyDeg'}.get(field, field)
  assert math.isnan(gps[key])
  if field == 'bearingAccuracy':
    assert math.isnan(gps['bearingDeg'])


@pytest.mark.parametrize("change", [{"ageMs": 2500}, {"ageMs": -1}, {"latitude": 91.}, {"latitude": 0., "longitude": 0.},
                                    {"horizontalAccuracy": 0}, {"latitude": True}, {"extra": 1}, {"timestamp": 1.5}])
def test_invalid_fixes_are_rejected(change):
  with pytest.raises(ValueError):
    phone_gps({**FIX, **change}, NOW)


def test_inaccurate_phone_fix_is_published_but_not_selected():
  sm = Messages()
  publish(sm, {**FIX, "horizontalAccuracy": 60.})
  assert sm.valid["starpilotNavigation"] and select_location(sm, NOW) is None


def test_navigation_status_ranks_phone_below_native():
  sm = Messages()
  with patch('openpilot.cereal.messaging.SubMaster', return_value=sm):
    status = NavigationStatusSource(mono_clock=lambda: NOW - 1_000_000_000, boot_clock=lambda: NOW)
  status.mono_clock = lambda: NOW
  publish(sm)
  assert (status.map_position() or {})['longitude'] == -110.
  native = messaging.new_message("gpsLocation", valid=True, logMonoTime=NOW - 500_000_000)
  native.gpsLocation = {"source": "qcomdiag", "hasFix": True, "horizontalAccuracy": 5., "longitude": -90., "latitude": 30.}
  sm.send("gpsLocation", native)
  assert (status.map_position() or {})['longitude'] == -90.


def native(sm):
  message = messaging.new_message("gpsLocation", valid=True, logMonoTime=NOW - 10_000_000)
  message.gpsLocation = {"source": "qcomdiag", "hasFix": True, "horizontalAccuracy": 5., "longitude": -90., "latitude": 30.}
  sm.send("gpsLocation", message)


def test_gps_modes_choose_phone_first_or_never():
  sm = Messages()
  publish(sm)
  assert select_location(sm, NOW, "comma") is None
  native(sm)
  assert select_location(sm, NOW, "backup")[1].longitude == -90.
  assert select_location(sm, NOW, "comma")[1].longitude == -90.
  assert select_location(sm, NOW, "phone")[1].longitude == -110.
  assert select_location(sm, NOW + 2_300_000_000, "phone")[1].longitude == -110.


def test_gps_mode_follows_navigation_settings(tmp_path, monkeypatch):
  monkeypatch.setattr("openpilot.starpilot.storage.starpilot_storage_root", lambda: tmp_path)
  monkeypatch.setattr(source, "_mode", [-math.inf, "backup"])
  assert gps_mode(10.) == "backup"
  (tmp_path / "navigation").mkdir()
  (tmp_path / "navigation" / "settings.json").write_text('{"gpsSource": "phone"}')
  assert gps_mode(10.5) == "backup"
  assert gps_mode(11.) == "phone"
  (tmp_path / "navigation" / "settings.json").write_text('{"gpsSource": "tunnel"}')
  assert gps_mode(12.) == "backup"


@pytest.mark.parametrize("mode,longitude,name", [("backup", -90., "comma"), ("phone", -110., "phone"), ("comma", -90., "comma")])
def test_navigation_status_reports_the_source_it_follows(mode, longitude, name):
  sm = Messages()
  with patch('openpilot.cereal.messaging.SubMaster', return_value=sm):
    status = NavigationStatusSource(mono_clock=lambda: NOW - 1_000_000_000, boot_clock=lambda: NOW)
  status.mono_clock = lambda: NOW
  publish(sm)
  native(sm)
  with patch('openpilot.starpilot.navigation.status.gps_mode', return_value=mode):
    position = status.map_position()
  assert position is not None and position['longitude'] == longitude and position['source'] == name
  assert 0 < position['validForMs'] <= 2500


def test_phone_map_lease_is_capped_without_shortening_source_timeout():
  sm = Messages()
  with patch('openpilot.cereal.messaging.SubMaster', return_value=sm):
    status = NavigationStatusSource(mono_clock=lambda: NOW - 1_000_000_000, boot_clock=lambda: NOW)
  clock = [NOW]
  status.mono_clock = lambda: clock[0]
  status.boot_clock = lambda: clock[0] + 1_000_000_000
  publish(sm)
  with patch('openpilot.starpilot.navigation.status.gps_mode', return_value='backup'):
    position = status.map_position()
    assert position is not None and position['validForMs'] == 2500
    clock[0] += 8_000_000_000
    position = status.map_position()
    assert position is not None and position['validForMs'] == 1700
    clock[0] += 2_000_000_000
    assert status.map_position() is None


def test_car_gps_ranks_against_the_phone_in_every_mode():
  sm = Messages()
  publish(sm)
  car = messaging.new_message("starpilotCarState", valid=True, logMonoTime=NOW)
  car.starpilotCarState.gps = {"sourceMonoTime": NOW - 1, "latitude": 41., "longitude": -111., "horizontalAccuracy": 5., "hasFix": True}
  sm.send("starpilotCarState", car)
  assert select_location(sm, NOW, "backup")[1].longitude == -111.
  assert select_location(sm, NOW, "comma")[1].longitude == -111.
  assert select_location(sm, NOW, "phone")[1].longitude == -110.
  with patch('openpilot.cereal.messaging.SubMaster', return_value=sm):
    status = NavigationStatusSource(mono_clock=lambda: NOW - 1_000_000_000, boot_clock=lambda: NOW)
  status.mono_clock = lambda: NOW
  for mode, name in (("backup", "car"), ("comma", "car"), ("phone", "phone")):
    with patch('openpilot.starpilot.navigation.status.gps_mode', return_value=mode):
      assert (status.map_position() or {})['source'] == name


def test_selected_source_is_named():
  sm = Messages()
  assert select_source(sm, NOW) is None
  publish(sm)
  assert select_source(sm, NOW)[0] == "phone"
  car = messaging.new_message("starpilotCarState", valid=True, logMonoTime=NOW)
  car.starpilotCarState.gps = {"sourceMonoTime": NOW - 1, "latitude": 41., "longitude": -111., "horizontalAccuracy": 5., "hasFix": True}
  sm.send("starpilotCarState", car)
  assert select_source(sm, NOW)[0] == "car"
  native(sm)
  assert select_source(sm, NOW)[0] == "comma"
  assert select_source(sm, NOW, "phone")[0] == "phone"


def test_zero_bearing_uncertainty_is_known():
  from openpilot.starpilot.gps.source import bearing
  gps = SimpleNamespace(**phone_gps({**FIX, "bearingAccuracy": 0}, NOW))
  assert bearing(gps) == 90.


def test_unknown_speed_keeps_position_without_heading_prediction(tmp_path):
  from openpilot.starpilot.navigation.runtime import location
  sm = Messages()
  publish(sm, {key: value for key, value in FIX.items() if key not in ('speed', 'bearing', 'bearingAccuracy')}, tmp_path=tmp_path)
  position = location(sm, NOW)
  assert position[1:] == ((-110., 40.), None, None)


def test_accuracy_drop_preserves_good_fix_until_original_expiry(tmp_path):
  path = tmp_path / 'gps.json'
  now = [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  publisher.publish(FIX)
  original = read_phone_gps(now[0], path)[0]
  now[0] += 1_000_000_000
  assert publisher.publish({**FIX, 'horizontalAccuracy': 26.})['reason'] == 'poorAccuracy'
  assert read_phone_gps(now[0], path)[0] == original
  assert phone_gps_status(now[0], path)['usable']
  now[0] = original + PHONE_MAX_AGE_NS + 1
  publisher.publish({**FIX, 'horizontalAccuracy': 26.})
  assert read_phone_gps(now[0], path) is None
  assert not phone_gps_status(now[0], path)['usable']
  publisher.publish({**FIX, 'timestamp': FIX['timestamp'] + 11000})
  assert read_phone_gps(now[0], path) is not None


def test_duplicate_and_reordered_fixes_do_not_renew_age(tmp_path):
  path = tmp_path / 'gps.json'
  now = [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  publisher.publish(FIX)
  stamp = read_phone_gps(now[0], path)[0]
  now[0] += 1_000_000_000
  for timestamp in (FIX['timestamp'], FIX['timestamp'] - 1000):
    assert publisher.publish({**FIX, 'timestamp': timestamp})['reason'] == 'outOfOrder'
    assert read_phone_gps(now[0], path)[0] == stamp


def test_timestamp_deltas_detect_added_queue_delay(tmp_path):
  path = tmp_path / 'gps.json'
  now = [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  publisher.publish(FIX)
  now[0] += 5_000_000_000
  assert publisher.publish({**FIX, 'timestamp': FIX['timestamp'] + 1000})['reason'] == 'delayed'
  # A current measurement recovers; rejected requests did not move the anchor.
  assert publisher.publish({**FIX, 'timestamp': FIX['timestamp'] + 5000}) == {'ok': True}
  assert read_phone_gps(now[0], path)[0] == now[0] - 300_000_000


def test_clock_change_can_recover_after_original_fix_expires(tmp_path):
  path = tmp_path / 'gps.json'
  now = [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  publisher.publish(FIX)
  now[0] += PHONE_MAX_AGE_NS + 1
  assert publisher.publish({**FIX, 'timestamp': FIX['timestamp'] - 60000}) == {'ok': True}


def test_invalid_upload_reports_reason_without_erasing_last_fix(tmp_path):
  path = tmp_path / 'gps.json'
  publisher = PhoneGpsPublisher(path, lambda: NOW)
  publisher.publish(FIX)
  with pytest.raises(ValueError):
    publisher.publish({**FIX, 'ageMs': 5000})
  status = phone_gps_status(NOW, path)
  assert status['reason'] == 'Location is too old' and status['usable']


def stream_fix(sequence=1, sample=1_000_000_000, **changes):
  return {**FIX, 'sessionId': 'fixture', 'sequence': sequence, 'sampleElapsedRealtimeNanos': sample,
          'sentElapsedRealtimeNanos': sample + 300_000_000, **changes}


def test_monotonic_stream_replay_protection_survives_hold_expiry_and_restart(tmp_path):
  path, now = tmp_path / 'gps.json', [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  assert publisher.publish(stream_fix())['accepted']
  original = read_phone_gps(now[0], path)[0]
  now[0] += PHONE_MAX_AGE_NS + 1
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  assert publisher.publish(stream_fix())['reason'] == 'outOfOrder'
  assert read_phone_gps(now[0], path) is None
  # A higher sequence carrying an older measurement is also a replay.
  assert publisher.publish(stream_fix(2, 900_000_000))['reason'] == 'outOfOrder'
  assert publisher.publish(stream_fix(3, 2_000_000_000))['reason'] == 'delayed'
  assert publisher.publish(stream_fix(4, 12_000_000_000, timestamp=FIX['timestamp'] - 60000))['accepted']
  assert read_phone_gps(now[0], path)[0] > original


def test_retired_session_cannot_take_over_after_restart_or_legacy_upload(tmp_path):
  path, now = tmp_path / 'gps.json', [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  publisher.publish(stream_fix())
  now[0] += 1_000_000_000
  assert publisher.publish(stream_fix(sessionId='new'))['accepted']
  now[0] += 1_000_000_000
  publisher.publish({**FIX, 'timestamp': FIX['timestamp'] + 2000})
  now[0] += PHONE_MAX_AGE_NS
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  assert publisher.publish(stream_fix(99, 20_000_000_000))['reason'] == 'retiredSession'


def test_android_calibration_contract_bounds_first_packet_transport_delay(tmp_path):
  path, now = tmp_path / 'gps.json', [10_200_000_000]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  payload = stream_fix(ageMs=200, sentElapsedRealtimeNanos=1_200_000_000, serverMonoTimeNs=10_100_000_000)
  assert publisher.publish(payload)['accepted']
  assert read_phone_gps(now[0], path)[0] == 9_850_000_000
  diagnostics = phone_gps_status(now[0], path)
  assert diagnostics['timingBounded'] and diagnostics['timingUncertaintyMs'] == 150
  assert diagnostics['fixAgeMs'] == 350
  now[0] += 3_000_000_000
  assert publisher.publish(stream_fix(2, 2_000_000_000, serverMonoTimeNs=10_300_000_000))['reason'] == 'delayed'
  assert phone_gps_status(now[0], path)['rejectedCount'] == 1


@pytest.mark.parametrize('change', [
  {'sessionId': ''}, {'sessionId': 'x' * 129}, {'sequence': True}, {'sequence': -1}, {'sequence': 1.5},
  {'sampleElapsedRealtimeNanos': -1}, {'sentElapsedRealtimeNanos': 0}, {'ageMs': 1},
  {'serverMonoTimeNs': True}, {'serverMonoTimeNs': NOW + 50_000_001},
])
def test_invalid_stream_fields_preserve_good_fix(tmp_path, change):
  path = tmp_path / 'gps.json'
  publisher = PhoneGpsPublisher(path, lambda: NOW)
  publisher.publish(FIX)
  with pytest.raises(ValueError):
    publisher.publish(stream_fix(**change))
  assert read_phone_gps(NOW, path)[0] == NOW - 300_000_000


def test_position_jump_preserves_fix_and_reacquires_after_expiry(tmp_path):
  path, now = tmp_path / 'gps.json', [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  publisher.publish(stream_fix())
  original = read_phone_gps(now[0], path)[0]
  now[0] += 1_000_000_000
  assert publisher.publish(stream_fix(2, 2_000_000_000, longitude=-100))['reason'] == 'implausibleJump'
  assert read_phone_gps(now[0], path)[0] == original
  # Rejected excursions do not consume sequence or move the motion anchor.
  assert publisher.publish(stream_fix(2, 2_000_000_000, longitude=-109.999))['accepted']
  now[0] += PHONE_MAX_AGE_NS + 1
  assert publisher.publish(stream_fix(3, 13_000_000_000, longitude=-100))['accepted']


def test_stream_metadata_stays_out_of_navigation_wire_and_exposes_unbounded_timing(tmp_path):
  sm = Messages()
  assert publish(sm, stream_fix(), tmp_path=tmp_path)['accepted']
  assert select_location(sm, NOW)[1].longitude == -110
  status = phone_gps_status(NOW, tmp_path / 'phone_gps.json')
  assert status['timingBounded'] is False and status['acceptedCount'] == 1


def test_fresh_calibration_recovers_from_slow_initial_probe(tmp_path):
  path, now = tmp_path / 'gps.json', [NOW]
  publisher = PhoneGpsPublisher(path, lambda: now[0])
  # A stationary measurement can still be useful with an initially slow probe.
  assert publisher.publish(stream_fix(speed=0, speedAccuracy=0, serverMonoTimeNs=NOW - 1_000_000_000))['accepted']
  now[0] += 5_000_000_000
  result = publisher.publish(stream_fix(2, 2_000_000_000, serverMonoTimeNs=now[0] - 10_000_000))
  assert result['accepted']
  assert read_phone_gps(now[0], path)[0] == now[0] - 360_000_000


def test_timing_uncertainty_inflates_motion_accuracy(tmp_path):
  path = tmp_path / 'gps.json'
  publisher = PhoneGpsPublisher(path, lambda: NOW)
  publisher.publish(stream_fix(serverMonoTimeNs=NOW - 100_000_000))
  assert read_phone_gps(NOW, path)[1].horizontalAccuracy == pytest.approx(5 + 23 * .15)
  # Transport timing alone can make a nominally accurate moving fix unusable.
  path2 = tmp_path / 'slow.json'
  result = PhoneGpsPublisher(path2, lambda: NOW).publish(stream_fix(serverMonoTimeNs=NOW - 1_000_000_000))
  assert result['reason'] == 'poorAccuracy'
  assert read_phone_gps(NOW, path2) is None


def test_boot_fence_rejects_old_fix_even_when_new_uptime_is_later(tmp_path):
  path = tmp_path / 'gps.json'
  with patch('openpilot.starpilot.gps.phone._boot_id', return_value='boot-a'):
    PhoneGpsPublisher(path, lambda: NOW).publish(stream_fix())
  with patch('openpilot.starpilot.gps.phone._boot_id', return_value='boot-b'):
    assert read_phone_gps(NOW + 1_000_000_000, path) is None
    assert phone_gps_status(NOW + 1_000_000_000, path) is None
    # New boot's session clock is independent, even if the app reuses an ID.
    assert PhoneGpsPublisher(path, lambda: NOW + 1_000_000_000).publish(stream_fix())['accepted']


def test_damaged_receiver_metadata_does_not_crash_upload(tmp_path):
  import json
  path = tmp_path / 'gps.json'
  publisher = PhoneGpsPublisher(path, lambda: NOW)
  publisher.publish(FIX)
  value = json.loads(path.read_text())
  value['_stream'] = {'received': 'bad', 'retired': 5}
  path.write_text(json.dumps(value))
  path.with_name(path.name + '.status').write_text('{"acceptedCount": null, "rejectedCount": "bad"}')
  assert PhoneGpsPublisher(path, lambda: NOW + 1_000_000_000).publish(stream_fix())['accepted']
  assert phone_gps_status(NOW + 1_000_000_000, path)['acceptedCount'] == 1
