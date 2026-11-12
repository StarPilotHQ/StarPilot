# Replay

`replay` allows you to simulate a driving session by replaying all messages logged during the use of openpilot. This provides a way to analyze and visualize system behavior as if it were live.

## Setup

Before starting a replay, you need to authenticate with your comma account using `auth.py`. This will allow you to access your routes from the server.

```bash
# Authenticate to access routes from your comma account:
python3 tools/lib/auth.py
```

## Replay a Remote Route
You can replay a route from your comma account by specifying the route name.

```bash
# Start a replay with a specific route:
tools/replay/replay <route-name>

# Example:
tools/replay/replay 'a2a0ccea32023010|2023-07-27--13-01-19'

# Replay the default demo route:
tools/replay/replay --demo
```

## Replay a Local Route
To replay a route stored locally on your machine, specify the route name and provide the path to the directory where the route files are stored.

```bash
# Replay a local route
tools/replay/replay <route-name> --data_dir="/path_to/route"

# Example:
# If you have a local route stored at /path_to_routes with segments like:
# a2a0ccea32023010|2023-07-27--13-01-19--0
# a2a0ccea32023010|2023-07-27--13-01-19--1
# You can replay it like this:
tools/replay/replay "a2a0ccea32023010|2023-07-27--13-01-19" --data_dir="/path_to_routes"
```

## Send Messages via ZMQ
By default, replay sends messages via MSGQ. To switch to ZMQ, set the ZMQ environment variable.

```bash
# Start replay and send messages via ZMQ:
ZMQ=1 tools/replay/replay <route-name>
```

## Usage
For more information on available options and arguments, use the help command:

``` bash
$ tools/replay/replay -h
Usage: tools/replay/replay [options] route
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
  --dcam                 load driver camera
  --ecam                 load wide road camera
  --no-loop              stop at the end of the route
  --no-cache             turn off local cache
  --qcam                 load qcamera
  --no-hw-decoder        disable HW video decoding
  --no-vipc              do not output video
  --all                  do output all messages including uiDebug, userBookmark.
                         this may causes issues when used along with UI
  --headless             run replay without the ncurses console UI

Arguments:
  route                  the drive to replay. find your drives at
                         connect.comma.ai
```

## Visualize the Replay in the openpilot UI
To visualize the replay within the openpilot UI, run the following commands:

```bash
tools/replay/replay <route-name>
cd selfdrive/ui && ./ui.py
```

For the StarPilot host workflow, the combined desktop launcher is:

```bash
./onroad --c3 <route-name>
```

## Desktop UI Recording

StarPilot can record the rendered onroad UI while replaying a route on a desktop. The additional recording modes below are desktop-only and are not enabled on comma hardware.

The C3 and C4 interfaces can be selected with `--c3` and `--c4`:

```bash
./onroad --c3 <route-name>
./onroad --c4 <route-name>
```

### Local comma routes

Routes copied directly from a comma device can be replayed locally without first downloading the route through a comma account.

On the comma device, recorded segments are stored under:

```text
/data/media/0/realdata/
```

A route is split into numbered segment directories. For example:

```text
/data/media/0/realdata/00000040--d083928baf--0/
/data/media/0/realdata/00000040--d083928baf--1/
/data/media/0/realdata/00000040--d083928baf--2/
```

A segment can contain files such as:

```text
rlog.zst
qlog.zst
fcamera.hevc
ecamera.hevc
dcamera.hevc
qcamera.ts
```

With SSH access already enabled on the comma, a complete route can be copied to the desktop with `rsync`:

```bash
mkdir -p ~/StarPilotRoutes

rsync -av --progress \
  'comma@<comma-ip>:/data/media/0/realdata/00000040--d083928baf--*' \
  ~/StarPilotRoutes/
```

The copied route can then be replayed by specifying its parent directory:

```bash
./onroad --c4 "00000040--d083928baf" \
  --data_dir="$HOME/StarPilotRoutes"
```

This local comma route format is accepted by replay itself but is not recognized by `SegmentRange`.

The desktop replay configuration therefore includes a local-route fallback that locates `initData` for these routes and restores the logged route parameters instead of silently falling back to desktop defaults.

### Recording modes

Recording is enabled with `RECORD=1`.

| Variable | Description |
| --- | --- |
| `RECORD_COMBINED=1` | Record the camera and rendered UI/HUD together |
| `RECORD_HUD_ONLY=1` | Record only the HUD with a transparent background |
| `RECORD_CAMERA_ONLY=1` | Record only the transformed camera view on the C4/Mici UI |
| `RECORD_METRIC=1` | Force metric units for the desktop recording |
| `RECORD_CAMERA_VIEW=auto` | Use automatic camera selection |
| `RECORD_CAMERA_VIEW=standard` | Force the standard road camera |
| `RECORD_CAMERA_VIEW=wide` | Force the wide road camera |
| `RECORD_CAMERA_RESOLUTION=render` | Use the normal rendered UI resolution |
| `RECORD_CAMERA_RESOLUTION=source` | Use the near-source C4/Mici recording scale |
| `RECORD_DURATION=<seconds>` | Stop after the requested amount of recorded video |
| `RECORD_OUTPUT=<path>` | Set the recording output path |
| `RECORD_QUALITY=<crf>` | Set H.264 CRF quality for normal/combined recording |
| `RECORD_BITRATE=<bitrate>` | Set an H.264 target bitrate instead of CRF |

