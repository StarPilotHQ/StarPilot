# Vendored PulseAudio server (AArch64)

Spotify Soloist plays only through PulseAudio or PipeWire, and AGNOS 20 ships neither the server nor
the client library. This directory is the minimal set needed to carry Soloist's audio to one output:
the `pulseaudio` daemon, `pactl` (to switch between the speaker and the silent sink without
restarting Soloist), the client libraries Soloist and the phone relay load (`libpulse`,
`libpulse-simple`, `libpulsecommon`, `libsndfile` and its codecs), and four modules (native protocol,
ALSA sink for BlueALSA, null sink, and the real-time pipe sink used for StarPort).

- Source: Ubuntu 24.04 (noble) arm64 binary packages, `pulseaudio` and `pulseaudio-utils`
  `1:16.1+dfsg1-2ubuntu10.1`, and the
  libraries listed in `PACKAGES.sha512` (SHA-512 of each `.deb` as published by Ubuntu).
- Every process that uses it gets `LD_LIBRARY_PATH=lib/`; only libraries AGNOS 20 still ships (glibc,
  ALSA, D-Bus, systemd, X11/xcb, opus, libwrap, libcap, apparmor, gomp) come from the OS. Regenerate with
  `./update.sh`; `SHA256SUMS` lists the extracted files.
- Licenses: PulseAudio, libpulse, libltdl, libsndfile, libasyncns and libmp3lame are LGPL-2.1-or-later,
  libmpg123 LGPL-2.1, libtdb LGPL-3.0-or-later, libsoxr LGPL-2.1-or-later, and liborc, speexdsp, FLAC,
  ogg and vorbis BSD-style. Full notices are in `licenses/`. Corresponding source is
  available from Ubuntu's archive (`apt-get source pulseaudio=1:16.1+dfsg1-2ubuntu10.1`, and likewise
  for each package above). The libraries are unmodified and dynamically linked.
