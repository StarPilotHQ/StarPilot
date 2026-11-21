from openpilot.starpilot.system.starport import radio_watch, supervisor


def test_supervisor_preserves_public_radio_watch_imports():
  assert supervisor.SharedRadioWatch is radio_watch.SharedRadioWatch
  assert supervisor.RADIO_CHECK_INTERVAL == radio_watch.RADIO_CHECK_INTERVAL == 0.5
  assert supervisor.RADIO_CHANGE_WINDOW == radio_watch.RADIO_CHANGE_WINDOW == 15.0
