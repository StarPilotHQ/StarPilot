"""Read live steering preferences without putting disk waits on the control loop."""

from dataclasses import dataclass
import threading

from openpilot.selfdrive.controls.lib.highway_correction_gain import read_gain
from openpilot.starpilot.lateral.lane_runtime import read_settings
from openpilot.starpilot.lateral.lane_centering import LaneCenteringSettings
from openpilot.starpilot.lateral.lane_change_preferences import read_saved, SavedLaneChange


@dataclass(frozen=True)
class SteeringPreferences:
  highway_gain: float
  lane_centering: LaneCenteringSettings
  lane_change: SavedLaneChange


class LiveSteeringPreferences:
  INTERVAL = 1.0

  def __init__(self, params):
    self.params = params
    self.current = self._read()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  def _read(self):
    return SteeringPreferences(read_gain(self.params), read_settings(self.params), read_saved(self.params))

  def start(self):
    if self._thread is None:
      self._thread = threading.Thread(target=self._run, name="steering-settings", daemon=True)
      self._thread.start()

  def _run(self):
    while not self._stop.wait(self.INTERVAL):
      current = self._read()
      if not self._stop.is_set():
        self.current = current

  def close(self):
    self._stop.set()
