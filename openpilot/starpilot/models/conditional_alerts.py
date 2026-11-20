"""Conditional Model notices use the existing alert manager and native UI surfaces."""
from openpilot.selfdrive.selfdrived.events import Alert, AlertStatus, AlertSize, Priority, VisualAlert, AudibleAlert, ET

ALERT_TYPES = ('conditionalModelSwitch/permanent', 'conditionalModelFailure/permanent')


def conditional_model_alert(state, *, holding, stalled=False):
  error = str(state.error) if state is not None else ''
  switching = holding or state is not None and str(state.phase) in ('waiting', 'switching', 'recovering')
  if not error and not switching:
    return None
  failed = bool(error or stalled)
  title = 'Conditional model switch stalled' if stalled else 'Conditional Model unavailable' if error else 'Switching conditional models'
  detail = ('Engagement blocked; waiting for model recovery' if stalled else
            error if error else 'Wait for completion before engaging')
  alert = Alert(title, detail, AlertStatus.userPrompt if failed else AlertStatus.normal,
                AlertSize.mid if failed else AlertSize.small, Priority.LOW if failed else Priority.LOWEST,
                VisualAlert.none, AudibleAlert.none, .1)
  alert.alert_type = ALERT_TYPES[1 if failed else 0]
  alert.event_type = ET.PERMANENT
  return alert
