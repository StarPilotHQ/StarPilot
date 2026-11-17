from types import SimpleNamespace

import pytest

TRACK = {'source': 'spotify', 'status': 'playing', 'title': 'Midnight City', 'artist': 'M83', 'album': '',
         'durationMs': 200000, 'position': {'ms': 50000, 'at': 0, 'speed': 0.0}, 'shuffle': False, 'repeat': 'off',
         'artwork': '', 'device': 'StarPilot'}
PHONE = {**TRACK, 'source': 'phone', 'shuffle': None, 'repeat': None, 'device': 'Pixel 9'}
ELSEWHERE = {'source': 'spotify', 'status': 'elsewhere', 'title': '', 'device': 'StarPilot'}
# Big card at x 10, y 20: centres of shuffle, previous, play/pause, next and repeat, and the row's centre line.
BUTTONS = {'shuffle': 138, 'previous': 234, 'play_pause': 330, 'next': 426, 'repeat': 522}
ROW = 228


@pytest.fixture
def widget(monkeypatch):
  from openpilot.starpilot.ui import onroad_now_playing
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingWidget
  drawn = []
  for name in ('draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex', 'draw_triangle', 'draw_circle_v', 'draw_line_ex', 'draw_ring',
               'draw_texture_pro', 'draw_rectangle_rec'):
    monkeypatch.setattr(onroad_now_playing.rl, name, lambda *args, name=name: drawn.append((name, args)))
  texts = []
  fonts = SimpleNamespace(measure=lambda text, role, size: SimpleNamespace(width=len(text) * size * .5, height=size * 1.2),
                          draw=lambda text, *args: texts.append(text))
  return NowPlayingWidget(fonts, clock=lambda: 0.0), drawn, texts


def state(enabled=True, opacity=85, alert='none', collapse=False, x=10, size='big'):
  placed = {'x': x, 'y': 20, 'enabled': enabled, 'opacity': opacity, 'collapse': collapse, 'size': size}
  return SimpleNamespace(alert=SimpleNamespace(size=alert), viewport_width=1860,
                         customization={'layouts': {'large': {'now_playing': placed}}})


def placed_of(current):
  return current.customization['layouts']['large']['now_playing']


def count(drawn, kind):
  return sum(name == kind for name, _ in drawn)


def test_big_card_draws_source_service_song_and_times_over_the_chosen_opacity(widget):
  card, drawn, texts = widget
  card.track = TRACK
  card.render(state(opacity=50))
  assert texts == ['StarPilot', 'Spotify', 'Midnight City', 'M83', '0:50', '3:20']
  assert drawn[0][0] == 'draw_rectangle_rounded' and drawn[0][1][0].height == 264 and drawn[0][1][3].a == round(255 * .5)


def test_phone_audio_is_labelled_phone_and_names_the_phone(widget):
  card, _, texts = widget
  card.track = PHONE
  card.render(state())
  assert texts[:2] == ['Phone', 'Pixel 9']


def test_small_card_is_a_pill_with_title_over_artist(widget):
  card, drawn, texts = widget
  card.track = TRACK
  card.render(state(size='small'))
  assert texts == ['Midnight City', 'M83']
  background = drawn[0][1]
  assert (background[0].width, background[0].height, background[1]) == (520, 96, 1.0)


def test_paused_shows_a_play_mark_and_playing_shows_pause_bars(widget):
  card, drawn, _ = widget
  card.track = {**TRACK, 'status': 'paused'}
  card.render(state())
  paused = count(drawn, 'draw_triangle')
  drawn.clear()
  card.track = TRACK
  card.render(state())
  assert count(drawn, 'draw_triangle') == paused - 1


def test_shuffle_and_repeat_are_dim_when_the_player_cannot_do_them(widget):
  from openpilot.starpilot.ui.onroad_now_playing import DIM
  card, drawn, _ = widget
  card.track = PHONE
  card.render(state())
  dims = [args for name, args in drawn if name == 'draw_line_ex' and args[-1] is DIM]
  assert dims  # shuffle strokes and repeat lines
  drawn.clear()
  card.track = TRACK
  card.render(state())
  assert not any(args[-1] is DIM for name, args in drawn if name == 'draw_line_ex')


