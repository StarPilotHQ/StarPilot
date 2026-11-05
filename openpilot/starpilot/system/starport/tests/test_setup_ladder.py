"""The setup ladder moves only when a car that answered the handshake refuses it before any video; nothing else moves it."""
import json

import pytest

from openpilot.starpilot.system.starport import setup_ladder as sl
from openpilot.starpilot.system.starport.bootstrap import CHANNEL_24GHZ, CHANNEL_DUAL, REFUSAL_PATIENCE

CAR = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def ladder(tmp_path):
  state = {"boot": "boot-1"}
  instance = sl.SetupLadder(tmp_path / "setup_state.json", boot_id=lambda: state["boot"], clock=lambda: 1000.0)
  instance.state = state
  return instance


def refuse(ladder, strategy, **overrides):
  values = {"ladder_failure": True, "video_seen": False, "version_major": 4, "adapter": False}
  values.update(overrides)
  return ladder.failed(CAR, strategy, **values)


def test_the_first_strategy_is_exactly_what_ran_before(ladder):
  for base in (False, True):
    strategy = ladder.strategy(CAR, base)
    assert (strategy.rung, strategy.modern_reply, strategy.channel_type, strategy.patience, strategy.experiments) == (
      "standard", base, None, REFUSAL_PATIENCE, False)


@pytest.mark.parametrize("name,overrides", [
  ("a car that never answered the handshake (asleep, refused, local trouble)", {"ladder_failure": False}),
  ("anything after video was acknowledged in the session (car-off teardown)", {"video_seen": True}),
])
def test_failures_that_say_nothing_about_the_handshake_never_move_the_ladder(ladder, name, overrides):
  strategy = ladder.strategy(CAR, False)
  for _ in range(50):
    assert refuse(ladder, strategy, **overrides) is None
  assert ladder.strategy(CAR, False).rung == "standard" and not ladder.exhausted(CAR)
  assert not (ladder.path.exists() and json.loads(ladder.path.read_text())["cars"])  # nothing was even recorded


def test_a_silent_car_walks_the_rungs_in_order_and_each_is_a_knob_that_already_existed(ladder):
  seen = []
  for step in range(5):
    strategy = ladder.strategy(CAR, False)
    seen.append(strategy)
    assert refuse(ladder, strategy) == ("advanced" if step < 4 else "wrapped")  # the last rung ends the pass
  assert [s.rung for s in seen] == ["standard", "other_reply", "modern_dual", "modern_24ghz", "patient"]
  assert [s.modern_reply for s in seen] == [False, True, True, True, False]
  assert [s.channel_type for s in seen] == [None, None, CHANNEL_DUAL, CHANNEL_24GHZ, None]
  assert seen[4].patience == sl.LONG_PATIENCE and all(not s.experiments for s in seen)


def test_the_other_reply_is_relative_to_what_the_car_was_using(ladder):
  assert ladder.strategy(CAR, True).modern_reply is True
  refuse(ladder, ladder.strategy(CAR, True))
  assert ladder.strategy(CAR, True).modern_reply is False


def test_a_car_older_than_protocol_four_skips_the_phone_style_rungs(ladder):
  refuse(ladder, ladder.strategy(CAR, False), version_major=3)
  assert ladder.strategy(CAR, False).rung == "patient"
  assert ladder.snapshot(CAR)["steps"][1]["state"] == "skipped"


def test_the_adapter_experiments_run_last_and_only_for_adapters(ladder):
  for _ in range(5):
    refuse(ladder, ladder.strategy(CAR, False), adapter=True)
  strategy = ladder.strategy(CAR, False)
  assert strategy.rung == "experiments" and strategy.experiments
  assert refuse(ladder, strategy, adapter=True) == "wrapped"
  other = sl.SetupLadder(ladder.path.with_name("other.json"), boot_id=lambda: "b")
  for _ in range(5):
    refuse(other, other.strategy(CAR, False))
  assert other.strategy(CAR, False).rung == "standard"  # no adapter, no experiments rung: back to the start
  assert "experiments" not in [step["id"] for step in other.snapshot(CAR)["steps"]]


def test_three_passes_per_boot_then_it_stops_trying_new_things(ladder):
  results = []
  for _ in range(5 * sl.MAX_PASSES):
    results.append(refuse(ladder, ladder.strategy(CAR, False)))
  assert results.count("exhausted") == 1 and ladder.exhausted(CAR)
  assert ladder.strategy(CAR, False).rung == "standard"   # settles on today's behavior
  assert refuse(ladder, ladder.strategy(CAR, False)) is None
  assert ladder.snapshot(CAR)["exhausted"]


