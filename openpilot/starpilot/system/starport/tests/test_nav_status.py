"""Mapbox turn-by-turn reaches the car's own displays when the head unit offers the navigation service."""

import pytest

from openpilot.starpilot.system.starport import nav_status as ns
from openpilot.starpilot.system.starport.session import ProjectionSession
from openpilot.starpilot.system.starport.wire import field, parse_fields

GUIDING = {'state': 'guiding', 'metric': False, 'road': 'Main Street', 'type': 'turn', 'modifier': 'left',
           'distance': 400.0, 'remaining': 8000.0, 'duration': 800.0}


class Session:
  def __init__(self, services=(ns.NAVIGATION_SERVICE,), navigation=None):
    self.channels = [{'id': 4, 'services': [3]}, {'id': 12, 'services': list(services), 'navigation': navigation or {}}]
    self.sent = []
    self.focus_sent = []
  def send(self, channel, kind, body=b'', control=False):
    if channel == 0:
      self.focus_sent.append((channel, kind, body))
      return
    self.sent.append((channel, kind, body))


class Feed:
  def __init__(self): self.value, self.closed = None, False
  def snapshot(self): return self.value
  def close(self): self.closed = True


@pytest.fixture
def nav():
  session, feed, now = Session(), Feed(), [100.0]
  stream = ns.NavStatus.create(session, lambda *a, **k: None, feed=feed, clock=lambda: now[0])
  return stream, session, feed, now


def start(stream, session, now):
  stream.service()
  assert session.sent[-1][:2] == (12, ns.MSG_CHANNEL_OPEN_REQUEST)
  assert stream.handle(12, ns.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]})
  assert stream.handle(12, ns.CLUSTER_START, {})
  session.sent.clear()


def tick(stream, now, seconds=1.0):
  now[0] += seconds
  stream.service()


@pytest.mark.parametrize('kind,modifier,expected', [
  ('turn', 'left', (ns.EVENT_TURN, ns.SIDE_LEFT)),
  ('turn', 'slightRight', (ns.EVENT_SLIGHT_TURN, ns.SIDE_RIGHT)),
  ('end of road', 'sharp left', (ns.EVENT_SHARP_TURN, ns.SIDE_LEFT)),
  ('turn', 'uturn', (ns.EVENT_U_TURN, ns.SIDE_UNSPECIFIED)),
  ('continue', 'straight', (ns.EVENT_STRAIGHT, ns.SIDE_UNSPECIFIED)),
  ('new name', 'none', (ns.EVENT_NAME_CHANGE, ns.SIDE_UNSPECIFIED)),
  ('off ramp', 'right', (ns.EVENT_OFF_RAMP, ns.SIDE_RIGHT)),
  ('on ramp', 'slight left', (ns.EVENT_ON_RAMP, ns.SIDE_LEFT)),
  ('merge', 'left', (ns.EVENT_MERGE, ns.SIDE_LEFT)),
  ('fork', 'right', (ns.EVENT_FORK, ns.SIDE_RIGHT)),
  ('roundabout', 'right', (ns.EVENT_ROUNDABOUT_ENTER_AND_EXIT, ns.SIDE_RIGHT)),
  ('exit roundabout', 'left', (ns.EVENT_ROUNDABOUT_EXIT, ns.SIDE_LEFT)),
  ('depart', 'none', (ns.EVENT_DEPART, ns.SIDE_UNSPECIFIED)),
  ('arrive', 'right', (ns.EVENT_DESTINATION, ns.SIDE_RIGHT)),
  ('', '', (ns.EVENT_UNKNOWN, ns.SIDE_UNSPECIFIED)),
])
def test_mapbox_maneuvers_map_to_the_cars_events(kind, modifier, expected):
  assert ns.maneuver_event(kind, modifier) == expected


@pytest.mark.parametrize('meters,metric,expected', [
  (180, True, (175000, ns.UNIT_METERS)),
  (740, True, (750000, ns.UNIT_METERS)),
  (1540, True, (1500, ns.UNIT_KILOMETERS_P1)),
  (23000, True, (23000, ns.UNIT_KILOMETERS)),
  (60, False, (200000, ns.UNIT_FEET)),
  (4000, False, (2500, ns.UNIT_MILES_P1)),
  (40000, False, (25000, ns.UNIT_MILES)),
])
def test_distance_is_rounded_like_the_comma_card(meters, metric, expected):
  assert ns.display_distance(meters, metric) == expected


