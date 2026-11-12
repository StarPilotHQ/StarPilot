"""Run this tree's LocalMapCorroborator over logged phone guidance and mapd junctions, and score each maneuver
against what the car logged (controlValid) and what it then did (heading change)."""
import argparse
import bisect
from collections import Counter
from types import SimpleNamespace
from typing import Any

from openpilot.starpilot.navigation.notification import LocalMapCorroborator
from openpilot.starpilot.navigation.replay.common import branch_rows, heading_change, is_phone, load_routes, phone_gps, usable_map

NEAR_M = 400.          # only the final approach is scored
TURN_DEG = 35.


def replay_route(rows, speed_source):
  device_gps = [(row['t'], row['brg'], row['spd']) for row in rows if row['k'] == 'gps' and row['fix']]
  phone_fixes = [(row['t'], gps[3], gps[2]) for row in rows if row['k'] == 'nav' and (gps := phone_gps(row))]
  local_map = LocalMapCorroborator()
  maneuvers: list[dict[str, Any]] = []
  current: dict[str, Any] | None = None
  frame = car = fix = None
  metres, odometer = 0., []

  def driven_since(stamp_ns):
    index = bisect.bisect_left(odometer, (stamp_ns, 0.))
    return metres - odometer[min(index, len(odometer) - 1)][1] if odometer else 0.

  for row in rows:
    if row['k'] == 'map':
      frame = row
    elif row['k'] == 'gps':
      fix = row if row['fix'] else fix
    elif row['k'] == 'cs':
      if car:
        metres += car['v'] * (row['t'] - car['t']) / 1e9
      car = row
      odometer.append((row['t'], metres))
    elif row['k'] == 'nav':
      text = row['text'] if is_phone(row) else None
      if current is not None and (text != current['text'] or row['dist'] > current['last'] + 100):
        current['end'] = row['t']
        maneuvers.append(current)
        current = None
      if text is None:
        continue
      if current is None:
        current = {'text': text, 'type': row['type'], 'mod': row['mod'], 'start': row['t'], 'last': row['dist'], 'logged': 0,
                   'status': Counter(), 'signal': Counter(), 'first': None, 'near': 0, 'error': [], 'flaps': 0, 'was': False,
                   'zero': None}
      current['last'] = row['dist']
      current['logged'] += row['ctl']
      if car:
        current['signal']['L' if car['lb'] else 'R' if car['rb'] else '-'] += 1
      local_map.fresh = frame is not None and row['t'] - frame['t'] < 1_200_000_000
      if frame is not None and usable_map(frame, row['t']):
        local_map.branches, local_map.frame = branch_rows(frame), SimpleNamespace(gps_mono_ns=frame['gps'])
        local_map.updated_boot_ns = frame['comp']
      else:
        local_map.branches, local_map.frame = (), None
      speed = car['v'] if car else None
      if speed_source == 'gps':
        # navigationd's own source: the phone fix, else the device's.
        gps = phone_gps(row)
        speed = gps[2] if gps else fix['spd'] if fix is not None and row['t'] - fix['t'] <= 2e9 else None
      phone = {'session': row['sess'], 'instruction': row['text'], 'maneuverType': row['type'], 'maneuverModifier': row['mod'],
               'distanceMeters': row['dist'] if row['dk'] else None, 'observed': row['obs']}
      resolved = local_map.resolve(phone, speed=speed, now_ns=row['t'])
      current['flaps'] += int(current['was'] and not resolved.control)
      current['was'] = resolved.control
      if resolved.control:
        current['zero'] = row['t'] + resolved.distance / max(speed or 1., 1.) * 1e9
      if row['dist'] <= NEAR_M:
        current['near'] += 1
        current['status'][resolved.status] += 1
        if resolved.control:
          current['first'] = current['first'] or (round(row['dist']), round(resolved.distance), resolved.kind, resolved.side)
          current['error'].append(round((resolved.distance - driven_since(resolved.anchor_ns)) - (row['dist'] - driven_since(row['obs']))))
  if current is not None:
    current['end'] = rows[-1]['t']
    maneuvers.append(current)
  for maneuver in maneuvers:
    sources = (phone_fixes, device_gps)
    maneuver['turn'] = heading_change(sources, maneuver['end'] - 8e9, maneuver['end'] + 6e9) if maneuver['last'] <= 60 else None
    maneuver['at_junction'] = heading_change(sources, maneuver['zero'] - 8e9, maneuver['zero'] + 8e9) if maneuver['zero'] else None
  return [maneuver for maneuver in maneuvers if maneuver['near']]


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('root')
  parser.add_argument('--route', default='', help='only routes containing this text')
  parser.add_argument('--speed', choices=('car', 'gps'), default='gps', help='gps is what navigationd sees; car is carState.vEgo')
  parser.add_argument('--hide-roads', action='store_true', help='leave road names out, for output that will be shared')
  args = parser.parse_args()
  total = Counter()
  for route, rows in load_routes(args.root, args.route).items():
    start = rows[0]['t']
    for m in replay_route(rows, args.speed):
      turn = m['turn']
      actual = '?' if turn is None else 'L' if turn < -TURN_DEG else 'R' if turn > TURN_DEG else 'S'
      control = sum(count for status, count in m['status'].items() if status in ('mapAligned', 'mapInferred'))
      total['maneuvers'] += 1
      total['logged control'] += bool(m['logged'])
      total['replayed control'] += bool(control)
      total['replayed control, real turn'] += bool(control and actual in 'LR')
      total['replayed control, no turn'] += bool(control and actual == 'S')
      text = '' if args.hide_roads else f" {m['text'][:30]:30}"
      print(' '.join((
        f"{route[:8]} {(m['start'] - start) / 1e9:6.0f}s [{m['type'] or '-'}|{m['mod'] or '-'}]{text}",
        f"did={actual}({'' if turn is None else round(turn):>4}) logged={m['logged']:3} replayed={control:3}/{m['near']:3}",
        f"{dict(m['status'])} first={m['first']} signal={dict(m['signal'])}",
        f"error_m={(min(m['error']), max(m['error'])) if m['error'] else ''} flaps={m['flaps']}",
        f"at_junction={'' if m['at_junction'] is None else round(m['at_junction'])}")))
  print(dict(total))


if __name__ == '__main__':
  main()