def test_shuffle_dot_only_when_on(widget):
  card, drawn, _ = widget
  card.track = TRACK
  card.render(state())
  off = count(drawn, 'draw_circle_v')
  drawn.clear()
  card.track = {**TRACK, 'shuffle': True}
  card.render(state())
  assert count(drawn, 'draw_circle_v') == off + 1


def test_repeat_shows_a_dot_when_on_and_a_bar_for_one_song(widget):
  card, drawn, _ = widget
  shown = {}
  for mode in ('off', 'context', 'track'):
    drawn.clear()
    card.track = {**TRACK, 'repeat': mode}
    card.render(state())
    shown[mode] = (count(drawn, 'draw_circle_v'), count(drawn, 'draw_rectangle_rounded'))
  assert shown['context'] == (shown['off'][0] + 1, shown['off'][1])
  assert shown['track'] == (shown['off'][0] + 1, shown['off'][1] + 1)


def test_seek_bar_without_a_length_has_no_times(widget):
  card, _, texts = widget
  card.track = {**TRACK, 'durationMs': 0}
  card.render(state())
  assert texts == ['StarPilot', 'Spotify', 'Midnight City', 'M83']


@pytest.mark.parametrize('size', ['big', 'small'])
def test_collapsed_paused_card_is_just_the_play_button(widget, size):
  card, drawn, texts = widget
  card.track = {**TRACK, 'status': 'paused'}
  card.render(state(collapse=True, size=size))
  assert texts == [] and count(drawn, 'draw_triangle') == 1


@pytest.mark.parametrize('current', [state(enabled=False), state(alert='full')])
def test_hidden_when_disabled_or_an_alert_is_up(widget, current):
  card, drawn, texts = widget
  card.track = TRACK
  card.render(current)
  assert drawn == [] and texts == []


def test_nothing_drawn_without_a_track(widget):
  card, drawn, texts = widget
  card.render(state())
  assert drawn == [] and texts == []


def test_big_buttons_sit_in_a_centered_row_in_order():
  from openpilot.starpilot.ui.onroad_now_playing import CONTROLS, card_layout
  rect, buttons = card_layout(placed_of(state()), TRACK, 1860)
  assert (rect.width, rect.height) == (640, 264) and tuple(buttons) == CONTROLS
  assert [round(box.x + box.width / 2) for box in buttons.values()] == list(BUTTONS.values())
  assert all(box.y == 20 + 160 and box.y + box.height == 20 + 264 for box in buttons.values())


def test_small_card_only_has_play_pause():
  from openpilot.starpilot.ui.onroad_now_playing import card_layout
  rect, buttons = card_layout(placed_of(state(size='small')), TRACK, 1860)
  assert (rect.width, rect.height) == (520, 96) and list(buttons) == ['play_pause']
  assert (buttons['play_pause'].x, buttons['play_pause'].width) == (10, 96)


def test_saved_layout_without_a_size_is_small():
  from openpilot.starpilot.ui.onroad_now_playing import card_layout
  placed = placed_of(state())
  del placed['size']
  rect, _ = card_layout(placed, TRACK, 1860)
  assert (rect.width, rect.height) == (520, 96)


@pytest.mark.parametrize('x, size, expected', [(10, 'big', (10, 188)), (1210, 'big', (1754, 188)),
                                               (10, 'small', (10, 20)), (1330, 'small', (1754, 20))])
def test_collapsed_play_button_sits_in_the_bottom_corner_nearer_the_edge(x, size, expected):
  from openpilot.starpilot.ui.onroad_now_playing import card_layout
  rect, buttons = card_layout(placed_of(state(collapse=True, x=x, size=size)), {**TRACK, 'status': 'paused'}, 1860)
  assert list(buttons) == ['play_pause'] and (rect.x, rect.y, rect.width, rect.height) == (*expected, 96, 96)


def tap(touch, current, track, x, y, lift=None):
  placed = placed_of(current)
  down = touch.touch('down', x, y, current, placed, track)
  up = touch.touch('up', *(lift or (x, y)), current, placed, track)
  return down, up


