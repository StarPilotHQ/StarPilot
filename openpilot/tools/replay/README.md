# Replay

`replay` allows you to simulate a driving session by replaying all messages logged during the use of openpilot. This provides a way to analyze and visualize system behavior as if it were live.

Logged CarParams are not written to persistent caches: doing that requires source
schema provenance and explicit conversion. Raw live `CarParams` seeding is allowed
only with a dedicated `--prefix replay-<name>` namespace; run consumers under the
same prefix. Other namespaces receive a diagnostic and no Params seeding. Playback
remains available, but isolation does not establish compatibility of logged schemas
or qualify a replay for vehicle validation.

## Setup

Before starting a replay, you need to authenticate with your comma account using `auth.py`. This will allow you to access your routes from the server.

```bash
# Authenticate to access routes from your comma account:
python3 openpilot/tools/lib/auth.py
```

## Replay a Remote Route
You can replay a route from your comma account by specifying the route name.

```bash
# Start a replay with a specific route:
openpilot/tools/replay/replay <route-name>

# Example:
openpilot/tools/replay/replay '5beb9b58bd12b691/0000010a--a51155e496'

# Replay the default demo route:
openpilot/tools/replay/replay --demo
```

## Replay a Local Route
To replay a route stored locally on your machine, specify the route name and provide the path to the directory where the route files are stored.

```bash
# Replay a local route
openpilot/tools/replay/replay <route-name> --data_dir="/path_to/route"

# Example:
# If you have a local route stored at /path_to_routes with segments like:
# 5beb9b58bd12b691/0000010a--a51155e496--0
# 5beb9b58bd12b691/0000010a--a51155e496--1
# You can replay it like this:
openpilot/tools/replay/replay "5beb9b58bd12b691/0000010a--a51155e496" --data_dir="/path_to_routes"
```

## Usage
For more information on available options and arguments, use the help command:

``` bash
$ openpilot/tools/replay/replay -h
Usage: openpilot/tools/replay/replay [options] route
Mock openpilot components by publishing logged messages.

Options:
  -h, --help             Displays this help.
  -a, --allow <allow>    whitelist of services to send (comma-separated)
  -b, --block <block>    blacklist of services to send (comma-separated)
  -c, --cache <n>        cache <n> segments in memory. default is 5
  -s, --start <seconds>  start from <seconds>
  -x <speed>             playback <speed>. between 0.2 - 3
  --demo                 use a demo route instead of providing your own
  --auto                 Auto load the route from the best available source (no video):
                         internal, openpilotci, comma_api, car_segments, testing_closet
  --data_dir <data_dir>  local directory with routes
  --prefix <prefix>      set OPENPILOT_PREFIX
  --cabin                  load cabin camera
  --wide-road                 load wide road camera
  --no-loop              stop at the end of the route
  --no-cache             turn off local cache
  --qcam                 load qcamera
  --no-hw-decoder        disable HW video decoding
  --no-vipc              do not output video
  --all                  do output all messages including uiDebug, userBookmark.
                         this may causes issues when used along with UI

Arguments:
  route                  the drive to replay. find your drives at
                         connect.comma.ai
```

## Visualize the Replay in the openpilot UI
To visualize the replay within the openpilot UI, run the following commands:

```bash
openpilot/tools/replay/replay <route-name>
cd openpilot/selfdrive/ui && ./ui.py
```

## Work with plotjuggler
If you want to use replay with plotjuggler, you can stream messages by running:

```bash
openpilot/tools/replay/replay <route-name>
openpilot/tools/plotjuggler/juggle.py --stream
```

## watch3

watch all three cameras simultaneously from your comma three routes with watch3

simply replay a route using the `--cabin` and `--wide-road` flags:

```bash
# start a replay
cd openpilot/tools/replay && ./replay --demo --cabin --wide-road

# then start watch3
cd openpilot/selfdrive/ui && ./watch3.py
```

![](https://i.imgur.com/IeaOdAb.png)

## Stream CAN messages to your device

Replay CAN messages as they were recorded using a [panda jungle](https://comma.ai/shop/products/panda-jungle). The jungle has 6x OBD-C ports for connecting all your comma devices. Check out the [jungle repo](https://github.com/commaai/panda_jungle) for more info.

In order to run your device as if it was in a car:
* connect a panda jungle to your PC
* connect a comma device or panda to the jungle via OBD-C
* run `can_replay.py`

``` bash
batman:replay$ ./can_replay.py -h
usage: can_replay.py [-h] [route_or_segment_name]

Replay CAN messages from a route to all connected pandas and jungles
in a loop.

positional arguments:
  route_or_segment_name
                        The route or segment name to replay. If not
                        specified, a default public route will be
                        used. (default: None)

optional arguments:
  -h, --help            show this help message and exit
```

## Isolated StarPilot desktop replay

`./onroad [jobs] [--c3|--c4|--all|--replay-only] [--prefix replay-NAME]
<route-or-replay-options>` starts the current replay binary and selected native
UI in one private host session. Without a UI choice, logged `initData.deviceType`
chooses compact for mici/C4 and large otherwise. `--replay-only` starts no UI
and does not load `initData` for display selection. The wrapper preserves replay
arguments after its own options, and `--help` needs no host build. It uses a
separate `replay-*` message namespace and disposable Params directory, copies
only validated logged display booleans, and stops its owned children on normal
exit or SIGINT/SIGTERM/SIGHUP. It creates and removes only its own native
message-queue directory; an existing explicit replay prefix is rejected.
Route playback may need the usual route access
and can download data requested by the route argument.

This port has no fake nav, CEM, CSC, alert, or Galaxy demo publishers. Their old
flags fail clearly. The current replay binary exposes its ncurses playback
controls, not the old state-file control bar. `--auto` alone is rejected because
the current replay binary still requires a route. A native UI preview also
requires the complete reviewed bitmap font bundle described in the StarPilot UI
README; this is an open zero-configuration host dependency.
