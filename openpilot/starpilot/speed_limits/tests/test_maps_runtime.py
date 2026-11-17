"""Real v2 cereal packets through the Maps consumer and actual saved owners."""
from dataclasses import replace
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.starpilot.maps import shadow_lifecycle
from openpilot.starpilot.speed_limits import acceptance as acc, map_source as ms, selection as sel
from openpilot.starpilot.speed_limits.map_observation import MapSource, copy_frame
from openpilot.starpilot.speed_limits.runtime import Runtime
from openpilot.starpilot.speed_limits.runtime_settings import parse, read_params
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change


MONO = 1_000_000_000
BOOT = 5_000_000_000


def packet(**changes):
  event = log.Event.new_message()
  event.valid = True
  event.logMonoTime = BOOT
  road = event.init('mapdOut')
  values = {'sampleVersion': 2, 'roadStatus': 'matchedLimit', 'gpsSource': 'external',
    'gpsMonoTime': BOOT-100_000_000, 'computedMonoTime': BOOT, 'sourceGeneration': 1,
    'producerSession': 41, 'speedLimit': 13.4112, 'tileLoaded': True, 'wayId': 123, 'waySelectionType': 'current'}
  values.update(changes)
  for n, v in values.items():
    setattr(road, n, v)
  return event


class SM:
  def __init__(self, event):
    self.data = {'mapdOut': event.mapdOut, 'carState': NS(canTimeout=False),
      'slcDashboardObservation': NS(status='valid', observedMonoTime=MONO,
        validUntilMonoTime=MONO+1_000_000_000, episode=1, producerSessionId='card',
        speedMps=20., carStateLogMonoTime=MONO)}
    self.valid = dict.fromkeys(self.data, True)
    self.alive = dict.fromkeys(self.data, True)
    self.updated = dict.fromkeys(self.data, True)
    self.logMonoTime = dict.fromkeys(self.data, MONO)
    self.logMonoTime['mapdOut'] = int(event.logMonoTime)

  def __getitem__(self, n):
    return self.data[n]


def test_maps_priority_slots_and_defaults_use_actual_runtime_projection():
  sm = SM(packet())
  cp = NS(carFingerprint='vehicle')
  for first, second, third, expected in (('Maps', 'Dashboard', 'Off', sel.Source.MAP),
                                         ('Dashboard', 'Maps', 'Off', sel.Source.DASHBOARD),
                                         ('Vision', 'Maps', 'Off', sel.Source.MAP),
                                         ('Vision', 'Off', 'Maps', sel.Source.MAP),
                                         ('Vision', 'Off', 'Off', None)):
    settings = parse({'SpeedLimitController': True, 'SLCPriority1': first, 'SLCPriority2': second, 'SLCPriority3': third})
    runtime = Runtime(settings, session_id='drive')
    observations = runtime._observations(sm, cp, MONO, BOOT)
    chosen = sel.select_limit(observations, settings.selection)
    assert chosen.selected_source is expected
  settings = parse({})
  assert settings.selection.slots == (sel.Source.VISION, sel.Source.MAP, None)
  chosen = sel.select_limit(Runtime(settings)._observations(sm, cp, MONO, BOOT), settings.selection)
  assert chosen.selected_source is sel.Source.MAP
  assert parse({'SLCPriority1': 'Map Data'}).selection.slots[0] is sel.Source.MAP


def test_expired_map_falls_through_to_current_dashboard_without_renewal():
  sm = SM(packet())
  settings = parse({'SpeedLimitController': True, 'SLCPriority1': 'Maps', 'SLCPriority2': 'Dashboard'})
  runtime = Runtime(settings, session_id='drive')
  cp = NS(carFingerprint='vehicle')
  assert runtime._observations(sm, cp, MONO, BOOT)[sel.Source.MAP].kind is acc.ObservationKind.VALID
  sm.updated['mapdOut'] = False
  values = runtime._observations(sm, cp, MONO, BOOT+ms.COMPUTED_MAX_AGE_NS+1)
  assert values[sel.Source.MAP].kind is acc.ObservationKind.STALE
  assert sel.select_limit(values, settings.selection).selected_source is sel.Source.DASHBOARD
  values = runtime._observations(sm, cp, MONO, BOOT+ms.COMPUTED_MAX_AGE_NS+2)
  assert values[sel.Source.MAP].candidate is None


