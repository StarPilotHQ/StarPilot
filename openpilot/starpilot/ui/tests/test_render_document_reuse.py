import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.starpilot.ui import onroad_customization as layout
from openpilot.starpilot.favorites import layout_actions as favorites


class TestRenderDocumentReuse(unittest.TestCase):
  def setUp(self):
    self.document = layout.default_document()
    self.raw = json.dumps(self.document, separators=(',', ':')).encode()

  def test_repeated_decode_keeps_independent_nested_values(self):
    first = layout.decode_document(self.raw)
    first['layouts']['compact']['torque_bar']['enabled'] = False
    second = layout.decode_document(self.raw)
    self.assertTrue(second['layouts']['compact']['torque_bar']['enabled'])
    self.assertIsNot(first, second)

  def test_unchanged_valid_bytes_are_validated_once(self):
    raw = self.raw + b' '
    with patch.object(layout, 'validate_document', wraps=layout.validate_document) as validate:
      self.assertEqual(layout.decode_document(raw), layout.decode_document(raw))
      self.assertEqual(validate.call_count, 1)

  def test_changed_bytes_are_validated_and_applied(self):
    layout.decode_document(self.raw)
    self.document['layouts']['compact']['torque_bar']['enabled'] = False
    changed = json.dumps(self.document).encode()
    self.assertFalse(layout.decode_document(changed)['layouts']['compact']['torque_bar']['enabled'])
    self.assertTrue(layout.decode_document(self.raw)['layouts']['compact']['torque_bar']['enabled'])

  def test_large_direct_inputs_do_not_expand_the_cache(self):
    raw = self.raw + b' ' * layout.MAX_BYTES
    with patch.object(layout, 'validate_document', wraps=layout.validate_document) as validate:
      self.assertEqual(layout.decode_document(raw), layout.decode_document(raw))
      self.assertEqual(validate.call_count, 2)

  def test_invalid_and_duplicate_fields_still_fail(self):
    for raw in (b'bad-json', b'{}', b'{"version":14,"version":14}', b'[]'):
      with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
        layout.decode_document(raw)

  def test_bytearray_keeps_original_supported_input(self):
    self.assertEqual(layout.decode_document(bytearray(self.raw)), layout.decode_document(self.raw))

  def test_selected_actions_keep_states_tokens_and_refusals(self):
    with tempfile.TemporaryDirectory() as root:
      path = Path(root) / layout.PARAM_KEY
      path.write_bytes(self.raw)
      params = SimpleNamespace(get_param_path=lambda key: str(Path(root) / key))
      allowed = [True]
      for profile in ('large', 'compact'):
        all_actions = favorites.layout_actions(params, profile, lambda: allowed[0])
        for key in favorites.TOGGLES:
          with self.subTest(profile=profile, key=key):
            selected = favorites.layout_actions(params, profile, lambda: allowed[0], requested_keys={key})
            self.assertEqual(set(selected), {key})
            self.assertEqual(selected[key], all_actions[key])
            allowed[0] = False
            invoke = selected[key].invoke
            if invoke is not None:
              self.assertFalse(invoke())
            allowed[0] = True
        saved = favorites.layout_actions(params, profile, lambda: True, requested_keys={'EnableTorqueBarWidget'})
        path.write_bytes(self.raw + b' ')
        invoke = saved['EnableTorqueBarWidget'].invoke
        assert invoke is not None
        self.assertFalse(invoke())
        path.write_bytes(self.raw)

  def test_unrequested_layout_actions_do_not_read_saved_layout(self):
    with patch.object(favorites, 'read_saved', side_effect=AssertionError('No layout actions requested')):
      self.assertEqual(favorites.layout_actions(None, 'compact', lambda: True, requested_keys={'Bookmark'}), {})
      self.assertEqual(favorites.layout_actions(None, 'compact', lambda: True, requested_keys=set()), {})

  def test_invalid_layout_remains_unavailable(self):
    with tempfile.TemporaryDirectory() as root:
      path = Path(root) / layout.PARAM_KEY
      path.write_bytes(b'bad-json')
      params = SimpleNamespace(get_param_path=lambda key: str(Path(root) / key))
      action = favorites.layout_actions(params, 'compact', lambda: True,
                                        requested_keys={'EnableTorqueBarWidget'})['EnableTorqueBarWidget']
      self.assertFalse(action.available)
      self.assertIsNone(action.invoke)


if __name__ == '__main__':
  unittest.main()
