from openpilot.selfdrive.selfdrived.events import AudibleAlert, ET, EventName, Events


def test_initialization_is_visible_without_an_enable_request():
  events = Events()
  events.add(EventName.selfdriveInitializing)
  assert events.contains(ET.NO_ENTRY)
  assert not events.contains(ET.ENABLE)
  alerts = events.create_alerts([ET.PERMANENT])
  assert len(alerts) == 1
  assert alerts[0].alert_text_1 == "System Initializing"
  assert alerts[0].audible_alert == AudibleAlert.none
  assert alerts[0].alert_size != 0
  events.clear()
  assert events.create_alerts([ET.PERMANENT]) == []


def test_initialization_does_not_change_engagement_refusal():
  events = Events()
  events.add(EventName.selfdriveInitializing)
  alerts = events.create_alerts([ET.NO_ENTRY])
  assert len(alerts) == 1
  assert alerts[0].alert_text_1 == "openpilot Unavailable"
  assert alerts[0].alert_text_2 == "System Initializing"
  assert alerts[0].audible_alert == AudibleAlert.refuse