def test_map_loss_no_metadata_and_lookahead_reuse_only_the_previous_accepted_limit():
  authority = acc.Authority(acc.Mode.COMBINED, acc.LongitudinalOwner.SYSTEM, True, True, False, False)
  prior = acc.AcceptedLimit(acc.Candidate('map', acc.ObservationIdentity(acc.IdentityKind.GEOGRAPHIC, value='previous-way'), 20.), 'drive', 0)
  frame = ms.decode_event(packet().to_bytes())
  without_limit = replace(frame, status='matchedNoLimit', speed_mps=0.)
  for changed in (replace(frame, way_selection='possible'), replace(frame, way_selection='predicted'),
                  replace(frame, way_selection='extended'), replace(frame, version=1),
                  replace(frame, tile_loaded=False), replace(frame, event_mono_ns=BOOT+1),
                  without_limit, replace(without_limit, way_selection='possible'),
                  replace(without_limit, event_valid=False), replace(without_limit, event_mono_ns=BOOT+1),
                  replace(frame, status='noCoverage', event_valid=False, tile_loaded=False, way_id=0, speed_mps=0.)):
    source = MapSource()
    observation = source.step(changed, received=True, now_boot_ns=BOOT,
      transport_alive=True, drive_session='drive', car_fingerprint='vehicle')
    assert source.way_id == (frame.way_id if changed is without_limit else 0)
    assert observation.candidate is None
    assert observation.kind is not acc.ObservationKind.ABSENT
    decision = acc.step(acc.new_session('drive', prior), observation, authority, acc.Policy(), now_ns=MONO)
    assert decision.control_target_mps == prior.candidate.speed_mps
    assert decision.state.accepted == prior
    assert decision.history_write is None


def test_map_identity_order_restart_and_transport_loss_are_fail_closed():
  source = MapSource()
  frame = ms.decode_event(packet().to_bytes())
  def apply(f, now=BOOT, alive=True, received=True):
    return source.step(f, received=received, now_boot_ns=now, transport_alive=alive,
      drive_session='drive', car_fingerprint='vehicle')
  first = apply(frame)
  assert first.kind is acc.ObservationKind.VALID
  second = apply(replace(frame, event_mono_ns=BOOT+1, computed_mono_ns=BOOT+1), BOOT+1)
  assert first.candidate.observation_identity == second.candidate.observation_identity
  other_road = replace(frame, event_mono_ns=BOOT+2, computed_mono_ns=BOOT+2, way_id=124)
  road = apply(other_road, BOOT+2)
  assert road.candidate.observation_identity != first.candidate.observation_identity
  restarted = replace(other_road, event_mono_ns=BOOT+3, computed_mono_ns=BOOT+3, producer_session=42)
  newer = apply(restarted, BOOT+3)
  assert newer.candidate.observation_identity != road.candidate.observation_identity
  assert apply(replace(other_road, event_mono_ns=BOOT+4, computed_mono_ns=BOOT+4), BOOT+4).candidate == newer.candidate
  assert apply(None, BOOT+5, alive=False, received=False).kind is acc.ObservationKind.STALE
  assert apply(restarted, BOOT-1).candidate is None
  source.reset()
  assert apply(restarted, BOOT+6).kind is acc.ObservationKind.VALID


def test_copied_reader_is_actual_v2_wire_and_diagnostic_tracker_stays_unqualified():
  event = packet()
  copied = copy_frame(event.mapdOut, event_valid=bool(event.valid), event_boot_ns=int(event.logMonoTime))
  assert copied == ms.decode_event(event.to_bytes())
  assert ms.MapTracker().step(copied, received=True, now_boot_ns=BOOT).observation.kind is acc.ObservationKind.UNKNOWN
  assert MapSource().step(copied, received=True, now_boot_ns=BOOT, transport_alive=True,
    drive_session='drive', car_fingerprint='vehicle').kind is acc.ObservationKind.VALID


