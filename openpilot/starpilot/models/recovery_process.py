"""Manager adapter for process-isolated modeld recovery; no GPU context here."""
import os
import signal
import time
from multiprocessing import Process

from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.models.receipt import process_start_ticks, read_receipt, read_pair_receipt
from openpilot.starpilot.models.recovery import KILL_GRACE_NS, STARTUP_FALLBACK_EXIT, ModelRecoveryOwner, RecoveryEvidence
from openpilot.starpilot.models.status import ModelVariant
from openpilot.system.manager.process import PythonProcess, join_process, launcher

SMALL_ONLY_ENV = 'STARPILOT_MODELD_RECOVERY_SMALL_ONLY'


def modeld_launcher(module, name, small_only):
  # Set in this fresh child before importing modeld; never alter manager's env.
  if small_only:
    os.environ[SMALL_ONLY_ENV] = '1'
  else:
    os.environ.pop(SMALL_ONLY_ENV, None)
  launcher(module, name)


class ModeldProcess(PythonProcess):
  def __init__(self, name, module, should_run, enabled=True):
    super().__init__(name, module, should_run, enabled=enabled)
    self.recovery = ModelRecoveryOwner()
    self.context_ready = False
    self.launch_drive_id = 0

  def observe(self, sm, started):
    now = time.monotonic_ns()
    alive = self.proc is not None and self.proc.is_alive()
    pid = self.proc.pid if self.proc is not None else 0
    ticks = process_start_ticks(pid or 0) if alive else 0
    identity = (pid, ticks) if pid and ticks else None
    load = read_receipt() if alive else None
    pair = read_pair_receipt() if alive else None
    pair_matches = pair is not None and identity == (pair.pid, pair.process_start_ticks)
    matches = load is not None and identity == (load.pid, load.process_start_ticks)
    cameras = []
    for service in ('narrowRoadCameraState', 'wideRoadCameraState'):
      if sm.seen[service] and sm.valid[service] and sm.alive[service]:
        cameras.append((int(sm.logMonoTime[service]), int(sm[service].frameId)))
    evidence = RecoveryEvidence(
      now, int(sm['deviceState'].startedMonoTime) if started else 0, identity, alive,
      bool(pair_matches or (matches and load.variant == ModelVariant.CHESTNUT)), pair.loaded_mono_ns if pair_matches else load.loaded_mono_ns if matches else 0,
      int(sm.logMonoTime['modelV2']), int(sm.logMonoTime['drivingModelData']),
      bool(sm.seen['modelV2'] and sm.seen['drivingModelData'] and sm.valid['modelV2'] and sm.valid['drivingModelData']),
      bool(sm['modelV2'].big), tuple(cameras),
    )
    device_fresh = (sm.seen['deviceState'] and sm.valid['deviceState'] and sm.alive['deviceState'] and
                    0 < int(sm.logMonoTime['deviceState']) <= now <= int(sm.logMonoTime['deviceState']) + 1_000_000_000)
    self.context_ready = bool(device_fresh and started)
    if (self.context_ready and self.proc is not None and not alive and not self.shutting_down and
        self.proc.exitcode == STARTUP_FALLBACK_EXIT and self.recovery.phase == 'monitor' and
        not self.recovery.small_only and 0 < self.launch_drive_id == evidence.drive_id):
      # Only our explicitly failed startup may request this one fresh process.
      # Never reuse the old GPU context or retry an arbitrary Small crash.
      self.proc.join(timeout=0)
      self.proc = None
      self.recovery.drive_id = evidence.drive_id
      self.recovery.small_only = True
      self.recovery.phase = 'small'
      cloudlog.event('modeld.recovery', action='small-after-startup-exit', pid=pid, drive=evidence.drive_id)
      return
    if not device_fresh and self.recovery.phase == 'monitor':
      self.recovery._withdraw()
      return
    action = self.recovery.step(evidence)
    if action in ('interrupt', 'kill'):
      # Owner identity is checked above; SIGKILL gets a bounded exit observation,
      # never an unbounded join and never a concurrent Small publisher.
      try:
        self.signal(signal.SIGINT if action == 'interrupt' else signal.SIGKILL)
      except ProcessLookupError:
        pass  # Exit is confirmed on the next owner observation.
      cloudlog.event('modeld.recovery', action=action, pid=pid, drive=evidence.drive_id)
    elif action == 'failed':
      cloudlog.error('modeld recovery could not confirm process exit; Small restart blocked')
    elif action in ('small', 'exit'):
      assert self.proc is not None and not self.proc.is_alive()
      self.proc.join(timeout=0)
      self.proc = None
      self.shutting_down = False
      cloudlog.event('modeld.recovery', action='small-after-exit', pid=pid, drive=evidence.drive_id)

  def stop(self, retry=True, block=True, sig=None):
    if self.recovery.phase in ('sigint', 'sigkill', 'failed'):
      # Recovery owns teardown even when manager transitions offroad. Never
      # enter the generic five-second/unbounded join for this known stalled PID.
      if self.proc is None:
        return None
      if self.proc.is_alive() and block:
        # manager_cleanup has no future observation tick. Kill this owned child
        # now and bound the exit wait instead of abandoning a stalled process.
        try:
          self.signal(signal.SIGKILL)
        except ProcessLookupError:
          pass
        join_process(self.proc, KILL_GRACE_NS / 1e9)
      if not self.proc.is_alive():
        self.proc.join(timeout=0)
        ret = self.proc.exitcode
        self.proc = None
        self.shutting_down = False
        return ret
      if block:
        self.recovery.phase = 'failed'
        cloudlog.error('modeld shutdown could not confirm stalled process exit')
      return None
    return super().stop(retry=retry, block=block, sig=sig)

  def start(self):
    if not self.context_ready or self.recovery.phase in ('sigint', 'sigkill', 'failed'):
      return
    if self.shutting_down:
      self.stop(block=False)
    if self.proc is not None:
      return
    self.proc = Process(name=self.name, target=modeld_launcher,
                        args=(self.module, self.name, self.recovery.small_only))
    self.launch_drive_id = self.recovery.drive_id
    self.proc.start()
    self.shutting_down = False
