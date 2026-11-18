# StarPilot native UI

StarPilot uses separate large (C3) and compact (C4) layouts over the native
camera, model renderer and application lifecycle. The same presentation
components serve the device and Galaxy's sample layout preview.

## Hardware and physical sizing

Use these native-device baselines when designing and reviewing text, controls
and touch targets:

| Profile | OLED display | Native resolution | Density | Native pixels per mm |
| --- | --- | --- | --- | --- |
| Large (C3/3X) | 6-inch | 2160 × 1080 | Approximately 402.5 PPI, derived from the diagonal | 15.85 |
| Compact (C4) | Nominally 1.9-inch touchscreen | 536 × 240 | Published 300 PPI | 11.81 |

For compact sizing, use the published 300 PPI directly; do not derive a different
density from the rounded 1.9-inch diagonal. Convert with
`mm = native_pixels × 25.4 / PPI`. For example, a 10 mm target needs at least
159 native pixels on large or 119 on compact. These are size conversions, not
validated usability minimums.

Reviews must distinguish visible lettering, the painted control and its full
touch area. Measure lettering after profile font scaling and layout transforms;
the nominal font size is not its visible height. `BitmapFonts.vertical_ink` and
`horizontal_ink` provide the renderer's glyph bounds. Keep opposing touch actions
separated and their hit areas nonoverlapping. Check clearance against the torque
bar's maximum footprint, rather than only the output shown in one capture.

Desktop previews, Galaxy zoom and projected displays do not establish physical
size on these devices. Confirm legibility and reach at the normal windshield
mounting position on actual hardware before treating a design as validated.

## Runtime ownership

- `runtime_app.py` connects the native navigation, touch handling, camera layers
  and settings owners. It cancels pending gestures when pages change.
- `runtime_snapshot.py` projects service observations and saved preferences into
  display state. Message validity, age and drive identity remain separate from
  saved configuration.
- `shell.py` combines Home, Settings and Onroad. Renderers consume snapshots;
  input handlers emit requests to the owner responsible for the action.
- `onroad.py` composes profile-specific widgets, overlays and alerts.
  `onroad_customization.py` owns the versioned layout and color document;
  the Galaxy editor and native renderers use the same widget registry.
- Quick Select keeps its lower-left corner cue and tap/swipe entry. Three equal
  176px controls share one opaque rounded shelf, drawn above the live HUD and
  below alerts and the engagement border. Ordinary widgets keep their positions
  and normal rendering. A vertical fallback protects moved speed readings; if
  both forms conflict, the drawer stays closed and the layout editor explains why.
  Alerts and pending confirmations dismiss it immediately. Touch stays captured
  through the 150ms exit; entrance takes 200ms. Compact keeps its three-zone input.
- Reduce Quick Select motion is available for Large UI in native Appearance and Galaxy Colors
  & Layout. The optional `reduceMotion` boolean uses the existing validated layout
  document without a version bump. See [the design notes](QUICK_SELECT_DESIGN.md).
- `ui_state.py` in `selfdrive/ui` follows hardwared's effective started state.
  Physical ignition remains separate. Camera reception, model filters and
  transition updates must continue when a settled opaque settings page skips
  their painting.

Preference editors do not grant driving authority. Feature owners validate
configuration changes; control consumers and native safety retain their own
checks. A speed-limit action carries the displayed decision's session and
identity, which the planner verifies before accepting it. Hiding or moving a
widget must preserve the corresponding hit target and must not hide alerts.

Shared feature contracts live with [speed limits](../speed_limits/README.md),
[conditional modes](../conditional_mode/README.md),
[lateral control](../lateral/README.md), [AOL](../aol/README.md),
[longitudinal control](../longitudinal/README.md),
[models](../models/README.md) and [Galaxy](../galaxy/README.md).

## Running and previewing

From the repository root, `./c3` and `./c4` launch the large and compact UI in
an isolated host runtime. Each accepts an optional leading build-job count.
`SP_C3_COMPILE_ONLY=1` or `SP_C4_COMPILE_ONLY=1` prepares that runtime without
opening a window. Host settings are kept in its private Params store.

