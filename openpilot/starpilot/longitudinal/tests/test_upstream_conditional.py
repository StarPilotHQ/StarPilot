"""Upstream planning policy with genuine CEM proposals and mode consumption."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.starpilot.conditional_mode.consumer import ModeConsumer
from openpilot.starpilot.conditional_mode.preferences import CEMOptions, ModeChoice, SavedPreferences, encode_preferences
from openpilot.starpilot.conditional_mode.status import settings_fingerprint
from openpilot.starpilot.conditional_mode.runtime_settings import ConditionalSettingsOwner
from openpilot.starpilot.conditional_mode.tests.test_projection import BOOT, MONO, FakeSubMaster, serialized_scene
from openpilot.starpilot.longitudinal.planner_selection import run_selected
from openpilot.starpilot.longitudinal.upstream import plannerd


class EndLoop(Exception):
  pass


class TestUpstreamConditional(unittest.TestCase):
  def run_loop(self, *, safe=False, stale=False, clocks=True, replay=False, show_speed_limits=False):
    directory = self.enterContext(tempfile.TemporaryDirectory())
    params = Params(directory)
    cp = CarInterface.get_non_essential_params(CAR.HONDA_CIVIC_BOSCH)
    cp.openpilotLongitudinalControl = True
    params.put('CarParams', cp.to_bytes(), block=True)
    params.put_bool('UseStarPilotLongitudinalPlanner', False, block=True)
    params.put_bool('SafeMode', safe, block=True)
    params.put_bool('ExperimentalMode', False, block=True)
    params.put_bool('ShowSpeedLimits', show_speed_limits, block=True)
    if show_speed_limits:
      params.put('SLCPriority1', 'Dashboard', block=True)
    params.put('ConditionalModeConfig', json.loads(encode_preferences(SavedPreferences(
      mode=ModeChoice.CEM, cem=CEMOptions(speed_mps=20., stop_lights=False, lead=False, slower_lead=False)))), block=True)
    settings = ConditionalSettingsOwner(params)
    consumer = ModeConsumer()
    results, statuses, plans, subscriptions = [], [], [], []

    class Scene(FakeSubMaster):
      def __init__(self):
        super().__init__({}, MONO)
        self.tick = 0
        self.frame = 0
        self.updated = {'modelV2': True}
        self.experimental = False
        self.now = MONO

      def all_checks(self, service_list=None):
        return not stale

      def update(self):
        self.tick += 1
        if self.tick > 100:
          raise EndLoop
        self.frame += 1
        self.now = MONO + self.tick * 50_000_000 + 1_000_000
        stamp = self.now - 1_000_000
        self.stamp(stamp)
        self.payloads = serialized_scene(stamp=BOOT + stamp - MONO, speed=15. if self.tick <= 50 else 25.,
                                         lead_present=False, experimental_mode=self.experimental)
        for name in ('deviceState', 'vehicleParameters'):
          event = messaging.new_message(name, valid=True)
          if name == 'deviceState':
            event.deviceState.started = True
            event.deviceState.startedMonoTime = MONO
          self.payloads[name] = getattr(messaging.log_from_bytes(event.to_bytes()), name)
          self.logMonoTime[name] = stamp
          self.recv_time[name] = stamp / 1e9
          self.valid[name] = self.alive[name] = self.seen[name] = True
        for name in ('carState', 'controlsState', 'modelV2'):
          event = messaging.new_message(name, valid=True)
          setattr(event, name, self.payloads[name])
          if name == 'carState':
            event.carState.vCruise = 108.
          elif name == 'controlsState':
            event.controlsState.longControlState = 'pid'
          else:
            event.modelV2.action.shouldStop = False
            event.modelV2.action.desiredAcceleration = -0.5
          self.payloads[name] = getattr(messaging.log_from_bytes(event.to_bytes()), name)
        if stale:
          self.logMonoTime['modelV2'] -= 1_000_000_000
        if show_speed_limits:
          dashboard = messaging.new_message('slcDashboardObservation', valid=True).slcDashboardObservation
          dashboard.carStateLogMonoTime = dashboard.observedMonoTime = stamp
          dashboard.validUntilMonoTime = stamp + 100_000_000
          dashboard.status, dashboard.speedMps, dashboard.episode = 'valid', 15., 1
          dashboard.producerSessionId = 'card'
          self.payloads['slcDashboardObservation'] = dashboard
          self.logMonoTime['slcDashboardObservation'] = stamp
          self.valid['slcDashboardObservation'] = self.alive['slcDashboardObservation'] = True

    sm = Scene()

    class Publisher:
      def send(self, service, event):
        if service == 'longitudinalPlan':
          plans.append(messaging.log_from_bytes(event.to_bytes()).longitudinalPlan)
        if service != 'slcState':
          return
        status = messaging.log_from_bytes(event.to_bytes())
        statuses.append(status)
        settings.refresh(sm.now)
        result = consumer.sample(status.slcState, now_ns=sm.now, now_boot_ns=BOOT + sm.now - MONO,
          sample_skew_ns=1000, message_ns=sm.now, receipt_ns=sm.now, drive_id=MONO,
          model_ns=sm.logMonoTime['modelV2'], car_state_ns=sm.logMonoTime['carState'], authority=not stale,
          stock_experimental=False, choice=ModeChoice.CEM, settings_fingerprint=settings_fingerprint(settings.current))
        results.append((sm.tick, result))
        sm.experimental = result.experimental

    def subscribe(services, **kwargs):
      subscriptions.extend(services)
      return sm

    environment = {'REPLAY': '1'} if replay else {}
    with patch.dict(os.environ, environment, clear=True), patch.object(plannerd, 'Params', return_value=params), \
         patch.object(plannerd, 'config_realtime_process'), patch.object(messaging, 'PubMaster', return_value=Publisher()), \
         patch.object(messaging, 'SubMaster', side_effect=subscribe), patch.object(messaging, 'sub_sock'), \
         patch.object(messaging, 'recv_one_or_none', return_value=None), \
         patch.object(plannerd, 'paired_clocks_ns', create=True,
                      side_effect=lambda: (sm.now, BOOT + sm.now - MONO, 1000) if clocks else None):
      with self.assertRaises(EndLoop):
        run_selected(params, lambda: self.fail('StarPilot planning policy selected'))
    return results, statuses, plans, subscriptions

  def test_cem_switches_upstream_policy_and_returns_to_chill(self):
    results, statuses, plans, subscriptions = self.run_loop()
    self.assertIn('deviceState', subscriptions)
    self.assertIn('longitudinalPlan', subscriptions)
    self.assertEqual(len(plans), 100)
    self.assertEqual(len(statuses), 100)
    self.assertTrue(any(tick < 50 and result.accepted and result.experimental for tick, result in results))
    self.assertTrue(any(tick > 70 and result.accepted and not result.experimental for tick, result in results))
    self.assertTrue(any(str(plan.longitudinalPlanSource) == 'e2e' for plan in plans[:50]))
    self.assertFalse(any(str(plan.longitudinalPlanSource) == 'e2e' for plan in plans[75:]))

  def test_stale_sources_never_produce_accepted_override(self):
    results, statuses, plans, _ = self.run_loop(stale=True)
    self.assertEqual(len(statuses), 100)
    self.assertFalse(any(result.accepted for _, result in results))
    self.assertFalse(any(str(plan.longitudinalPlanSource) == 'e2e' for plan in plans))

  def test_cem_and_speed_limit_share_one_status_per_model_frame(self):
    results, statuses, plans, subscriptions = self.run_loop(show_speed_limits=True)
    self.assertEqual(subscriptions.count('deviceState'), 1)
    self.assertEqual(len(statuses), len(plans))
    self.assertTrue(any(result.accepted and result.experimental for _, result in results))
    self.assertTrue(all(status.slcState.displayOnly and status.slcState.speedLimit == 15. and
                        status.slcState.conditionalMode.version == 1 for status in statuses))

  def test_safe_mode_retains_upstream_without_conditional_transport(self):
    results, statuses, plans, subscriptions = self.run_loop(safe=True)
    self.assertFalse(results or statuses)
    self.assertNotIn('starpilotRadarState', subscriptions)
    self.assertNotIn('starpilotNavigation', subscriptions)
    self.assertEqual(len(plans), 100)

  def test_missing_live_clock_does_not_publish_conditional_proposals(self):
    results, statuses, plans, _ = self.run_loop(clocks=False)
    self.assertFalse(results or statuses)
    self.assertEqual(len(plans), 100)

  def test_replay_never_invents_live_clock_permission(self):
    results, statuses, plans, _ = self.run_loop(replay=True)
    self.assertFalse(results or statuses)
    self.assertEqual(len(plans), 100)


if __name__ == '__main__':
  unittest.main()