def test_the_next_ignition_gets_its_passes_back_and_resumes_at_the_first_untried_rung(ladder):
  for _ in range(2):
    refuse(ladder, ladder.strategy(CAR, False))
  ladder.state["boot"] = "boot-2"
  assert ladder.strategy(CAR, False).rung == "modern_dual"
  assert ladder.snapshot(CAR)["passes"] == 0
  for _ in range(5 * sl.MAX_PASSES):
    refuse(ladder, ladder.strategy(CAR, False))
  assert ladder.exhausted(CAR)
  ladder.state["boot"] = "boot-3"
  assert not ladder.exhausted(CAR) and ladder.snapshot(CAR)["passes"] == 0


def test_what_worked_is_used_first_next_time_even_for_a_different_remembered_reply(ladder):
  refuse(ladder, ladder.strategy(CAR, False))
  worked = ladder.strategy(CAR, False)
  assert worked.rung == "other_reply"
  ladder.worked(CAR, worked, t_ready_s=18.4)
  # The reply style is now remembered as the phone-style one; "the other reply" must not flip back to standard.
  again = ladder.strategy(CAR, True)
  assert (again.rung, again.modern_reply) == ("other_reply", True)
  snapshot = ladder.snapshot(CAR)
  assert snapshot["learned"] and snapshot["t_ready_s"] == 18.4 and snapshot["successes"] == 1
  assert [step["state"] for step in snapshot["steps"]][1] == "worked"


def test_a_learned_strategy_is_forgotten_only_after_two_handshake_failures(ladder):
  ladder.worked(CAR, sl.resolve("modern_dual", False))
  learned = ladder.strategy(CAR, False)
  assert refuse(ladder, learned) == "counted" and ladder.strategy(CAR, False).rung == "modern_dual"
  assert refuse(ladder, learned, ladder_failure=False) is None and refuse(ladder, learned, video_seen=True) is None
  assert refuse(ladder, learned) == "dropped"
  # Not back to the start: the strategy that just failed twice is not tried first, the next rung is.
  assert ladder.strategy(CAR, False).rung == "modern_24ghz" and not ladder.snapshot(CAR)["learned"]
  assert ladder.snapshot(CAR)["steps"][2]["state"] == "failed"


def test_state_survives_a_restart_and_a_damaged_file_means_a_fresh_start(tmp_path):
  path = tmp_path / "setup_state.json"
  first = sl.SetupLadder(path, boot_id=lambda: "b")
  refuse(first, first.strategy(CAR, False))
  first.worked(CAR, sl.resolve("patient", False), 12.0)
  second = sl.SetupLadder(path, boot_id=lambda: "b")
  assert second.strategy(CAR.lower(), False).rung == "patient" and path.stat().st_mode & 0o777 == 0o600
  for damage in ("not json", json.dumps({"version": 99, "cars": {}}), json.dumps({"version": 1, "cars": {CAR: {"working": {"rung": "x"}}}}),
                 json.dumps({"version": 1, "cars": [1]}), ""):
    path.write_text(damage)
    assert sl.SetupLadder(path, boot_id=lambda: "b").strategy(CAR, False).rung == "standard"


def test_a_read_only_disk_never_stops_a_connection(ladder):
  ladder.path = ladder.path / "blocked" / "x"   # parent is a missing path under a file below
  ladder.path.parent.parent.parent.mkdir(exist_ok=True)
  (ladder.path.parent.parent).write_text("a file where a directory should be")
  refuse(ladder, ladder.strategy(CAR, False))     # must not raise
  assert ladder.strategy(CAR, False).rung == "other_reply"


def test_only_sixteen_cars_are_kept_newest_first(tmp_path):
  clock = iter(range(1000, 2000))
  ladder = sl.SetupLadder(tmp_path / "s.json", boot_id=lambda: "b", clock=lambda: next(clock))
  for number in range(20):
    ladder.worked(f"AA:00:00:00:00:{number:02X}", sl.resolve("patient", False))
  reloaded = sl.SetupLadder(tmp_path / "s.json", boot_id=lambda: "b")
  assert len(reloaded.cars) == sl.MAX_CARS and "AA:00:00:00:00:13" in reloaded.cars and "AA:00:00:00:00:00" not in reloaded.cars


# ---- the supervisor's use of it -------------------------------------------------------------------------------------
import time as _time
from types import SimpleNamespace

