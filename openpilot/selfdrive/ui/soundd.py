import math
import numpy as np
import time
from pathlib import Path


from openpilot.cereal import log, messaging
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import Ratekeeper
from openpilot.common.utils import retry
from openpilot.common.swaglog import cloudlog

from openpilot.system import micd
from openpilot.common.hardware import HARDWARE, PC
from openpilot.common.params import Params
from openpilot.starpilot.aol.wire import INTENT_SERVICE
from openpilot.starpilot.audio.alert_volume import AUTO, VOLUMES, effective_volume, read_volume
from openpilot.starpilot.audio.slc_warning import SlcWarning
from openpilot.starpilot.audio.axis_alerts import AxisAlerts
from openpilot.starpilot.audio.sound_pack import PACK_ROOT, SoundPackLoader, migrate_removed_selection

SAMPLE_RATE = 48000
SAMPLE_BUFFER = 4096 # (approx 100ms)
MAX_VOLUME = 1.0
MIN_VOLUME = 0.1
STARPILOT_AUTO_MIN = 0.50
ALERT_RAMP_TIME = 4 # seconds to ramp critical alerts to max volume
ALERT_MAX_TIME = 8 # seconds before critical alerts switch to the max sound
SELFDRIVE_STATE_TIMEOUT = 5 # 5 seconds
STREAM_REPORT_INTERVAL = 5.0
FILTER_DT = 1. / (micd.SAMPLE_RATE / micd.FFT_SAMPLES)

AMBIENT_DB = 22 # DB where MIN_VOLUME is applied
DB_SCALE = 30 # AMBIENT_DB + DB_SCALE is where MAX_VOLUME is applied

VOLUME_BASE = 20
if HARDWARE.get_device_type() in ("tici", "tizi"):
  AMBIENT_DB = 30
  VOLUME_BASE = 10

AudibleAlert = log.SelfdriveState.AudibleAlert
CRITICAL_MAX = -1 # internal sound key, not an AudibleAlert


sound_list: dict[int, tuple[str, int | None, float]] = {
  # AudibleAlert, file name, play count (none for infinite)
  AudibleAlert.engage: ("engage.wav", 1, MAX_VOLUME),
  AudibleAlert.disengage: ("disengage.wav", 1, MAX_VOLUME),
  AudibleAlert.refuse: ("refuse.wav", 1, MAX_VOLUME),

  AudibleAlert.prompt: ("warning.wav", 1, MAX_VOLUME),
  AudibleAlert.promptRepeat: ("warning.wav", None, MAX_VOLUME),
  AudibleAlert.promptDistracted: ("dm_warning.wav", None, MAX_VOLUME),

  AudibleAlert.preAlert: ("pre_alert.wav", 1, MAX_VOLUME),

  AudibleAlert.warningSoft: ("critical.wav", None, MAX_VOLUME),
  AudibleAlert.warningImmediate: ("dm_critical.wav", None, MAX_VOLUME),
  CRITICAL_MAX: ("dm_critical_max.wav", None, MAX_VOLUME),
}

ALERT_VOLUME_KEYS = {
  AudibleAlert.engage: "EngageVolume",
  AudibleAlert.disengage: "DisengageVolume",
  AudibleAlert.refuse: "RefuseVolume",
  AudibleAlert.prompt: "PromptVolume",
  AudibleAlert.promptRepeat: "PromptVolume",
  AudibleAlert.preAlert: "PromptDistractedVolume",
  AudibleAlert.promptDistracted: "PromptDistractedVolume",
  AudibleAlert.warningSoft: "WarningSoftVolume",
  AudibleAlert.warningImmediate: "WarningImmediateVolume",
}

def check_selfdrive_timeout_alert(sm):
  ss_missing = time.monotonic() - sm.recv_time['selfdriveState']

  if ss_missing > SELFDRIVE_STATE_TIMEOUT:
    if sm['selfdriveState'].enabled and (ss_missing - SELFDRIVE_STATE_TIMEOUT) < 10:
      return True

  return False


