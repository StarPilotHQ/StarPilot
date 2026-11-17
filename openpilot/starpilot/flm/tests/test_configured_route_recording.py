"""Explicit setups and original masked commands must remain separate."""
from dataclasses import replace

import pytest

from openpilot.cereal import messaging
from openpilot.starpilot.flm import route_recording as recording
from openpilot.starpilot.flm.learning_wire import read_control, write_control
from openpilot.starpilot.flm.offroad_control_metadata import read_control_metadata
from openpilot.starpilot.flm.offroad_processor import continuous_control_window
from openpilot.starpilot.flm.tests.test_control_wire_fields import row_with_fields
from openpilot.starpilot.flm.tests.test_offroad_backend import scope, values


def control(*, inhibited=False, published=False, legacy=False):
  configured = tuple(values(.005).items())
  return replace(
    row_with_fields(), stage_schema_revision=recording.BASES[0] + '@' + recording.REVISION + ('' if legacy else '+configured-v1'),
    route_applied_coordinates=tuple(values().items()) if inhibited else configured,
    route_configured_coordinates=() if legacy else configured,
    route_effect_revision=recording.REVISION, route_configuration_scope=scope().key(), live_applied_coordinates=(),
    active=True, enabled=True, valid=True, manual=False, override=False, fault=False, saturated=False, limited=published,
    replay_inputs=(('active', 1.0), ('safety_limited', float(inhibited)), ('curvature_limited', 0.0)),
  )


def wire(row):
  event = messaging.new_message('starpilotLateralState', valid=True, logMonoTime=row.control_ns)
  write_control(event.starpilotLateralState, row)
  return messaging.log_from_bytes(event.to_bytes())


@pytest.mark.parametrize('inhibited', [False, True])
@pytest.mark.parametrize('published', [False, True])
def test_configuration_and_actual_command_roundtrip_on_both_original_clocks(inhibited, published):
  original = control(inhibited=inhibited, published=published)
  event = wire(original)
  for read in (read_control, read_control_metadata):
    parsed = read(event)
    assert parsed.route_configured_coordinates == original.route_configured_coordinates
    assert parsed.route_applied_coordinates == original.route_applied_coordinates
    assert parsed.limited is published
    setup = recording.configuration_values(parsed.route_applied_coordinates, parsed.route_configured_coordinates, parsed.replay_inputs)
    assert setup == tuple(values(.005).items())


def test_legacy_recordings_keep_their_actual_map_without_invented_settings():
  original = control(inhibited=True, published=True, legacy=True)
  event = wire(original)
  for read in (read_control, read_control_metadata):
    parsed = read(event)
    assert parsed.route_configured_coordinates == ()
    assert recording.configuration_values(parsed.route_applied_coordinates, (), parsed.replay_inputs) == tuple(values().items())
  with pytest.raises(ValueError, match='explicit recording version'):
    recording.encode(replace(original, route_configured_coordinates=tuple(values(.005).items())))


@pytest.mark.parametrize('changed', ['mask_flag', 'nonzero_actual', 'active', 'count', 'version', 'prefix'])
def test_new_setup_does_not_hide_unexplained_execution_or_malformed_metadata(changed):
  event = wire(control(inhibited=True)).as_builder()
  row = event.starpilotLateralState.learningEvidence
  if changed in ('mask_flag', 'active'):
    row.replayInputs[1 if changed == 'mask_flag' else 0].number = 0.0
  elif changed == 'nonzero_actual':
    next(value for value in row.stageValues if str(value.name).startswith(recording.PREFIX)).value = .002
  elif changed == 'count':
    row.stageValues = list(row.stageValues)[:-1]
  elif changed == 'prefix':
    entry = next(value for value in row.stageValues if str(value.name).startswith(recording.CONFIGURED_PREFIX))
    entry.name = str(entry.name).removeprefix(recording.CONFIGURED_PREFIX)
  else:
    row.stageSchemaRevision = recording.BASES[0] + '@' + recording.REVISION
  for read in (read_control, read_control_metadata):
    with pytest.raises(ValueError):
      read(event.as_reader())


def test_normal_masking_keeps_continuous_setup_but_real_changes_and_gaps_split_it():
  from openpilot.starpilot.flm.tests.test_offroad_coordinate_policy import TestCrossDriveEvidence

  inputs, _ = TestCrossDriveEvidence.inputs(1)
  window = next(iter(inputs.windows.values()))[0]
  template = read_control_metadata(wire(control()))
  expected = tuple(values(.005).items())

  def context(row, _):
    return recording.configuration_values(row.route_applied_coordinates, row.route_configured_coordinates, row.replay_inputs)

  timeline = []
  for i, point in enumerate(window.points):
    now = point.mono_ns
    inhibited, published = bool(i % 2), bool(i % 3)
    row = replace(template, control_ns=now, state_ns=now, source_receipt_ns=now,
                  native_observed_ns=now, native_expires_ns=now + 100_000_000, limited=published,
                  route_applied_coordinates=tuple(values().items()) if inhibited else expected,
                  replay_inputs=(('active', 1.0), ('safety_limited', float(inhibited)), ('curvature_limited', 0.0)))
    timeline.append((now, row))
  assert continuous_control_window(window, timeline, context, expected)
  changed = list(timeline)
  stamp, original = changed[150]
  changed[150] = (stamp, replace(original, route_configured_coordinates=tuple(values(.006).items()), route_applied_coordinates=tuple(values(.006).items())))
  assert not continuous_control_window(window, changed, context, expected)
  assert not continuous_control_window(window, timeline[:100] + timeline[110:], context, expected)
  assert not continuous_control_window(window, [(t, replace(row, native_allowed=False)) for t, row in timeline], context, expected)