def test_head_unit_descriptor_is_parsed():
  raw = field(1, 500) + field(2, ns.TYPE_IMAGE) + field(3, field(1, 128) + field(2, 64) + field(3, 16))
  assert ns.parse_navigation(raw) == {'min_interval_ms': 500, 'type': ns.TYPE_IMAGE, 'image_width': 64,
                                      'image_height': 128, 'image_depth': 16}


def test_only_head_units_offering_the_service_get_it():
  assert ns.NavStatus.create(Session([3]), lambda *a, **k: None, feed=Feed()) is None


def test_rejected_or_silent_channel_stays_quiet(nav):
  stream, session, feed, now = nav
  stream.service()
  now[0] += ns.STEP_TIMEOUT + 1
  stream.service()
  assert stream.state == 'unavailable'
  feed.value = GUIDING
  tick(stream, now)
  assert len(session.sent) == 1


def test_nothing_is_sent_until_the_car_asks_or_the_grace_passes(nav):
  stream, session, feed, now = nav
  feed.value = GUIDING
  stream.service()
  stream.handle(12, ns.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]})
  session.sent.clear()
  tick(stream, now, 0.5)
  assert session.sent == []
  tick(stream, now, ns.START_GRACE)
  assert [kind for _, kind, _ in session.sent] == [ns.NAV_STATUS, ns.NAV_TURN, ns.NAV_NOTIFICATION, ns.NAV_DISTANCE, ns.NAV_CURRENT_POSITION]


def test_a_car_that_stopped_the_cluster_is_not_fed_again(nav):
  stream, session, feed, now = nav
  start(stream, session, now)
  stream.handle(12, ns.CLUSTER_STOP, {})
  feed.value = GUIDING
  tick(stream, now, ns.START_GRACE + 1)
  assert session.sent == []
  stream.handle(12, ns.CLUSTER_START, {})
  tick(stream, now)
  assert [kind for _, kind, _ in session.sent] == [ns.NAV_STATUS, ns.NAV_TURN, ns.NAV_NOTIFICATION, ns.NAV_DISTANCE, ns.NAV_CURRENT_POSITION]


def test_route_guidance_then_updates_then_end(nav):
  stream, session, feed, now = nav
  start(stream, session, now)
  tick(stream, now)
  assert [(kind, parse_fields(body)) for _, kind, body in session.sent] == [(ns.NAV_STATUS, {1: [ns.STATUS_INACTIVE]})]
  session.sent.clear()
  feed.value = GUIDING
  tick(stream, now)
  status, turn, notification, distance, position = session.sent
  assert (status[1], parse_fields(status[2])) == (ns.NAV_STATUS, {1: [ns.STATUS_ACTIVE]})
  assert turn[1] == ns.NAV_TURN
  assert parse_fields(turn[2]) == {1: [b'Main Street'], 2: [7], 3: [ns.SIDE_LEFT]}
  assert distance[1] == ns.NAV_DISTANCE
  assert parse_fields(distance[2]) == {1: [400], 2: [40], 3: [200], 4: [ns.UNIT_MILES_P1]}
  session.sent.clear()
  tick(stream, now)
  assert session.sent == []  # nothing changed
  feed.value = {**GUIDING, 'distance': 300.0}
  tick(stream, now)
  assert [kind for _, kind, _ in session.sent] == [ns.NAV_DISTANCE, ns.NAV_CURRENT_POSITION]
  session.sent.clear()
  feed.value = {**GUIDING, 'distance': 20.0, 'road': 'Elm Street', 'type': 'arrive', 'modifier': 'right'}
  tick(stream, now)
  assert [kind for _, kind, _ in session.sent] == [ns.NAV_TURN, ns.NAV_NOTIFICATION, ns.NAV_DISTANCE, ns.NAV_CURRENT_POSITION]
  session.sent.clear()
  feed.value = {'state': 'routing'}
  tick(stream, now)
  assert parse_fields(session.sent[-1][2]) == {1: [ns.STATUS_REROUTING]}
  feed.value = None
  tick(stream, now)
  tick(stream, now)
  assert parse_fields(session.sent[-1][2]) == {1: [ns.STATUS_INACTIVE]}
  assert [kind for _, kind, _ in session.sent].count(ns.NAV_STATUS) == 2


def test_distance_updates_respect_the_cars_minimum_interval():
  session, feed, now = Session(navigation={'min_interval_ms': 5000, 'type': ns.TYPE_ENUM}), Feed(), [100.0]
  stream = ns.NavStatus.create(session, lambda *a, **k: None, feed=feed, clock=lambda: now[0])
  start(stream, session, now)
  feed.value = GUIDING
  tick(stream, now)
  session.sent.clear()
  feed.value = {**GUIDING, 'distance': 300.0}
  tick(stream, now, 1.0)
  assert session.sent == []
  tick(stream, now, 5.0)
  assert [kind for _, kind, _ in session.sent] == [ns.NAV_DISTANCE, ns.NAV_CURRENT_POSITION]


