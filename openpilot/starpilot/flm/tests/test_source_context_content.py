"""Identity content reuse never grants physical, native or temporal authority."""
from collections import OrderedDict
from dataclasses import replace
import pytest
from openpilot.starpilot.flm import live_tracking_response as response
from openpilot.starpilot.flm.tests.test_tracking_source_context import context


@pytest.fixture(autouse=True)
def isolated_content(monkeypatch):
  monkeypatch.setattr(response, '_VALID_SOURCE_CONTEXTS', OrderedDict(), raising=False)


def test_same_immutable_content_avoids_repeated_revision_work(monkeypatch):
  original = context()
  response._VALID_SOURCE_CONTEXTS.clear()
  calls = []
  revision = response._revision
  def measured(value):
    calls.append(value)
    return revision(value)
  monkeypatch.setattr(response, '_revision', measured)
  assert response.validate_source_context(original) is original
  clone = replace(original, binding=replace(original.binding))
  assert response.validate_source_context(clone) is clone
  assert len(calls) == 1


@pytest.mark.parametrize('field,value', [
  ('version', 'different'), ('lag_ns', 500_000_001), ('observed_ns', 0),
  ('source_inventory_sha256', 'e'*64), ('model_revision', 'e'*64),
  ('provenance', 'different'), ('parent_model_revision', 'e'*64),
])
def test_cached_identity_does_not_validate_changed_content(field, value):
  original = context()
  assert response.validate_source_context(original) is original
  with pytest.raises(ValueError):
    response.validate_source_context(replace(original, **{field:value}))


def test_cached_identity_detects_mutation_on_the_same_object():
  original = context()
  object.__setattr__(original, 'lag_ns', original.lag_ns+1)
  with pytest.raises(ValueError):
    response.validate_source_context(original)


def test_cached_identity_detects_changed_binding():
  original = context()
  changed = replace(original, binding=replace(original.binding, source_epoch=('0'if original.binding.source_epoch!='0'*64 else'1')*64))
  with pytest.raises(ValueError):
    response.validate_source_context(changed)


def test_class_accessor_change_is_rejected_even_after_content_validation(monkeypatch):
  original = context()
  monkeypatch.setattr(response.TrackingSourceContext, 'observed_ns', property(lambda self:1), raising=False)
  with pytest.raises(ValueError):
    response.validate_source_context(original)


def test_cached_identity_still_requires_current_witness_and_expected_binding():
  original = context()
  now = 10_000_000_000
  raw = response.ResponseWitness(original, original.binding, now-original.lag_ns,
    now-original.lag_ns, now, now-350_000_000, 8, None, now+100_000_000)
  prepared = response.prepare_response_witness(raw, original.binding, now)
  assert response.validate_response_witness(prepared, original.binding, now) is raw
  with pytest.raises(ValueError):
    response.validate_response_witness(prepared, original.binding, now+100_000_001)
  with pytest.raises(ValueError):
    response.validate_response_witness(prepared, replace(original.binding, source_epoch=('0'if original.binding.source_epoch!='0'*64 else'1')*64), now)
  with pytest.raises(ValueError):
    response.validate_response_witness(replace(raw, independent_poses=7), original.binding, now)


def test_validated_identity_storage_remains_bounded():
  original = context()
  for index in range(20):
    value = replace(original, observed_ns=index+2)
    revision = response._revision(value)
    value = replace(value, model_revision=revision, binding=replace(value.binding, physical_model_revision=revision))
    assert response.validate_source_context(value) is value
  assert 0 < len(response._VALID_SOURCE_CONTEXTS) <= 8