from openpilot.starpilot.system.starport.tests.fake_head_unit import make_identity
from openpilot.starpilot.system.starport.tests.test_starport import make_supervisor

RECEIVER = "AA:BB:CC:DD:EE:01"  # the car make_supervisor selects


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
  return make_identity(tmp_path_factory.mktemp("identity"))


@pytest.fixture
def sup(identity, tmp_path, monkeypatch):
  supervisor, _ = make_supervisor(identity, tmp_path, monkeypatch, lambda *a, **k: None)
  return supervisor


def streamed(seconds):
  return SimpleNamespace(video_confirmed=True, video_confirmed_at=_time.monotonic() - seconds)


def test_the_supervisor_keeps_its_ladder_beside_the_config_so_tests_never_touch_real_state(sup):
  from openpilot.starpilot.system.starport import identity as identity_store
  assert sup._ladder.path == identity_store.CONFIG_PATH.with_name("setup_state.json")


def test_a_minute_of_video_makes_the_strategy_the_one_this_car_starts_with(sup):
  strategy = sl.resolve("other_reply", False)
  sup._strategy, sup._run_started = strategy, _time.monotonic() - 150
  sup._note_streamed(streamed(30))                      # too short to count, but video was seen
  assert sup._video_seen and not sup._ladder.snapshot(RECEIVER)["learned"]
  sup._note_streamed(streamed(90))
  snapshot = sup._ladder.snapshot(RECEIVER)
  assert snapshot["learned"] and snapshot["current"] == "other_reply" and 50 < snapshot["t_ready_s"] < 70


def test_a_session_that_never_got_video_learns_nothing(sup):
  sup._strategy = sl.resolve("patient", False)
  sup._note_streamed(SimpleNamespace(video_confirmed=False, video_confirmed_at=0.0))
  assert not sup._video_seen and not sup._ladder.snapshot(RECEIVER)["learned"]


def test_status_shows_the_ladder_the_switch_and_the_cars_to_choose_between(sup):
  status = sup.status()
  assert status["new_setup"] in (True, False) and status["car_candidates"] == []
  assert [step["id"] for step in status["setup_ladder"]["steps"]][:2] == ["standard", "other_reply"]
  assert status["setup_ladder"]["max_passes"] == sl.MAX_PASSES and not status["setup_ladder"]["learned"]


def test_forgetting_a_car_forgets_what_was_learned_about_it(sup):
  sup._ladder.worked(RECEIVER, sl.resolve("patient", False), 10.0)
  assert sup.forget_receiver(RECEIVER) is True
  assert not sup._ladder.snapshot(RECEIVER)["learned"] and RECEIVER not in sup._ladder.cars


def test_a_slow_car_gets_the_patience_it_once_needed_and_an_unknown_car_gets_no_opinion(ladder):
  assert ladder.ready_patience(CAR) == 0
  ladder.worked(CAR, sl.resolve("standard", False), 14.0)
  assert ladder.ready_patience(CAR) == 1
  ladder.worked(CAR, sl.resolve("standard", False), 70.0)
  assert ladder.ready_patience(CAR) == 4   # 70 s at 20 s per stage timeout
  ladder.worked(CAR, sl.resolve("standard", False), 3000.0)
  assert ladder.ready_patience(CAR) == 9   # capped


def test_a_learned_standard_strategy_that_stops_working_moves_on_to_the_other_reply(ladder):
  ladder.worked(CAR, sl.resolve("standard", False))
  learned = ladder.strategy(CAR, False)
  assert refuse(ladder, learned) == "counted"
  assert refuse(ladder, learned) == "dropped"
  assert ladder.strategy(CAR, False).rung == "other_reply"   # one failure fewer than starting over, as the dongle flip did


def test_the_passes_come_back_with_each_ignition_even_without_a_reboot(ladder):
  for _ in range(5 * sl.MAX_PASSES):
    refuse(ladder, ladder.strategy(CAR, False))
  assert ladder.exhausted(CAR) and ladder.snapshot(CAR)["passes"] == sl.MAX_PASSES
  ladder.begin_cycle(CAR)                         # the next drive starts: same boot, same comma
  assert not ladder.exhausted(CAR) and ladder.snapshot(CAR)["passes"] == 0
  assert refuse(ladder, ladder.strategy(CAR, False)) == "advanced"
  ladder.begin_cycle("AA:00:00:00:00:99")        # an unknown car is a no-op
  assert "AA:00:00:00:00:99" not in ladder.cars


