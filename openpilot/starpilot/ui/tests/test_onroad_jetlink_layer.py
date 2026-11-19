from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from openpilot.starpilot.models.catalog import BUNDLED_CURRENT
from openpilot.starpilot.models.jetlink_status import JetlinkStatus
from openpilot.starpilot.models.status import ModelVariant
from openpilot.starpilot.ui import onroad_jetlink
from openpilot.starpilot.ui.onroad_customization import default_document
from openpilot.starpilot.ui.onroad_jetlink import (
  ACCENT, GRAY, GREEN, ORANGE, TEXT, JetlinkStatusWidget, abbreviate_model_name, big_model_card, gpu_inputs,
  local_model_names, scroll_offset,
)
from openpilot.starpilot.ui.onroad_jetlink_layer import NativeJetlinkLayer
from openpilot.starpilot.ui.presentation import FontRole, TextSize


def test_native_jetlink_polls_only_while_its_widget_is_showing_onroad():
  with patch('openpilot.starpilot.ui.onroad_jetlink_layer.gui_app') as app, \
       patch('openpilot.starpilot.ui.onroad_jetlink_layer.JetlinkStatusFeed') as feed:
    widget, ui = Mock(), NS(started=True, chestnut_state=NS(value=None), sm={'deviceState': NS(chestnutPresent=False)})
    layer = NativeJetlinkLayer(widget, ui)
    state = NS(customization=default_document())
    app.add_render_prepare.assert_called_once_with(layer.prepare, layer.close)
    layer.render(state)
    layer.prepare()
    widget.render.assert_not_called()  # Off by default.
    feed.return_value.set_enabled.assert_called_with(False)
    state.customization['layouts']['large']['jetlink_status']['enabled'] = True
    layer.render(state)
    assert widget.status is feed.return_value.snapshot.return_value
    assert (widget.gpu, widget.chestnut) == (None, False)  # nothing tracks the GPU on this fake UIState
    widget.render.assert_called_once_with(state)
    layer.prepare()
    feed.return_value.set_enabled.assert_called_with(True)
    layer.prepare()  # No render request: the page is hidden.
    feed.return_value.set_enabled.assert_called_with(False)
    layer.render(state)
    ui.started = False
    layer.prepare()
    feed.return_value.set_enabled.assert_called_with(False)
    layer.close()
    app.remove_render_prepare.assert_called_once_with(layer.prepare)
    feed.return_value.close.assert_called_once()


@pytest.mark.parametrize('jetlink,gpu,chestnut,expected', [
  (JetlinkStatus(), None, False, ('Small model', '', GREEN, None)),
  (JetlinkStatus(), 'disconnected', False, ('Small model', '', GREEN, None)),
  (JetlinkStatus('Computer', 'active', 24.6), 'active', False, ('Big model', 'Jetlink', GREEN, 24.6)),
  (JetlinkStatus('Computer', 'ready', offer=True), 'loading', False, ('Small model', 'Re-engage to switch', ACCENT, None)),
  (JetlinkStatus('Computer', 'ready', offer=True), 'failed', False, ('Small model', 'Re-engage to switch', ORANGE, None)),
  (JetlinkStatus('Computer', 'connecting'), 'failed', False, ('Small model', 'Fell back', ORANGE, None)),
  (JetlinkStatus('Computer', 'ready'), None, False, ('Small model', 'Jetlink ready', ACCENT, None)),
  (JetlinkStatus('Computer', 'connected', building=True), 'loading', False, ('Small model', 'Jetlink building', ACCENT, None)),
  (JetlinkStatus('Computer', 'disconnected'), 'failed', False, ('Small model', 'Jetlink offline', GRAY, None)),
  (JetlinkStatus(state='unavailable'), 'failed', False, ('Small model', 'Jetlink error', ORANGE, None)),
  (JetlinkStatus(), 'active', True, ('Big model', 'Chestnut', GREEN, None)),
  (JetlinkStatus(), 'failed', True, ('Small model', 'Fell back', ORANGE, None)),
  (JetlinkStatus(), 'loading', True, ('Small model', 'Chestnut loading', ACCENT, None)),
])
def test_big_model_card_says_which_model_drives_in_a_few_words(jetlink, gpu, chestnut, expected):
  assert tuple(big_model_card(jetlink, gpu, chestnut)) == expected


def test_gpu_inputs_read_uistate():
  ui = NS(chestnut_state=NS(value='active'), sm={'deviceState': NS(chestnutPresent=True)})
  assert gpu_inputs(ui) == ('active', True)
  with pytest.raises(AttributeError):
    gpu_inputs(NS())

  proj_ui = NS(
    projection_read_only=True,
    chestnut_state=NS(value='disconnected'),
    sm={'deviceState': NS(chestnutPresent=True)},
    _update_chestnut_state=lambda: setattr(proj_ui, 'chestnut_state', NS(value='active')),
  )
  assert gpu_inputs(proj_ui) == ('active', True)