def test_actual_saved_maps_priority_starts_existing_readonly_provider_without_new_flag():
  with tempfile.TemporaryDirectory() as directory:
    params = Params(directory)
    cp = NS(notCar=False)
    assert params.get_default_value('SLCPriority2') == 'Map Data'
    assert read_params(params).selection.slots == (sel.Source.VISION, sel.Source.MAP, None)
    assert shadow_lifecycle.enabled(True, params, cp)
    params.put('SLCPriority2', 'Dashboard', block=True)
    assert not shadow_lifecycle.enabled(True, params, cp)
    params.put('SLCPriority2', 'Off', block=True)
    params.put('SLCPriority3', 'Map Data', block=True)
    assert shadow_lifecycle.enabled(True, params, cp)
    assert not Path(params.get_param_path('MapdShadowEnabled')).exists()
    params.put_bool('ShowSpeedLimits', False, block=True)
    assert not shadow_lifecycle.enabled(True, params, cp)
    params.put_bool('SpeedLimitController', True, block=True)
    assert shadow_lifecycle.enabled(True, params, cp)
    assert not shadow_lifecycle.enabled(False, params, cp)
    params.put('SLCPriority2', 'bad value', block=True)
    assert not shadow_lifecycle.enabled(True, params, cp)


def test_shared_settings_owner_maps_labels_defaults_and_ready_change_guard():
  with tempfile.TemporaryDirectory() as directory:
    params = Params(directory)
    ready = True
    owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: 'vehicle',
      vision_development=lambda: True, maps_available=lambda: ready)
    def row(key):
      state = owner.snapshot('slc', parked=True, system_long=False, lateral_context=False, metric=False)
      return next(r for r in state.rows if r.key == key)
    assert row('SLCPriority1').value == 'Vision'
    assert row('SLCPriority2').value == row('SLCPriority2').default_value == 'Maps'
    assert row('SLCPriority3').value == row('SLCPriority3').default_value == 'Off'
    params.put('SLCPriority2', 'Dashboard', block=True)
    for key in ('SLCPriority1', 'SLCPriority2', 'SLCPriority3'):
      assert 'Maps' in row(key).choices
      assert ('Off' in row(key).choices) == (key != 'SLCPriority1')
      while row(key).value != 'Maps':
        request = row_change(row(key))
        assert request is not None
        assert owner.apply(request)
      assert params.get(key) == 'Map Data'
      if key != 'SLCPriority1':
        request = row_change(row(key))
        assert request is not None and request.value == 'Off'
        assert owner.apply(request)
        assert params.get(key) == 'Off'
    request = row_change(row('SLCPriority1'))
    assert request is not None
    ready = False
    assert not owner.apply(request)
    assert params.get('SLCPriority1') == 'Map Data'
    assert 'Maps' not in owner._source_choices()
    hub = owner.snapshot('hub', parked=True, system_long=False, lateral_context=False, metric=False)
    pages = [r.page for r in hub.rows]
    assert pages.index('curve') < pages.index('slc')


def test_manager_skips_full_slc_settings_read_when_saved_priorities_exclude_maps():
  with tempfile.TemporaryDirectory() as directory:
    params = Params(directory)
    with patch('openpilot.starpilot.speed_limits.runtime_settings.read_params', side_effect=AssertionError('full scan')):
      params.put('SLCPriority1', 'Vision', block=True)
      params.put('SLCPriority2', 'Dashboard', block=True)
      assert not shadow_lifecycle.enabled(True, params, NS(notCar=False))
    params.put('SLCPriority2', 'Map Data', block=True)
    with patch('openpilot.starpilot.speed_limits.runtime_settings.read_params', wraps=read_params) as read:
      assert shadow_lifecycle.enabled(True, params, NS(notCar=False))
      assert read.call_count == 1