def test_short_sessions_keep_the_same_other_reply_until_it_is_learned(sup, monkeypatch):
  from openpilot.starpilot.system.starport import supervisor as module
  sup._ladder.failed(RECEIVER, sup._ladder.strategy(RECEIVER, False), ladder_failure=True,
                    video_seen=False, version_major=4, adapter=False)
  replies = []

  def bootstrap(*args, **kwargs):
    replies.append(kwargs["modern_reply"])
    return SimpleNamespace(version_major=4, modern_reply=kwargs["modern_reply"], join_is_cancelled=lambda: False,
                           run=lambda *a, **k: SimpleNamespace(head_unit={}), keepalive=lambda stop: None)

  monkeypatch.setattr(module, "WirelessBootstrap", bootstrap)
  monkeypatch.setattr(sup, "_wait", lambda delay: None)
  monkeypatch.setattr(sup, "_connect_rfcomm", lambda *args: None)

  def short_session(*args):
    raise RuntimeError("TCP disconnected before sustained video")

  monkeypatch.setattr(sup, "_project", short_session)
  for _ in range(2):
    with pytest.raises(RuntimeError, match="TCP disconnected"):
      sup._attempt(SimpleNamespace())
  assert replies == [True, True] and not sup.config["modern_reply_cache"]
  monkeypatch.setattr(sup, "_project", lambda *args: sup._ladder.worked(RECEIVER, sup._strategy))
  sup._attempt(SimpleNamespace())
  assert sup.config["modern_reply_cache"] == {RECEIVER: True}


def test_the_phone_identity_rung_is_only_for_unofficial_dongles_on_protocol_four(ladder):
  seen = []
  for _ in range(7):
    strategy = ladder.strategy(CAR, False)
    seen.append(strategy.rung)
    refuse(ladder, strategy, adapter=True, unofficial=True)
  assert seen == ["standard", "other_reply", "modern_dual", "modern_24ghz", "phone_identity", "patient", "experiments"]
  assert ladder.strategy(CAR, False).rung == "standard"
  rung = sl.resolve("phone_identity", False)
  assert rung.phone_identity and rung.modern_reply and rung.channel_type is None and rung.patience == sl.REFUSAL_PATIENCE
  assert not sl.applicable("phone_identity", 4, True) and not sl.applicable("phone_identity", 3, True, True)
  assert "phone_identity" not in [step["id"] for step in ladder.snapshot(CAR, adapter=True)["steps"]]
  assert "phone_identity" in [step["id"] for step in ladder.snapshot(CAR, adapter=True, unofficial=True)["steps"]]


def test_a_learned_phone_identity_strategy_survives_a_restart(ladder):
  strategy = sl.resolve("phone_identity", False)
  ladder.worked(CAR, strategy)
  again = sl.SetupLadder(ladder.path, boot_id=lambda: "boot-1").strategy(CAR, False)
  assert again.rung == "phone_identity" and again.phone_identity


def reject(ladder, strategy, **overrides):
  return refuse(ladder, strategy, ladder_failure=False, rejected=True, **overrides)


def test_a_rejected_rung_goes_straight_back_to_the_last_strategy_that_streamed(ladder):
  ladder.worked(CAR, sl.resolve("standard", False))
  learned = ladder.strategy(CAR, False)
  assert refuse(ladder, learned) == "counted" and refuse(ladder, learned) == "dropped"   # two failures, same drive
  assert ladder.strategy(CAR, False).rung == "other_reply"
  assert reject(ladder, ladder.strategy(CAR, False)) == "reverted"
  back = ladder.strategy(CAR, False)
  assert (back.rung, back.modern_reply) == ("standard", False)
  snapshot = ladder.snapshot(CAR)
  assert snapshot["learned"] and snapshot["steps"][1]["state"] == "failed"


def test_a_rejected_rung_is_skipped_from_then_on(ladder):
  ladder.worked(CAR, sl.resolve("standard", False))
  for _ in range(2):
    refuse(ladder, ladder.strategy(CAR, False))
  reject(ladder, ladder.strategy(CAR, False))                     # other_reply: hung up on
  for _ in range(2):
    refuse(ladder, ladder.strategy(CAR, False))                   # standard drops again
  assert ladder.strategy(CAR, False).rung == "modern_dual"        # not other_reply a second time
  assert ladder.snapshot(CAR)["steps"][1]["state"] == "failed"


