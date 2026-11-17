import ast
from collections import deque
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import NamedTuple
import unittest


class MousePos(NamedTuple):
  x: float
  y: float


class MouseEvent(NamedTuple):
  pos: tuple
  slot: int
  left_pressed: bool
  left_released: bool
  left_down: bool
  t: float


def source_methods(path, name, methods, namespace):
  tree = ast.parse(path.read_text())
  original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
  original.body = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in methods]
  original.bases = []
  original.decorator_list = []
  module = ast.Module(body=[original], type_ignores=[])
  exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
  return namespace[name]


ROOT = Path(__file__).resolve().parents[1]
MouseState = source_methods(ROOT / 'lib/application.py', 'MouseState', {'_append_mouse_event', '_handle_mouse_event'},
                            {'MouseEvent': MouseEvent, 'MousePos': MousePos, 'MAX_TOUCH_SLOTS': 2,
                             'time': SimpleNamespace(monotonic=lambda: 1.0)})
Widget = source_methods(ROOT / 'widgets/__init__.py', 'Widget', {'_process_mouse_events'},
                        {'gui_app': SimpleNamespace(mouse_events=[]),
                         'rl': SimpleNamespace(Vector2=lambda x, y: (x, y), check_collision_point_rec=lambda pos, rect: True)})


class TestMouseStateEdges(unittest.TestCase):
  def state(self):
    state = MouseState()
    state._events = deque(maxlen=140)
    state._prev_mouse_event = [None, None]
    state._lock = Lock()
    state._scale = 1.0
    return state

  def dispatch(self, events):
    widget = Widget()
    widget._hit_rect = None
    widget._touch_valid = lambda: True
    widget._multi_touch = False
    widget._press_started = [None, None]
    widget._Widget__is_pressed = [False, False]
    widget._Widget__tracking_is_pressed = [False, False]
    widget._long_press_callback = None
    calls = []
    widget._handle_mouse_press = lambda pos: calls.append('press')
    widget._handle_mouse_release = lambda pos: calls.append('release')
    widget._handle_mouse_event = lambda ev: None
    Widget._process_mouse_events.__globals__['gui_app'].mouse_events = list(events)
    widget._process_mouse_events()
    return calls

  def test_coalesced_edges_deliver_complete_widget_click(self):
    state = self.state()
    state._append_mouse_event(MouseEvent(MousePos(120, 300), 0, True, True, False, 1.0))
    self.assertEqual([(ev.left_pressed, ev.left_released, ev.left_down) for ev in state._events],
                     [(True, False, True), (False, True, False)])
    self.assertEqual([ev.t for ev in state._events], [1.0, 1.0])
    self.assertEqual(self.dispatch(state._events), ['press', 'release'])

  def test_regular_press_move_release_remain_unchanged(self):
    state = self.state()
    events = [MouseEvent(MousePos(120, 300), 0, True, False, True, 1.0),
              MouseEvent(MousePos(122, 300), 0, False, False, True, 1.1),
              MouseEvent(MousePos(122, 300), 0, False, True, False, 1.2)]
    for event in events:
      state._append_mouse_event(event)
    self.assertEqual(list(state._events), events)
    self.assertEqual(self.dispatch(state._events), ['press', 'release'])

  def test_unchanged_samples_do_not_duplicate_events(self):
    state = self.state()
    event = MouseEvent(MousePos(120, 300), 0, False, False, True, 1.0)
    state._append_mouse_event(event)
    state._append_mouse_event(event._replace(t=1.1))
    self.assertEqual(list(state._events), [event])

  def test_secondary_slot_split_preserves_slot_and_position(self):
    state = self.state()
    state._append_mouse_event(MouseEvent(MousePos(180, 320), 1, True, True, False, 2.0))
    self.assertEqual([(ev.pos, ev.slot) for ev in state._events], [((180, 320), 1), ((180, 320), 1)])

  def test_raylib_sampler_coalesced_flags_reach_widget_as_click(self):
    state = self.state()
    MouseState._handle_mouse_event.__globals__['rl'] = SimpleNamespace(
      get_touch_position=lambda slot: SimpleNamespace(x=120, y=300),
      is_mouse_button_pressed=lambda slot: slot == 0,
      is_mouse_button_released=lambda slot: slot == 0,
      is_mouse_button_down=lambda slot: False,
    )
    state._handle_mouse_event()
    self.assertEqual(self.dispatch(state._events), ['press', 'release'])


if __name__ == '__main__':
  unittest.main()
