"""Numerical chronology only; no fixture grants current/native authority."""
from dataclasses import replace
import pytest
from openpilot.starpilot.flm.tests.test_tracking_source_context import owner, refresh
from openpilot.starpilot.flm.tests.test_live_tracking import episode

NS=1_000_000_000


def delayed_selection():
  o=owner()
  for index in (0,1):
    row=replace(episode(o,index,'ff_gain_right'),normalization_accel=0.5)
    now=(98+index)*NS
    refresh(o,now)
    o.observe_episode(row,now_ns=now)
  assert o.discovery is not None and o.offer_state is None
  assert o.discovery['selected_end_ns']==99*NS
  return o


@pytest.mark.parametrize('name',['ff_gain_left','ff_gain_right'])
def test_preselection_backlog_cannot_cancel_or_score_the_new_comparison(name):
  o=delayed_selection()
  prior=o.discovery.copy()
  row=replace(episode(o,2,name),normalization_accel=0.5)
  refresh(o,100*NS)
  assert not o.observe_episode(row,now_ns=100*NS)
  assert o.discovery==prior and o.comparison==[] and o.before==()
  assert o.offer_state is None and not any(o.accepted.values())
  # Three genuinely newer independently observed rows remain mandatory.
  compared=[]
  for index in range(3):
    row=replace(episode(o,20+index,'ff_gain_right',start=(101+index)*NS),normalization_accel=0.5)
    refresh(o,row.end_ns)
    compared.append(row)
    o.observe_episode(row,now_ns=row.end_ns)
    if index<2:
      assert o.offer_state is None
  assert o.offer_state is not None and tuple(o.before)==tuple(compared)
  assert all(r.start_ns>prior['selected_end_ns']for r in o.before)
  assert not any(o.accepted.values())


def test_genuinely_new_maneuver_still_releases_the_old_comparison():
  o=delayed_selection()
  row=replace(episode(o,30,'ff_gain_left',start=101*NS),normalization_accel=0.5)
  refresh(o,row.end_ns)
  assert not o.observe_episode(row,now_ns=row.end_ns)
  assert o.discovery is None and o.selector_hint is None and o.comparison==[]
  assert o.offer_state is None and not any(o.accepted.values())


def test_original_expiry_still_discards_the_study_despite_old_backlog():
  o=delayed_selection()
  now=o.discovery['expires_ns']+1
  row=replace(episode(o,2,'ff_gain_left'),normalization_accel=0.5)
  refresh(o,now)
  assert not o.observe_episode(row,now_ns=now)
  assert o.discovery is None and o.selector_hint is None and o.offer_state is None
  assert not any(o.accepted.values())