def test_the_rung_that_streamed_is_never_marked_rejected(ladder):
  ladder.worked(CAR, sl.resolve("standard", False))
  learned = ladder.strategy(CAR, False)
  assert reject(ladder, learned) == "counted"                     # an ordinary failure for the known-good rung
  assert reject(ladder, learned) == "dropped"
  assert ladder.cars[CAR]["rejected"] == [] and ladder.strategy(CAR, False).rung == "other_reply"


def test_a_car_that_never_streamed_moves_on_at_once_when_it_hangs_up(ladder):
  assert reject(ladder, ladder.strategy(CAR, False)) == "advanced"
  assert ladder.strategy(CAR, False).rung == "other_reply"
  assert reject(ladder, ladder.strategy(CAR, False)) == "advanced"
  for _ in range(3):
    refuse(ladder, ladder.strategy(CAR, False))                   # modern_dual, modern_24ghz, patient: wraps
  assert ladder.strategy(CAR, False).rung == "modern_dual"        # the new pass starts past the two it hung up on


def test_each_drive_starts_with_the_last_strategy_that_streamed(ladder):
  ladder.worked(CAR, sl.resolve("standard", False), 15.0)
  for _ in range(2):
    refuse(ladder, ladder.strategy(CAR, False))
  refuse(ladder, ladder.strategy(CAR, False))                     # other_reply timed out (not hung up on): modern_dual
  assert ladder.strategy(CAR, False).rung == "modern_dual" and not ladder.snapshot(CAR)["learned"]
  ladder.begin_cycle(CAR)
  assert ladder.strategy(CAR, False).rung == "standard" and ladder.snapshot(CAR)["learned"]
  ladder.state["boot"] = "boot-2"                                 # a reboot alone keeps the drive's progress
  assert ladder.strategy(CAR, False).rung == "standard"


def test_last_good_and_rejections_survive_a_restart_and_older_files_get_a_last_good(tmp_path):
  path = tmp_path / "setup_state.json"
  first = sl.SetupLadder(path, boot_id=lambda: "b")
  first.worked(CAR, sl.resolve("standard", False))
  for _ in range(2):
    refuse(first, first.strategy(CAR, False))
  second = sl.SetupLadder(path, boot_id=lambda: "b")
  assert second.strategy(CAR, False).rung == "other_reply"
  assert reject(second, second.strategy(CAR, False)) == "reverted"
  assert sl.SetupLadder(path, boot_id=lambda: "b").cars[CAR]["rejected"] == ["other_reply"]
  old = {"version": 1, "cars": {CAR: {"rung": "patient", "working": sl.resolve("patient", False).as_dict()}}}
  path.write_text(json.dumps(old))
  assert sl.SetupLadder(path, boot_id=lambda: "b").cars[CAR]["last_good"]["rung"] == "patient"


def test_resetting_starts_over_at_standard(ladder):
  ladder.worked(CAR, sl.resolve("standard", False))
  for _ in range(2):
    refuse(ladder, ladder.strategy(CAR, False))
  assert ladder.reset(CAR) is True and ladder.reset(CAR) is False
  assert ladder.strategy(CAR, False).rung == "standard" and ladder.cars[CAR]["last_good"] is None


def test_bootstrap_tells_a_busy_car_from_one_that_hangs_up_on_the_reply():
  from openpilot.starpilot.system.starport import bootstrap as bs
  boot = bs.WirelessBootstrap(None, lambda *a, **k: None)
  assert not boot.only_not_ready()                                # silent: still a real timeout
  boot.refusals = [bs.STATUS_NOT_READY] * 11
  assert boot.only_not_ready()
  boot.refusals.append(-4)
  assert not boot.only_not_ready()
  boot.refusals, boot.version_major, boot.replied_at = [], 4, _time.monotonic()
  assert boot.hung_up_on_reply(ConnectionResetError(104, "Connection reset by peer"))
  assert boot.hung_up_on_reply(bs.BootstrapClosed("rfcomm", "head unit closed the RFCOMM connection"))
  assert not boot.hung_up_on_reply(bs.BootstrapTimeout("wifi_start", "slow"))
  boot.replied_at = _time.monotonic() - bs.REPLY_REJECT_SECONDS - 1
  assert not boot.hung_up_on_reply(ConnectionResetError())         # dropped later: not about the reply
  boot.replied_at, boot.refusals = _time.monotonic(), [bs.STATUS_NOT_READY]
  assert not boot.hung_up_on_reply(ConnectionResetError())         # it was already talking Wi-Fi setup


