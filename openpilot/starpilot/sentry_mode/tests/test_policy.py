"""Frozen numeric motion behavior and fail-closed parked policy boundaries."""

from unittest.mock import Mock

import pytest

from openpilot.starpilot.sentry_mode.policy import (
  Inputs, MotionSample, SentryPolicy, Settings,
)


def active(sample: MotionSample | None = None) -> Inputs:
  return Inputs(enabled=True, offroad=True, voltage_ok=True, device_fresh=True, ignition_off=True, sample=sample)


def motion(now: float, z: float) -> MotionSample:
  return MotionSample(now, (0.0, 0.0, z))


def fresh_through(policy: SentryPolicy, start: float, end: float, z: float = 9.8):
  result = None
  for tenth in range(round(start * 10) + 1, round(end * 10) + 1):
    now = tenth / 10
    result = policy.update(now, active(motion(now, z)))
  return result


def armed(policy: SentryPolicy) -> None:
  assert policy.update(0.0, active(motion(0.0, 9.8))).state == "arming"
  assert fresh_through(policy, 0.0, 89.9).state == "arming"
  assert policy.update(90.0, active(motion(90.0, 9.8))).state == "armed"


def test_frozen_arming_warning_and_alarm_numeric_sequence():
  policy = SentryPolicy(Settings(sensitivity=0.1, warning_time_seconds=0.3))
  armed(policy)
  assert policy.settings.warning_trigger_count == 3
  assert policy.update(90.1, active(motion(90.1, 10.0))).event is None
  assert policy.update(90.2, active(motion(90.2, 9.8))).event is None
  assert policy.update(90.3, active(motion(90.3, 10.0))).event == "warning"
  assert policy.update(90.4, active(motion(90.4, 9.8))).event is None
  # The frozen detector requires strictly more than 25 changes and 30 s from
  # the first change. Feed 10 Hz fresh stable samples while that time elapses.
  for index in range(5, 26):
    now = 90.0 + index * 0.1
    assert policy.update(now, active(motion(now, 10.0 if index % 2 else 9.8))).event is None
  assert policy.trigger_count == 25
  assert fresh_through(policy, 92.5, 120.0, 10.0).event is None
  assert policy.update(120.1, active(motion(120.1, 9.8))).event == "alarm"
  assert policy.update(120.2, active(motion(120.2, 10.0))).event is None


def test_duplicate_stale_invalid_and_out_of_order_samples_do_not_accumulate():
  policy = SentryPolicy(Settings(sensitivity=0.1, warning_time_seconds=0.2))
  armed(policy)
  assert policy.update(90.1, active(motion(90.1, 10.0))).event is None
  assert policy.update(90.2, active(motion(90.1, 9.8))).state == "sensor_unavailable"
  assert policy.trigger_count == 0
  assert policy.update(90.3, active(motion(90.1, 9.8))).state == "sensor_unavailable"
  assert policy.update(90.4, active(motion(90.4, 9.8))).state == "arming"
  assert policy.update(90.5, active(motion(90.3, 10.0))).state == "sensor_unavailable"
  assert policy.update(90.6, active(motion(90.6, 9.8))).state == "arming"


def test_missing_sensor_during_arming_and_after_armed_requires_full_rearm():
  policy = SentryPolicy()
  assert policy.update(0.0, active()).state == "sensor_unavailable"
  assert policy.update(0.1, active(motion(0.1, 9.8))).state == "arming"
  assert fresh_through(policy, 0.1, 90.0).state == "arming"
  assert policy.update(90.1, active()).state == "sensor_unavailable"
  assert policy.update(90.2, active(motion(90.2, 9.8))).state == "arming"
  assert fresh_through(policy, 90.2, 180.3).state == "armed"
  assert policy.update(180.4, active()).state == "sensor_unavailable"
  assert policy.update(180.5, active(motion(180.5, 9.8))).state == "arming"