@pytest.mark.parametrize('raw,expected', [
  ('Regret Driven Framework V4 👀📡', 'RDF V4'),
  ('Terrific Super Fantastic Do Over 👀📡', 'TSF Do Over'),
  ('Vegetarian-Filet-O-Fish 👀📡', 'V-Filet-O-Fish'),
  ('Cinque v3 (September 17, 2026)', 'Cinque v3'),
  ('Cinque v3 Model', 'Cinque v3'),
  ('Regret Driven Framework V4 / Cinque v3', 'RDF V4 / Cinque v3'),
  ('Alpha · Beta — Gamma', 'Alpha - Beta - Gamma'),
])
def test_abbreviate_model_name_strips_noise_and_shortens_long_series(raw, expected):
  assert abbreviate_model_name(raw) == expected


def test_big_model_card_uses_abbreviated_model_names_and_tier():
  active_jl = big_model_card(
    JetlinkStatus('Computer', 'active', 24.6, model='Cinque v3 (September 17, 2026)'),
    'active',
    False,
    small='Regret Driven Framework V4 👀📡',
    big='ResFit',
  )
  assert (active_jl.title, active_jl.detail, active_jl.tier) == ('Cinque v3', 'Jetlink', 'BIG')

  active_cn = big_model_card(
    JetlinkStatus(),
    'active',
    True,
    small='Regret Driven Framework V4 👀📡',
    big='ResFit Model',
  )
  assert (active_cn.title, active_cn.detail, active_cn.tier) == ('ResFit', 'Chestnut', 'BIG')

  fallback = big_model_card(
    JetlinkStatus('Computer', 'connecting'),
    'failed',
    False,
    small='Regret Driven Framework V4 👀📡',
    big='Cinque v3',
  )
  assert (fallback.title, fallback.detail, fallback.tier) == ('RDF V4', 'Fell back', 'SMALL')


def test_local_model_names_reads_live_receipts_and_caches():
  entries = {'tr16223': NS(name='TR16'), 'cinquev3': NS(name='Cinque v3'), BUNDLED_CURRENT: NS(name='Cheddar')}
  pair = NS(pid=123, process_start_ticks=456, loaded_mono_ns=1, lateral_id='tr16223', longitudinal_id='cinquev3')
  onroad_jetlink._model_cache = None
  with patch.object(onroad_jetlink, 'model_entries', return_value=entries), \
       patch.object(onroad_jetlink, 'read_pair_receipt', return_value=pair), \
       patch.object(onroad_jetlink, 'process_start_ticks', return_value=456):
    assert local_model_names(now=10.0) == ('TR16 / Cinque v3', 'TR16 / Cinque v3')
  with patch.object(onroad_jetlink, 'read_pair_receipt', side_effect=AssertionError('should use cache')):
    assert local_model_names(now=10.5) == ('TR16 / Cinque v3', 'TR16 / Cinque v3')

  for variant, expected in ((ModelVariant.SMALL, ('Cheddar', '')), (ModelVariant.CHESTNUT, ('', ''))):
    onroad_jetlink._model_cache = None
    load = NS(pid=123, process_start_ticks=456, loaded_mono_ns=1, model_id=BUNDLED_CURRENT, variant=variant)
    with patch.object(onroad_jetlink, 'model_entries', return_value=entries), \
         patch.object(onroad_jetlink, 'read_pair_receipt', return_value=None), \
         patch.object(onroad_jetlink, 'read_receipt', return_value=load), \
         patch.object(onroad_jetlink, 'process_start_ticks', return_value=456):
      assert local_model_names(now=20.0) == expected
  onroad_jetlink._model_cache = None


@pytest.mark.parametrize('ticks', [0, 789])
def test_local_model_names_reject_stale_process_receipts(ticks):
  load = NS(pid=123, process_start_ticks=456, loaded_mono_ns=1, model_id='tr16223', variant=ModelVariant.SMALL)
  pair = NS(pid=123, process_start_ticks=456, loaded_mono_ns=1, lateral_id='tr16223', longitudinal_id='tr16223')
  onroad_jetlink._model_cache = None
  with patch.object(onroad_jetlink, 'model_entries', return_value={'tr16223': NS(name='TR16')}), \
       patch.object(onroad_jetlink, 'read_pair_receipt', return_value=pair), \
       patch.object(onroad_jetlink, 'read_receipt', return_value=load), \
       patch.object(onroad_jetlink, 'process_start_ticks', return_value=ticks):
    assert local_model_names(now=30.0) == ('', '')
  onroad_jetlink._model_cache = None


def test_local_model_names_unavailable_does_not_guess_from_preferences():
  onroad_jetlink._model_cache = None
  with patch.object(onroad_jetlink, 'read_pair_receipt', return_value=None), \
       patch.object(onroad_jetlink, 'read_receipt', return_value=None):
    assert local_model_names(now=30.0) == ('', '')
  onroad_jetlink._model_cache = None
  with patch.object(onroad_jetlink, 'model_entries', side_effect=OSError('unavailable')):
    assert local_model_names(now=30.0) == ('', '')
  onroad_jetlink._model_cache = None


