"""Check the replay against the car: run the matcher from the commit the drive was recorded on and compare,
frame by frame, with the controlValid it logged. Trust replay.py's numbers only when these agree."""
import argparse
import subprocess
import sys
import types
from collections import Counter

from openpilot.starpilot.navigation.replay.common import branch_rows, is_phone, load_routes, usable_map

SOURCE = 'openpilot/starpilot/navigation/notification.py'
CONTROL_AGE_NS = 25_000_000_000


def matcher_at(ref):
  source = subprocess.run(['git', 'show', f'{ref}:{SOURCE}'], check=True, capture_output=True, text=True).stdout
  module = types.ModuleType('recorded_notification')
  sys.modules[module.__name__] = module
  exec(compile(source, f'{ref}:{SOURCE}', 'exec'), module.__dict__)
  return module.LocalMapCorroborator()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('root')
  parser.add_argument('--route', default='', help='only routes containing this text')
  parser.add_argument('--ref', default='4f11ab708b', help='commit the device ran; only matchers with status(phone) are supported')
  args = parser.parse_args()
  for route, rows in load_routes(args.root, args.route).items():
    local_map, frame, counts = matcher_at(args.ref), None, Counter()
    for row in rows:
      if row['k'] == 'map':
        frame = row
      elif row['k'] == 'nav' and is_phone(row):
        # That matcher's rows had no road name.
        local_map.branches = tuple(branch[:5] for branch in branch_rows(frame)) if usable_map(frame, row['t']) else ()
        status = local_map.status({'maneuverType': row['type'], 'maneuverModifier': row['mod'],
                                   'distanceMeters': row['dist'] if row['dk'] else None})
        replayed = status == 'mapAligned' and row['dist'] > 0 and row['t'] - row['obs'] <= CONTROL_AGE_NS
        counts[(bool(row['ctl']), replayed)] += 1
    print(route, {f'logged={logged} replayed={replayed}': count for (logged, replayed), count in sorted(counts.items())})


if __name__ == '__main__':
  main()
