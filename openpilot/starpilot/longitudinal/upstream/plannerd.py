#!/usr/bin/env python3
import os
import time
from collections import deque

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.plannerd import current_manual_event, current_ui_manual_event
from openpilot.starpilot.conditional_mode.planner_host import ConditionalPlannerHost
from openpilot.starpilot.conditional_mode.projection import paired_clocks_ns
from openpilot.starpilot.feature_runtime import enabled as feature_enabled
from openpilot.starpilot.feature_runtime import slc_transport_capable
from openpilot.starpilot.longitudinal.upstream.planner import LongitudinalPlanner
from openpilot.starpilot.speed_limits.planner_host import (
  SLC_ACTIONS, SLC_CRUISE_EVENTS, SavedSlcLifecycle, action_from_message, cruise_event_from_message, receive_slc_events,
)
import openpilot.cereal.messaging as messaging


def main():
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  ldw = LaneDepartureWarning()
  longitudinal_planner = LongitudinalPlanner(CP)
  replay = 'REPLAY' in os.environ
  conditional = ConditionalPlannerHost(params) if feature_enabled(params, CP, 'conditional', os.environ) else None
  slc = SavedSlcLifecycle(params, CP, os.environ) if slc_transport_capable(CP, os.environ) else None
  native_inputs = ['carControl', 'carState', 'controlsState', 'vehicleParameters', 'radarState', 'modelV2', 'selfdriveState']
  optional = ['deviceState'] if slc is not None or conditional is not None else []
  if slc is not None:
    slc.refresh(0)
    optional.extend(slc.source_services())
  if conditional is not None:
    optional.extend(['longitudinalPlan', 'starpilotRadarState', 'starpilotNavigation'])
  pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance'] + (['slcState'] if optional else []))
  sm = messaging.SubMaster(native_inputs + optional, poll='modelV2',
                           ignore_alive=optional, ignore_valid=optional, ignore_avg_freq=optional)
  cruise_sock = messaging.sub_sock('slcCruiseEvent', conflate=False) if optional else None
  action_sock = messaging.sub_sock('slcAction', conflate=False) if optional else None
  pending_actions, pending_cruise = deque(maxlen=64), deque(maxlen=64)
  pending_manual, pending_ui = deque(maxlen=64), deque(maxlen=64)
  drive_start_ns = 0

  try:
    while True:
      if slc is not None and not replay:
        slc.refresh(time.monotonic_ns(), pending_actions=pending_actions, pending_cruise=pending_cruise)
      sm.update()
      if sm.updated['modelV2']:
        clocks = paired_clocks_ns() if conditional is not None and not replay else None
        now_ns = int(sm.logMonoTime['modelV2']) if replay else clocks[0] if clocks is not None else time.monotonic_ns()
        drive_id = (int(sm['deviceState'].startedMonoTime) if optional and sm.valid['deviceState'] and sm.alive['deviceState'] and
                    0 < sm.logMonoTime['deviceState'] <= now_ns else 0)
        drive_changed = drive_id > 0 and drive_start_ns > 0 and drive_id != drive_start_ns
        if drive_changed and conditional is not None and not replay:
          conditional.reset()
          pending_manual.clear()
          pending_ui.clear()

        if slc is not None or clocks is not None:
          if slc is not None and replay:
            slc.refresh(now_ns, pending_actions=pending_actions, pending_cruise=pending_cruise)
          for event in receive_slc_events(cruise_sock):
            if event.valid:
              kind = str(event.slcCruiseEvent.kind)
              if slc is not None and kind in SLC_CRUISE_EVENTS:
                pending_cruise.append(cruise_event_from_message(event))
              elif clocks is not None and kind == 'conditionalMode':
                pending_manual.append(event)
          for event in receive_slc_events(action_sock):
            if event.valid:
              kind = str(event.slcAction.kind)
              if slc is not None and kind in SLC_ACTIONS:
                pending_actions.append(action_from_message(event))
              elif clocks is not None and kind == 'conditionalModeCycle':
                pending_ui.append(event)

        output = slc.step(sm, now_ns, pending_actions, pending_cruise, drive_changed=drive_changed) if slc is not None else None
        ceiling = output.result.ceiling if output is not None else None
        status = output.message if output is not None else None
        longitudinal_planner.update(sm, cruise_ceiling=ceiling)
        if conditional is not None and clocks is not None:
          _, boot_ns, skew_ns = clocks
          proposal, snapshot = conditional.sample(
            sm, CP, longitudinal_planner, now_mono_ns=now_ns, now_boot_ns=boot_ns, sample_skew_ns=skew_ns,
            drive_id=drive_id, native_plan_valid=bool(sm.all_checks(native_inputs)),
            cruise_ceiling_mps=ceiling.speed_mps if ceiling is not None else None,
            manual_event=current_manual_event(pending_manual, now_ns, int(sm.logMonoTime['carState'])),
            ui_event=current_ui_manual_event(pending_ui, now_ns))
          status = conditional.status.attach(status, proposal, snapshot, now_ns=now_ns, drive_id=drive_id,
                                             model_ns=int(sm.logMonoTime['modelV2']), car_state_ns=int(sm.logMonoTime['carState']))
        if status is not None:
          pm.send('slcState', status)
        drive_start_ns = drive_id or drive_start_ns
        longitudinal_planner.publish(sm, pm)

        ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
        msg = messaging.new_message('driverAssistance')
        msg.valid = sm.all_checks(native_inputs)
        msg.driverAssistance.leftLaneDeparture = ldw.left
        msg.driverAssistance.rightLaneDeparture = ldw.right
        pm.send('driverAssistance', msg)
  finally:
    if conditional is not None:
      conditional.close()


if __name__ == "__main__":
  main()
