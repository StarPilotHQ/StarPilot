"""Transport-only fixtures; numerical/native admission stays in the worker."""
from copy import deepcopy
import hashlib
import json
from unittest import TestCase

from openpilot.starpilot.flm.controller_replay import pack_primitive_snapshot
from openpilot.starpilot.flm.live_tracking_probe import _primitive
from openpilot.starpilot.flm.live_tracking_probe_process import (
  VERSION, _prepared_context, decode_probe_job, prepare_probe_payloads, prepare_probe_wire,
)


def payloads():
  rows = []
  for offset in range(4):
    stamp = 10_000_000_000 + offset * 10_000_000
    control = {'control_ns': stamp, 'stage_schema_revision': 'transport-only', 'replay_snapshot': (('x', 0.),)}
    rows.append({'version': VERSION, 'replay': b'transport-only', 'prefix_controls': (control,),
                 'control': control, 'output': {'apply_ns': stamp + 1}, 'binding': {'identity': 'transport-only'},
                 'accepted_full30': (), 'anchor': (1., 1., 1.), 'group': 'all30', 'names': ('transport-only',),
                 'archived_clock': False, 'source': {'enabled': False}, 'evidence_ns': 11_000_000_000,
                 'queued_ns': 11_000_000_000})
  return tuple(rows)


def legacy(rows):
  jobs = []
  for row in rows:
    packed = pack_primitive_snapshot(row)
    jobs.append({'version': VERSION, 'payload': packed.hex(), 'sha256': hashlib.sha256(packed).hexdigest()})
  return jobs


class ProbeBatchPreparationTest(TestCase):
  def test_direct_preparation_preserves_every_decoded_field_and_context(self):
    rows = payloads()
    context = _prepared_context({'batch': rows})
    old = prepare_probe_wire(legacy(rows), queued_ns=11_000_000_000, expected=context)
    new = prepare_probe_payloads(rows, queued_ns=11_000_000_000, expected=context)
    def decoded(result):
      wire = json.loads(result['wire'])
      del wire['preparedContextSha256']
      return _primitive(decode_probe_job(wire))
    self.assertEqual(decoded(old), decoded(new))
    self.assertEqual(old['context_sha256'], new['context_sha256'])

  def test_direct_preparation_keeps_original_order_and_two_second_input_bound(self):
    rows = payloads()
    context = _prepared_context({'batch': rows})
    for changed, clock in (((), 11_000_000_000), (rows + rows[:1], 11_000_000_000),
                            (tuple(reversed(rows)), 11_000_000_000), (rows, 13_000_000_001)):
      with self.subTest(clock=clock, count=len(changed)), self.assertRaises(ValueError):
        prepare_probe_payloads(changed, queued_ns=clock, expected=context)

  def test_direct_preparation_rejects_changed_or_nonprimitive_inputs(self):
    rows = payloads()
    context = _prepared_context({'batch': rows})
    for field, value in (('version', 'unknown'), ('replay', b''), ('prefix_controls', ()),
                         ('anchor', (float('nan'),)), ('queued_ns', 0), ('source', {'callback': object()})):
      changed = deepcopy(rows)
      changed[0][field] = value
      with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
        prepare_probe_payloads(changed, queued_ns=11_000_000_000, expected=context)
    with self.assertRaises(ValueError):
      prepare_probe_payloads(rows, queued_ns=11_000_000_000, expected={'batch': ()})
