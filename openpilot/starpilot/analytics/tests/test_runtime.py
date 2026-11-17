from types import SimpleNamespace
from unittest.mock import Mock

from openpilot.starpilot.analytics.runtime import AnalyticsWorker


def jetlink(record):
  """Jetlink's hooks with runtime() answering `record`."""
  return SimpleNamespace(runtime=lambda: record)


class Source:
  def __init__(self):
    self.data = {'deviceState': SimpleNamespace(started=False), 'carState': SimpleNamespace(vEgo=10),
                 'carControl': SimpleNamespace(latActive=True, longActive=False),
                 'selfdriveState': SimpleNamespace(enabled=False), 'modelV2': SimpleNamespace(big=False)}
    self.valid = dict.fromkeys(self.data, True)
    self.alive = dict(self.valid)
    self.logMonoTime = dict.fromkeys(self.data, 1_000_000_000)
    self.valid.update(carParams=False, gpsLocationExternal=False)
    self.alive.update(carParams=False, gpsLocationExternal=False)
    self.logMonoTime.update(carParams=0, gpsLocationExternal=0)

  def __getitem__(self, key):
    return self.data[key]

  def update(self, timeout):
    pass


def worker():
  values = {'IsOffroad': True, 'UsageStatsState': {'version': 1, 'context': {'driving_model': 'executed'}}}
  params = Mock()
  params.get.side_effect = values.get
  params.get_bool.side_effect = lambda key: values.get(key, False)
  params.put.side_effect = lambda key, value, **kwargs: values.__setitem__(key, value)
  source = Source()
  clock = [1.0]
  client = Mock()
  client.send.return_value = True
  obj = AnalyticsWorker(params, source, collector=lambda *a: {}, token_reader=lambda: 'token',
                        client=client, commit_reader=lambda *a, **k: None, clock_valid=lambda: True, monotonic=lambda: clock[0], wall=lambda: 1_800_000_000,
                        identity=lambda *a: {'driving_model': 'executed'})
  return obj, values, source, clock, client


def test_initial_send_and_offroad_rechecked_at_transport():
  obj, values, source, clock, client = worker()
  obj.poll_once()
  assert client.send.call_count == 1
  gate = client.send.call_args.kwargs['allowed']
  source.data['deviceState'].started = True
  assert not gate()
  source.data['deviceState'].started = False
  values['IsOffroad'] = False
  assert not gate()


def test_driving_snapshot_survives_offroad_clear_and_counts_actual_aol():
  obj, values, source, clock, client = worker()
  obj.poll_once()
  source.data['deviceState'].started = True
  values['IsOffroad'] = False
  clock[0] = 1.1
  obj.poll_once()
  assert client.send.call_count == 1
  assert obj.counters['drives'] == 1
  assert abs(obj.counters['total_aol_seconds'] - .1) < 1e-6
  source.valid['modelV2'] = False
  source.data['deviceState'].started = False
  values['IsOffroad'] = True
  clock[0] = 1.2
  obj.poll_once()
  assert b'driving_model=executed' in client.send.call_args.args[0]


def test_missing_credentials_and_stale_source_never_send():
  obj, values, source, clock, client = worker()
  obj.token_reader = lambda: None
  obj.poll_once()
  client.send.assert_not_called()
  assert values['UsageStatsStatus']['state'] == 'awaiting_credentials'
  obj.token_reader = lambda: 'token'
  clock[0] = 40
  obj.poll_once()
  client.send.assert_not_called()


def test_failures_have_finite_parked_retry_budget_and_bounded_stop():
  obj, values, source, clock, client = worker()
  client.send.return_value = False
  for _ in range(10):
    source.logMonoTime = dict.fromkeys(source.logMonoTime, int(clock[0] * 1e9))
    obj.poll_once()
    clock[0] += 301
  assert client.send.call_count == 5
  obj.start()
  obj.close()
  assert not obj.thread.is_alive()


def test_startup_onroad_never_contacts_transport():
  obj, values, source, clock, client = worker()
  source.data['deviceState'].started = True
  values['IsOffroad'] = False
  obj.poll_once()
  client.send.assert_not_called()
  source.data['deviceState'].started = False
  values['IsOffroad'] = True
  obj.poll_once()
  assert client.send.call_count == 1


def test_host_optional_hook_does_not_construct_network_worker():
  from unittest.mock import patch
  from openpilot.starpilot.analytics.runtime import build_worker
  with patch('openpilot.common.hardware.COMMA_HARDWARE', False), patch('openpilot.starpilot.analytics.runtime.AnalyticsWorker') as factory:
    assert build_worker() is None
    factory.assert_not_called()


