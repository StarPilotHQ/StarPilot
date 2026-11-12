"""Per route: where guidance came from, what maneuvers the phone sent, and what mapd had."""
import argparse
from collections import Counter

from openpilot.starpilot.navigation.replay.common import is_phone, load_routes


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('root')
  parser.add_argument('--route', default='', help='only routes containing this text')
  parser.add_argument('--timeline', action='store_true', help='also list each distinct phone update')
  args = parser.parse_args()
  for route, rows in load_routes(args.root, args.route).items():
    nav = [row for row in rows if row['k'] == 'nav']
    phone = [row for row in nav if is_phone(row)]
    print(f'\n=== {route} nav={len(nav)} phone={len(phone)} controlValid={sum(row["ctl"] for row in phone)}')
    print(' sources ', Counter(row['src'] + '/' + row['status'] for row in nav).most_common(6))
    print(' maneuver', Counter((row['type'], row['mod']) for row in phone).most_common(12))
    print(' mapd    ', Counter((row['st'], 'branches' if row['br'] else 'none') for row in rows if row['k'] == 'map').most_common(8))
    for row in rows:
      if row['k'] == 'error':
        print(' unreadable', row['which'], row['e'])
        break
    if not args.timeline:
      continue
    start, last, car = rows[0]['t'], None, None
    for row in rows:
      if row['k'] == 'cs':
        car = row
      elif row['k'] == 'nav' and is_phone(row):
        key = (row['text'], row['type'], row['mod'], round(row['dist']))
        if key != last:
          last = key
          speed = car['v'] if car else -1
          print(f"  {(row['t'] - start) / 1e9:7.1f}s rev={row['rev']:>5} age={(row['t'] - row['obs']) / 1e9:5.1f}s",
                f"v={speed:5.1f} {row['dist']:7.0f}m [{row['type']}|{row['mod']}] {row['text'][:60]}")


if __name__ == '__main__':
  main()