`RECORD_CAMERA_VIEW` supports `auto`, `standard`, and `wide` on both the C3 and C4 desktop interfaces.

### C4 recording resolution

The C4/Mici UI has a base canvas of 536x240.

`RECORD_CAMERA_RESOLUTION=source` uses a 2.5x scale, producing a 1340x600 recording. This preserves the UI aspect ratio and geometry so camera transformations and overlays remain aligned.

This is a near-source rendering mode, not a raw 1344x760 camera export.

Example:

```bash
RECORD=1 \
RECORD_COMBINED=1 \
RECORD_METRIC=1 \
RECORD_CAMERA_VIEW=standard \
RECORD_CAMERA_RESOLUTION=source \
RECORD_DURATION=20 \
RECORD_OUTPUT="$HOME/StarPilotRoutes/c4-combined" \
RECORD_QUALITY=16 \
./onroad --c4 "00000040--d083928baf" \
  --data_dir="$HOME/StarPilotRoutes"
```

For a higher-resolution C4 HUD, use the normal render mode with an explicit scale. For example, `SCALE=4.5` produces a 2412x1080 canvas:

```bash
SCALE=4.5 \
RECORD=1 \
RECORD_COMBINED=1 \
RECORD_METRIC=1 \
RECORD_CAMERA_VIEW=standard \
RECORD_OUTPUT="$HOME/StarPilotRoutes/c4-1080" \
RECORD_QUALITY=16 \
./onroad --c4 "00000040--d083928baf" \
  --data_dir="$HOME/StarPilotRoutes"
```

### C3 recording

The C3 desktop UI has a native 2160x1080 canvas. Use `SCALE=1` to record it at that resolution:

```bash
SCALE=1 \
RECORD=1 \
RECORD_COMBINED=1 \
RECORD_METRIC=1 \
RECORD_CAMERA_VIEW=wide \
RECORD_DURATION=20 \
RECORD_OUTPUT="$HOME/StarPilotRoutes/c3-wide" \
RECORD_QUALITY=16 \
./onroad --c3 "00000040--d083928baf" \
  --data_dir="$HOME/StarPilotRoutes"
```

### Transparent HUD recording

HUD-only mode records the interface without the camera image.

Camera frames continue to be processed internally so camera geometry, calibration, and model-overlay transformations remain correctly aligned. Only the camera pixels are omitted from the rendered recording.

The output uses lossless PNG/RGBA frames in a Matroska container, preserving the alpha channel:

```bash
RECORD=1 \
RECORD_HUD_ONLY=1 \
RECORD_METRIC=1 \
RECORD_CAMERA_VIEW=standard \
RECORD_OUTPUT="$HOME/StarPilotRoutes/hud" \
./onroad --c4 "00000040--d083928baf" \
  --data_dir="$HOME/StarPilotRoutes"
```

The resulting `.mkv` can therefore be composited over another video while retaining the HUD transparency.

### Camera-only recording

On the C4/Mici UI, camera-only mode stops rendering after the transformed camera frame, omitting the HUD and other UI elements.

The recording uses lossless FFVHUFF in a Matroska container:

```bash
RECORD=1 \
RECORD_CAMERA_ONLY=1 \
RECORD_CAMERA_VIEW=wide \
RECORD_CAMERA_RESOLUTION=source \
RECORD_OUTPUT="$HOME/StarPilotRoutes/camera-wide" \
./onroad --c4 "00000040--d083928baf" \
  --data_dir="$HOME/StarPilotRoutes"
```

### Recording duration

`RECORD_DURATION` stops recording according to the number of rendered recording frames rather than wall-clock time.

For example:

```bash
RECORD=1 RECORD_COMBINED=1 RECORD_DURATION=30 ...
```

stops after the number of rendered frames corresponding to 30 seconds at the normal recording frame rate.

### Output formats

The recording mode determines the output format:

| Mode | Container | Codec | Purpose |
| --- | --- | --- | --- |
| Normal `RECORD` | MP4 | H.264 | Standard UI recording |
| `RECORD_COMBINED` | MP4 | H.264 | Camera + HUD/UI |
| `RECORD_HUD_ONLY` | MKV | PNG/RGBA | Lossless HUD with transparency |
| `RECORD_CAMERA_ONLY` | MKV | FFVHUFF | Lossless transformed camera recording |

For H.264 recording, `RECORD_QUALITY` controls the CRF value. `RECORD_BITRATE` can instead be used to specify a target bitrate.

## Work with plotjuggler
If you want to use replay with plotjuggler, you can stream messages by running:

```bash
tools/replay/replay <route-name>
tools/plotjuggler/juggle.py --stream
```

## watch3

watch all three cameras simultaneously from your comma three routes with watch3

simply replay a route using the `--dcam` and `--ecam` flags:

```bash
# start a replay
cd tools/replay && ./replay --demo --dcam --ecam

# then start watch3
cd selfdrive/ui && ./watch3.py
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
