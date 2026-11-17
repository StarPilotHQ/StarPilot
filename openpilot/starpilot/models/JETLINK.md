# JetLink with StarPilot

JetLink runs a driving model on a USB-connected computer or phone. The comma
runs **StarPilot**. JetLink is experimental; a prepared host model is not proof
that StarPilot has switched to it. Set up parked, with both devices online.

## Set up StarPilot

1. Install or update **StarPilot**, following the [StarPilot repository instructions](../../../README.md).
   Use a StarPilot release containing JetLink support. Finish installation and restart the comma.
2. Open **Galaxy → Model Manager → Jetlink**. Choose **USB computer** for a
   Jetson, Mac, Linux PC, or Android host; choose **iPhone / iPad** for an Apple phone or tablet.
   **Off** is the default. These selections are separate from Small and Chestnut models.
3. Use a USB 3 data cable. Power the comma and host separately. Every cable,
   hub, and port in the connection must support USB 3. Chestnut takes precedence
   when connected; wireless Android Auto can stay enabled.
4. Install and start the host application below. Leave StarPilot parked while
   the host downloads and prepares its model. Watch Galaxy's connection and
   preparation status; do not substitute a host icon or benchmark for StarPilot's runtime status.

## Install the host

The host program is the upstream **Jetlink** application, developed by Zeph
Leggett / ZoomPilot. Its download identifiers and commands keep their upstream
names; installing the host does not require installing ZoomPilot on the comma.
The bundled client uses Jetlink protocol 3 (package 0.8.7). A different host
release still needs a successful compatible handshake and device validation.

### Jetson and Linux PC

On a supported NVIDIA host, review and run the [upstream installer](https://github.com/zoompilot/jetlink/blob/v0.9.0/install.sh).
The upstream Jetson guide specifies an Orin Nano Super 8 GB and JetPack 7.2.1;
the Linux guide specifies an RTX 20-series or newer GPU with driver 580 or newer.
Keep the computer powered, cool, and awake. Connect its USB-A port to the comma's USB-C port.
Use `jetlink status` to check the host and `jetlink logs` to inspect problems.
Host power, sleep, and driver setup details remain in the credited
[Jetson](https://github.com/zoompilot/jetlink/blob/v0.9.0/docs/jetson.md) and
[Linux](https://github.com/zoompilot/jetlink/blob/v0.9.0/docs/linux-pc.md) references;
their comma-side ZoomPilot instructions are replaced by **Set up StarPilot** above.

### Mac

Use an Apple silicon Mac with macOS 15 or newer. Get the Jetlink DMG from
[the upstream releases](https://github.com/zoompilot/jetlink/releases), put the app
in Applications, and start it. Keep it awake and connect by USB 3.
Use **USB computer** in StarPilot's Galaxy, rather than following the comma-side
ZoomPilot installation instructions in the credited
[Mac host reference](https://github.com/zoompilot/jetlink/blob/v0.9.0/docs/macos-app.md).

### iPhone and iPad

Install the upstream [Jetlink TestFlight beta](https://testflight.apple.com/join/DAsYk5sP).
It requires a USB-C device supporting USB 3 and iOS/iPadOS 26.1 or newer.
Choose **iPhone / iPad** in StarPilot, allow the host app's Local Network access,
connect by USB 3, and keep the app open and the device unlocked. Check sustained
thermal performance with the app's benchmark; that benchmark excludes cable latency.
The [credited Apple host reference](https://github.com/zoompilot/jetlink/blob/v0.9.0/docs/iphone-app.md)
contains hardware details. StarPilot supplies the comma-side instructions above.

### Android

Android is experimental and performance is not established. Install the
upstream Jetlink Android APK from [its releases](https://github.com/zoompilot/jetlink/releases).
Use a recent USB 3-capable Android 12+ phone, a powered USB 3 hub, and separate
comma power. Choose **USB computer** in StarPilot. Follow the host's USB permission
prompt and benchmark sustained performance before use.
[Credited Android host reference](https://github.com/zoompilot/jetlink/blob/v0.9.0/docs/android-app.md).

## Use and recovery

Before changing the remote model, disengage cruise **and** any independent
Always On Lateral steering, armed or paused. Wait for Galaxy's fresh **Active · Jetlink remote model**
status before re-engaging; engaging is refused for a second after the switch. If
the link stops or stalls while engaged, StarPilot stays engaged on the local Small
model and shows **Big Model Lost**; be ready to take control as behavior changes.
Jetlink reconnects by itself and switches back at the next full disengagement. Set
Jetlink to **Off** while parked to stop using the remote connection.

With Jetlink on, the USB-C port belongs to its host: a downloaded Chestnut pick is
used only when a Chestnut is detected, never because a cable is plugged in. When
Jetlink is on but cannot run, an offroad alert says why.

If a connection fails, check separate power, USB 3 support, host sleep, cooling,
and the chosen StarPilot connection mode. An iPhone must stay unlocked with the
app foregrounded. Save host and StarPilot logs when reporting an issue; do not
interpret “prepared” as “active”. Phone charging is a separate Galaxy preference
and defaults off.

## Attribution and license

This StarPilot guide adapts the host setup structure from
[ZoomPilot / Jetlink at v0.9.0](https://github.com/zoompilot/jetlink/tree/v0.9.0/docs).
Jetlink is copyright (c) 2026 Zeph Leggett and licensed under the
[MIT license](../../../jetlink_repo/LICENSE), whose full text is preserved.
ZoomPilot source links, project names, authorship, and history are attribution;
they are not instructions to replace StarPilot on the comma. Host binaries and
external installation scripts remain upstream artifacts, with their original identity.