@pytest.mark.parametrize('action', list(BUTTONS))
def test_tap_sends_the_button_under_the_finger(action):
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call))
  assert tap(touch, state(), TRACK, BUTTONS[action], ROW) == (True, True)
  args = {'shuffle': {'enabled': True}, 'repeat': {'mode': 'context'}}.get(action, {})
  assert sent == [(action, args)]


def test_shuffle_and_repeat_are_inert_for_phone_audio():
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call))
  assert tap(touch, state(), PHONE, BUTTONS['shuffle'], ROW) == (True, True)
  assert tap(touch, state(), PHONE, BUTTONS['repeat'], ROW) == (True, True)
  tap(touch, state(), PHONE, BUTTONS['next'], ROW)
  assert sent == [('next', {})]


def test_shuffle_tap_toggles_off_and_repeat_cycles():
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call))
  tap(touch, state(), {**TRACK, 'shuffle': True}, BUTTONS['shuffle'], ROW)
  for mode in ('context', 'track'):
    tap(touch, state(), {**TRACK, 'repeat': mode}, BUTTONS['repeat'], ROW)
  assert sent == [('shuffle', {'enabled': False}), ('repeat', {'mode': 'track'}), ('repeat', {'mode': 'off'})]


def test_small_card_plays_from_its_disc_and_swallows_the_rest():
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call))
  assert tap(touch, state(size='small'), TRACK, 58, 68) == (True, True)
  assert tap(touch, state(size='small'), TRACK, 300, 68) == (True, True)  # the title: claimed, no action
  assert tap(touch, state(size='small'), TRACK, 500, 68) == (True, True)  # the source glyph: a label only
  assert sent == [('play_pause', {})]


def test_card_swallows_taps_but_only_fires_on_the_same_button():
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call))
  assert tap(touch, state(), TRACK, 300, 90) == (True, True)
  assert tap(touch, state(), TRACK, BUTTONS['next'], ROW, lift=(BUTTONS['previous'], ROW)) == (True, True)
  assert tap(touch, state(), TRACK, 900, ROW) == (False, False)
  assert sent == []


def test_collapsed_button_resumes_and_the_rest_of_the_card_rect_falls_through():
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call))
  paused = {**TRACK, 'status': 'paused'}
  assert tap(touch, state(collapse=True), paused, 300, 90) == (False, False)
  assert tap(touch, state(collapse=True), paused, 58, 236) == (True, True)
  assert sent == [('play_pause', {})]


def test_alert_or_missing_card_releases_the_touch():
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call))
  placed = placed_of(state())
  assert touch.touch('down', BUTTONS['next'], ROW, state(), placed, TRACK)
  assert touch.touch('up', BUTTONS['next'], ROW, state(alert='full'), placed, TRACK)
  assert not touch.touch('down', BUTTONS['next'], ROW, None, placed, TRACK)
  assert sent == []


def test_scroll_holds_then_runs_one_pass_and_restarts():
  from openpilot.starpilot.ui.onroad_now_playing import SCROLL_GAP, SCROLL_PAUSE, SCROLL_SPEED, scroll_offset
  assert scroll_offset(100, 200, 5.0) == 0.0
  assert scroll_offset(300, 200, SCROLL_PAUSE - .1) == 0.0
  assert scroll_offset(300, 200, SCROLL_PAUSE + 1) == pytest.approx(SCROLL_SPEED)
  cycle = SCROLL_PAUSE + (300 + SCROLL_GAP) / SCROLL_SPEED
  assert scroll_offset(300, 200, cycle + .5) == 0.0


def test_long_song_line_scrolls_inside_a_clip_and_short_one_does_not(widget, monkeypatch):
  from openpilot.starpilot.ui import onroad_now_playing
  card, _, texts = widget
  clips = []
  monkeypatch.setattr(onroad_now_playing.clip, 'begin_scissor_mode', lambda *args: clips.append(args))
  monkeypatch.setattr(onroad_now_playing.clip, 'end_scissor_mode', lambda: clips.append('end'))
  card.track = TRACK
  card.render(state())
  assert clips == []
  card.track = {**TRACK, 'artist': 'A very long list of featured artists on this one track'}
  line = [card.track['title'], card.track['artist']]
  now = [100.0]
  card.ticks = lambda: now[0]
  texts.clear()
  card.render(state())
  assert texts[2:-2] == line and clips[-1] == 'end'
  now[0] += onroad_now_playing.SCROLL_PAUSE + 14
  texts.clear()
  card.render(state())
  assert texts[2:-2] == line * 2
  assert clips[0][0] == 10 + 40 and clips[0][2] == 640 - 2 * 40


