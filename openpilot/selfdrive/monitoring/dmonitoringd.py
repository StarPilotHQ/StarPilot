#!/usr/bin/env python3
import os
import time
import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process
from openpilot.selfdrive.monitoring.policy import DriverMonitoring
from openpilot.starpilot.aol.runtime import monitoring_lateral_engaged
from openpilot.starpilot.feature_runtime import requested as feature_requested


def _read_allow_manumatic(params: Params) -> bool | None:
  try:
    cp_bytes = params.get("CarParams")
  except Exception:
    return None
  if not cp_bytes:
    return None
  from opendbc.car.structs import car
  with car.CarParams.from_bytes(cp_bytes) as cp:
    return cp.carFingerprint == "SUBARU_LEGACY_2025"


def dmonitoringd_thread():
  config_realtime_process([0, 1, 2, 3], 5)

  params = Params()
  pm = messaging.PubMaster(['driverMonitoringState'])
  aol_replay = os.getenv('AOL_REPLAY_RUNTIME') == '1' or feature_requested(params, 'aol')
  required = ['driverStateV2', 'extrinsicsCalibration', 'carState', 'selfdriveState', 'modelV2']
  sm = messaging.SubMaster(required + (['aolAxisState', 'aolSafetyWire'] if aol_replay else []), poll='driverStateV2')

  allow_manumatic = _read_allow_manumatic(params)
  cp_checked = allow_manumatic is not None
  DM = DriverMonitoring(rhd_saved=params.get_bool("IsRhdDetected"), always_on=params.get_bool("AlwaysOnDM"),
                        allow_manumatic=bool(allow_manumatic))
  demo_mode=False

  # 20Hz <- dmonitoringmodeld
  while True:
    sm.update()
    if not sm.updated['driverStateV2']:
      # iterate when model has new output
      continue

    valid = sm.all_checks(required)
    aol_engaged = aol_replay and monitoring_lateral_engaged(sm, now_ns=time.monotonic_ns())
    if demo_mode and sm.valid['driverStateV2']:
      DM.run_step(sm, demo=True)
    elif valid:
      DM.run_step(sm, demo=demo_mode, aol_lateral_engaged=aol_engaged)

    # publish
    dat = DM.get_state_packet(valid=valid)
    pm.send('driverMonitoringState', dat)

    # load live always-on toggle
    if sm['driverStateV2'].frameId % 40 == 1:
      DM.always_on = params.get_bool("AlwaysOnDM")
      demo_mode = params.get_bool("IsDriverViewEnabled")
      if not cp_checked:
        allow_manumatic = _read_allow_manumatic(params)
        if allow_manumatic is not None:
          DM.allow_manumatic = allow_manumatic
          cp_checked = True

    # save rhd virtual toggle every 5 mins
    if (sm['driverStateV2'].frameId % 6000 == 0 and not demo_mode and
     DM.wheelpos_offsetter.filtered_stat.n > DM.settings._WHEELPOS_FILTER_MIN_COUNT and
     DM.wheel_on_right == (DM.wheelpos_offsetter.filtered_stat.M > DM.settings._WHEELPOS_THRESHOLD)):
      params.put_bool("IsRhdDetected", DM.wheel_on_right)

def main():
  dmonitoringd_thread()


if __name__ == '__main__':
  main()
