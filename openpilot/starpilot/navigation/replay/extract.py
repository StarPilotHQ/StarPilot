"""Reduce rlogs to the signals the phone-navigation replay needs: one nav.jsonl beside each rlog.zst."""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from openpilot.tools.lib.logreader import LogReader

CAR_STATE_EVERY_NS = 100_000_000


def navigation_row(message):
  nav = message.starpilotNavigation.navigation
  instruction = nav.instruction
  gps = message.starpilotNavigation.phoneGps
  return {'k': 'nav', 't': message.logMonoTime, 'src': nav.source, 'status': nav.status, 'sess': nav.sessionId, 'rev': nav.revision,
          'ctl': nav.controlValid, 'loc': nav.locationMonoTime, 'obs': nav.observationMonoTime, 'dk': nav.distanceKnown,
          'text': instruction.text, 'type': instruction.maneuverType, 'mod': instruction.maneuverModifier,
          'dist': instruction.distanceMeters, 'pg': [gps.latitude, gps.longitude, gps.speed, gps.bearingDeg]}


def map_row(message):
  road = message.mapdOut
  branches = [[b.wayId, b.junctionLatitude, b.junctionLongitude, round(b.distanceMeters, 1), round(b.angleDegrees, 1),
               b.roadName, str(b.highwayClass)] for b in road.navigationBranches]
  return {'k': 'map', 't': message.logMonoTime, 'ver': road.sampleVersion, 'st': str(road.roadStatus), 'gs': road.gpsSource.raw,
          'gps': road.gpsMonoTime, 'comp': road.computedMonoTime, 'way': road.wayId, 'road': road.roadName, 'br': branches}


def extract(segment):
  out = Path(segment) / 'nav.jsonl'
  if out.exists() and out.stat().st_size:
    return segment, 'cached'
  rows, last_car = [], 0
  try:
    for message in LogReader(str(Path(segment) / 'rlog.zst')):
      kind, t = message.which(), message.logMonoTime
      try:
        if kind == 'starpilotNavigation':
          rows.append(navigation_row(message))
        elif kind == 'mapdOut':
          rows.append(map_row(message))
        elif kind == 'carState' and t - last_car >= CAR_STATE_EVERY_NS:
          last_car = t
          car = message.carState
          rows.append({'k': 'cs', 't': t, 'v': round(car.vEgo, 2), 'lb': car.leftBlinker, 'rb': car.rightBlinker,
                       'sa': round(car.steeringAngleDeg, 1)})
        elif kind in ('gpsLocationExternal', 'gpsLocation'):
          gps = getattr(message, kind)
          rows.append({'k': 'gps', 't': t, 'spd': round(gps.speed, 2), 'brg': round(gps.bearingDeg, 1), 'fix': gps.hasFix})
        elif kind == 'logMessage' and 'notification navigation' in message.logMessage:
          rows.append({'k': 'log', 't': t, 'm': message.logMessage[:600]})
      except Exception as error:
        rows.append({'k': 'error', 't': t, 'which': kind, 'e': repr(error)[:200]})
  except Exception as error:
    rows.append({'k': 'error', 't': 0, 'which': 'reader', 'e': repr(error)[:200]})
  out.write_text('\n'.join(json.dumps(row, separators=(',', ':')) for row in rows))
  return segment, len(rows)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('root', help='folder of <route>--<segment>/rlog.zst')
  parser.add_argument('--jobs', type=int, default=8)
  args = parser.parse_args()
  segments = sorted(str(path.parent) for path in Path(args.root).glob('*/rlog.zst'))
  with ProcessPoolExecutor(args.jobs) as pool:
    for segment, rows in pool.map(extract, segments):
      print(Path(segment).name, rows, flush=True)


if __name__ == '__main__':
  main()
