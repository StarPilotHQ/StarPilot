"""Live Card source snapshots: bounded age, coherent adoption and no main reads."""
from dataclasses import replace
import fcntl
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.card_settings import CardSettingsObservation, CardSettingsWorker
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner, DocumentState, REVERIFY_NS


@pytest.fixture
def worker(tmp_path):
  return CardSettingsWorker(Params(str(tmp_path)))


def ready(worker, now):
  return worker.view.verdict(worker.view.refresh(now), now_mono_ns=now, drive_id=1).status == 'ready'


def test_startup_denies_and_verified_reads_retain_pre_io_clock(worker):
  assert not ready(worker, 1_000_000_000)
  worker.refresh(1_000_000_000)
  assert worker.published.snapshot.verified_mono_ns == 1_000_000_000
  assert not ready(worker, 1_000_000_000)  # no adoption before frame boundary
  worker.adopt()
  assert ready(worker, 1_499_999_999)
  assert not ready(worker, 1_500_000_000)
  assert not ready(worker, 2_000_000_000)
  assert not ready(worker, 999_999_999)


def test_proactive_read_reverifies_before_original_500ms_period(worker):
  worker.refresh(1_000_000_000)
  first = worker.published.snapshot
  worker.refresh(1_100_000_000)
  second = worker.published.snapshot
  assert second is not first and second.revision == first.revision
  assert second.observed_mono_ns == first.observed_mono_ns
  assert second.verified_mono_ns == 1_100_000_000


def test_worker_publication_cannot_change_current_frame_and_main_never_reads(worker):
  worker.refresh(1_000_000_000)
  worker.adopt()
  old = worker.view.current
  Path(worker.reader.params.get_param_path('SafeMode')).write_bytes(b'1')
  worker.refresh(1_100_000_000)
  assert worker.view.current is old
  assert ready(worker, 1_100_000_000)
  with patch('openpilot.starpilot.conditional_mode.runtime_settings.read_mode_sources', side_effect=AssertionError('main I/O')):
    for _ in range(100):
      assert worker.view.refresh(1_100_000_000, force=True, wait=True) is old
      assert ready(worker, 1_100_000_000)
    worker.adopt()
    assert not ready(worker, 1_100_000_000)
  assert worker.reader is not worker.view
  assert worker.view.current.safe_mode_raw == b'1'


def test_writer_busy_denies_without_retaining_one_second_permission(worker):
  worker.refresh(1_000_000_000)
  worker.adopt()
  assert ready(worker, 1_000_000_000)
  root = Path(worker.reader.params.get_param_path('ConditionalModeConfig')).parent.parent
  fd = os.open(root / '.lock', os.O_RDONLY)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    worker.refresh(1_100_000_000)
  finally:
    os.close(fd)
  assert worker.published.status == 'busy'
  worker.adopt()
  assert not ready(worker, 1_100_000_000)
  worker.refresh(1_200_000_000)
  worker.adopt()
  assert ready(worker, 1_200_000_000)


@pytest.mark.parametrize('key,raw', [('SafeMode', b'1'), ('SafeMode', b'bad'), ('ConditionalModeConfig', b'{')])
def test_restrictive_and_invalid_updates_revoke(worker, key, raw):
  worker.refresh(1_000_000_000)
  worker.adopt()
  old = worker.view.current
  Path(worker.reader.params.get_param_path(key)).write_bytes(raw)
  worker.refresh(1_100_000_000)
  worker.adopt()
  assert not ready(worker, 1_100_000_000)
  assert not worker.view.affirm(old, now_mono_ns=1_100_000_000)


def test_unreadable_and_unexpected_errors_recover_without_escaping(worker):
  worker.refresh(1_000_000_000)
  with patch.object(worker.reader, 'refresh', side_effect=RuntimeError('failed read')):
    worker.refresh(1_100_000_000)
  assert worker.published.status == 'error'
  worker.adopt()
  assert not ready(worker, 1_100_000_000)
  worker.refresh(1_200_000_000)
  worker.adopt()
  assert ready(worker, 1_200_000_000)
  with patch('openpilot.starpilot.conditional_mode.runtime_settings.read_mode_sources',
             return_value=((b'', False), (b'', False), (b'', False))):
    worker.refresh(1_300_000_000)
  worker.adopt()
  assert worker.view.current.document_state is DocumentState.READ_ERROR
  assert not ready(worker, 1_300_000_000)


def test_slow_completed_read_is_stale_on_arrival_and_foreign_owner_denies(worker):
  worker.refresh(1_000_000_000)
  worker.adopt()
  assert not ready(worker, 1_000_000_000 + REVERIFY_NS)
  foreign = replace(worker.published.snapshot, owner_token='foreign')
  worker.view.adopt(CardSettingsObservation(foreign, 'verified', 1_000_000_000))
  assert not ready(worker, 1_000_000_000)


def test_clock_rollback_invalidates_and_never_renews_on_adoption(worker):
  worker.refresh(1_000_000_000)
  worker.refresh(999_999_999)
  worker.adopt()
  assert not ready(worker, 1_000_000_000)
  worker.refresh(1_100_000_000)
  worker.adopt()
  snapshot = worker.view.current
  for _ in range(50):
    worker.adopt()
  assert worker.view.current is snapshot and snapshot.verified_mono_ns == 1_100_000_000