class Soundd:
  def __init__(self, params=None, pack_root=PACK_ROOT):
    self.volume_params = params if params is not None else Params()
    migrate_removed_selection(self.volume_params, pack_root)
    self.pack_loader = SoundPackLoader(self.volume_params, Path(__file__).resolve().parents[1] / "assets/sounds", pack_root,
                                       device_type=HARDWARE.get_device_type())
    self.load_sounds()
    self.saved_volumes = {key: AUTO for key, _, _ in VOLUMES}
    self.volume_read_at = 0.0

    self.current_alert = AudibleAlert.none
    self.current_sound = AudibleAlert.none
    self.current_volume = MIN_VOLUME
    self.slc_warning = SlcWarning()
    self.slc_warning_playing = False
    self.slc_warning_volume = 0.
    self.current_sound_frame = 0

    self.ramp_start_volume = MIN_VOLUME
    self.ramp_start_time = 0.

    self.selfdrive_timeout_alert = False
    self.pending_stop = False
    self.pending_stream_status = None
    self.stream_status_count = 0
    self.output_underflow_count = 0
    self.reported_output_underflows = 0
    self.stream_report_at = None
    self.native_audio = None
    self.axis_alerts = AxisAlerts()

    self.spl_filter_weighted = FirstOrderFilter(0, 2.5, FILTER_DT, initialized=False)

  def load_sounds(self):
    loaded = self.pack_loader.refresh(tuple(dict.fromkeys(spec[0] for spec in sound_list.values())), time.monotonic())
    if loaded is not None:
      self.loaded_sounds = {sound: loaded[spec[0]] for sound, spec in sound_list.items()}

  def get_sound_data(self, frames): # get "frames" worth of data from the current alert sound, looping when required

    ret = np.zeros(frames, dtype=np.float32)
    producing_alert = self.current_alert
    loaded_sounds = self.loaded_sounds
    sound_data = None

    if self.current_alert != AudibleAlert.none:
      num_loops = sound_list[self.current_alert][1]
      sound_data = loaded_sounds[self.current_sound]
      written_frames = 0

      current_sound_frame = self.current_sound_frame % len(sound_data)
      loops = self.current_sound_frame // len(sound_data)

      while written_frames < frames and (num_loops is None or loops < num_loops):
        available_frames = sound_data.shape[0] - current_sound_frame
        frames_to_write = min(available_frames, frames - written_frames)
        ret[written_frames:written_frames+frames_to_write] = sound_data[current_sound_frame:current_sound_frame+frames_to_write]
        written_frames += frames_to_write
        self.current_sound_frame += frames_to_write
        current_sound_frame = self.current_sound_frame % len(sound_data)
        loops = self.current_sound_frame // len(sound_data)
        if self.pending_stop and current_sound_frame == 0:
          self.current_alert = AudibleAlert.none
          self.pending_stop = False
          break

    key = self.volume_key(producing_alert)
    ambient = self.current_volume
    if sound_data is not None and self.pack_loader.is_builtin(sound_data):
      ambient = max(ambient, STARPILOT_AUTO_MIN)
    gain = ambient if key is None else effective_volume(
      key, self.saved_volumes.get(key), ambient,
      immediate_ramp=self.current_volume if producing_alert in (AudibleAlert.warningSoft, AudibleAlert.warningImmediate) else None)
    return ret * gain

  def volume_key(self, alert):
    return ("SLCWarningVolume" if self.slc_warning_playing and alert == AudibleAlert.preAlert
            else ALERT_VOLUME_KEYS.get(alert))

  def refresh_saved_volumes(self, now: float) -> None:
    """Read slow Params state from the service loop, never the audio callback."""
    if now - self.volume_read_at < 0.25:
      return
    self.volume_read_at = now
    self.saved_volumes = {key: read_volume(self.volume_params, key).value for key, _, _ in VOLUMES}
    self.slc_warning_volume = self.saved_volumes["SLCWarningVolume"] or 0.

  def callback(self, data_out: np.ndarray, frames: int, time, status) -> None:
    data_out[:frames, 0] = self.get_sound_data(frames)
    if status:
      self.pending_stream_status = status
      self.stream_status_count += 1
      self.output_underflow_count += int(bool(getattr(status, "output_underflow", False)))

  def log_pending_stream_status(self, stream=None) -> None:
    status = self.pending_stream_status
    if status is None and self.output_underflow_count == self.reported_output_underflows:
      return
    now = time.monotonic()
    if self.stream_report_at is not None and now - self.stream_report_at < STREAM_REPORT_INTERVAL:
      return
    self.pending_stream_status = None
    self.stream_report_at = now
    underflows = self.output_underflow_count
    message = (f"soundd stream diagnostics: status={status} status_callbacks={self.stream_status_count} "
               + f"output_underflows={underflows}")
    # Output dropouts must reach errorLogMessage, which is retained in qlog.
    if underflows > self.reported_output_underflows:
      cloudlog.error(message)
    else:
      cloudlog.warning(message)
    self.reported_output_underflows = underflows

  def update_alert(self, new_alert):
    current_alert_played_once = self.current_alert == AudibleAlert.none or self.current_sound_frame >= len(self.loaded_sounds[self.current_sound])
    # let looping sounds finish the current loop instead of cutting off mid tone
    if new_alert == AudibleAlert.none and self.current_alert != AudibleAlert.none and sound_list[self.current_alert][1] is None:
      # Complete the current loop even when dismissal arrives during the first
      # pass. Cutting that pass early creates a step from its current sample to
      # silence. get_sound_data stops at the next loop endpoint; repeated None
      # updates leave that endpoint pending rather than restarting playback.
      completed_boundary = self.current_sound_frame % len(self.loaded_sounds[self.current_sound]) == 0
      if completed_boundary:
        self.current_alert = AudibleAlert.none
        self.pending_stop = False
      else:
        self.pending_stop = True
      return
    self.pending_stop = False
    if self.current_alert != new_alert and (new_alert != AudibleAlert.none or current_alert_played_once):
      if new_alert in (AudibleAlert.warningSoft, AudibleAlert.warningImmediate):
        self.ramp_start_volume = self.current_volume
        self.ramp_start_time = time.monotonic()
      self.current_alert = new_alert
      self.current_sound = new_alert
      self.current_sound_frame = 0

  def get_audible_alert(self, sm):
    if sm.updated['selfdriveState']:
      new_alert = sm['selfdriveState'].alertSound.raw
      now_ns = time.monotonic_ns()
      new_alert = self.axis_alerts.update(sm, new_alert, now_ns)
      if new_alert != AudibleAlert.none:
        self.slc_warning_playing = False
      warn = self.slc_warning.update(sm, now_ns,
        busy=new_alert != AudibleAlert.none or self.current_alert != AudibleAlert.none,
        enabled=self.slc_warning_volume > 0)
      if warn:
        self.slc_warning_playing = True
        new_alert = AudibleAlert.preAlert
      self.update_alert(new_alert)
    elif check_selfdrive_timeout_alert(sm):
      self.update_alert(AudibleAlert.warningImmediate)
      self.selfdrive_timeout_alert = True
    elif self.selfdrive_timeout_alert:
      self.update_alert(AudibleAlert.none)
      self.selfdrive_timeout_alert = False

  def update_critical_sound(self, now: float) -> None:
    if self.current_alert in (AudibleAlert.warningSoft, AudibleAlert.warningImmediate):
      elapsed = now - self.ramp_start_time
      ramp_vol = float(np.interp(elapsed, [0, ALERT_RAMP_TIME], [self.ramp_start_volume, MAX_VOLUME]))
      self.current_volume = max(self.current_volume, ramp_vol)
      # Dismissal owns the current waveform's remaining tail. Escalating here
      # would reset its cursor and start an additional critical-max loop.
      if elapsed >= ALERT_MAX_TIME and self.current_sound != CRITICAL_MAX and not self.pending_stop:
        self.current_sound = CRITICAL_MAX
        self.current_sound_frame = 0

  def calculate_volume(self, weighted_db):
    volume = ((weighted_db - AMBIENT_DB) / DB_SCALE) * (MAX_VOLUME - MIN_VOLUME) + MIN_VOLUME
    return math.pow(VOLUME_BASE, (np.clip(volume, MIN_VOLUME, MAX_VOLUME) - 1))

  @retry(attempts=10, delay=3)
  def get_stream(self, sd):
    self.audio_backend = "python"
    self.audio_fallback_reason = "host" if PC else None
    # reload sounddevice to reinitialize portaudio
    sd._terminate()
    sd._initialize()
    if not PC:
      from openpilot.starpilot.audio.native_playback import NativePlayback
      try:
        playback = NativePlayback(self, sd)
      except (OSError, AttributeError, ValueError, MemoryError) as error:
        self.audio_fallback_reason = "initialization:" + type(error).__name__
      else:
        try:
          stream = playback.stream()
        except (AttributeError, TypeError, ValueError) as error:
          self.audio_fallback_reason = "stream:" + type(error).__name__
          playback.close()
        except BaseException:
          playback.close()
          raise
        else:
          self.native_audio = playback
          self.audio_backend = "native"
          return stream
    return sd.OutputStream(channels=1, samplerate=SAMPLE_RATE, callback=self.callback, blocksize=SAMPLE_BUFFER)

  def soundd_thread(self):
    # sounddevice must be imported after forking processes
    import sounddevice as sd
    micd.patch_sounddevice(sd)

    sm = messaging.SubMaster(['selfdriveState', 'soundPressure', 'aolAxisState', INTENT_SERVICE, 'slcState'])

    with self.get_stream(sd) as stream:
      rk = Ratekeeper(20)

      cloudlog.info(f"soundd stream started: {stream.samplerate=} {stream.channels=} {stream.dtype=} {stream.device=}, " +
                   f"{stream.blocksize=}, {stream.latency=}, backend={self.audio_backend}, fallback={self.audio_fallback_reason}")
      while True:
        sm.update(0)
        if self.native_audio is not None:
          self.native_audio.sync_owner()
          self.native_audio.report_underflow_timing()
        self.log_pending_stream_status(stream)
        self.refresh_saved_volumes(time.monotonic())
        self.load_sounds()

        # freeze volume during alerts to avoid mic feedback increasing volume
        if sm.updated['soundPressure']:
          self.spl_filter_weighted.update(sm["soundPressure"].soundPressureWeightedDb)
          if self.current_alert == AudibleAlert.none:
            self.current_volume = self.calculate_volume(float(self.spl_filter_weighted.x))

        self.get_audible_alert(sm)

        self.update_critical_sound(time.monotonic())
        if self.native_audio is not None:
          self.native_audio.publish()

        rk.keep_time()

        active = stream.active
        if not active and self.native_audio is not None:
          self.native_audio.report_underflow_timing(closing=True)
        assert active


def main():
  s = Soundd()
  s.soundd_thread()


if __name__ == "__main__":
  main()
