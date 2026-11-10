#!/usr/bin/env python3
import os
import sys
import time

from openpilot.cereal import messaging
from openpilot.common.hardware import COMMA_HARDWARE, HARDWARE
from openpilot.common.realtime import Priority, config_realtime_process, drop_realtime, set_core_affinity
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.main import MainLayout
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.ui_state import device, ui_state
from openpilot.starpilot.ui.presentation import Profile
from openpilot.starpilot.ui.startup import select_ui
from openpilot.starpilot.ui.wheel_input import poll_screen_off_wheel

BIG_UI = gui_app.big_ui()


def update_frame(preview=None, controllers=None, star=None, game_mode=None, arabesque=None):
  gui_app.measure_frame_phase("ui_state", ui_state.update)
  if arabesque is not None:
    arabesque()
  if star is not None:
    # Android Auto bookmarks must be consumed even when the comma screen is off
    # or another widget covers the StarPilot page.
    star._poll_projection_bookmark(time.monotonic_ns())
    star._poll_projection_slc(time.monotonic_ns())
    poll_screen_off_wheel(star, device.awake, time.monotonic_ns())
  if game_mode is not None:
    game_mode()
  if controllers is not None:
    gui_app.measure_frame_phase("controllers", controllers.poll)
  if preview is not None:
    preview.poll()


def main():
  device_type = HARDWARE.get_device_type() if COMMA_HARDWARE else ""
  camera_core_ui = device_type in ("mici", "tici", "tizi")
  background_ui = device_type == "mici"
  cores = {0, 1, 2, 7} if background_ui else {6} if camera_core_ui else {5}
  display_nice = 5 if background_ui else 0
  main_thread_configured = False
  if background_ui:
    drop_realtime()
    os.setpriority(os.PRIO_PROCESS, 0, display_nice)
    set_core_affinity(list(cores))
  else:
    config_realtime_process(0, Priority.UI)

  selection = select_ui(Profile.LARGE if BIG_UI else Profile.COMPACT, os.environ)
  print(f"UI: {'StarPilot' if selection.custom else 'upstream'} selected ({selection.reason})", file=sys.stderr, flush=True)
  star_ui = selection.custom
  gui_app.init_window("UI")
  if star_ui:
    from openpilot.starpilot.ui.runtime_app import StarMainLayout, StarMiciMainLayout
    from openpilot.starpilot.ui.layout_preview_runtime import LayoutPreviewRuntime
    layout = StarMainLayout() if BIG_UI else StarMiciMainLayout()
    from openpilot.starpilot.controllers.runtime import ControllerRuntime
    from openpilot.starpilot.ui.spacepilot.native import SpacePilotPage, poll_game_mode
    def game_input():
      page = gui_app.get_active_widget()
      return page.capture_controller if isinstance(page, SpacePilotPage) else None
    from openpilot.starpilot.galaxy.settings import LiveContextSource
    # Both runtimes read the UI collector; neither advances or closes its sockets.
    controller_authority = LiveContextSource(ui_state.params, messages=ui_state.sm,
                                             borrowed_messages=True, evidence_wait_ms=0)
    preview_authority = LiveContextSource(ui_state.params, messages=ui_state.sm,
                                          borrowed_messages=True, evidence_wait_ms=0)
    controllers = None if ui_state.replay_clock is not None else ControllerRuntime(ui_state.params, actions=layout.star._favorite_actions,
                                    favorites=layout.star.favorites_owner.snapshot,
                                    invoke_favorite=layout.star.favorites_owner.invoke, authority=controller_authority, input_capture=game_input)
    preview = None if ui_state.replay_clock is not None else LayoutPreviewRuntime(ui_state.params, (Profile.LARGE if BIG_UI else Profile.COMPACT).value,
                                   offroad_hint=ui_state.is_offroad, authority=preview_authority)
  else:
    poll_game_mode = None
    layout = MainLayout() if BIG_UI else MiciMainLayout()
    preview = None
    controllers = None

  pm = messaging.PubMaster(['uiDebug'])
  star = layout.star if star_ui else None
  try:
    poll_arabesque = layout.poll_arabesque if BIG_UI else None
    for should_render, frame_time, cpu_time in gui_app.render(before_frame=lambda: update_frame(preview, controllers, star, poll_game_mode, poll_arabesque)):
      if should_render:
        if camera_core_ui and not main_thread_configured:
          # Display work stays below real-time driving and audio playback.
          drop_realtime()
          os.setpriority(os.PRIO_PROCESS, 0, display_nice)
          if os.sched_getscheduler(0) != os.SCHED_OTHER or os.getpriority(os.PRIO_PROCESS, 0) != display_nice:
            raise RuntimeError("Could not configure display scheduling")
          main_thread_configured = True
        # reaffine after power save offlines our core
        if COMMA_HARDWARE and os.sched_getaffinity(0) != cores:
          try:
            set_core_affinity(list(cores))
          except OSError:
            pass

        msg = messaging.new_message('uiDebug')
        msg.uiDebug.cpuTimeMillis = cpu_time * 1000
        msg.uiDebug.frameTimeMillis = frame_time * 1000
        pm.send('uiDebug', msg)
  finally:
    if star_ui:
      try:
        if preview is not None:
          preview.close()
      finally:
        try:
          if controllers is not None:
            controllers.close()
        finally:
          layout.close()
    elif BIG_UI:
      layout.close()


if __name__ == "__main__":
  main()
