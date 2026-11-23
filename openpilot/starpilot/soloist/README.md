# Spotify and phone audio

Galaxy → Spotify and Phone Audio supports two mutually exclusive music sources:
Spotify Connect (Soloist) and a paired phone's Bluetooth A2DP audio. Both use the
private PulseAudio output, either Android Auto's PCM media channel or an existing
BlueALSA Bluetooth output. Phone mode needs neither Soloist nor a Spotify API key.
The feature's existing enable flag starts `spotifyd` for either source; with the phone source it
never launches Soloist, and switching to it stops a running Soloist.

## Phone setup

1. While parked, enable Bluetooth and pair the phone from Galaxy's Bluetooth page.
2. Choose **Phone over Bluetooth**, then select the paired phone.
3. Choose **Android Auto** as the output and connect projection to the head unit,
   or choose a different paired Bluetooth speaker/head unit.
4. Turn on **Phone Audio**. Select the comma as the phone's media audio output,
   then play music. Use the phone and car for playback and volume.

Changing the source stops the previous player. Pairing, source/device selection,
and output changes retain Galaxy's existing session and parked/offroad checks.
Phone calls, microphone forwarding, head-unit metadata and steering-wheel track
controls are not implemented. AVRCP pause is attempted when Android Auto loses
media focus permanently; even if the phone cannot be paused, forwarding relinquishes
focus. The comma's warning speaker and global ALSA default are not changed.

## Companion apps

Pairing a phone in the Android or iPhone companion app also sets it up for phone
audio. The app asks the comma to open a short pairing window (Galaxy's **Let a
phone pair** button does the same). Only one phone can claim it, only phones
(Bluetooth device class) are accepted, and a confirmation code must be approved.
Afterward the phone appears as the music source if none was chosen yet. Android
bonds from the app; on iPhone the app asks you to tap the comma in
Settings → Bluetooth. The window refuses to open while Android Auto, another
pairing, or a search holds the adapter, and adapter visibility returns to what it
was before. The companion's own key pairing never creates a Bluetooth bond.

Installing Soloist is only allowed from the local network, the Galaxy hotspot or
the tunnel. Over the companion's Bluetooth link the page says so and sends nothing.
Phone audio itself needs no install and works over Bluetooth. The Android app and
the iPhone app each carry their own copy of the Galaxy pages, so they show this
page only after they are updated.

## Runtime behavior

The checkout bundles BlueALSA 5.0.0's daemon and matching PCM/CTL plugins for
phone reception and Bluetooth output. `phone_profile.py` uses temporary `/run`
overrides; the system's BlueALSA 4.1.1 installation stays intact. Phone capture
uses `hwcompat "silence"` for continuity across input interruptions. Private ALSA
configs select the matching plugins and explicitly preserve software volume.
See [the bundle documentation](bluealsa/README.md) for build inputs, tests, and
the old-commit rollback contract (restart music/openpilot or reboot after checkout).

Phone audio exists only while the car is on. About a minute after ignition off the supervisor
drops the phone's audio profile (its other links to the comma stay), stops paging it and stops
advertising the comma as a speaker, so a phone near a parked car no longer attaches its audio
to it; a shorter stop keeps the music. As soon as ignition is on again it advertises and pages the
phone. The pairing window advertises the receiver regardless. The Galaxy page shows the reason
while parked.

Only the selected paired phone is captured. The existing trusted pairing policy
still determines which phones may establish Bluetooth connections; this feature
does not alter trust or erase bonds. `arecord` opens an ALSA plug PCM for that
phone, converting to signed 16-bit 48 kHz stereo. A separate Python worker writes
to the private server using AGNOS's `libpulse-simple.so.0`, with bounded buffers.
The supervisor retries disconnects without repeatedly restarting Bluetooth.
The versioned BlueALSA bundle and private PulseAudio server are included with the checkout.

## Validation

Automated tests cover source migration/validation, authenticated settings, parked
selection, no-Soloist startup, radio-off, disconnect/retry, temporary-profile
restoration, selected-phone AVRCP targeting, and clearing audio on source changes.
Run the Soloist, Galaxy Spotify setup, and Android Auto media-audio Python tests,
plus `node openpilot/starpilot/galaxy/tests/test_spotify.mjs`.

Device acceptance remains necessary: test Android/iPhone pairing and audio,
44.1/48 kHz tracks, radio focus loss/resume, phone disconnect/reconnect, Bluetooth
off/on, source switching, service restart and reboot. Test wireless Android Auto
and Bluetooth output separately for radio coexistence, latency and dropouts.

## Now playing on the car screen

Android Auto layouts include a **Now playing** card (Galaxy → Android Auto layout) that shows the title,
artist and progress of Spotify or of the phone's Bluetooth player, with a background opacity setting. The
supervisor publishes the snapshot in its status file; the car screen reads it once a second only while the
card is enabled, and the card stays hidden when nothing is playing or an alert is up. Phone metadata comes
from the phone's Bluetooth media player (BlueZ `MediaPlayer1`), so it depends on the phone sharing it.

## Spotify Connect recovery

Pair once from Spotify's device picker on a shared Wi-Fi network or hotspot that
allows device-to-device traffic. Both devices need internet access; the Galaxy
hotspot and wireless Android Auto alone do not provide it. After pairing, use the
same Spotify account and turn off **Local device visibility** in Spotify to control
the comma across networks. On iPhone, allow Spotify **Local Network** access.

Soloist retains its account across restarts, network handoffs, output changes and
cache clearing. Startup waits for an IPv4 or IPv6 default route. A disconnected
Bluetooth speaker uses a private silent sink; two consecutive background checks,
twenty seconds apart, trigger an output switch without restarting Soloist. A failed
switch restarts playback with the saved account. Recovery never requests playback
transfer from another Spotify device.

The local API checks authentication every fifteen seconds and reconnects after
forty-five seconds without a response. Two minutes without a working API or a
previously observed login restarts Soloist; loss of login while playing allows ten
minutes for cached music. First-time pairing has no timeout. Repeated starts that
never answer the API can roll back the Soloist package. A route or API response
does not prove internet access; Spotify off/on retries without clearing the account.

Device acceptance remains necessary: Android/iPhone pairing on Wi-Fi and hotspots,
Wi-Fi/cellular handoffs, saved-account recovery, speaker reconnects, and control
across networks. See [Spotify Connect](https://support.spotify.com/us/article/spotify-connect/)
and the [Soloist API](https://developer.spotify.com/documentation/soloist/reference/websocket-api).