def test_process_saves_counters_when_stopped():
  from unittest.mock import patch
  from openpilot.starpilot.analytics import runtime
  obj, values, _source, _clock, client = worker()
  values.pop('UsageStatsState', None)
  obj.stop_event.set()
  with patch.object(runtime, 'build_worker', return_value=obj), patch('signal.signal'):
    runtime.main()
  assert 'UsageStatsState' in values
  client.close.assert_called()


def test_malformed_saved_state_has_finite_sanitized_counters():
  obj, values, source, clock, client = worker()
  values['UsageStatsState'] = {'version': 1, 'counters': 'bad', 'region': {'latitude': 12.345678}, 'extra': 'x' * 100000}
  other = AnalyticsWorker(obj.params, source, client=client, collector=lambda *a: {}, token_reader=lambda: None,
                          monotonic=lambda: clock[0], clock_valid=lambda: True)
  assert sum(other.counters.values()) == 0
  assert 'extra' not in other.state and 'region' not in other.state


def test_registered_native_params_roundtrip_and_restart_context():
  import tempfile
  from openpilot.common.params import Params
  from pathlib import Path
  from openpilot.starpilot.state_migration import prepare_manager_start
  with tempfile.TemporaryDirectory() as directory:
    params = Params(str(Path(directory) / 'params'))
    storage = Path(directory) / 'recovery'
    prepare_manager_start(params, storage)
    params.put_bool('IsOffroad', True, block=True)
    source = Source()
    obj = AnalyticsWorker(params, source, collector=lambda *a: {}, client=Mock(), token_reader=lambda: None,
                          monotonic=lambda: 1.0, clock_valid=lambda: True)
    obj.context = {'car_make': 'gm', 'car_model': 'actual', 'driving_model': 'verified'}
    obj.persist()
    assert isinstance(params.get('UsageStatsState'), dict)
    saved = params.get('UsageStatsState')
    prepare_manager_start(params, storage, dry_run=True)
    prepare_manager_start(params, storage)
    assert params.get('UsageStatsState') == saved
    restored = AnalyticsWorker(params, source, collector=lambda *a: {}, client=Mock(), token_reader=lambda: None,
                               monotonic=lambda: 1.0, clock_valid=lambda: True)
    assert restored.context['driving_model'] == 'verified'
    assert restored.metadata['dongle_id'] == obj.metadata['dongle_id']
    restored.poll_once()
    restored.client.send.assert_not_called()
    del restored, obj, params


def test_legacy_sharing_value_cannot_disable_automatic_report():
  obj, values, source, clock, client = worker()
  values['ShareUsageStats'] = False
  obj.poll_once()
  assert client.send.call_count == 1
  assert obj.allowed()


def test_executed_model_reports_verified_fallback_not_saved_request():
  from unittest.mock import patch
  from openpilot.starpilot.analytics.runtime import executed_model
  from openpilot.starpilot.models.catalog import BY_ID
  from openpilot.starpilot.models.status import ModelHealth, ModelVariant
  source = Source()
  status = SimpleNamespace(health=ModelHealth.ACTIVE, loaded_id='rdf43', artifact_sha256='a' * 64,
                           variant=ModelVariant.SMALL, requested_id='some-selected-large-model')
  with patch('openpilot.starpilot.models.jetlink_adapter.hooks', return_value=jetlink(None)), \
       patch('openpilot.starpilot.models.runtime.snapshot', return_value=status):
    result = executed_model(source, Mock(), 1_000_000_000)
  assert result['driving_model'] == BY_ID['rdf43'].name
  assert result['metrics']['model_variant'] == 'small'
  assert 'some-selected-large-model' not in str(result)
  source.data['modelV2'].big = True
  with patch('openpilot.starpilot.models.jetlink_adapter.hooks',
             return_value=jetlink(SimpleNamespace(active=True, sha256='b' * 64))):
    remote = executed_model(source, Mock(), 1_000_000_000)
  assert remote['metrics']['model_sha256'] == 'b' * 64
  assert remote['metrics']['model_variant'] == 'jetlink'


