"""Actual exported native status + actual production C++ negotiate/write/HB/read order."""

import ctypes
import subprocess
from openpilot.cereal import messaging
from openpilot.starpilot.aol.wire import PandaSlot, SafetyState, encode_safety


class PandadOrder:
  def __init__(self, safety, library_path, executable):
    self.safety = safety
    self.lib = ctypes.CDLL(str(library_path))
    self.process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    self.heartbeat = False
    self.reported_diagnostic_mask = 0

  def status(self):
    # Board USB0xd5 initializer reads request first, permission second.
    request = int(self.safety.aol_get_request_mask())
    permission = int(self.safety.aol_get_permission_mask())
    mode = ctypes.c_uint16.in_dll(self.lib, 'current_safety_mode').value
    word = ctypes.c_uint16.in_dll(self.lib, 'current_safety_param').value
    capability = int(ctypes.c_void_p.in_dll(self.lib, 'aol_policy').value is not None)
    return request, permission, mode, word, capability

  def exchange(self, line):
    assert self.process.poll() is None
    self.process.stdin.write(line + '\n')
    self.process.stdin.flush()
    result = self.process.stdout.readline().strip().split()
    assert result, self.process.poll()
    return result

  def step(self, driven, published, now, *, after_health_read=None):
    health_controls = bool(self.safety.get_controls_allowed()) if after_health_read is not None else None
    if after_health_read is not None:
      after_health_read()
    before = self.status()
    axis_event = driven['aolAxisState']
    axis = axis_event.aolAxisState
    intent_event = published['aolIntentWire']
    fields = [
      now,
      1,
      int(axis.qualified),
      axis.sessionId,
      axis.observedMonoTime,
      axis.validUntilMonoTime,
      axis_event.logMonoTime,
      int(axis.desiredLateral),
      int(axis.desiredLongitudinal),
      1,
      axis.sourceCarStateMonoTime,
      intent_event.logMonoTime,
      bytes(intent_event.aolIntentWire).hex(),
    ]
    capable, request, retain = map(int, self.exchange('PREP ' + ' '.join(map(str, (*before, *fields)))))
    if capable:
      self.safety.aol_set_host_request(request)
    # Actual pandad OR happens only after successful write.
    self.heartbeat = bool(driven['selfdriveState'].selfdriveState.enabled or (capable and (request or retain)))
    self.safety.set_aol_test_heartbeat(self.heartbeat)
    after = self.status()
    compatible, session = self.exchange('DONE ' + ' '.join(map(str, (*after, capable))))
    request_after, permission, mode, word, _ = after
    wire = messaging.new_message('aolSafetyWire', 0, valid=bool(int(compatible)), logMonoTime=now)
    wire.aolSafetyWire = encode_safety(
      SafetyState(
        1,
        bool(int(compatible)),
        now,
        now + 200_000_000,
        mode,
        word,
        bool(permission & 1),
        bool(permission & 2),
        bool(request_after & 1),
        bool(request_after & 2),
        'private-native-honda',
        '' if session == '-' else session,
        sourcePandaStatesMonoTime=now,
        pandaInventory=(
          PandaSlot(
            0,
            'private-native-honda',
            mode,
            word,
            32,
            bool(self.safety.get_controls_allowed()) if health_controls is None else health_controls,
            not bool(self.safety.safety_config_valid()),
            False,
            (int(bool(self.safety.get_relay_malfunction())) | self.reported_diagnostic_mask),
          ),
        ),
      )
    )
    return wire, {'before': before, 'plan': (capable, request, retain), 'heartbeat': self.heartbeat, 'after': after, 'session': session}

  def close(self):
    self.process.stdin.close()
    try:
      assert self.process.wait(timeout=10) == 0
    finally:
      self.process.stdout.close()