def test_image_head_units_get_the_turn_arrow_at_their_size():
  session, feed, now = Session(navigation={'type': ns.TYPE_IMAGE, 'image_width': 64, 'image_height': 64}), Feed(), [100.0]
  stream = ns.NavStatus.create(session, lambda *a, **k: None, feed=feed, clock=lambda: now[0])
  start(stream, session, now)
  feed.value = GUIDING
  tick(stream, now)
  turn = parse_fields(next(body for _, kind, body in session.sent if kind == ns.NAV_TURN))
  assert turn[4][0].startswith(b'\x89PNG')


def test_enum_head_units_get_no_image(nav):
  stream, session, feed, now = nav
  start(stream, session, now)
  feed.value = GUIDING
  tick(stream, now)
  assert 4 not in parse_fields(next(body for _, kind, body in session.sent if kind == ns.NAV_TURN))


def test_session_hands_the_nav_channel_to_nav_status():
  session = ProjectionSession.__new__(ProjectionSession)
  session.audio, session.mode, session.input_channel = None, None, 3
  got = []
  session.nav = type('N', (), {'handle': lambda self, c, k, f: got.append((c, k)) or c == 12})()
  session.handle(12, ns.CLUSTER_START, b'')
  assert got == [(12, ns.CLUSTER_START)]


def test_focus_is_requested_before_cluster_start_and_released_at_route_end(nav):
  stream, session, feed, now = nav
  stream.service()
  stream.handle(12, ns.MSG_CHANNEL_OPEN_RESPONSE, {1: [0]})
  feed.value = GUIDING
  tick(stream, now, 0.25)
  assert session.focus_sent == [(0, 0x0d, b'\x08\x02')]
  assert not any(kind == ns.NAV_TURN for _, kind, _ in session.sent)
  stream.handle(0, 0x0e, {1: [2]})
  stream.handle(12, ns.CLUSTER_START, {})
  tick(stream, now)
  tick(stream, now)
  assert len(session.focus_sent) == 1
  feed.value = {'state': 'routing'}
  tick(stream, now)
  assert len(session.focus_sent) == 1
  feed.value = None
  tick(stream, now)
  assert session.focus_sent[-1] == (0, 0x0d, b'\x08\x01')
  feed.value = GUIDING
  tick(stream, now)
  assert session.focus_sent[-1] == (0, 0x0d, b'\x08\x02')


def test_native_focus_pauses_updates_and_projected_focus_resends(nav):
  stream, session, feed, now = nav
  start(stream, session, now)
  feed.value = GUIDING
  tick(stream, now)
  session.sent.clear()
  assert stream.handle(0, 0x0e, {1: [1]})
  feed.value = {**GUIDING, 'distance': 100.0}
  tick(stream, now)
  assert session.sent == []
  assert stream.handle(0, 0x0e, {1: [2]})
  tick(stream, now)
  assert [kind for _, kind, _ in session.sent] == [ns.NAV_STATUS, ns.NAV_TURN, ns.NAV_NOTIFICATION, ns.NAV_DISTANCE, ns.NAV_CURRENT_POSITION]


def test_repeated_cluster_start_resends_current_guidance(nav):
  stream, session, feed, now = nav
  start(stream, session, now)
  feed.value = GUIDING
  tick(stream, now)
  session.sent.clear()
  stream.handle(12, ns.CLUSTER_START, {})
  tick(stream, now)
  assert [kind for _, kind, _ in session.sent] == [ns.NAV_STATUS, ns.NAV_TURN, ns.NAV_NOTIFICATION, ns.NAV_DISTANCE, ns.NAV_CURRENT_POSITION]


def test_legacy_distance_matches_captured_phone_packet():
  assert ns.distance_message(13925, 517, True) == bytes.fromhex('08 e5 6c 10 85 04 18 b0 6d 20 02')


def test_non_square_cluster_image_uses_descriptor_width_and_height():
  import io
  from PIL import Image
  options = ns.parse_navigation(bytes.fromhex('08 f4 03 10 02 1a 05 08 40 10 80 01'))
  image = ns.turn_image('turn', 'left', options['image_width'], options['image_height'])
  assert image is not None
  with Image.open(io.BytesIO(image)) as icon:
    assert icon.size == (128, 64)