def test_cached_provider_verification_invalidates_on_replacement_and_source_addition():
  from openpilot.starpilot.maps.operation_owner import _PackageReadinessCache
  # Exercise real file identity changes on Linux-local storage.
  with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    root = Path(directory)
    binary, source = root / 'provider', root / 'source'
    binary.write_bytes(b'qualified')
    source.mkdir()
    calls = []
    def validate():
      calls.append(True)
      return binary.read_bytes() == b'qualified' and not (source / 'new.go').exists()
    def watch():
      return (root, binary, source, *sorted(source.iterdir()))
    ready = _PackageReadinessCache(validate, watch)
    assert ready()
    assert ready() and len(calls) == 1
    replacement = root / 'replacement'
    replacement.write_bytes(b'corrupted')
    replacement.replace(binary)
    assert not ready() and len(calls) == 2
    assert not ready() and len(calls) == 2
    prior = binary.stat()
    binary.write_bytes(b'qualified')
    # Equal-length writes in one filesystem timestamp tick may retain every
    # watched metadata field. This tests metadata invalidation, so publish a
    # distinct mtime explicitly rather than depending on clock resolution.
    os.utime(binary, ns=(prior.st_atime_ns, prior.st_mtime_ns + 1_000_000_000))
    assert binary.stat().st_mtime_ns != prior.st_mtime_ns
    assert ready() and len(calls) == 3
    prior_source = source.stat()
    (source / 'new.go').write_bytes(b'package main')
    # Directory additions can share a metadata timestamp tick too. The cache
    # watches directory identity before enumerating newly added source files.
    os.utime(source, ns=(prior_source.st_atime_ns, prior_source.st_mtime_ns + 1_000_000_000))
    assert source.stat().st_mtime_ns != prior_source.st_mtime_ns
    assert not ready() and len(calls) == 4


def test_lazy_malformed_reader_revokes_maps_without_escaping_runtime():
  import pytest
  class MalformedReader:
    @property
    def sampleVersion(self):
      raise RuntimeError('lazy segment pointer failure')
  with pytest.raises(ValueError, match='invalid mapdOut reader'):
    copy_frame(MalformedReader(), event_valid=True, event_boot_ns=BOOT)
  settings = parse({'SpeedLimitController': True, 'SLCPriority1': 'Maps', 'SLCPriority2': 'Dashboard'})
  runtime = Runtime(settings, session_id='drive')
  sm = SM(packet())
  cp = NS(carFingerprint='vehicle')
  assert runtime._observations(sm, cp, MONO, BOOT)[sel.Source.MAP].kind is acc.ObservationKind.VALID
  sm.data['mapdOut'] = MalformedReader()
  observations = runtime._observations(sm, cp, MONO+1, BOOT+1)
  assert observations[sel.Source.MAP].candidate is None
  assert observations[sel.Source.MAP].kind is acc.ObservationKind.UNKNOWN
  assert sel.select_limit(observations, settings.selection).selected_source is sel.Source.DASHBOARD


def test_actual_runtime_maps_display_only_publishes_sign_without_ceiling_or_command():
  from openpilot.cereal import messaging
  from opendbc.car.honda.interface import CarInterface
  from opendbc.car.honda.values import CAR
  from openpilot.starpilot.longitudinal.tests.test_cruise_ceiling import messages
  from openpilot.starpilot.speed_limits.tests.test_runtime_replay import ReplaySM
  cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC)
  assert cp.openpilotLongitudinalControl
  data, envelopes = messages()
  dashboard = messaging.new_message('slcDashboardObservation')
  data['slcDashboardObservation'] = dashboard.slcDashboardObservation
  event = packet()
  data['mapdOut'] = event.mapdOut
  sm = ReplaySM(data, MONO)
  sm.logMonoTime['mapdOut'] = BOOT
  sm['carState'].vCruiseCluster = 100.0
  sm['carState'].vEgoCluster = 20.0
  sm['carControl'].enabled = True
  sm['carControl'].longActive = True
  settings = parse({'SpeedLimitController': False, 'ShowSpeedLimits': True,
                    'SLCPriority1': 'Maps', 'SLCPriority2': 'Dashboard'})
  assert settings.display and not settings.enabled
  runtime = Runtime(settings, session_id='display-drive')
  result = runtime.step(sm, cp, now_ns=MONO, now_boot_ns=BOOT)
  state = result.message.slcState
  assert state.displayOnly and not state.enabled
  assert state.observationKind == 'valid' and state.source == 'map'
  assert abs(state.speedLimit-13.4112) < .001
  assert not state.hasCeiling and result.result.ceiling is None
  assert not state.hasPending and not state.hasAccepted
  # Keep the real cereal envelopes live for the complete reader lifetime.
  assert len(envelopes) == 7 and dashboard.which() == 'slcDashboardObservation'
