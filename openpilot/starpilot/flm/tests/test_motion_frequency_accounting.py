"""A slower blocking cadence must not relax receiver frequency thresholds."""
from openpilot.cereal import messaging
from openpilot.starpilot.flm.steerlearnerd import source_receiver


def test_motion_pacing_preserves_every_original_frequency_threshold():
  services = ['deviceMotion', 'extrinsicsCalibration', 'carState', 'pandaStates',
              'lateralDelay', 'vehicleParameters', 'driverMonitoringState', 'onroadEvents', 'aolSafetyWire']
  before = messaging.SubMaster(services, ignore_alive=['aolSafetyWire'], ignore_valid=['aolSafetyWire'],
                              ignore_avg_freq=['aolSafetyWire', 'carState'])
  after = source_receiver(services)
  assert after.non_polled_services == set(services) - {'deviceMotion'}
  assert before.update_freq == after.update_freq
  for name in services:
    first, second = before.freq_tracker[name], after.freq_tracker[name]
    assert (first.min_freq, first.max_freq) == (second.min_freq, second.max_freq)
  assert before.ignore_average_freq == after.ignore_average_freq
  assert before.ignore_alive == after.ignore_alive and before.ignore_valid == after.ignore_valid


def test_independently_timed_healthy_sources_do_not_conflate_each_others_frequency(monkeypatch):
  # Both source publishers run at20Hz. Driver-monitoring jitter alternates25/75ms;
  # waiting only for motion drops alternate driver packets and measures10Hz.
  # Exercise real SubMaster.update/update_msgs and FrequencyTracker, with only
  # socket delivery and its monotonic clock replaced by a deterministic bus.
  services = ['deviceMotion', 'driverMonitoringState', 'carState']

  class Socket:
    def __init__(self, name):
      self.name, self.pending, self.conflate = name, [], True

  class Bus:
    def __init__(self):
      self.now = 10.0
      self.sockets = {}
      motion = [(tick * 50, 'deviceMotion') for tick in range(100)]
      driver = [(tick * 100 + offset, 'driverMonitoringState') for tick in range(50) for offset in (10, 35)]
      state = [(tick * 10, 'carState') for tick in range(500)]
      self.events = sorted(motion + driver + state)
      self.position = 0

    def advance(self):
      stamp = self.events[self.position][0]
      self.now = 10.0 + stamp / 1000
      while self.position < len(self.events) and self.events[self.position][0] == stamp:
        _, name = self.events[self.position]
        packet = messaging.new_message(name)
        packet.valid = True
        packet.logMonoTime = round(self.now * 1e9)
        socket = self.sockets[name]
        if socket.conflate:
          socket.pending.clear()
        socket.pending.append(packet.as_reader())
        self.position += 1

  def observe(motion_only):
    bus = Bus()

    class Poller:
      def __init__(self):
        self.sockets = []

      def registerSocket(self, socket):
        self.sockets.append(socket)

      def poll(self, _timeout):
        while not any(bool(socket.pending) for socket in self.sockets):
          if bus.position == len(bus.events):
            return []
          bus.advance()
        return [socket for socket in self.sockets if bool(socket.pending)]

    def sub_sock(name, *, poller=None, conflate=True, **_kwargs):
      socket = bus.sockets[name] = Socket(name)
      socket.conflate = conflate
      if poller is not None:
        poller.registerSocket(socket)
      return socket

    def recv(socket):
      return socket.pending.pop(0) if socket.pending else None

    with monkeypatch.context() as patch:
      patch.setattr(messaging, 'Poller', Poller)
      patch.setattr(messaging, 'sub_sock', sub_sock)
      patch.setattr(messaging, 'recv_one_or_none', recv)
      def receipt_clock():
        bus.now += 0.000001  # Real receive work advances the monotonic clock.
        return bus.now
      patch.setattr(messaging.time, 'monotonic', receipt_clock)
      sm = source_receiver(services)
      if motion_only:
        for name in ('deviceMotion', 'driverMonitoringState'):
          sm.sock[name].conflate = True
        sm.poller = Poller()
        sm.poller.registerSocket(sm.sock['deviceMotion'])
        sm.non_polled_services = set(services) - {'deviceMotion'}
      while bus.position < len(bus.events):
        sm.update()
      return sm

  aliased, corrected = observe(True), observe(False)
  assert not aliased.freq_ok['driverMonitoringState']
  assert corrected.all_checks(['deviceMotion', 'driverMonitoringState', 'carState'])
  for name in services:
    before, after = aliased.freq_tracker[name], corrected.freq_tracker[name]
    assert (before.min_freq, before.max_freq) == (after.min_freq, after.max_freq)
