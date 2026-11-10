import pytest

from openpilot.starpilot.navigation.notification import notification_instruction
from openpilot.starpilot.navigation.phone import select_navigation

NOW = 100_000_000_000


def guidance(**patch):
  return {'observed': NOW, 'received': NOW, 'active': True, 'instruction': 'Take exit 12B toward Airport',
          'maneuverType': 'off ramp', 'maneuverModifier': 'right', 'distanceMeters': 120,
          'source': 'googleMaps', 'session': 'test', 'sequence': 1, **patch}


@pytest.mark.parametrize('kind,side', [('turn', 'left'), ('turn', 'right'), ('turn', 'sharpLeft'),
    ('turn', 'slightRight'), ('turn', 'uturn'), ('off ramp', 'right'), ('on ramp', 'left'),
    ('fork', 'slightLeft'), ('fork', 'slightRight'), ('merge', 'right'), ('roundabout', ''),
    ('continue', 'straight'), ('arrive', ''), ('', '')])
def test_available_notification_guidance_needs_no_route_or_gps(kind, side):
  phone = guidance(maneuverType=kind, maneuverModifier=side)
  diagnostics = {}
  selected = select_navigation({'status': 'noDestination'}, phone, now_ns=NOW, drive_id=NOW + 1,
                               diagnostics=diagnostics)
  assert selected['instruction'] == {'text': phone['instruction'], 'maneuverType': kind,
                                     'maneuverModifier': side, 'distanceMeters': 120}
  assert diagnostics['reason'] == 'notificationGuidance'
  assert selected['source'] == 'phone:googleMaps'
  assert selected['route'] == [] and selected['nextManeuver'] == {}
  assert not selected['controlValid'] and selected['locationMonoTime'] == 0


@pytest.mark.parametrize('patch,reason', [({'active': False}, 'inactiveNotification'),
    ({'observed': NOW + 1}, 'staleObservation'), ({'observed': NOW - 91_000_000_000}, 'staleObservation'),
    ({'received': NOW - 16_000_000_000}, 'expiredLease'), ({'received': NOW + 1}, 'expiredLease')])
def test_lifecycle_still_rejects_unusable_notifications(patch, reason):
  base, diagnostics = {'status': 'noDestination'}, {}
  assert select_navigation(base, guidance(**patch), now_ns=NOW, drive_id=1, diagnostics=diagnostics) is base
  assert diagnostics['reason'] == reason


def test_missing_fields_stay_unknown_and_metadata_does_not_require_map_matching():
  phone = guidance(maneuverType='', maneuverModifier='', distanceMeters=None,
                   roadName='Main St', exitNumber='12B', destination='Airport')
  instruction, reason = notification_instruction(phone, now_ns=NOW)
  assert reason == 'notificationGuidance'
  assert instruction['text'] == phone['instruction']
  assert instruction['maneuverType'] == '' and instruction['maneuverModifier'] == ''
  selected = select_navigation({'status': 'needsKey'}, phone, now_ns=NOW, drive_id=0)
  assert not selected['distanceKnown'] and not selected['totalsKnown']


def test_no_notification_reports_reason():
  assert notification_instruction(None, now_ns=NOW) == (None, 'noNotification')