For a deterministic presentation capture with supplied sample state:

```sh
./dev python -m openpilot.starpilot.ui.preview_shell \
  --profile compact \
  --font-directory openpilot/starpilot/ui/assets/fonts \
  --asset-directory openpilot/selfdrive/assets \
  --output /tmp/starpilot-ui-preview
```

Use `--profile large` for C3. `preview_typography`, `preview_home`,
`preview_settings`, `preview_device`, `preview_software` and
`preview_slc_actions` provide narrower fixtures; each exposes its arguments
with `--help`. Previews require a graphics context. They do not launch driving
services, operate a vehicle or establish device performance.

## Arabesque Files

The large UI's Developer panel opens Arabesque. Its dock offers Terminal and
Files; Desktop closes the active tool. Files provides touch navigation, small
UTF-8 text previews, new folders, rename, copy, move and confirmed permanent
deletion. Copy and move use a destination picker, one item at a time.

Writes are limited to Home and Data, with the running software, Params,
persistence and recording roots protected. Links and special files are
read-only; folders containing them cannot be copied. Moves require the same
filesystem; use Copy between filesystems. Text previews stop at 256 KiB.
File work requires confirmed off-road access. Closing Files cancels pending
work; an interrupted recursive deletion can already have removed some items.

## Performance diagnostics

`UI_FRAME_TIMING=1 ./c4` (or `./c3`) enables phase timing. UI Debug Mode also
shows FPS and enables timing. Slow-frame warnings separate drawing, caller
updates and presentation wall time from thread CPU time; camera, model and
other nested phases help locate the work. Nested timings overlap and must not
be added together.

Without continuous tracing, a slow-frame warning starts a bounded diagnostic
window: up to 360 slow samples over six seconds, followed by a 60-second
cooldown. The log reports per-phase medians and maxima. Frame pacing and the
`uiDebug` message schema are unchanged.

Compare the same source, assets, saved layout, input sequence and render mode
when measuring an optimization. Preserve update and input behavior as well as
pixels. Offscreen rendering and component timings exclude parts of the running
application; verify sustained performance on the device with its normal camera
and model workload.

## Assets and recovery

`startup.py` validates bundled fonts and artwork before acquiring graphics
resources. Missing or modified assets select the upstream UI and log the
reason. `STARPILOT_UI=upstream` explicitly selects that recovery presentation.
`STARPILOT_UI_DEV=1` instead fails visibly when custom assets are invalid; the
explicit upstream selection takes precedence.

`STARPILOT_UI_FONT_DIR` can point to a complete, validated Inter/Unifont bitmap
set. Sora's Home wordmark uses its bundled bitmap. Font descriptors, atlases and
artwork have source-controlled hash manifests. Keep their glyph metrics and
profile scales together; do not regenerate atlases as part of a routine build.
Close owned fonts and textures before destroying the graphics context.

The adjacent [LICENSE](LICENSE) and [asset notices](assets/README.md) retain
applicable code, font and image attribution. Hash validation establishes asset
identity, not redistribution rights.

## Numbered bookmarks

The native saved-bookmark alert includes its number, for example
`Bookmark #2 Saved`. Large and compact keep their existing alert styling;
there is no additional onroad control or setting. The shared selfdrived counter
counts `userBookmark` events from UI, wheel and projection sources, not the
accompanying `bookmarkButton` signal. Going offroad or observing a new drive
identity resets it; the next bookmark is #1. The count is atomically checkpointed
in volatile shared memory with its boot and drive identity only when a bookmark
arrives, so restarting selfdrived during the same drive preserves the sequence.
Manager startup and offroad transitions clear the
checkpoint. This is notification numbering, not an additional field in recorded
bookmark messages. If storage is unavailable, counting continues in memory and
the persistence error is logged; restart recovery is unavailable in that case.

`./dev python -m openpilot.starpilot.ui.preview_bookmarks --output /tmp/bookmark-review`
produces stills and short animations for both profiles using production widgets
and alert text. It shows #1, #2, offroad, then #1. These are deterministic sample
states without camera footage, not drive replay or hardware validation.
