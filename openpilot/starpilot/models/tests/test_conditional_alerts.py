from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import AlertStatus, AlertSize, Priority, VisualAlert, AudibleAlert, Alert
from openpilot.starpilot.models.conditional_alerts import conditional_model_alert, ALERT_TYPES
from openpilot.starpilot.models.conditional_runtime import ConditionalModelGate


@pytest.mark.parametrize('phase', ['waiting', 'switching', 'recovering'])
def test_banner_describes_handoff_even_without_engagement_attempt(phase):
  alert = conditional_model_alert(NS(phase=phase, error=''), holding=True)
  assert alert.alert_text_1 == 'Switching conditional models'
  assert alert.alert_size == AlertSize.small
  assert alert.priority == Priority.LOWEST and alert.audible_alert == AudibleAlert.none


def test_errors_and_stalls_are_visible_but_never_claim_ready():
  error = conditional_model_alert(NS(phase='ready', error='Candidate inference failed; previous model restored'), holding=False)
  assert error.alert_text_1 == 'Conditional Model unavailable'
  assert 'previous model restored' in error.alert_text_2
  stalled = conditional_model_alert(None, holding=True, stalled=True)
  assert stalled.alert_text_1 == 'Conditional model switch stalled'
  assert 'Engagement blocked' in stalled.alert_text_2
  assert conditional_model_alert(None, holding=False) is None
  assert conditional_model_alert(NS(phase='ready', error=''), holding=False) is None


@pytest.mark.parametrize('priority', [Priority.LOWEST, Priority.LOW, Priority.HIGHEST])
def test_road_notice_does_not_replace_equal_or_higher_priority_vehicle_alert(priority):
  gate = ConditionalModelGate.__new__(ConditionalModelGate)
  gate.alert = Mock(return_value=conditional_model_alert(None, holding=True))
  native = Alert('TAKE CONTROL', '', AlertStatus.critical, AlertSize.full, priority, VisualAlert.steerRequired, AudibleAlert.warningImmediate, 1.)
  alerts, manager = [native], AlertManager()
  manager.alerts.update({key: object() for key in ALERT_TYPES})
  gate.update_alerts(alerts, manager)
  assert alerts == [native] and not manager.alerts
  manager.add_many(0, alerts)
  manager.process_alerts(0, set())
  assert manager.current_alert is native


def test_completion_removes_only_conditional_model_notices():
  gate = ConditionalModelGate.__new__(ConditionalModelGate)
  gate.alert = Mock(return_value=None)
  manager = NS(alerts={ALERT_TYPES[0]: object(), 'vehicle-alert': object()})
  alerts = []
  gate.update_alerts(alerts, manager)
  assert not alerts and set(manager.alerts) == {'vehicle-alert'}


def test_retained_native_alert_remains_visible_without_road_banner():
  gate = ConditionalModelGate.__new__(ConditionalModelGate)
  gate.alert = Mock(return_value=conditional_model_alert(None, holding=True))
  manager = AlertManager()
  native = Alert('Vehicle warning', '', AlertStatus.userPrompt, AlertSize.mid, Priority.LOW,
                 VisualAlert.none, AudibleAlert.none, 1.)
  native.alert_type = 'native/warning'
  manager.add_many(0, [native])
  alerts = []
  gate.update_alerts(alerts, manager, frame=1)
  assert not alerts and 'native/warning' in manager.alerts