def test_card_shape_strips_cover_the_card_and_round_its_corners():
  from openpilot.starpilot.ui.onroad_now_playing import pill_rows
  for height, radius in ((264, 40), (96, 48)):
    rows = sorted(pill_rows(height, radius))
    assert sum(h for _, h, _ in rows) == pytest.approx(height)
    assert all(a[0] + a[1] == pytest.approx(b[0]) for a, b in zip(rows, rows[1:], strict=False))
    assert rows[0][2] > radius * .6 and min(inset for *_, inset in rows) < .5


@pytest.mark.parametrize('ms, shown', [(0, '0:00'), (58_900, '0:58'), (269_000, '4:29'), (3_725_000, '62:05')])
def test_times(ms, shown):
  from openpilot.starpilot.ui.onroad_now_playing import clock_text
  assert clock_text(ms) == shown


@pytest.fixture
def textures(monkeypatch):
  from openpilot.starpilot.ui import onroad_now_playing
  rl = onroad_now_playing.rl
  loads, unloads, uploads = [], [], []
  monkeypatch.setattr(rl, 'Image', lambda *args: SimpleNamespace(args=args))
  monkeypatch.setattr(rl, 'load_texture_from_image', lambda image: loads.append(image.args[1:3]) or
                      SimpleNamespace(id=len(loads), width=image.args[1], height=image.args[2]))
  monkeypatch.setattr(rl, 'update_texture', lambda texture, pixels: uploads.append(len(pixels)))
  monkeypatch.setattr(rl, 'set_texture_filter', lambda *args: None)
  monkeypatch.setattr(rl, 'is_window_ready', lambda: True)
  monkeypatch.setattr(rl, 'rl_draw_render_batch_active', lambda: None)
  monkeypatch.setattr(rl, 'unload_texture', lambda texture: unloads.append(texture.id))
  return loads, unloads, uploads


@pytest.mark.parametrize('size, height', [('big', 264), ('small', 96)])
def test_cover_is_uploaded_once_and_drawn_in_the_card_shape(widget, textures, size, height):
  loads, unloads, uploads = textures
  card, drawn, _ = widget
  card.track = TRACK
  card.artwork = ('https://i.scdn.co/image/a', (480, 480, b'\0' * 480 * 480 * 4))
  card.render(state(size=size))
  strips = [args for name, args in drawn if name == 'draw_texture_pro']
  card.render(state(size=size))
  assert loads == [(480, 480)] and uploads == [480 * 480 * 4]
  assert sum(dest.height for _, _, dest, *_ in strips) == pytest.approx(height) and min(dest.x for _, _, dest, *_ in strips) == pytest.approx(10, abs=.5)
  sources = [src for _, src, *_ in strips]
  band = 480 * height / (640 if size == 'big' else 520)
  assert min(src.y for src in sources) == pytest.approx((480 - band) / 2)
  assert not any(name == 'draw_rectangle_rounded' and args[0].height == height for name, args in drawn)
  card.track, card.artwork = None, ('', None)
  card.render(state(size=size))  # held song between tracks keeps its cover
  assert unloads == []
  card.track = TRACK
  card.render(state(size=size))
  assert unloads == [1]
  card.close()


def test_phone_audio_never_draws_a_cover(widget, textures):
  loads, _, _ = textures
  card, drawn, _ = widget
  card.track = PHONE
  card.artwork = ('https://i.scdn.co/image/a', (4, 4, b'\0' * 64))
  card.render(state())
  assert loads == [] and count(drawn, 'draw_texture_pro') == 0


