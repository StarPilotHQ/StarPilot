# Phone navigation replay

Tools for checking forwarded phone guidance (Google Maps notifications) against real drives, off the car.
They answer three questions:

- What did the phone actually send, and what did mapd know at the time?
- Would this tree's junction matcher (`LocalMapCorroborator` in `../notification.py`) have confirmed each maneuver?
- Was it right, judged by what the car then did?

Run everything from the repository root on a laptop. Nothing here runs on the comma.

## 1. Copy the logs

Find the route on the device, then copy only its `rlog.zst` files (about 8 MB a minute):

```bash
ssh comma@<device> 'ls /data/media/0/realdata | sed "s/--[0-9]*$//" | sort -u | tail'
mkdir -p ~/navlogs && cd ~/navlogs
ssh comma@<device> 'cd /data/media/0/realdata && nice -n 19 tar cf - <route>--*/rlog.zst' | tar xf -
```

Keep the logs outside the repository. They hold your road names and destinations.

## 2. Reduce them

```bash
.venv/bin/python -m openpilot.starpilot.navigation.replay.extract ~/navlogs
```

This writes a small `nav.jsonl` beside each `rlog.zst`: navigation frames, mapd junctions, car speed and blinkers,
GPS, and navigationd's status log lines. Segments already reduced are skipped; delete a `nav.jsonl` to redo it.
The other tools read only these files.

## 3. Look

```bash
# What each route contained. Add --timeline to list every distinct phone update.
.venv/bin/python -m openpilot.starpilot.navigation.replay.summary ~/navlogs --route <route>

# One approach, second by second: phone distance beside mapd's junctions, speed, steering and blinkers.
.venv/bin/python -m openpilot.starpilot.navigation.replay.peek ~/navlogs <route> <start s> <end s>
```

Times are seconds from the start of the route, as `summary --timeline` and `replay` print them.

## 4. Check the replay against the car

```bash
.venv/bin/python -m openpilot.starpilot.navigation.replay.calibrate ~/navlogs --route <route> --ref <commit the device ran>
```

This runs the matcher from the commit the drive was recorded on and compares it, frame by frame, with the
`controlValid` the car logged. Only trust step 5 when the two agree:

```
<route> {'logged=False replayed=False': 1567, 'logged=True replayed=True': 42}
```

It supports matchers that expose `status(phone)` on rows without a road name, which is every commit before
arrow-only guidance was confirmed by road name. For later drives, read navigationd's status line in the log instead
(below).

## 5. Replay the working tree

```bash
.venv/bin/python -m openpilot.starpilot.navigation.replay.replay ~/navlogs --route <route>
```

One line per maneuver that came within 400 m, then totals:

| Field | Meaning |
|---|---|
| `[type\|side]` | what the phone sent; `[-\|-]` is arrow-only guidance, a distance and a road name |
| `did` | what the car did at the end: `L`, `R`, `S` (under 35°), `?` (too slow to tell), with degrees |
| `logged` | frames the car logged as control-valid |
| `replayed a/b` | frames this tree confirms, of the frames within 400 m |
| `{status: n}` | the matcher's verdict per frame |
| `first` | phone distance, junction distance, kind and side at the first confirmation |
| `signal` | blinker frames during the maneuver |
| `error_m` | junction distance minus phone distance, both advanced to the same moment (min, max) |
| `flaps` | times control dropped during the approach; each is a released and re-applied slowdown |
| `at_junction` | heading change around the moment the junction distance reached zero |

Options: `--speed car` uses `carState.vEgo` where the default `gps` uses what navigationd sees (the phone fix, else
the device's); `--hide-roads` leaves road names out of output you intend to share.

A change is an improvement when more real turns (`did=L` or `R`) are confirmed, none is confirmed where the car
went straight, `error_m` stays within the phone's rounding (about 80 m far out, 15 m close in), and `flaps` stays
at zero or one.

## What to keep in mind

- `did` needs GPS bearing while moving. A turn after a long stop often shows `?`; use `peek` and the steering angle.
- An exit ramp that is confirmed shows as `S`. Ramps do not turn 35°, and they neither slow nor steer the car.
- Once a maneuver is confirmed, the published frame carries the junction's distance and the resolved kind, not the
  phone's. The phone's own values are in navigationd's change-only log line
  (`notification navigation: <reason> <map status> phone=(type, side, metres, kind, side, junction metres)`),
  which `extract` keeps as `log` rows.
- mapd lists only ways that end at a junction, and one-way roads only in their legal direction. A divided road
  can therefore show the turn on one side only, which is why the side of an arrow-only turn is left to the blinker.
- `replay` and `calibrate` feed junctions to the matcher directly. They do not run mapd's frame tracker, so
  `calibrate` is the check that this shortcut matches the car.