def test_pending_messages_preserve_countdown_but_never_count_as_motion():
  policy = SentryPolicy()
  pending = Inputs(True, True, True, True, True, sample_pending=True)
  assert policy.update(0.0, pending).state == "sensor_unavailable"
  for tenth in range(1, 905):
    now = tenth / 10
    decision = policy.update(now, active(motion(now, 9.8)) if tenth % 2 else pending)
    assert decision.state == ("arming" if now < 90.1 else "armed")
    assert decision.event is None
  assert policy.trigger_count == 0
  # The previous sample is 90.3; repeated polls cannot renew its freshness.
  assert policy.update(90.7, pending).state == "armed"
  assert policy.update(90.9, pending).state == "sensor_unavailable"
  assert policy.update(91.0, active(motion(91.0, 9.8))).seconds_remaining == 90


def test_disabled_onroad_low_voltage_and_lost_device_rearm_for_full_90_seconds():
  for stopped, expected in ((Inputs(False, True, True, True, True), "disabled"),
                            (Inputs(True, False, True, True, True), "disabled_onroad"),
                            (Inputs(True, True, False, True, True), "low_voltage"),
                            (Inputs(True, True, True, False, True), "unavailable"),
                            (Inputs(True, True, True, True, False), "disabled_ignition")):
    policy = SentryPolicy()
    armed(policy)
    assert policy.update(90.1, active(motion(90.1, 10.0))).state == "armed"
    assert policy.update(90.2, stopped).state == expected
    assert policy.update(90.3, active(motion(90.3, 9.8))).state == "arming"
    assert fresh_through(policy, 90.3, 180.2).state == "arming"
    assert fresh_through(policy, 180.2, 180.4).state == "armed"
    assert policy.trigger_count == 0


def test_monotonic_reversal_and_motion_window_expiry_fail_closed():
  policy = SentryPolicy(Settings(sensitivity=0.1, warning_time_seconds=0.2))
  armed(policy)
  policy.update(90.1, active(motion(90.1, 10.0)))
  assert policy.update(60.0, active(motion(60.0, 9.8))).state == "unavailable"
  assert policy.update(60.1, active(motion(60.1, 9.8))).state == "arming"
  assert fresh_through(policy, 60.1, 150.0).state == "arming"
  assert fresh_through(policy, 150.0, 150.2).state == "armed"
  assert policy.update(150.3, active(motion(150.3, 10.0))).event is None
  assert fresh_through(policy, 150.3, 210.2, 10.0).event is None
  assert policy.update(210.3, active(motion(210.3, 9.8))).event is None
  assert policy.trigger_count == 1


def test_two_samples_90_seconds_apart_never_arm():
  policy = SentryPolicy()
  assert policy.update(0.0, active(motion(0.0, 9.8))).state == "arming"
  assert policy.update(90.0, active(motion(90.0, 9.8))).state == "unavailable"
  assert policy.update(90.1, active(motion(90.1, 9.8))).state == "arming"
  assert fresh_through(policy, 90.1, 180.2).state == "armed"


@pytest.mark.parametrize("settings", [
  {"sensitivity": 0.004}, {"sensitivity": float("nan")},
  {"sensitivity": True}, {"sensitivity": "0.04"},
  {"warning_time_seconds": 10.1}, {"warning_time_seconds": float("inf")},
  {"warning_time_seconds": False},
])
def test_settings_reject_invalid_values(settings):
  with pytest.raises(ValueError):
    Settings(**settings)


@pytest.mark.parametrize("sample", [
  (float("nan"), (0.0, 0.0, 9.8)),
  (1.0, (0.0, float("inf"), 9.8)),
  (1.0, (1e308, 0.0, 9.8)),
  (1.0, (True, 0.0, 9.8)),
  (1.0, (0.0, 9.8)),
])
def test_sample_requires_finite_time_and_three_axes(sample):
  with pytest.raises(ValueError):
    MotionSample(*sample)


def test_authority_inputs_reject_truthy_strings():
  with pytest.raises(ValueError):
    Mock(wraps=Inputs)("off", True, True, True, True)