def test_new_drive_clears_prior_identity_brief_unavailable_retains_last_used():
  obj, values, source, clock, client = worker()
  obj.poll_once()
  source.data['deviceState'].started = True
  values['IsOffroad'] = False
  obj.poll_once()
  assert obj.context['driving_model'] == 'executed'
  obj.identity = lambda *a: {}
  clock[0] = 2.0
  source.logMonoTime['modelV2'] = 2_000_000_000
  source.logMonoTime['deviceState'] = 2_000_000_000
  obj.poll_once()
  assert obj.context['driving_model'] == 'executed'
  source.data['deviceState'].started = False
  values['IsOffroad'] = True
  obj.poll_once()
  source.data['deviceState'].started = True
  values['IsOffroad'] = False
  obj.poll_once()
  assert 'driving_model' not in obj.context
  obj.identity = lambda *a: {'driving_model': 'verified-fallback'}
  clock[0] = 3.1
  source.logMonoTime['modelV2'] = 3_100_000_000
  source.logMonoTime['deviceState'] = 3_100_000_000
  obj.poll_once()
  assert obj.context['driving_model'] == 'verified-fallback'


def test_new_month_offroad_does_not_report_old_month_distance():
  obj, values, source, clock, client = worker()
  obj.state['month'] = '2000-01'
  obj.counters['current_months_meters'] = 1000
  obj.poll_once()
  assert obj.counters['current_months_meters'] == 0


def test_executed_model_real_message_catalog_identity_and_actual_small_fallback():
  from unittest.mock import patch
  from openpilot.cereal import messaging
  from openpilot.starpilot.analytics.runtime import executed_model
  from openpilot.starpilot.models.catalog import BY_ID
  from openpilot.starpilot.models.status import ModelHealth, ModelStatus, ModelVariant
  source = Source()
  source.data['modelV2'] = messaging.new_message('modelV2').modelV2
  status = ModelStatus('selected-large', 'rdf43', ModelVariant.SMALL, ModelHealth.ACTIVE, True,
                       'large model unavailable', 'a' * 64)
  with patch('openpilot.starpilot.models.jetlink_adapter.hooks', return_value=jetlink(None)), \
       patch('openpilot.starpilot.models.runtime.snapshot', return_value=status) as projection:
    params = Mock()
    result = executed_model(source, params, 1_000_000_000)
    projection.assert_called_once_with(source, params, 1_000_000_000)
  assert result == {'driving_model': BY_ID['rdf43'].name,
                    'metrics': {'model_sha256': 'a' * 64, 'model_variant': 'small'}}
  assert 'selected-large' not in str(result)


def test_executed_model_remote_requires_actual_big_output_not_remote_selection():
  from unittest.mock import patch
  from openpilot.cereal import messaging
  from openpilot.starpilot.analytics.runtime import executed_model
  from openpilot.starpilot.models.catalog import BY_ID
  from openpilot.starpilot.models.status import ModelHealth, ModelStatus, ModelVariant
  source = Source()
  source.data['modelV2'] = messaging.new_message('modelV2').modelV2
  small = ModelStatus('selected-large', 'rdf43', ModelVariant.SMALL, ModelHealth.ACTIVE, False, None, 'a' * 64)
  remote = SimpleNamespace(active=True, sha256='b' * 64)
  with patch('openpilot.starpilot.models.jetlink_adapter.hooks', return_value=jetlink(remote)), \
       patch('openpilot.starpilot.models.runtime.snapshot', return_value=small) as projection:
    source.data['modelV2'].big = True
    big = executed_model(source, Mock(), 1_000_000_000)
    projection.assert_not_called()
    assert big == {'driving_model': 'jetlink:' + 'b' * 16,
                   'metrics': {'model_sha256': 'b' * 64, 'model_variant': 'jetlink'}}
    source.data['modelV2'].big = False
    fallback = executed_model(source, Mock(), 1_000_000_000)
    assert fallback['driving_model'] == BY_ID['rdf43'].name
    assert fallback['metrics']['model_sha256'] == 'a' * 64


def test_executed_model_unverified_receipt_never_reports_requested_identity():
  from unittest.mock import patch
  from openpilot.cereal import messaging
  from openpilot.starpilot.analytics.runtime import executed_model
  from openpilot.starpilot.models.status import ModelHealth, ModelStatus
  source = Source()
  source.data['modelV2'] = messaging.new_message('modelV2').modelV2
  for health in (ModelHealth.UNAVAILABLE, ModelHealth.IDENTITY_UNAVAILABLE, ModelHealth.LOADING,
                 ModelHealth.STALE, ModelHealth.FAILED):
    status = ModelStatus('selected-large', None, None, health, True, 'unverified', None)
    with patch('openpilot.starpilot.models.jetlink_adapter.hooks', return_value=jetlink(None)), \
         patch('openpilot.starpilot.models.runtime.snapshot', return_value=status):
      assert executed_model(source, Mock(), 1_000_000_000) == {}


