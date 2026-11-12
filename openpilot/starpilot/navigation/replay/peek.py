"""Second by second through a time window: phone guidance beside mapd's junctions, speed and blinkers."""
import argparse

from openpilot.starpilot.navigation.replay.common import load_routes


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('root')
  parser.add_argument('route', help='text identifying one route')
  parser.add_argument('start', type=float, help='seconds from the route start, as summary --timeline prints them')
  parser.add_argument('end', type=float)
  args = parser.parse_args()
  routes = load_routes(args.root, args.route)
  if len(routes) != 1:
    parser.error(f'{args.route!r} matches {len(routes)} routes')
  rows = next(iter(routes.values()))
  start, nav, car, printed = rows[0]['t'], None, None, -9.
  for row in rows:
    seconds = (row['t'] - start) / 1e9
    if row['k'] == 'nav':
      nav = row
    elif row['k'] == 'cs':
      car = row
    elif row['k'] == 'map' and args.start <= seconds <= args.end and seconds - printed >= 1. and nav and car:
      printed = seconds
      print(' '.join((
        f"{seconds:7.1f} v={car['v']:5.1f} steer={car['sa']:6.1f} signal={'L' if car['lb'] else '-'}{'R' if car['rb'] else '-'}",
        f"phone={nav['dist']:.0f}m age={(row['t'] - nav['obs']) / 1e9:.1f}s [{nav['type']}|{nav['mod']}] '{nav['text'][:28]}'",
        f"| {row['st']} {row['road'][:22]} fix age={(row['comp'] - row['gps']) / 1e9:.1f}s")))
      for way, _, _, distance, angle, name, road_class in row['br']:
        print(f"           {distance:6.0f}m {angle:+6.0f}° {road_class:14} {name[:34]:34} {'(this road)' if way == row['way'] else ''}")


if __name__ == '__main__':
  main()
