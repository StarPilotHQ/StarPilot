"""Per-call binding validation deduplicates identities, never clocks or grants."""
from dataclasses import replace
import pytest
from openpilot.starpilot.flm import live_tracking_response as response
from openpilot.starpilot.flm.tests.test_tracking_source_context import context


@pytest.mark.parametrize('same_witness,same_expected', [(True, True), (False, True), (True, False), (False, False)])
def test_each_distinct_binding_validated_once_per_witness_call(monkeypatch, same_witness, same_expected):
  certificate = context()
  now = 10_000_000_000
  actual = certificate.binding if same_witness else replace(certificate.binding)
  expected = certificate.binding if same_expected else replace(certificate.binding)
  raw = response.ResponseWitness(certificate, actual, now-certificate.lag_ns,
    now-certificate.lag_ns, now, now-350_000_000, 8, None, now+100_000_000)
  calls = []
  original = response._binding_values
  def measured(value):
    calls.append(value)
    return original(value)
  monkeypatch.setattr(response, '_binding_values', measured)
  assert response.validate_response_witness(raw, expected, now) is raw
  assert len(calls) == len({id(actual), id(expected), id(certificate.binding)})


def test_binding_deduplication_does_not_reuse_a_previous_call():
  certificate = context()
  now = 10_000_000_000
  raw = response.ResponseWitness(certificate, certificate.binding, now-certificate.lag_ns,
    now-certificate.lag_ns, now, now-350_000_000, 8, None, now+100_000_000)
  assert response.validate_response_witness(raw, certificate.binding, now) is raw
  with pytest.raises(ValueError):
    response.validate_response_witness(raw, certificate.binding, now+100_000_001)
  with pytest.raises(ValueError):
    response.validate_response_witness(raw, replace(certificate.binding, source_epoch='0'*64), now)
