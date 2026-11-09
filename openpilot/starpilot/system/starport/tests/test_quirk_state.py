import json

from openpilot.starpilot.system.starport import quirk_state as qs
from openpilot.starpilot.system.starport.quirk_state import QuirkState, SessionOutcome

FP = {"car_make": "A", "car_model": "B"}
EARLY = SessionOutcome(video_seen=True, streamed_s=20, first_frame_s=3, ended_by_car=True)
GOOD = SessionOutcome(video_seen=True, streamed_s=200, first_frame_s=3)


def state(tmp_path):
  return QuirkState(tmp_path / "quirk_state.json")


def run(q, key, outcome, ladder=3):
  return q.record(key, FP, outcome, rule_id="r", ladder_len=ladder, trial_generation=q.trial(key, "r")["generation"])


def test_classify_cases():
  frozen = {"sessions_with_video": 2, "first_frame_s": [3, 3, 3]}
  assert qs.classify(EARLY, frozen) == qs.FAILED
  assert qs.classify(GOOD, frozen) == qs.SUCCESS
  assert qs.classify(SessionOutcome(video_seen=True, streamed_s=30, user_stopped=True), frozen) == qs.INCONCLUSIVE
  assert qs.classify(SessionOutcome(waiting_for_driver=True), frozen) == qs.INCONCLUSIVE
  assert qs.classify(SessionOutcome(video_seen=True, streamed_s=30), frozen) == qs.INCONCLUSIVE
  assert qs.classify(SessionOutcome(pairing_prompt=True), frozen) == qs.REGRESSION
  assert qs.classify(SessionOutcome(hangup=True), frozen) == qs.REGRESSION
  assert qs.classify(SessionOutcome(video_seen=True, streamed_s=200, first_frame_s=40), frozen) == qs.REGRESSION


def test_ladder_moves_up_only_on_repeated_failure_and_confirms_on_success(tmp_path):
  q = state(tmp_path)
  key = "bt:AA"
  assert q.begin(key, FP, "r")["level"] == 1
  run(q, key, EARLY)
  assert q.trial(key, "r")["level"] == 1
  run(q, key, EARLY)
  assert q.trial(key, "r")["level"] == 2
  run(q, key, GOOD)
  run(q, key, GOOD)
  assert q.trial(key, "r")["state"] == qs.CONFIRMED


def test_inconclusive_sessions_never_move_the_ladder(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  for _ in range(6):
    run(q, "bt:AA", SessionOutcome(video_seen=True, streamed_s=10, user_stopped=True))
  assert q.trial("bt:AA", "r")["level"] == 1 and q.trial("bt:AA", "r")["state"] == qs.TRYING


def test_exhausted_ladder_turns_off(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  for _ in range(6):
    run(q, "bt:AA", EARLY, ladder=2)
  assert q.trial("bt:AA", "r")["state"] == qs.OFF


def test_two_regressions_revert(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  run(q, "bt:AA", SessionOutcome(pairing_prompt=True))
  assert q.trial("bt:AA", "r")["state"] == qs.TRYING
  run(q, "bt:AA", SessionOutcome(pairing_prompt=True))
  assert q.trial("bt:AA", "r")["state"] == qs.OFF


def test_baseline_is_frozen_when_trial_starts(tmp_path):
  q = state(tmp_path)
  q.record("bt:AA", FP, SessionOutcome(video_seen=True, streamed_s=10, ended_by_car=True), now=1)
  q.begin("bt:AA", FP, "r")
  run(q, "bt:AA", EARLY)
  trial = q.trial("bt:AA", "r")
  assert trial["frozen"]["sessions"] == 1
  assert q.entry("bt:AA", FP)["baseline"]["sessions"] == 1


def test_reset_discards_in_flight_result_and_clears_entry(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  generation = q.trial("bt:AA", "r")["generation"]
  q.reset("bt:AA")
  assert q.trial("bt:AA", "r") is None
  assert q.record("bt:AA", FP, EARLY, rule_id="r", ladder_len=3, trial_generation=generation) == qs.INCONCLUSIVE
  assert "bt:AA" not in q.cars


def test_changed_fingerprint_starts_car_over(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  assert q.entry("bt:AA", {"car_make": "Other"})["trials"] == {}


def test_wired_state_is_never_written(tmp_path):
  q = state(tmp_path)
  full = dict.fromkeys(("car_make", "car_model", "car_year", "head_unit_make", "head_unit_model", "head_unit_software_version"), "x")
  key = QuirkState.wired_key(full)
  assert key.startswith("usb:") and QuirkState.wired_key({"car_make": "x"}) == ""
  q.begin(key, full, "r")
  q.save()
  assert key not in json.loads((tmp_path / "quirk_state.json").read_text()).get("cars", {})
  assert QuirkState(tmp_path / "quirk_state.json").trial(key, "r") is None


def test_round_trip_and_corrupt_file(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  q.dismiss("bt:AA", "r")
  q.save()
  again = state(tmp_path)
  assert again.trial("bt:AA", "r")["level"] == 1 and again.dismissed("bt:AA", "r")
  (tmp_path / "quirk_state.json").write_text("{nope")
  assert state(tmp_path).cars == {}


def test_forget_offline_removes_entry(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  q.save()
  assert qs.forget_offline("aa", tmp_path / "quirk_state.json")
  assert state(tmp_path).cars == {}
  assert not qs.forget_offline("aa", tmp_path / "quirk_state.json")


def test_resetting_one_car_keeps_the_other_cars_trials(tmp_path):
  q = state(tmp_path)
  q.begin("bt:AA", FP, "r")
  q.begin("bt:BB", FP, "r")
  run(q, "bt:BB", EARLY)
  run(q, "bt:BB", EARLY)
  q.reset("bt:AA")
  assert q.trial("bt:AA", "r") is None
  assert q.trial("bt:BB", "r")["level"] == 2


def test_oversized_or_linked_state_file_is_ignored(tmp_path):
  path = tmp_path / "quirk_state.json"
  path.write_text(json.dumps({"version": qs.STATE_VERSION, "cars": {}, "pad": "x" * qs.MAX_STATE_BYTES}))
  assert state(tmp_path).cars == {}
  real = tmp_path / "real.json"
  q = QuirkState(real)
  q.begin("bt:AA", FP, "r")
  q.save()
  path.unlink()
  path.symlink_to(real)
  assert state(tmp_path).cars == {}