def _failing_bootstrap(monkeypatch, sup, error, refusals=()):
  from openpilot.starpilot.system.starport import bootstrap as bs
  from openpilot.starpilot.system.starport import supervisor as module
  replies = []

  class Boot(bs.WirelessBootstrap):
    def __init__(self, *args, **kwargs):
      super().__init__(None, lambda *a, **k: None, modern_reply=kwargs["modern_reply"])
      replies.append(kwargs["modern_reply"])

    def run(self, *args, **kwargs):
      self.version_major, self.replied_at, self.refusals = 4, _time.monotonic(), list(refusals)
      raise error(self)

  monkeypatch.setattr(module, "WirelessBootstrap", Boot)
  monkeypatch.setattr(sup, "_wait", lambda delay: None)
  monkeypatch.setattr(sup, "_connect_rfcomm", lambda *args: None)
  return replies


def test_a_car_that_only_says_not_ready_keeps_its_learned_handshake(sup, monkeypatch):
  from openpilot.starpilot.system.starport.bootstrap import STATUS_NOT_READY, BootstrapTimeout
  sup._ladder.worked(RECEIVER, sl.resolve("standard", False), 18.0)
  _failing_bootstrap(monkeypatch, sup, lambda boot: BootstrapTimeout("wifi_start", "head unit did not answer in time"),
                     refusals=[STATUS_NOT_READY] * 11)
  for _ in range(4):
    with pytest.raises(BootstrapTimeout):
      sup._attempt(SimpleNamespace())
  entry = sup._ladder.cars[RECEIVER]
  assert entry["working"]["rung"] == "standard" and entry["working_failures"] == 0


def test_the_supervisor_reverts_when_the_car_hangs_up_on_the_other_reply(sup, monkeypatch):
  sup._ladder.worked(RECEIVER, sl.resolve("standard", False), 18.0)
  for _ in range(2):                                              # a silent car really did drop it
    sup._ladder.failed(RECEIVER, sup._ladder.strategy(RECEIVER, False), ladder_failure=True, video_seen=False, version_major=4,
                       adapter=False)
  replies = _failing_bootstrap(monkeypatch, sup, lambda boot: ConnectionResetError(104, "Connection reset by peer"))
  for _ in range(2):
    with pytest.raises(ConnectionResetError):
      sup._attempt(SimpleNamespace())
  assert replies == [True, False]                                 # one hang-up on the phone-style reply, then standard again
  assert sup._ladder.snapshot(RECEIVER)["learned"]


def test_reset_handshake_keeps_the_pairing(sup):
  sup._ladder.worked(RECEIVER, sl.resolve("standard", False))
  for _ in range(2):
    sup._ladder.failed(RECEIVER, sup._ladder.strategy(RECEIVER, False), ladder_failure=True, video_seen=False, version_major=4,
                       adapter=False)
  assert sup.reset_handshake() is True and sup.reset_handshake() is False
  assert sup.config["receiver_address"].upper() == RECEIVER and sup._ladder.strategy(RECEIVER, False).rung == "standard"
  # Done while idle: nothing is written yet, and the next session's log gets it as its first ladder event.
  assert not any(record["event"] == "ladder_reset" for record in sup.log.recent)
  note = sup._reset_note
  assert (note["from_rung"], note["last_good"], note["rejected"]) == ("other_reply", "standard", []) and note["reset_at"]
  sup._flush_reset_note()
  logged = [record for record in sup.log.recent if record["event"] == "ladder_reset"]
  assert len(logged) == 1 and logged[0]["from_rung"] == "other_reply" and sup._reset_note is None
  sup._flush_reset_note()
  assert sum(record["event"] == "ladder_reset" for record in sup.log.recent) == 1   # written once


def test_reset_handshake_forgets_the_remembered_reply(sup):
  sup.config["modern_reply_cache"] = {RECEIVER: True}
  sup._modern_reply[RECEIVER] = True
  assert sup.reset_handshake() is True
  assert RECEIVER not in sup.config["modern_reply_cache"] and RECEIVER not in sup._modern_reply


def test_a_reset_during_a_session_is_written_at_once(sup, monkeypatch):
  sup._ladder.worked(RECEIVER, sl.resolve("standard", False))
  monkeypatch.setattr(sup, "_session_alive", lambda: True)
  assert sup.reset_handshake() is True
  assert [record["from_rung"] for record in sup.log.recent if record["event"] == "ladder_reset"] == ["standard"]
  assert sup._reset_note is None


def test_only_galaxy_can_reset_the_handshake():
  from openpilot.starpilot.system.starport import daemon
  assert "reset_handshake" in daemon.AUTHENTICATED_COMMANDS
