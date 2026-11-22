# StarPilot


[![Ask DeepWiki](https://img.shields.io/badge/Ask-DeepWiki-blue?logo=readthedocs&logoColor=white)](https://deepwiki.com/StarPilotHQ/StarPilot)
[![Discord](https://img.shields.io/discord/1387432184121393333?label=Discord)](https://firestar.link/discord)
[![Last Updated](https://img.shields.io/github/last-commit/StarPilotHQ/StarPilot)](https://github.com/StarPilotHQ/StarPilot)
[![Wiki](https://img.shields.io/badge/Wiki-StarPilot-blue?logo=wiki)](https://wiki.firestar.link)
[![App Store](https://img.shields.io/badge/App_Store-black?logo=apple&logoColor=white)](https://firestar.link/ios)
[![Google Play](https://img.shields.io/badge/Google_Play-green?logo=googleplay&logoColor=white)](https://firestar.link/android)

StarPilot is an open source driver assistance system for comma 3, 3X, and 4. This branch is where you can try StarPilot 7.0.

## What's on testing

* **Lane Centering 2.0** can keep the car centered when only one lane line is visible in certain scenarios. When lane markings are unreliable, it falls back to the driving model's path.
* **Enhanced Stops** improves stops at stop signs and traffic lights. Vision Stop Sign Detection uses the road camera to recognize stop signs.
* **Custom Controllers** improve turning behavior on supported vehicles. The Firestar Lateral Method adapts steering to your car, and StarPilot's longitudinal planner manages acceleration and braking on supported radar and vision-only configurations.
* **Custom Models** let you choose from community fine tunes in Galaxy's Model Manager.
* **On-Device UI Replay** lets you review a recorded drive with the driving interface overlaid on the footage.

## StarPort and companion apps

**StarPort** puts the driving display on a compatible Android Auto head unit, with navigation, music controls, and model connection status. Use Mapbox or Google Maps for route guidance, and play audio through compatible car speakers with Spotify Connect or phone audio.

**Galaxy** lets you change settings, download models, update StarPilot, run diagnostics, and manage dashcam footage from your phone. The native apps connect over Wi-Fi, Firestar Link (HTTPS), or Bluetooth. [Download for iOS](https://firestar.link/ios) · [Download for Android](https://firestar.link/android)

## Getting started

Find supported hardware, installation steps, and settings in the [StarPilot wiki](https://wiki.firestar.link). Ask questions in the [community Discord](https://firestar.link/discord) or [open a driving support ticket](https://firestar.link/logs).

## More driving and device features

* Always On Lateral keeps steering assistance available independently of adaptive cruise on supported vehicles.
* Speed Limit Controller uses dashboard, map, or vision data. Curve Speed Controller learns from how you take curves.
* Driving Profiles and Traffic Mode, adjustable alert volume, and vehicle-specific tuning.
* Bluetooth keyboards, controllers, and steering wheel remotes.
* Sentry Mode records motion while parked. V-ASM uses the driver-facing camera to detect vehicles alongside the car.

## GM-only Features

* Increased LKAS fault resiliency
* ASCM_INT and SASCM support
* Custom lateral torque controller, with special tuning for Bolts
* 50% extra torque on 2017 Chevy Bolt
* Improved lateral and longitudinal tuning
* Dashboard cruise control display speed spoofing for vehicles with pedal interceptor
* Extra steering wheel button functionality for vehicles with pedal interceptor
* Optional toggle to boot comma when remote starting your vehicle

## Development

Dependencies are included as ordinary source folders. No recursive submodule checkout is needed. [upstream-sync.json](upstream-sync.json) records the openpilot baseline and the source revisions used for vendored dependencies.

The familiar development entry points are retained:

| Command | Purpose |
| --- | --- |
| `./build` | Build the device software using the matching AGNOS environment |
| `./build --panda` | Build the supported Panda firmware targets |
| `./c3` | Open the comma 3 interface on a development host |
| `./c4` | Open the comma four interface on a development host |
| `./onroad` | Open the onroad development view |
| `./dev` | Use the isolated host development environment |
| `./test` | Run local pre-push regression checks without changing device artifacts |

See the [build guide](docs/how-to/laptop-device-build.md) for setup, prerequisites, and target-specific behavior. The [UI guide](openpilot/starpilot/ui/README.md) describes desktop previews and their limits.

## Testing and vehicle support

StarPilot retains upstream vehicle and Panda safety interfaces and adds regression coverage for its custom behavior. The [safety workflow](.github/workflows/safety.yaml) and feature tests cover distinct parts of the system. A passing interface test, model comparison, or bench run does not establish driving support for every configuration.

Support depends on the vehicle configuration and enabled feature. An existing vehicle identifier or settings page does not by itself establish driving support. Model Laboratory currently exposes management controls, but paired driving inference remains unavailable pending integration and Chestnut validation.

## Integrations and credits

StarPilot builds on [comma.ai's openpilot](https://comma.ai/openpilot), which provides automated lane centering, adaptive cruise control, lane change assist, and driver monitoring.

* **JetLink** is an experimental integration with Zeph Leggett / ZoomPilot's host software. Run a larger driving model on a compatible USB-connected laptop, phone, tablet, or Jetson. See the [JetLink setup and hardware guide](openpilot/starpilot/models/JETLINK.md).
* **Chestnut eGPU** is supported on comma 3, 3X, and 4. The 3 and 3X may need additional GPS setup. The model switcher also supports comma's tinygrad driving models.
* Additional vehicle and hardware integrations include the comma pedal interceptor, Toyota SDSU, ZSS and CAN filter configurations, pre-AP Tesla, Ford, and Polestar 2.

Ford-specific lateral-control and vehicle-support work includes substantial adaptations from
[BluePilot](https://github.com/BluePilotDev/bluepilot/tree/bp-7.0), principally developed by
[Alan Polk](https://github.com/alan-polk) with additional BluePilot contributors. StarPilot's
implementation has since diverged, but that does not erase its lineage. See [CREDITS.md](CREDITS.md)
for the code-level provenance and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for applicable
upstream notices and terms. BluePilot and its contributors do not maintain or endorse StarPilot;
please direct support requests for this adaptation to the StarPilot project.

Hyundai, Kia, and Genesis angle steering and related vehicle support include substantial adaptations
from [sunnypilot](https://github.com/sunnypilot/sunnypilot/tree/hkg-angle-steering-2025) and its
[opendbc angle-steering branch](https://github.com/sunnypilot/opendbc/tree/hkg-angle-steering-2025).
StarPilot's implementation has diverged significantly; the upstream contributors do not maintain
this adaptation. Detailed code lineage is recorded in [CREDITS.md](CREDITS.md).

## Third-Party Notices

* Portions of this software include modified versions of the Material Design Icons provided by Google under the Apache License 2.0. A copy of the license is included in the `LICENSE-MDI` file.
* Ford support includes software adapted from BluePilot's `bp-7.0` branch. The source repository contains both a standard MIT notice and a separate custom SUNNYPILOT LLC notice; StarPilot preserves both in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
* Hyundai, Kia, and Genesis support includes software adapted directly from sunnypilot's HKG angle-steering branch and sunnypilot/opendbc. StarPilot preserves the applicable notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

> This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.