def test_fresh_install_waits_for_verified_drive_instead_of_sending_unknown():
  obj, values, source, clock, client = worker()
  obj.context = {}
  obj.poll_once()
  client.send.assert_not_called()
  assert values['UsageStatsStatus'] == {'state': 'awaiting_model_identity'}
  writes = obj.params.put.call_count
  obj.poll_once()
  assert obj.params.put.call_count == writes
  source.data['deviceState'].started = True
  values['IsOffroad'] = False
  clock[0] = 1.1
  obj.poll_once()
  client.send.assert_not_called()
  source.data['deviceState'].started = False
  values['IsOffroad'] = True
  clock[0] = 1.2
  obj.poll_once()
  assert client.send.call_count == 1
  assert b'driving_model=executed' in client.send.call_args.args[0]
  assert b'driving_model=unknown' not in client.send.call_args.args[0]


def test_real_current_carparams_does_not_interrupt_model_collection():
  from openpilot.cereal import messaging
  obj, values, source, clock, client = worker()
  cp = messaging.new_message('carParams')
  cp.carParams.brand = 'hyundai'
  cp.carParams.carFingerprint = 'HYUNDAI_IONIQ_6'
  cp.carParams.openpilotLongitudinalControl = True
  source.data['carParams'] = cp.as_reader().carParams
  source.valid['carParams'] = source.alive['carParams'] = True
  source.logMonoTime['carParams'] = 1_000_000_000
  source.data['deviceState'].started = True
  values['IsOffroad'] = False
  obj.poll_once()
  assert obj.previous_started is True
  assert obj.context['driving_model'] == 'executed'
  assert obj.context['car_model'] == 'HYUNDAI_IONIQ_6'
  assert obj.context['metrics']['has_openpilot_longitudinal'] is True
  assert 'has_pedal' not in obj.context['metrics']
  source.data['deviceState'].started = False
  values['IsOffroad'] = True
  clock[0] = 1.1
  obj.poll_once()
  assert b'driving_model=executed' in client.send.call_args.args[0]


def test_current_pedal_flags_report_present_and_absent_without_deprecated_field():
  from importlib import import_module
  from openpilot.starpilot.analytics.runtime import pedal_metrics
  from opendbc.car.structs import CarParams
  for brand, enum, name in [('gm', 'GMFlags', 'PEDAL_LONG'), ('honda', 'HondaFlags', 'GAS_INTERCEPTOR'),
                            ('toyota', 'ToyotaFlags', 'GAS_INTERCEPTOR')]:
    mask = getattr(getattr(import_module('opendbc.car.' + brand + '.values'), enum), name)
    cp = CarParams.new_message(brand=brand)
    assert pedal_metrics(cp.as_reader()) == {'has_pedal': False}
    cp.flags = int(mask)
    assert pedal_metrics(cp.as_reader()) == {'has_pedal': True}


def test_unknown_saved_identity_does_not_get_guessed_from_selection():
  obj, values, _source, _clock, client = worker()
  obj.context = {'driving_model': 'unknown'}
  values['DrivingModelName'] = 'a requested model that never ran'
  obj.poll_once()
  client.send.assert_not_called()
  assert values['UsageStatsStatus']['state'] == 'awaiting_model_identity'



def test_real_worker_metadata_reports_product_major_independently_of_branch():
  from unittest.mock import patch
  from openpilot.starpilot.analytics import runtime
  from openpilot.starpilot.ui.brand import DISPLAY_VERSION
  metadata = SimpleNamespace(channel='StarPilot', openpilot=SimpleNamespace(git_commit='a' * 40))
  with patch('openpilot.common.hardware.COMMA_HARDWARE', True), \
       patch('openpilot.common.version.get_build_metadata', return_value=metadata), \
       patch('openpilot.cereal.messaging.SubMaster'), patch.object(runtime, 'AnalyticsWorker') as factory:
    runtime.build_worker()
  assert factory.call_args.kwargs['metadata']['branch'] == 'StarPilot'
  assert factory.call_args.kwargs['metadata']['metrics'] == {'commit': 'a' * 40, 'software_major': int(DISPLAY_VERSION.split('.')[0])}