def test_projection_session_dispatches_navigation_focus(nav):
  stream, _, _, _ = nav
  session = ProjectionSession.__new__(ProjectionSession)
  session.audio, session.mode, session.nav = None, None, stream
  session.handle(0, 0x0e, b'\x08\x02')
  assert stream.focus == ns.FOCUS_PROJECTED


@pytest.mark.parametrize('kind,modifier,wire_value', [
  ('turn', 'left', 7), ('turn', 'right', 8), ('turn', 'slightRight', 6),
  ('turn', 'sharpLeft', 9), ('arrive', 'right', 42), ('arrive', '', 39),
  ('merge', '', 29), ('fork', 'left', 25), ('continue', 'straight', 36),
  ('on ramp', 'slight right', 14), ('off ramp', 'left', 23),
  ('roundabout', 'right', 0), ('turn', 'uturn', 0), ('', '', 0),
])
def test_modern_maneuver_wire_values(kind, modifier, wire_value):
  assert ns.modern_maneuver(kind, modifier) == wire_value


def test_navigation_wire_schema_golden_bytes():
  # Independent literals from the proto field numbers: left turn = Maneuver.TYPE_TURN_NORMAL_LEFT (7).
  assert ns.turn_message('Main', 7, 1) == bytes.fromhex('0a044d61696e10071801')
  assert ns.notification_message('Main', 7) == bytes.fromhex('0a0c0a02080712060a044d61696e')
  assert ns.current_position_message(400, 40, False) == bytes.fromhex('0a0e0a0a0890031203302e3218051028')
  # Zero-distance display is valid; it must not disappear at the actual turn.
  assert ns.current_position_message(0, 0, False) == bytes.fromhex('0a0b0a07080012013018061000')


def test_rerouting_resends_unchanged_instruction(nav):
  stream, session, feed, now = nav
  start(stream, session, now)
  feed.value = GUIDING
  tick(stream, now)
  feed.value = {'state': 'routing'}
  tick(stream, now)
  session.sent.clear()
  feed.value = GUIDING
  tick(stream, now)
  assert [kind for _, kind, _ in session.sent] == [0x8003, 0x8004, 0x8006, 0x8005, 0x8007]


def test_honda_descriptor_uses_next_turn_image_without_dimensions():
  # Honda T20 discovery from session 000066: channel 9, interval 1000, type 2.
  options = ns.parse_navigation(bytes.fromhex('08e8071002'))
  assert options['type'] == ns.TYPE_IMAGE
  assert ns.TYPE_ENUM == 1
  session = Session(navigation=options)
  stream = ns.NavStatus.create(session, lambda *a, **k: None, feed=Feed())
  assert stream.image_size is None  # no invented size; semantic guidance is still sent


def phone_feed(monkeypatch, *, observed_age_s, speed, distance=200., known=True):
  import time
  from types import SimpleNamespace as NS
  now = time.monotonic_ns()
  instruction = NS(text='Turn right onto Main St', maneuverType='turn', maneuverModifier='right', distanceMeters=distance,
                   remainingDistanceMeters=0., remainingDurationSeconds=0.)
  nav = NS(version=2, status='guiding', source='phone:googleMaps', distanceKnown=known, instruction=instruction,
           observationMonoTime=now - int(observed_age_s * 1e9))

  class SM(dict):
    valid = {'starpilotNavigation': True, 'carState': True}
    alive = {'starpilotNavigation': True, 'carState': True}
    logMonoTime = {'starpilotNavigation': now}

    def update(self, _):
      pass

  feed = ns.NavFeed.__new__(ns.NavFeed)
  feed._sm = SM(starpilotNavigation=NS(navigation=nav), carState=NS(vEgo=speed))
  monkeypatch.setattr('openpilot.common.params.Params', lambda: NS(get_bool=lambda key: False))
  return feed


def test_phone_guidance_reaches_the_car_display_and_counts_down(monkeypatch):
  snapshot = phone_feed(monkeypatch, observed_age_s=4., speed=10.)._read_cereal()
  assert snapshot['road'] == 'Turn right onto Main St' and snapshot['type'] == 'turn'
  assert 155 < snapshot['distance'] < 165


def test_phone_guidance_without_distance_or_past_the_turn_is_not_shown(monkeypatch):
  assert phone_feed(monkeypatch, observed_age_s=1., speed=10., known=False)._read_cereal() is None
  assert phone_feed(monkeypatch, observed_age_s=20., speed=15., distance=30.)._read_cereal() is None