def _fake_fonts():
  def measure(text, role, size):
    return TextSize(len(text) * size * 0.55, size * 1.242)
  fonts = Mock()
  fonts.measure.side_effect = measure
  return fonts


def _enabled_state():
  doc = default_document()
  doc['layouts']['large']['jetlink_status']['enabled'] = True
  return NS(customization=doc, alert=NS(size='none'))


def test_widget_renders_option4_eyebrow_latency_and_scrolls_long_model_names():
  clock = [100.0]
  fonts = _fake_fonts()
  widget = JetlinkStatusWidget(fonts, ticks=lambda: clock[0], models=lambda: ('RDF V4', 'Cinque v3'))
  widget.status = JetlinkStatus('Computer', 'active', 43.8, model='Cinque v3')
  widget.gpu = 'active'
  state = _enabled_state()

  with patch.object(onroad_jetlink.rl, 'draw_rectangle_rounded'), \
       patch.object(onroad_jetlink.rl, 'draw_circle_v') as circles, \
       patch.object(onroad_jetlink.clip, 'begin_scissor_mode') as begin_scissor, \
       patch.object(onroad_jetlink.clip, 'end_scissor_mode') as end_scissor:
    widget.render(state)

  # Two circles: status dot (7.5) and eyebrow separator dot (2.8)
  assert [c.args[1] for c in circles.call_args_list] == [7.5, 2.8]
  begin_scissor.assert_not_called()
  end_scissor.assert_not_called()
  drawn = [(c.args[0], c.args[1], c.args[2], round(c.args[3], 1), c.args[5]) for c in fonts.draw.call_args_list]
  assert any(t == 'BIG' and sz == 20 for t, _, sz, _, _ in drawn)
  assert any(t == 'JETLINK' for t, _, _, _, _ in drawn)
  assert any(t == '43.8' and col is ORANGE for t, _, _, _, col in drawn)
  assert any(t == 'Cinque v3' and sz == 28 and col is TEXT for t, _, sz, _, col in drawn)

  # Long model name (> 356px) pauses then scrolls horizontally inside a scissor clip.
  fonts.draw.reset_mock()
  long_name = 'Watermelon Indigo v10 / Cinque Divergence Mask'
  widget.status = JetlinkStatus('Computer', 'active', 24.6, model=long_name)
  with patch.object(onroad_jetlink.rl, 'draw_rectangle_rounded'), \
       patch.object(onroad_jetlink.rl, 'draw_circle_v'), \
       patch.object(onroad_jetlink.clip, 'begin_scissor_mode') as begin_scissor, \
       patch.object(onroad_jetlink.clip, 'end_scissor_mode') as end_scissor:
    widget.render(state)
    clock[0] = 103.0  # 1s past the 2s pause -> scrolled 40px left
    fonts.draw.reset_mock()
    widget.render(state)
    placed_x = state.customization['layouts']['large']['jetlink_status']['x'] + 32
    title_draws = [c.args for c in fonts.draw.call_args_list if c.args[0] == long_name]
    assert len(title_draws) == 1
    assert title_draws[0][3] == pytest.approx(placed_x - 40.0)

    clock[0] = 113.0  # 11s past the 2s pause -> repeat copy enters the 356px clip
    fonts.draw.reset_mock()
    widget.render(state)

  begin_scissor.assert_called()
  end_scissor.assert_called()
  title_draws = [c.args for c in fonts.draw.call_args_list if c.args[0] == long_name]
  assert len(title_draws) == 2
  expected_offset = scroll_offset(fonts.measure(long_name, FontRole.SEMI_BOLD, 28).width, 420 - 64, 13.0)
  assert expected_offset == pytest.approx(440.0)
  assert title_draws[0][3] == pytest.approx(placed_x - expected_offset)
  assert title_draws[1][3] > title_draws[0][3]


def test_widget_shows_driving_model_only_when_jetlink_and_chestnut_are_off():
  fonts = _fake_fonts()
  widget = JetlinkStatusWidget(fonts, models=lambda: ('RDF V4', ''))
  state = _enabled_state()

  card = big_model_card(JetlinkStatus(), None, False, small='Regret Driven Framework V4 👀📡')
  assert (card.title, card.detail, card.tier, card.dot is GREEN) == ('RDF V4', '', 'SMALL', True)

  with patch.object(onroad_jetlink.rl, 'draw_rectangle_rounded'), \
       patch.object(onroad_jetlink.rl, 'draw_circle_v') as circles:
    widget.render(state)

  # Single status circle (no separator bullet) and 'DRIVING MODEL' eyebrow
  assert len(circles.call_args_list) == 1
  assert circles.call_args_list[0].args[1] == 7.5
  assert circles.call_args_list[0].args[2] is GREEN
  drawn = [(c.args[0], c.args[2], c.args[5]) for c in fonts.draw.call_args_list]
  assert ('DRIVING MODEL', 20, onroad_jetlink.MUTED) in drawn
  assert ('RDF V4', 28, TEXT) in drawn
