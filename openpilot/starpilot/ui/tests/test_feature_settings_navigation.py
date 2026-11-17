"""Protected Settings root coordinates and guarded large child actions."""

import unittest
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS

from openpilot.common.params import Params
from openpilot.starpilot.longitudinal.profile_document import migrate_profile_document
from openpilot.starpilot.ui import feature_settings_compact as compact
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import (
  FEATURE_ROW_TOP, FEATURE_ROW_HEIGHT, feature_row_top, FeatureInput, FeatureRow, FeatureSettingsState, FeatureUiAction, row_change,
  FEATURE_PAGE_COUNTER_WIDTH, feature_page_counter_left,
  FEATURE_FOOTER_BOTTOM, feature_footer_buttons, feature_footer_top,
)
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.settings_state import Destination, SettingsInput, SettingsState, tile_rects
from opendbc.car.honda.interface import CarInterface as HondaCarInterface
from opendbc.car.honda.values import CAR as HONDA_CAR


class FeatureNavigationTests(unittest.TestCase):
  def test_page_counter_is_inert_and_footer_buttons_remain_tappable(self):
    for expanded in (True, False):
      state = FeatureSettingsState(sidebar_expanded=expanded)
      counter_left = feature_page_counter_left(state)
      actions = []
      controller = FeatureInput(actions.append)
      for x in (counter_left, counter_left + FEATURE_PAGE_COUNTER_WIDTH / 2, counter_left + FEATURE_PAGE_COUNTER_WIDTH):
        with self.subTest(expanded=expanded, x=x):
          controller.press(x, 1015, state)
          controller.release(x, 1015, state)
          self.assertFalse(actions)
          self.assertIsNone(controller.held)
      left = 520 if expanded else 20
      for x, direction in (((left + 25 + 1320) / 2, -1), (1720, 1)):
        controller.press(x, 1015, state)
        controller.release(x, 1015, state)
        self.assertEqual(actions.pop(), FeatureUiAction("scroll", direction=direction))

  def test_footer_touch_zones_cover_the_full_band_around_the_drawn_buttons(self):
    for expanded in (True, False):
      for subtitle in ("", "Panel description"):
        state = FeatureSettingsState(sidebar_expanded=expanded, subtitle=subtitle)
        left = 520 if expanded else 20
        top = feature_footer_top(state)
        counter_left = feature_page_counter_left(state)
        counter_right = counter_left + FEATURE_PAGE_COUNTER_WIDTH
        buttons = feature_footer_buttons(state)
        for direction, (bx, by, width, height) in zip((-1, 1), buttons, strict=True):
          self.assertGreaterEqual(by, top)
          self.assertLessEqual(by + height, FEATURE_FOOTER_BOTTOM)
          self.assertTrue(bx + width < counter_left if direction == -1 else bx > counter_right)
          for x in (bx, bx + width / 2, bx + width):
            for y in (by, by + height / 2, by + height):
              with self.subTest(expanded=expanded, subtitle=subtitle, x=x, y=y):
                self.assertEqual(FeatureInput.target(x, y, state), FeatureUiAction("scroll", direction=direction))
        for x, y, direction in ((left, top, -1), (counter_left - 1, FEATURE_FOOTER_BOTTOM, -1),
                                (counter_right + 1, top, 1), (2140, FEATURE_FOOTER_BOTTOM, 1)):
          with self.subTest(expanded=expanded, subtitle=subtitle, x=x, y=y):
            self.assertEqual(FeatureInput.target(x, y, state), FeatureUiAction("scroll", direction=direction))
        self.assertIsNone(FeatureInput.target(1720, FEATURE_FOOTER_BOTTOM + 1, state))
        self.assertIsNone(FeatureInput.target(1720, FEATURE_FOOTER_BOTTOM - 5, replace(state, editor=True)))
        actions = []
        controller = FeatureInput(actions.append)
        bx, by, width, height = buttons[1]
        controller.press(bx + width / 2, by + 4, state)
        controller.move(bx + width / 2 + 20, by - 26, state)
        controller.release(bx + width / 2 + 20, by - 26, state)
        self.assertEqual(actions, [FeatureUiAction("scroll", direction=1)])

  def test_drag_tracks_only_horizontal_body_movement(self):
    state = FeatureSettingsState()
    controller = FeatureInput(lambda _action: None)
    for dx, dy, expected, canceled in ((20, 0, 0, False), (-80, 0, -80, False), (80, 10, 80, False),
                                        (80, 41, 0, False), (0, 80, 0, True)):
      with self.subTest(dx=dx, dy=dy):
        controller.press(1000, 200, state)
        controller.move(1000 + dx, 200 + dy, state)
        self.assertEqual(controller.drag_x, expected)
        self.assertEqual(controller.held is None, canceled)
    controller.press(1000, 200, state)
    controller.move(920, 200, state)
    controller.move(980, 200, state)
    self.assertEqual(controller.drag_x, 0)
    controller.move(1080, 200, state)
    self.assertEqual(controller.drag_x, 80)

  def test_drag_resets_through_existing_cancel_paths(self):
    state = FeatureSettingsState()
    controller = FeatureInput(lambda _action: None)
    resets = (lambda: controller.cancel(), lambda: controller.press(1000, 200, state),
              lambda: controller.release(920, 200, state),
              lambda: controller.move(920, 80, state),
              lambda: controller.move(920, 200, replace(state, scroll=5)),
              lambda: controller.move(920, 200, replace(state, page="child")),
              lambda: controller.move(920, 200, replace(state, sidebar_expanded=False)))
    for reset in resets:
      controller.press(1000, 200, state)
      controller.move(920, 200, state)
      self.assertEqual(controller.drag_x, -80)
      reset()
      self.assertEqual(controller.drag_x, 0)

  def test_native_toyota_swap_has_separate_authority_on_both_profiles(self):
    from opendbc.car.toyota.interface import CarInterface as ToyotaInterface
    from opendbc.car.toyota.values import CAR as ToyotaCAR
    from openpilot.starpilot.controllers.toyota_cruise import ToyotaCruisePreference
    from openpilot.starpilot.ui import runtime_app

    vehicle = ToyotaInterface.get_non_essential_params(ToyotaCAR.TOYOTA_COROLLA_TSS2)
    self.assertTrue(vehicle.openpilotLongitudinalControl and vehicle.pcmCruise)
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      for profile in (Profile.LARGE, Profile.COMPACT):
        with self.subTest(profile=profile):
          session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
          session._mode, session.profile = runtime_app.ShellMode.SETTINGS, profile
          self.enterContext(patch.object(session, '_unavailable', lambda _reason: None))
          session.feature_owner = FeatureSettingsOwner(params, session._feature_authority,
            vehicle_fingerprint=lambda: vehicle.carFingerprint, vehicle_params=lambda: vehicle)
          with patch.object(runtime_app, 'ui_state', NS(CP=vehicle)):
            self.assertTrue(session._feature_authority('long'))
            self.assertTrue(session._feature_authority('toyota_cruise'))
            for expected in (True, False):
              state = session.feature_owner.snapshot('wheel', parked=True, system_long=False,
                                                      lateral_context=False, metric=False)
              rows = {row.key: row for row in state.rows}
              self.assertNotIn('CustomCruise', rows)
              self.assertNotIn('CustomCruiseLong', rows)
              self.assertTrue(rows['ReverseCruise'].available)
              request = row_change(rows['ReverseCruise'])
              assert request is not None
              self.assertTrue(session.feature_request(request))
              self.assertEqual(params.get_bool('ReverseCruise'), expected)
              self.assertEqual(ToyotaCruisePreference(vehicle, params).update(), expected)
            session._mode = runtime_app.ShellMode.ONROAD
            with patch.object(session, '_favorite_authority', return_value=False):
              self.assertFalse(session._feature_authority('toyota_cruise'))
          session._mode = runtime_app.ShellMode.SETTINGS
          other = HondaCarInterface.get_non_essential_params(HONDA_CAR.HONDA_CIVIC)
          with patch.object(runtime_app, 'ui_state', NS(CP=other)):
            self.assertFalse(session._feature_authority('toyota_cruise'))
            self.assertTrue(session._feature_authority('long'))

  def test_native_saved_preferences_need_settings_or_favorite_context_not_cp(self):
    from openpilot.starpilot.ui import runtime_app

    cp = HondaCarInterface.get_non_essential_params(HONDA_CAR.HONDA_CIVIC)
    self.assertTrue(cp.openpilotLongitudinalControl and cp.pcmCruise)
    session = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
    session._mode = runtime_app.ShellMode.SETTINGS
    with patch.object(session, "confirmed_offroad", return_value=True), patch.object(runtime_app, "ui_state", NS(CP=cp)):
      self.assertTrue(session._feature_authority("long"))
      self.assertTrue(session._feature_authority("preferences"))
      cp.openpilotLongitudinalControl = False
      self.assertFalse(session._feature_authority("long"))
      self.assertTrue(session._feature_authority("preferences"))
      cp.openpilotLongitudinalControl = True
      cp.passive = True
      self.assertFalse(session._feature_authority("long"))
      self.assertTrue(session._feature_authority("preferences"))
    with patch.object(session, "confirmed_offroad", return_value=True), patch.object(runtime_app, "ui_state", NS(CP=None)):
      self.assertTrue(session._feature_authority("preferences"))
    with patch.object(session, "confirmed_offroad", return_value=False), patch.object(runtime_app, "ui_state", NS(CP=None)):
      self.assertTrue(session._feature_authority("preferences"))
      self.assertFalse(session._feature_authority("lane"))
    session._mode = runtime_app.ShellMode.ONROAD
    with patch.object(session, "_favorite_authority", return_value=False):
      self.assertFalse(session._feature_authority("preferences"))
    with patch.object(session, "_favorite_authority", return_value=True):
      self.assertTrue(session._feature_authority("preferences"))
      self.assertFalse(session._feature_authority("parked_preferences"))

  def test_aol_save_does_not_block_navigation_or_queue_duplicate_requests(self):
    import fcntl
    from typing import Any
    import threading
    import time
    from openpilot.starpilot.ui import runtime_app
    from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsRequest
    from openpilot.starpilot import saved_document
    from openpilot.starpilot.ui.feature_settings import FeatureSettingsView
    from openpilot.starpilot.ui.presentation import BitmapFonts

    for profile, withdrawal in ((profile, withdrawal) for profile in (Profile.LARGE, Profile.COMPACT)
                                for withdrawal in ("back", "cancel", "close")):
      with self.subTest(profile=profile, withdrawal=withdrawal), tempfile.TemporaryDirectory() as directory:
        params = Params(directory)
        params.put_bool("AlwaysOnLateral", True, block=True)
        session: Any = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
        session.profile = profile
        session._mode = runtime_app.ShellMode.SETTINGS
        session.selected = Destination.DRIVING_CONTROLS
        session.feature_page, session.feature_root_page = "aol", "hub"
        session.feature_scroll = 0
        session.input = NS(cancel=lambda: None)
        session.favorites = NS(cancel=lambda: None)
        fonts = BitmapFonts.__new__(BitmapFonts)
        fonts.profile = profile
        session.view = NS(features=FeatureSettingsView(fonts) if profile == Profile.LARGE else None,
                          onroad=NS(navigation=NS(cancel=lambda: None)), close=lambda: None)
        session.drive_state = NS(physical=NS(close=lambda: None))
        session.galaxy_flow = session.pip_warning = session.pip_renderer = session.fonts = NS(close=lambda: None)
        session.bluetooth_source = session.model_source = session.map_source = None
        session.feature_owner = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: None)
        request = FeatureSettingsRequest("AlwaysOnLateral", b"1", "Off")
        entered = threading.Event()
        acquire = saved_document.acquire_native_lock

        def blocked(fd, entered=entered, acquire=acquire):
          entered.set()
          acquire(fd)
        root = Path(params.get_param_path("AlwaysOnLateral")).parent.parent
        with (root / ".lock").open("a") as lock, patch.object(session, "_feature_authority", return_value=True), \
             patch.object(session, "feature_snapshot", return_value=FeatureSettingsState(page="aol")), \
             patch.object(saved_document, "acquire_native_lock", blocked):
          fcntl.flock(lock, fcntl.LOCK_EX)
          started = time.monotonic()
          self.assertFalse(session.feature_request(request))
          self.assertLess(time.monotonic() - started, .2)
          pending = session._aol_save
          self.assertTrue(entered.wait(1.))
          self.assertEqual(Path(params.get_param_path("AlwaysOnLateral")).read_bytes(), b"1")
          self.assertIn("Saving", session.notice)
          self.assertFalse(session.feature_request(request))
          self.assertIs(session._aol_save, pending)
          if withdrawal == "back":
            session._feature_ui(FeatureUiAction("back"))
            self.assertEqual(session.feature_page, "hub")
          else:
            getattr(session, withdrawal)()
            self.assertTrue(pending.cancel.is_set())
          fcntl.flock(lock, fcntl.LOCK_UN)
          self.assertTrue(pending.done.wait(1.))
          session._poll_aol_save()
          self.assertEqual(Path(params.get_param_path("AlwaysOnLateral")).read_bytes(), b"1")
          self.assertIn("not saved", session.notice)
          if withdrawal == "close":
            self.assertFalse(session.feature_request(request))
            self.assertIsNone(session._aol_save)
            continue
          session.feature_page = "aol"
          self.assertFalse(session.feature_request(request))
          pending = session._aol_save
          self.assertTrue(pending.done.wait(1.))
          session._poll_aol_save()
          self.assertEqual(Path(params.get_param_path("AlwaysOnLateral")).read_bytes(), b"0")
          self.assertEqual(session.notice, "AOL preference saved")
          self.assertIsNone(session._aol_save)

  def test_aol_enable_worker_rechecks_live_vehicle_after_native_lock(self):
    import fcntl
    import threading
    from typing import Any
    from openpilot.starpilot import saved_document
    from openpilot.starpilot.ui import runtime_app
    from opendbc.car import gen_empty_fingerprint
    from opendbc.car.gm.interface import CarInterface
    from opendbc.car.gm.values import CAR

    fingerprint = gen_empty_fingerprint()
    fingerprint[0][0x201] = 6
    fingerprint[2][0x320] = 8
    fingerprint[2][0x180] = 4
    cp = CarInterface.get_params(CAR.CHEVROLET_BOLT_ACC_2022_2023_PEDAL, fingerprint, [], True, False, False)
    current = [cp]
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      params.put_bool("AlwaysOnLateral", False, block=True)
      session: Any = runtime_app.StarShellSession.__new__(runtime_app.StarShellSession)
      session._mode = runtime_app.ShellMode.SETTINGS
      session.selected = Destination.DRIVING_CONTROLS
      session.feature_page = "aol"
      session.feature_owner = FeatureSettingsOwner(params, lambda _: True, vehicle_fingerprint=lambda: cp.carFingerprint,
                                                   vehicle_params=lambda: current[0])
      def request():
        state = session.feature_owner.snapshot("aol", parked=True, system_long=True, lateral_context=True, metric=False)
        row = next(row for row in state.rows if row.key == "AlwaysOnLateral")
        self.assertTrue(row.available)
        return row_change(row)
      with patch.object(session, "_feature_authority", return_value=True):
        self.assertFalse(session.feature_request(request()))
        self.assertTrue(session._aol_save.done.wait(1.))
        session._poll_aol_save()
        self.assertTrue(params.get_bool("AlwaysOnLateral"))
        self.assertEqual(session.notice, "AOL preference saved")
        params.put_bool("AlwaysOnLateral", False, block=True)
        entered = threading.Event()
        acquire = saved_document.acquire_native_lock
        def blocked(fd):
          entered.set()
          acquire(fd)
        root = Path(params.get_param_path("AlwaysOnLateral")).parent.parent
        with (root / ".lock").open("a") as lock, patch.object(saved_document, "acquire_native_lock", blocked):
          fcntl.flock(lock, fcntl.LOCK_EX)
          self.assertFalse(session.feature_request(request()))
          pending = session._aol_save
          self.assertTrue(entered.wait(1.))
          current[0] = None
          fcntl.flock(lock, fcntl.LOCK_UN)
          self.assertTrue(pending.done.wait(1.))
          session._poll_aol_save()
          self.assertFalse(params.get_bool("AlwaysOnLateral"))
          self.assertIn("not saved", session.notice)

  def test_large_root_tile_geometry_and_destination(self):
    state = SettingsState()
    self.assertEqual(tile_rects(state)[2], (1611, 112, 529, 461))
    actions = []
    controller = SettingsInput(Profile.LARGE, actions.append)
    controller.press(1800, 300, state)
    controller.release(1800, 300, state)
    self.assertEqual(actions[0].destination.destination, Destination.DRIVING_CONTROLS)

  def test_press_release_cancels_on_drag_or_changed_source(self):
    actions = []
    controller = FeatureInput(actions.append)
    source = FeatureRow("LaneCentering", "Enable Lane Centering", "Off", b"0", ("Off", "On"), available=True)
    state = FeatureSettingsState(page="lane", rows=(source,))
    controller.press(1990, feature_row_top(state) + 92, state)
    controller.move(1800, feature_row_top(state) + 92, state)
    controller.release(1800, feature_row_top(state) + 92, state)
    self.assertFalse(actions)
    controller.press(1990, feature_row_top(state) + 92, state)
    changed = FeatureSettingsState(page="lane", rows=(FeatureRow("LaneCentering", "Enable Lane Centering", "On", b"1", ("Off", "On"), available=True),))
    controller.release(1990, feature_row_top(state) + 92, changed)
    self.assertFalse(actions)
    controller.press(1990, feature_row_top(state) + 92, state)
    controller.release(1990, feature_row_top(state) + 92, state)
    self.assertEqual(row_change(actions[0].row).expected, b"0")

  def test_swipes_page_from_controls_disabled_rows_and_blank_space(self):
    controls = (FeatureRow("switch", "Switch", "Off", choices=("Off", "On"), available=True),
                FeatureRow("number", "Number", "50", step=5, available=True),
                FeatureRow("", "Child", "", page="child", available=True),
                FeatureRow("pip:reset", "Reset", "", available=True),
                FeatureRow("action", "Action", "", actions=(("OPEN", True),), available=True),
                FeatureRow("disabled", "Disabled", "", actions=(("OPEN", False),)))
    for expanded in (True, False):
      for subtitle in ("", "Description"):
        for row in controls:
          for scroll, start, end, direction in ((0, 2000, 1700, 1), (5, 1800, 2100, -1)):
            with self.subTest(expanded=expanded, subtitle=subtitle, row=row.key, direction=direction):
              state = FeatureSettingsState(rows=(row,) * 7, scroll=scroll, sidebar_expanded=expanded, subtitle=subtitle)
              actions = []
              controller = FeatureInput(actions.append)
              y = feature_row_top(state) + 102
              controller.press(start, y, state)
              controller.move(end, y, state)
              self.assertFalse(actions)
              controller.release(end, y, state)
              controller.release(end, y, state)
              self.assertEqual(actions, [FeatureUiAction("scroll", direction=direction)])
        state = FeatureSettingsState(rows=(controls[0],) * 7, scroll=5, sidebar_expanded=expanded, subtitle=subtitle)
        actions = []
        controller = FeatureInput(actions.append)
        y = feature_row_top(state) + 3.5 * FEATURE_ROW_HEIGHT
        controller.press(1500, y, state)
        controller.release(1800, y, state)
        self.assertEqual(actions, [FeatureUiAction("scroll", direction=-1)])

  def test_swipe_rejections_never_restore_a_canceled_tap(self):
    row = FeatureRow("", "Child", "", page="child", available=True)
    for expanded in (True, False):
      state = FeatureSettingsState(rows=(row,) * 7, sidebar_expanded=expanded)
      cases = (((1500, 200), [(1619, 200)]),  # short drag
               ((1500, 200), [(1620, 261)]),  # diagonal
               ((1500, 200), [(1500, 280), (1800, 280)]),  # vertical first
               ((1500, 200), [(1800, 200), (1500, 200)]),  # return to origin
               ((1500, 200), [(1500, 80), (1800, 200)]),  # leave vertically and reenter
               ((700, 50), [(1000, 50)]),  # header
               ((1000, 1015), [(1300, 1015)]),  # footer
               ((30, 580), [(330, 580)]),  # collapse handle
               ((200, 200), [(1000, 200)]) if expanded else ((10, 200), [(1000, 200)]))
      for start, moves in cases:
        with self.subTest(expanded=expanded, start=start, moves=moves):
          actions = []
          controller = FeatureInput(actions.append)
          controller.press(*start, state)
          for position in moves:
            controller.move(*position, state)
          controller.release(*moves[-1], state)
          self.assertFalse(actions)
      for scroll, count, end in ((0, 7, 1800), (5, 7, 1200), (0, 5, 1200)):
        actions = []
        controller = FeatureInput(actions.append)
        boundary = replace(state, scroll=scroll, rows=(row,) * count)
        controller.press(1500, 200, boundary)
        controller.release(end, 200, boundary)
        self.assertFalse(actions)

  def test_canceled_footer_tap_cannot_become_a_body_swipe(self):
    for expanded in (True, False):
      with self.subTest(expanded=expanded):
        state = FeatureSettingsState(rows=(FeatureRow("row", "Row", ""),) * 7, scroll=5, sidebar_expanded=expanded)
        actions = []
        controller = FeatureInput(actions.append)
        controller.press(900, 1015, state)
        self.assertIsNotNone(controller.held)
        controller.cancel_tap()
        controller.release(1800, 800, state)
        self.assertFalse(actions)
        self.assertIsNone(controller.held)

  def test_flicks_use_recent_movement_and_reject_noise_pauses_and_reversals(self):
    row = FeatureRow("", "Child", "", page="child", available=True)
    cases = (
      ("fast", ((0, 0), (.02, 22), (.05, 55), (.075, 80)), (.08, 80), True),
      ("hold then flick", ((0, 0), (.5, 0), (.52, 24), (.55, 58), (.575, 80)), (.58, 80), True),
      ("long slow drag", ((0, 0), (.15, 40), (.3, 90), (.5, 125)), (.51, 125), True),
      ("short slow drag", ((0, 0), (.15, 25), (.3, 55), (.5, 80)), (.51, 80), False),
      ("below flick speed", ((0, 0), (.04, 25), (.08, 55), (.12, 80)), (.13, 80), False),
      ("too short", ((0, 0), (.02, 25), (.04, 45)), (.05, 45), False),
      ("pause before lift", ((0, 0), (.02, 22), (.05, 55), (.075, 80)), (.18, 80), False),
      ("lift position spike", ((0, 0), (.02, 22), (.05, 55)), (.06, 80), False),
      ("single movement spike", ((0, 0), (.05, 80)), (.06, 80), False),
      ("reversal", ((0, 0), (.02, 40), (.05, 110), (.075, 70)), (.08, 70), False),
      ("duplicate timestamps", ((0, 0), (0, 22), (0, 55), (0, 80)), (.01, 80), False),
      ("out of order timestamps", ((0, 0), (.02, 22), (.01, 55), (.04, 80)), (.05, 80), False),
    )
    for scroll, start, direction in ((0, 2000, 1), (5, 1800, -1), (5, 519, -1)):
      state = FeatureSettingsState(rows=(row,) * 13, scroll=scroll)
      for name, samples, release, accepted in cases:
        with self.subTest(direction=direction, gesture=name):
          actions = []
          controller = FeatureInput(actions.append)
          controller.press(start, 200, state)
          for timestamp, distance in samples:
            controller.move(start - direction * distance, 200, state, now=timestamp)
          self.assertFalse(actions)
          timestamp, distance = release
          controller.release(start - direction * distance, 200, state, now=timestamp)
          controller.release(start - direction * distance, 200, state, now=timestamp)
          self.assertEqual(actions, [FeatureUiAction("scroll", direction=direction)] if accepted else [])

  def test_sidebar_swipes_require_horizontal_travel_and_a_release_in_the_body(self):
    row = FeatureRow("action", "Action", "", actions=(("OPEN", True),), available=True)
    for subtitle in ("", "Description"):
      state = FeatureSettingsState(rows=(row,) * 7, scroll=5, subtitle=subtitle)
      y = feature_row_top(state) + 80
      cases = (((400, y), ((900, y),), True),
               ((519, y), ((638, y),), False),  # below distance threshold
               ((519, y), ((639, y),), True),
               ((100, y), ((500, y),), False),  # remains in sidebar
               ((400, y), ((900, y), (400, y)), False),
               ((400, y), ((2145, y),), False),
               ((400, y), ((900, y + 250),), True),  # horizontal ratio threshold
               ((400, y), ((900, y + 251),), False),
               ((400, y), ((400, y + 80), (900, y + 80)), False),
               ((400, y), ((400, 80), (900, y)), False),
               ((400, 50), ((900, y),), False),
               ((400, 1015), ((900, y),), False),
               ((-1, y), ((900, y),), False))
      for start, moves, accepted in cases:
        with self.subTest(subtitle=subtitle, start=start, moves=moves):
          actions = []
          controller = FeatureInput(actions.append)
          controller.press(*start, state)
          for position in moves:
            controller.move(*position, state)
          self.assertFalse(actions)
          controller.release(*moves[-1], state)
          controller.release(*moves[-1], state)
          self.assertEqual(actions, [FeatureUiAction("scroll", direction=-1)] if accepted else [])
          self.assertIsNone(controller.held)
      for changed in (replace(state, page="child"), replace(state, scroll=0), replace(state, sidebar_expanded=False)):
        actions = []
        controller = FeatureInput(actions.append)
        controller.press(400, y, state)
        controller.move(900, y, changed)
        controller.release(900, y, state)
        self.assertFalse(actions)

  def test_flick_history_does_not_leak_into_a_new_press_or_canceled_touch(self):
    state = FeatureSettingsState(rows=(FeatureRow("", "Child", "", page="child", available=True),) * 7)
    for reset in (lambda controller: controller.cancel(), lambda controller: controller.press(2000, 200, state)):
      actions = []
      controller = FeatureInput(actions.append)
      controller.press(2000, 200, state)
      for timestamp, x in ((0, 2000), (.03, 1960), (.06, 1920)):
        controller.move(x, 200, state, now=timestamp)
      reset(controller)
      controller.release(1920, 200, state, now=.07)
      self.assertFalse(actions)

  def test_swipe_context_changes_cancel_but_row_value_refresh_does_not(self):
    row = FeatureRow("switch", "Switch", "Off", b"0", ("Off", "On"), available=True)
    state = FeatureSettingsState(rows=(row,) * 7)
    for changed in (replace(state, page="child"), replace(state, scroll=5), replace(state, sidebar_expanded=False)):
      actions = []
      controller = FeatureInput(actions.append)
      controller.press(2000, 204, state)
      controller.move(1850, 204, changed)
      controller.release(1700, 204, state)
      self.assertFalse(actions)
    actions = []
    controller = FeatureInput(actions.append)
    changed = replace(state, rows=(replace(row, value="On", source=b"1"),) * 7)
    controller.press(2000, 204, state)
    controller.move(1850, 204, changed)
    controller.release(1700, 204, changed)
    self.assertEqual(actions, [FeatureUiAction("scroll")])
    actions.clear()
    controller.press(2000, 204, state)
    controller.cancel()
    controller.release(1700, 204, state)
    self.assertFalse(actions)

  def test_swipe_clears_touch_before_dispatch(self):
    state = FeatureSettingsState(rows=(FeatureRow("", "Child", "", page="child", available=True),) * 7)
    actions = []
    def emit(action):
      self.assertIsNone(controller.held)
      actions.append(action)
    controller = FeatureInput(emit)
    controller.press(1500, 200, state)
    controller.release(1380, 260, state)
    self.assertEqual(actions, [FeatureUiAction("scroll")])

  def test_boolean_both_action_halves_preserve_requests_and_source_evidence(self):
    for value, source, desired in (("Off", b"0", "On"), ("On", b"1", "Off")):
      for x in (1800, 1990):
        with self.subTest(value=value, x=x):
          row = FeatureRow("LaneCentering", "Switch", value,
                           source, ("Off", "On"), available=True, dependencies=(("master", b"1"),))
          state = FeatureSettingsState(page="lane", rows=(row,))
          actions = []
          controller = FeatureInput(actions.append)
          controller.press(x, feature_row_top(state) + 92, state)
          controller.release(x, feature_row_top(state) + 92, state)
          self.assertEqual(len(actions), 1)
          request = row_change(actions[0].row, actions[0].direction)
          self.assertIsNotNone(request)
          self.assertEqual((request.value, request.expected, request.dependencies), (desired, source, row.dependencies))
          actions.clear()
          unavailable = replace(row, available=False)
          state = replace(state, rows=(unavailable,))
          controller.press(x, feature_row_top(state) + 92, state)
          controller.release(x, feature_row_top(state) + 92, state)
          self.assertFalse(actions)
          self.assertIsNone(row_change(unavailable))

  def test_curve_page_uses_shared_large_input_rows(self):
    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      owner = FeatureSettingsOwner(params, lambda group: group == "long", vehicle_fingerprint=lambda: "TOYOTA COROLLA TSS2")
      hub = owner.snapshot("hub", parked=True, system_long=True, lateral_context=True, metric=False)
      self.assertIn("curve", [row.page for row in hub.rows])
      page = owner.snapshot("curve", parked=True, system_long=True, lateral_context=True, metric=False)
      master = page.rows[0]
      self.assertEqual(master.key, "CurveSpeedController")
      request = row_change(master)
      self.assertIsNotNone(request)
      if request is None:
        self.fail("the curve master has no action")
      self.assertTrue(owner.apply(request))
      self.assertTrue(params.get_bool("CurveSpeedController"))

  def test_compact_entry_opens_child_scroller(self):
    class Button:
      def __init__(self, text, value):
        self.text, self.value, self.click = text, value, None

      def set_click_callback(self, callback):
        self.click = callback

    class Scroller:
      def __init__(self):
        self.cards = []
        self.items = self.cards
        self._scroller = self

      def add_widgets(self, cards):
        self.cards.extend(cards)

    class Session:
      def feature_snapshot(self, page):
        return FeatureSettingsState(page=page, title="Driving Controls",
                                    rows=(FeatureRow("", "Speed Limit Controller", "Saved settings", page="slc", available=True),))
      def feature_request(self, request):
        return False

    pushed = []
    with patch.object(compact, "BigButton", Button), patch.object(compact, "GreyBigButton", Button), \
         patch.object(compact, "NavScroller", Scroller), patch.object(compact.gui_app, "push_widget", pushed.append):
      entry = compact.FeatureSettingsCompact(Session()).entry_button()
      self.assertEqual(entry.text, "driving controls")
      entry.click()
    self.assertEqual([card.text for card in pushed[0].cards], ["driving controls", "speed limit controller"])

  def test_compact_dependent_cards_and_confirmed_reset(self):
    class Button:
      def __init__(self, text, value):
        self.text, self.value, self.click = text, value, None

      def set_click_callback(self, callback):
        self.click = callback

      def set_enabled(self, enabled):
        self.enabled = enabled

    class ReadButton(Button):
      pass

    class Scroller:
      def __init__(self):
        self.items = []
        self._scroller = self

      def add_widgets(self, cards):
        self.items.extend(cards)

    class Dialog:
      def __init__(self, title, icon, confirm_callback, **kwargs):
        self.confirm = confirm_callback

    class Session:
      def __init__(self, owner):
        self.owner = owner

      def feature_snapshot(self, page):
        return self.owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False)

      def feature_request(self, request):
        return self.owner.apply(request)

    def card(page, text):
      return next(item for item in page.items if item.text == text)

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      cp = HondaCarInterface.get_non_essential_params("HONDA_CIVIC_BOSCH")
      cp.openpilotLongitudinalControl = True
      cp.pcmCruise = False
      cp.carVin = "TEST"
      owner = FeatureSettingsOwner(params, lambda group: True, vehicle_fingerprint=lambda: "TOYOTA COROLLA TSS2",
                                   vehicle_params=lambda: cp)
      adapter = compact.FeatureSettingsCompact(Session(owner))
      pushed = []
      with patch.object(compact, "BigButton", Button), patch.object(compact, "GreyBigButton", ReadButton), \
           patch.object(compact, "NavScroller", Scroller), patch.object(compact, "BigConfirmationDialog", Dialog), \
           patch.object(compact.gui_app, "push_widget", pushed.append), patch.object(compact.gui_app, "texture", lambda *args: None):
        adapter.open("slc")
        slc = pushed[-1]
        self.assertFalse(any(item.text in ('confirm lower limits', 'confirm higher limits') for item in slc.items))
        adoption = card(slc, "confirm limits")
        self.assertFalse(params.get('SLCConfirmation'))
        adoption.click()
        self.assertTrue(params.get('SLCConfirmation'))
        self.assertFalse(any(item.text in ('confirm lower limits', 'confirm higher limits') for item in slc.items))

        adapter.open("aggressive/acceleration")
        curve = pushed[-1]
        for _ in range(6):
          card(curve, "preset").click()
        self.assertEqual(len([item for item in curve.items if item.text.endswith("mph point +")]), 10)

        params.put_bool("CustomPersonalities", True, block=True)
        path = Path(params.get_param_path("LongitudinalPersonalityProfiles"))
        path.write_bytes(b"{invalid")
        adapter.open("profiles")
        profiles = pushed[-1]
        card(profiles, "reset invalid profiles").click()
        pushed[-1].confirm()
        self.assertFalse(params.get_bool("CustomPersonalities"))
        self.assertIsNotNone(migrate_profile_document(path.read_bytes()))
        self.assertFalse(any(item.text == "reset invalid profiles" for item in profiles.items))

  def test_large_hub_back_returns_to_settings_root(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.starpilot.ui.shell import ShellMode
    session = StarShellSession.__new__(StarShellSession)
    session._mode = ShellMode.SETTINGS
    session.selected = Destination.DRIVING_CONTROLS
    session.feature_page = "hub"
    session.feature_scroll = 0
    self.enterContext(patch.object(session, "feature_snapshot", lambda: FeatureSettingsState()))
    self.enterContext(patch.object(session, "input", type("Input", (), {"cancel": lambda self: None})(), create=True))
    session._feature_ui(FeatureUiAction("back"))
    self.assertEqual(session.selected, Destination.STAR)

  def test_curve_reset_confirmation_on_both_native_pages(self):
    from openpilot.starpilot.ui.runtime_app import StarShellSession
    from openpilot.system.ui.widgets import DialogResult

    class Dialog:
      def __init__(self, _question, *_args, callback=None, **_kwargs):
        self.confirm = callback or _args[-1]

    with tempfile.TemporaryDirectory() as directory:
      params = Params(directory)
      legacy = Path(params.get_param_path("CurvatureData"))
      legacy.write_bytes(b'{"0.001":{"average":2.0,"count":2}}')
      owner = FeatureSettingsOwner(params, lambda _group: True, vehicle_fingerprint=lambda: "TOYOTA COROLLA TSS2")
      row = next(row for row in owner.snapshot("curve", parked=True, system_long=True,
                     lateral_context=True, metric=False).rows if row.key == "curve_reset")
      self.assertEqual(FeatureInput.target(1900, FEATURE_ROW_TOP + 77, FeatureSettingsState(page="curve", rows=(row,))).kind, "reset")
      large = StarShellSession.__new__(StarShellSession)
      self.enterContext(patch.object(large, "feature_request", owner.apply))
      pushed = []
      with patch("openpilot.system.ui.widgets.confirm_dialog.ConfirmDialog", Dialog), \
           patch("openpilot.starpilot.ui.runtime_app.gui_app.push_widget", pushed.append):
        large._confirm_feature_reset(row)
        self.assertIsNone(params.get("CurveComfortData"))
        pushed[-1].confirm(DialogResult.CONFIRM)
      self.assertIsNotNone(params.get("CurveComfortData"))

      params.remove("CurveComfortData")
      row = next(row for row in owner.snapshot("curve", parked=True, system_long=True,
                     lateral_context=True, metric=False).rows if row.key == "curve_reset")
      class Session:
        def feature_snapshot(self, page):
          return owner.snapshot(page, parked=True, system_long=True, lateral_context=True, metric=False)
        def feature_request(self, request):
          return owner.apply(request)
      adapter = compact.FeatureSettingsCompact(Session())
      pushed = []
      with patch.object(compact, "BigConfirmationDialog", Dialog), \
           patch.object(compact.gui_app, "push_widget", pushed.append), \
           patch.object(compact.gui_app, "texture", lambda *_args: None):
        adapter._confirm_reset(row, lambda: None)
        legacy.write_bytes(b'{"0.001":{"average":2.0,"count":2}}')
        pushed[-1].confirm()
        self.assertEqual(params.get("CurveComfortData"), {"version": 1, "buckets": {}})
        row = next(row for row in owner.snapshot("curve", parked=True, system_long=True,
                       lateral_context=True, metric=False).rows if row.key == "curve_reset")
        adapter._confirm_reset(row, lambda: None)
        pushed[-1].confirm()
      self.assertIsNotNone(params.get("CurveComfortData"))


if __name__ == "__main__":
  unittest.main()