class CoveringFonts:
  profile = SimpleNamespace(font_scale=1.0)

  def __init__(self):
    self.drawn = []

  def covers(self, text, role):
    return text.isascii()

  def measure(self, text, role, size):
    return SimpleNamespace(width=len(text) * size * .5, height=size)

  def draw(self, text, *args):
    self.drawn.append(text)


@pytest.fixture
def cut_fonts(monkeypatch):
  from openpilot.starpilot.ui import onroad_now_playing
  loads, unloads, ttf_drawn = [], [], []
  monkeypatch.setattr(onroad_now_playing.TrackText, '_load', staticmethod(lambda text, role: loads.append((text, role)) or f'font:{text}'))
  monkeypatch.setattr(onroad_now_playing.rl, 'is_window_ready', lambda: True)
  monkeypatch.setattr(onroad_now_playing.rl, 'unload_font', unloads.append)
  monkeypatch.setattr(onroad_now_playing.rl, 'rl_draw_render_batch_active', lambda: None)
  monkeypatch.setattr(onroad_now_playing.rl, 'draw_text_ex', lambda font, text, *args: ttf_drawn.append(text))
  monkeypatch.setattr(onroad_now_playing.rl, 'measure_text_ex', lambda font, text, size, spacing: SimpleNamespace(x=len(text) * size * .5, y=size))
  monkeypatch.delattr(onroad_now_playing.rl, '_orig_draw_text_ex', raising=False)
  return onroad_now_playing.TrackText(CoveringFonts()), loads, unloads, ttf_drawn


def test_ascii_names_stay_on_the_bitmap_atlas(cut_fonts):
  from openpilot.starpilot.ui.presentation import FontRole
  text, loads, _, ttf_drawn = cut_fonts
  text.draw('Midnight City', FontRole.SEMI_BOLD, 30, 0, 0, None)
  assert text.fonts.drawn == ['Midnight City'] and loads == [] and ttf_drawn == []


def test_accented_name_is_cut_from_the_ttf_once_and_freed_on_the_next_song(cut_fonts):
  from openpilot.starpilot.ui.presentation import FontRole
  text, loads, unloads, ttf_drawn = cut_fonts
  title = 'Em Về Tinh Khôi'
  for _ in range(3):
    text.measure(title, FontRole.SEMI_BOLD, 30)
    text.draw(title, FontRole.SEMI_BOLD, 30, 0, 0, None)
  assert loads == [(title, FontRole.SEMI_BOLD)] and ttf_drawn == [title] * 3 and text.fonts.drawn == []
  text.draw('Midnight City', FontRole.SEMI_BOLD, 30, 0, 0, None)
  assert unloads == [f'font:{title}'] and text.fonts.drawn == ['Midnight City']
  text.draw('Nắng Ấm Xa Dần', FontRole.NORMAL, 22, 0, 0, None)
  text.close()
  assert unloads == [f'font:{title}', 'font:Nắng Ấm Xa Dần']


def test_failed_ttf_load_falls_back_to_the_atlas(cut_fonts, monkeypatch):
  from openpilot.starpilot.ui import onroad_now_playing
  from openpilot.starpilot.ui.presentation import FontRole
  text, _, unloads, _ = cut_fonts
  monkeypatch.setattr(onroad_now_playing.TrackText, '_load', staticmethod(lambda text, role: None))
  text.draw('Khôi', FontRole.SEMI_BOLD, 30, 0, 0, None)
  text.close()
  assert text.fonts.drawn == ['Khôi'] and unloads == []


def test_decomposed_accents_are_composed_before_drawing(widget):
  card, _, texts = widget
  card.track = {**TRACK, 'title': 'Về', 'artist': 'Khôi'}
  card.render(state())
  assert texts[2:4] == ['Về', 'Khôi']


class Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


@pytest.fixture
def timed(widget):
  card, drawn, texts = widget
  card.ticks = clock = Clock()
  return card, drawn, texts, clock


def spinning(drawn):
  return sum(name == 'draw_ring' and args[4] - args[3] == 270 for name, args in drawn) == 1