@pytest.mark.parametrize('replay', [False, True])
def test_actual_card_installation_and_replay_owner_contract(worker, replay):
  from openpilot.selfdrive.car import card as module
  card: Any = module.Car.__new__(module.Car)
  card.params = worker.reader.params
  card.manual_settings_owner = ConditionalSettingsOwner(card.params)
  card.switchback_settings_owner = ConditionalSettingsOwner(card.params)
  old = (card.manual_settings_owner, card.switchback_settings_owner)
  with patch.object(module, 'REPLAY', replay):
    card.configure_conditional_settings_worker()
  if replay:
    assert card.conditional_settings_worker is None
    assert (card.manual_settings_owner, card.switchback_settings_owner) == old
  else:
    assert card.manual_settings_owner is card.switchback_settings_owner is card.conditional_settings_worker.view
    assert card.manual_settings_owner is not card.conditional_settings_worker.reader


def test_actual_params_thread_continues_after_new_reader_exception(worker):
  from openpilot.selfdrive.car import card as module
  card: Any = module.Car.__new__(module.Car)
  calls = []
  card.params = SimpleNamespace(get_bool=lambda key: calls.append(key) or False, get=worker.reader.params.get)
  card.CP = SimpleNamespace(openpilotLongitudinalControl=True, pcmCruise=False)
  card.CI = SimpleNamespace(CP=card.CP, CC=None)
  card.aol_card_intent = None
  card.conditional_settings_worker = worker
  card.v_cruise_helper = SimpleNamespace()
  card.read_slc_configuration = lambda: None
  event = SimpleNamespace(is_set=lambda: bool(calls))
  with patch.object(module, 'read_cruise_intervals'), patch.object(module.time, 'sleep'), \
       patch.object(module.time, 'monotonic_ns', return_value=1_000_000_000), \
       patch.object(worker.reader, 'refresh', side_effect=RuntimeError('reader failed')):
    card.params_thread(event)
  assert calls == ['IsMetric', 'ExperimentalMode', 'LowSpeedCruiseOnSet']
  assert worker.published.status == 'error' and worker.published.read_started_ns == 1_000_000_000


@pytest.mark.parametrize('full_op', [False, True])
def test_actual_card_stale_settings_do_not_clear_ordinary_or_aol_authority(full_op):
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.car import card as module
  from openpilot.starpilot.aol.tests.test_ioniq_availability import IoniqAvailabilityTests
  from openpilot.starpilot.conditional_mode.manual import ButtonTracker, IoniqMediaMapCache

  fixture = IoniqAvailabilityTests()
  try:
    with OpenpilotPrefix():
      sd, _, drive, card, receive = fixture._ioniq_availability_callers(full_op=full_op)
      card.conditional_replay = True
      card.manual_button_tracker = ButtonTracker()
      card.traffic_button_tracker = ButtonTracker()
      card.ioniq_media_map = IoniqMediaMapCache()
      card.manual_settings_owner = ConditionalSettingsOwner(card.params)
      with patch.object(module, 'REPLAY', False):
        card.configure_conditional_settings_worker()
      worker = card.conditional_settings_worker
      assert worker is not None
      for tick, status in enumerate(('startup', 'ready', 'stale', 'busy', 'invalid'), start=2):
        now = 10_000_000_000 + tick * 10_000_000
        if status == 'ready':
          worker.refresh(now)
        elif status == 'stale':
          assert worker.published.snapshot is not None
          worker.published = replace(worker.published,
            snapshot=replace(worker.published.snapshot, verified_mono_ns=now - REVERIFY_NS))
        elif status == 'busy':
          worker.published = CardSettingsObservation(None, 'busy', now)
        elif status == 'invalid':
          Path(card.params.get_param_path('ConditionalModeConfig')).write_bytes(b'{')
          worker.refresh(now)
        axis, _, command = drive(tick)
        assert axis.lateralActive and command.latActive
        assert bool(axis.longitudinalActive) is full_op and bool(command.longActive) is full_op
        adopted = []
        original_manual, original_traffic = module.conditional_manual_candidate, module.conditional_traffic_candidate

        def manual(*args, adopted=adopted, original_manual=original_manual, **kwargs):
          adopted.append(args[3].current)
          return original_manual(*args, **kwargs)

        def traffic(*args, adopted=adopted, original_traffic=original_traffic, **kwargs):
          adopted.append(args[3].current)
          return original_traffic(*args, **kwargs)

        with patch.object(worker, 'adopt', wraps=worker.adopt) as adoption, \
             patch.object(module, 'conditional_manual_candidate', side_effect=manual), \
             patch.object(module, 'conditional_traffic_candidate', side_effect=traffic), \
             patch('openpilot.starpilot.conditional_mode.runtime_settings.read_mode_sources',
                   side_effect=AssertionError('main settings I/O')):
          assert receive(now, axis=sd.pm.messages['aolAxisState'], event=sd.pm.messages['onroadEvents'])
        assert adoption.call_count == 1 and len(adopted) == 3
        assert all(snapshot is adopted[0] for snapshot in adopted)
        assert card.manual_receipt is None and card.traffic_receipt is None and card.switchback_receipt is None
      axis, _, command = drive(7)
      assert axis.lateralActive and command.latActive
      assert bool(axis.longitudinalActive) is full_op and bool(command.longActive) is full_op
  finally:
    fixture.doCleanups()
