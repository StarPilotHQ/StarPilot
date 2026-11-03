#!/usr/bin/env python3
import os
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
from openpilot.starpilot.longitudinal.upstream.planner import LongitudinalPlanner
import openpilot.cereal.messaging as messaging


def main():
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  ldw = LaneDepartureWarning()
  longitudinal_planner = LongitudinalPlanner(CP)
  conditional = ConditionalPlannerHost(params) if feature_enabled(params, CP, 'conditional', os.environ) else None
  native_inputs = ['carControl', 'carState', 'controlsState', 'vehicleParameters', 'radarState', 'modelV2', 'selfdriveState']
  optional = ['deviceState', 'longitudinalPlan', 'starpilotRadarState', 'starpilotNavigation'] if conditional is not None else []
  pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance'] + (['slcState'] if conditional is not None else []))
  sm = messaging.SubMaster(native_inputs + optional, poll='modelV2',
                           ignore_alive=optional, ignore_valid=optional, ignore_avg_freq=optional)
  cruise_sock = messaging.sub_sock('slcCruiseEvent', conflate=False) if conditional is not None else None
  action_sock = messaging.sub_sock('slcAction', conflate=False) if conditional is not None else None
  pending_manual, pending_ui = deque(maxlen=64), deque(maxlen=64)
  drive_start_ns = 0

  try:
    while True:
      sm.update()
      if sm.updated['modelV2']:
        longitudinal_planner.update(sm)
        if conditional is not None and 'REPLAY' not in os.environ:
          clocks = paired_clocks_ns()
          if clocks is not None:
            now_ns, boot_ns, skew_ns = clocks
            drive_id = (int(sm['deviceState'].startedMonoTime) if sm.valid['deviceState'] and sm.alive['deviceState'] and
                        0 < sm.logMonoTime['deviceState'] <= now_ns else 0)
            if drive_id > 0 and drive_start_ns > 0 and drive_id != drive_start_ns:
              conditional.reset()
              pending_manual.clear()
              pending_ui.clear()
            for sock, service, kind, pending in ((cruise_sock, 'slcCruiseEvent', 'conditionalMode', pending_manual),
                                                  (action_sock, 'slcAction', 'conditionalModeCycle', pending_ui)):
              for _ in range(8):
                event = messaging.recv_one_or_none(sock)
                if event is None:
                  break
                if event.valid and str(getattr(event, service).kind) == kind:
                  pending.append(event)
            proposal, snapshot = conditional.sample(
              sm, CP, longitudinal_planner, now_mono_ns=now_ns, now_boot_ns=boot_ns, sample_skew_ns=skew_ns,
              drive_id=drive_id, native_plan_valid=bool(sm.all_checks(native_inputs)),
              manual_event=current_manual_event(pending_manual, now_ns, int(sm.logMonoTime['carState'])),
              ui_event=current_ui_manual_event(pending_ui, now_ns))
            status = conditional.status.attach(None, proposal, snapshot, now_ns=now_ns, drive_id=drive_id,
                                               model_ns=int(sm.logMonoTime['modelV2']), car_state_ns=int(sm.logMonoTime['carState']))
            pm.send('slcState', status)
            if drive_id > 0:
              drive_start_ns = drive_id
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