def test_card_holds_the_last_song_while_the_player_buffers_the_next(timed):
  card, drawn, texts, clock = timed
  card.track = TRACK
  card.render(state())
  card.track = None
  clock.now += 1
  texts.clear()
  drawn.clear()
  card.render(state())
  assert 'Midnight City' in texts and spinning(drawn) and card.showing() is TRACK
  clock.now += 4
  drawn.clear()
  card.render(state())
  assert drawn == [] and card.showing() is None


def test_pending_skip_ignores_routine_position_updates(timed):
  card, _, _, clock = timed
  card.track = TRACK
  card.press('next')
  card.release('next')
  position = TRACK['position']
  assert isinstance(position, dict)
  card.track = {**TRACK, 'position': {**position, 'ms': 51000}}
  assert card._busy(card.track, clock())
  card.track = {**card.track, 'title': 'The next song'}
  assert not card._busy(card.track, clock())


def test_play_button_spins_until_the_player_answers_a_skip(timed):
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  card, drawn, _, clock = timed
  card.track = TRACK
  touch = NowPlayingTouch(lambda *call: None, card)
  tap(touch, state(), TRACK, BUTTONS['next'], ROW)
  card.render(state())
  assert spinning(drawn)
  card.track = {**TRACK, 'title': 'Wait'}
  drawn.clear()
  card.render(state())
  assert not spinning(drawn)
  tap(touch, state(), card.track, BUTTONS['play_pause'], ROW)
  clock.now += 3
  drawn.clear()
  card.render(state())
  assert not spinning(drawn)


def test_a_pressed_button_lights_and_fades_after_the_lift(timed):
  from openpilot.starpilot.ui.onroad_now_playing import BUTTON_R, NowPlayingTouch
  card, drawn, _, clock = timed
  card.track = TRACK
  placed = placed_of(state())
  touch = NowPlayingTouch(lambda *call: None, card)

  def lit():
    return [args for name, args in drawn if name == 'draw_circle_v' and args[1] == BUTTON_R + 8 and abs(args[0].x - BUTTONS['previous']) < 1]

  touch.touch('down', BUTTONS['previous'], ROW, state(), placed, TRACK)
  card.render(state())
  assert lit()[0][2].a == 60
  touch.touch('up', BUTTONS['previous'], ROW, state(), placed, TRACK)
  clock.now += .2
  drawn.clear()
  card.render(state())
  assert 0 < lit()[0][2].a < 60
  clock.now += .2
  drawn.clear()
  card.render(state())
  assert lit() == []


@pytest.mark.parametrize('size', ['big', 'small'])
def test_play_on_starpilot_shows_while_spotify_plays_elsewhere(timed, size):
  card, drawn, texts, _ = timed
  card.track = ELSEWHERE
  card.render(state(size=size, collapse=True))
  assert texts[0] == 'Spotify is on another device'
  assert texts[-1] == ('Play on StarPilot' if size == 'big' else 'Tap to play on StarPilot')
  assert not spinning(drawn)


@pytest.mark.parametrize('size', ['big', 'small'])
def test_play_on_starpilot_tap_activates_spotify_here_and_spins_until_the_song_arrives(timed, size):
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch, card_layout
  card, drawn, texts, clock = timed
  card.track = ELSEWHERE
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call), card)
  box = card_layout(placed_of(state(size=size)), ELSEWHERE, 1860)[1]['play_here']
  assert tap(touch, state(size=size), ELSEWHERE, box.x + box.width / 2, box.y + box.height / 2) == (True, True)
  assert sent == [('activate', {})]
  card.render(state(size=size))
  assert spinning(drawn)
  card.track = TRACK
  texts.clear()
  card.render(state(size=size))
  assert 'Midnight City' in texts
  card.track = ELSEWHERE
  clock.now += 1
  card.render(state(size=size))
  card.track = None
  assert card.showing() is None


def test_big_play_on_starpilot_only_fires_from_its_button(timed):
  from openpilot.starpilot.ui.onroad_now_playing import NowPlayingTouch
  card, _, _, _ = timed
  sent = []
  touch = NowPlayingTouch(lambda *call: sent.append(call), card)
  assert tap(touch, state(), ELSEWHERE, 60, 60) == (True, True)
  assert sent == []
