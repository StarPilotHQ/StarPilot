import os
import operator
import platform

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.common.hardware import PC, COMMA_HARDWARE
from openpilot.system.manager.process import PythonProcess, NativeProcess, DaemonProcess
from openpilot.starpilot.maps.shadow_lifecycle import MapdShadowProcess
from openpilot.starpilot.models.recovery_process import ModeldProcess
from openpilot.starpilot.system.jetlink.process import JetlinkProcess
from openpilot.starpilot.sentry_mode.preferences import enabled as sentry_enabled
from openpilot.starpilot.galaxy.camera_request import requested as camera_requested
from openpilot.starpilot.spot_monitor.preferences import enabled as vasm_enabled
from openpilot.starpilot.spot_monitor.inference import DEFAULT_MODEL_PATH

WEBCAM = os.getenv("USE_WEBCAM") is not None

def driverview(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started or params.get_bool("IsDriverViewEnabled")

def notcar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and CP.notCar

def iscar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not CP.notCar

def logging(started: bool, params: Params, CP: car.CarParams) -> bool:
  run = (not CP.notCar) or not params.get_bool("DisableLogging")
  return started and run

def ublox_available() -> bool:
  return os.path.exists('/dev/ttyHS0') and not os.path.exists('/persist/comma/use-quectel-gps')

def ublox(started: bool, params: Params, CP: car.CarParams) -> bool:
  use_ublox = ublox_available()
  if use_ublox != params.get_bool("UbloxAvailable"):
    params.put_bool("UbloxAvailable", use_ublox, block=True)
  return started and use_ublox

def joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("JoystickDebugMode")

def not_joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not params.get_bool("JoystickDebugMode")

def long_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("LongitudinalManeuverMode")

def lat_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("LateralManeuverMode")

def not_long_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not params.get_bool("LongitudinalManeuverMode")

def qcomgps(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not ublox_available()

def always_run(started: bool, params: Params, CP: car.CarParams) -> bool:
  return True

def only_onroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started

def steering_learning(started: bool, params: Params, CP: car.CarParams) -> bool:
  if not started:
    return False
  from openpilot.starpilot.lateral.learning_preferences import read_preference
  preference = read_preference(params, CP)
  return preference.valid and preference.enabled

def vision_slc_development(started: bool, params: Params, CP: car.CarParams) -> bool:
  from openpilot.starpilot.feature_runtime import enabled as feature_enabled
  return started and (feature_enabled(params, CP, 'vision', os.environ) or
                      feature_enabled(params, CP, 'vision_stop', os.environ))

def galaxy_local(started: bool, params: Params, CP: car.CarParams) -> bool:
  return os.getenv("STARPILOT_GALAXY_DISABLE") != "1"

def starport_enabled(started: bool, params: Params, CP: car.CarParams) -> bool:
  from openpilot.starpilot.system.starport.settings import enabled
  return COMMA_HARDWARE and platform.system() == "Linux" and enabled(params)

def spotify_enabled(started: bool, params: Params, CP: car.CarParams) -> bool:
  from openpilot.starpilot.soloist.paths import enabled
  return COMMA_HARDWARE and platform.system() == "Linux" and enabled()

def jetlink_enabled(started: bool, params: Params, CP: car.CarParams) -> bool:
  if not COMMA_HARDWARE or platform.system() != 'Linux':
    return False
  from openpilot.starpilot.system.jetlink.lifecycle import enabled
  return enabled(params)

def navigation(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("NavigationEnabled")

def only_offroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not started

def sentry_motion(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not started and sentry_enabled(params)

def vasm_monitor(started: bool, params: Params, CP: car.CarParams) -> bool:
  model_path = os.getenv("STARPILOT_VASM_MODEL_PATH", str(DEFAULT_MODEL_PATH))
  return (started and bool(model_path) and os.path.isabs(model_path) and os.path.isfile(model_path) and
          vasm_enabled(params, development=True))

def sensord_run(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started or sentry_motion(started, params, CP)

def parked_camera(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not started and camera_requested()


def livestream(started: bool, params: Params, CP: car.CarParams) -> bool:
  return params.get_bool("IsLiveStreaming")

def or_(*fns):
  return lambda *args: operator.or_(*(fn(*args) for fn in fns))

def and_(*fns):
  return lambda *args: operator.and_(*(fn(*args) for fn in fns))

procs = [
  DaemonProcess("manage_athenad", "openpilot.system.athena.manage_athenad", "AthenadPid"),

  NativeProcess("loggerd", "openpilot/system/loggerd", ["./loggerd"], logging),
  NativeProcess("encoderd", "openpilot/system/loggerd", ["./encoderd"], only_onroad),
  NativeProcess("stream_encoderd", "openpilot/system/loggerd", ["./encoderd", "--stream"], or_(livestream, notcar)),
  PythonProcess("logmessaged", "openpilot.system.logmessaged", always_run),

  NativeProcess("camerad", "openpilot/system/camerad", ["./camerad"], or_(or_(driverview, livestream), parked_camera), enabled=not WEBCAM),
  PythonProcess("webcamerad", "openpilot.system.camerad.webcam.camerad", or_(driverview, parked_camera), enabled=WEBCAM),
  PythonProcess("proclogd", "openpilot.system.proclogd", only_onroad, enabled=platform.system() != "Darwin"),
  PythonProcess("journald", "openpilot.system.journald", only_onroad, platform.system() != "Darwin"),
  PythonProcess("micd", "openpilot.system.micd", iscar),
  PythonProcess("timed", "openpilot.system.timed", always_run, enabled=not PC),

  ModeldProcess("modeld", "openpilot.selfdrive.modeld.modeld", only_onroad),
  PythonProcess("navd", "openpilot.selfdrive.navigation.navd", navigation),
  PythonProcess("mapsd", "openpilot.selfdrive.navigation.mapsd", navigation),
  PythonProcess("vision_slc", "openpilot.starpilot.speed_limits.vision.producer", vision_slc_development),
  PythonProcess("dmonitoringmodeld", "openpilot.selfdrive.modeld.dmonitoringmodeld", driverview, enabled=(WEBCAM or not PC)),

  PythonProcess("sensord", "openpilot.system.sensord.sensord", sensord_run, enabled=not PC, restart_on_exit=True),
  PythonProcess("sentry_motion", "openpilot.starpilot.sentry_mode.runtime", sentry_motion, enabled=not PC),
  PythonProcess("vasm_monitor", "openpilot.starpilot.spot_monitor.runtime", vasm_monitor, enabled=not PC),
  PythonProcess("ui", "openpilot.selfdrive.ui.ui", always_run, restart_on_exit=True),
  PythonProcess("navigationd", "openpilot.starpilot.navigation.runtime", always_run),
  PythonProcess("navtilesd", "openpilot.starpilot.navigation.navtilesd", always_run, enabled=platform.system() == "Linux"),
  PythonProcess("galaxy", "openpilot.starpilot.galaxy.managed", galaxy_local),
  PythonProcess("galaxy_phone", "openpilot.starpilot.galaxy_bluetooth.bootstrap", galaxy_local,
                enabled=COMMA_HARDWARE and not os.environ.get("OPENPILOT_PREFIX"), restart_on_exit=True),
  PythonProcess("replay_owner", "openpilot.starpilot.replay.owner", only_offroad,
                enabled=platform.system() == "Linux", restart_on_exit=True),
  PythonProcess("galaxy_hotspot", "openpilot.starpilot.galaxy.hotspot_daemon", galaxy_local,
                enabled=COMMA_HARDWARE and not os.environ.get('OPENPILOT_PREFIX'), restart_on_exit=True),
  PythonProcess("companion_uplink", "openpilot.starpilot.galaxy.companion_uplink_daemon", galaxy_local,
                enabled=COMMA_HARDWARE and not os.environ.get('OPENPILOT_PREFIX'), restart_on_exit=True),
  JetlinkProcess("jetlinkd", "openpilot.starpilot.models.jetlink_adapter", jetlink_enabled, enabled=COMMA_HARDWARE,
                 restart_on_exit=True),
  PythonProcess("starportd", "openpilot.starpilot.system.starport.daemon", starport_enabled, enabled=COMMA_HARDWARE,
                restart_on_exit=True),  # boot can race bluetoothd (NoSuchUnit)
  PythonProcess("spotifyd", "openpilot.starpilot.soloist.supervisor", spotify_enabled, enabled=COMMA_HARDWARE, restart_on_exit=True),
  # Boot can race the audio device; selfdrived blocks engagement while soundd is down.
  PythonProcess("soundd", "openpilot.selfdrive.ui.soundd", driverview, restart_on_exit=True),
  PythonProcess("locationd", "openpilot.selfdrive.locationd.locationd", only_onroad),
  MapdShadowProcess(),
  PythonProcess("map_snapshot_operations", "openpilot.starpilot.maps.operation_owner", only_offroad, enabled=platform.system() == "Linux"),
  NativeProcess("_pandad", "openpilot/selfdrive/pandad", ["./pandad"], always_run, enabled=False),
  PythonProcess("calibrationd", "openpilot.selfdrive.locationd.calibrationd", only_onroad),
  PythonProcess("torqued", "openpilot.selfdrive.locationd.torqued", only_onroad),
  PythonProcess("steerlearnerd", "openpilot.starpilot.flm.steerlearnerd", steering_learning, restart_on_exit=True),
  PythonProcess("controlsd", "openpilot.selfdrive.controls.controlsd", and_(not_joystick, iscar)),
  PythonProcess("joystickd", "openpilot.tools.joystick.joystickd", or_(joystick, notcar)),
  PythonProcess("selfdrived", "openpilot.selfdrive.selfdrived.selfdrived", only_onroad),
  PythonProcess("card", "openpilot.selfdrive.car.card", only_onroad),
  PythonProcess("deleter", "openpilot.system.loggerd.deleter", always_run),
  PythonProcess("dmonitoringd", "openpilot.selfdrive.monitoring.dmonitoringd", driverview, enabled=(WEBCAM or not PC)),
  PythonProcess("qcomgpsd", "openpilot.system.qcomgpsd.qcomgpsd", qcomgps, enabled=COMMA_HARDWARE),
  PythonProcess("pandad", "openpilot.selfdrive.pandad.pandad", always_run),
  PythonProcess("paramsd", "openpilot.selfdrive.locationd.paramsd", only_onroad),
  PythonProcess("lagd", "openpilot.selfdrive.locationd.lagd", only_onroad),
  PythonProcess("ubloxd", "openpilot.system.ubloxd.ubloxd", ublox, enabled=COMMA_HARDWARE),
  PythonProcess("pigeond", "openpilot.system.ubloxd.pigeond", ublox, enabled=COMMA_HARDWARE),
  PythonProcess("plannerd", "openpilot.selfdrive.controls.plannerd", not_long_maneuver),
  PythonProcess("maneuversd", "openpilot.tools.longitudinal_maneuvers.maneuversd", long_maneuver),
  PythonProcess("lateral_maneuversd", "openpilot.tools.lateral_maneuvers.lateral_maneuversd", lat_maneuver),
  PythonProcess("radard", "openpilot.selfdrive.controls.radard", only_onroad),
  PythonProcess("hardwared", "openpilot.system.hardware.hardwared", always_run),
  PythonProcess("modem", "openpilot.common.hardware.comma.modem", always_run, enabled=COMMA_HARDWARE),
  PythonProcess("usage_stats", "openpilot.starpilot.analytics.runtime", always_run, enabled=COMMA_HARDWARE),
  PythonProcess("tombstoned", "openpilot.system.tombstoned", always_run, enabled=not PC),
  PythonProcess("updated", "openpilot.system.updated.updated", only_offroad, enabled=not PC),
  PythonProcess("uploader", "openpilot.system.loggerd.uploader", always_run),

  # debug procs
  PythonProcess("webrtcd", "openpilot.system.webrtc.webrtcd", or_(livestream, notcar)),
  PythonProcess("joystick", "openpilot.tools.joystick.joystick_control", and_(joystick, iscar)),
]

managed_processes = {p.name: p for p in procs}
