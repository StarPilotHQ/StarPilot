#!/usr/bin/env bash
# Regenerate this directory from Ubuntu 24.04 (noble) arm64 packages. Requires Docker.
# AGNOS 20 ships no libpulse0 or libsndfile1, so the client libraries and their codecs are vendored too.
set -euo pipefail
cd "$(dirname "$0")"
docker run --rm --platform linux/arm64 -v "$PWD:/out" ubuntu:24.04 bash -euc '
  apt-get -qq update >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get -qq install -y --no-install-recommends libasound2t64 libwrap0 \
    libx11-xcb1 libgomp1 libsystemd0 libudev1 libdbus-1-3 libopus0 >/dev/null
  PKGS="pulseaudio pulseaudio-utils libpulse0 libltdl7 liborc-0.4-0t64 libsoxr0 libspeexdsp1 libtdb1 libsndfile1 libasyncns0
    libflac12t64 libogg0 libvorbis0a libvorbisenc2 libmpg123-0t64 libmp3lame0"
  cd /tmp && apt-get download $PKGS >/dev/null
  sha512sum *.deb > /out/PACKAGES.sha512
  for deb in *.deb; do dpkg-deb -x "$deb" root; done
  R=/tmp/root; M=$R/usr/lib/pulse-16.1+dfsg1/modules
  rm -rf /out/bin /out/lib /out/modules /out/licenses && mkdir -p /out/bin /out/lib /out/modules /out/licenses
  cp $R/usr/bin/pulseaudio $R/usr/bin/pactl /out/bin/
  for m in module-native-protocol-unix module-alsa-sink module-null-sink module-pipe-sink; do cp $M/$m.so /out/modules/; done
  # Soloist loads libpulse.so.0 and the phone relay libpulse-simple.so.0 from here as well.
  cp -L $R/usr/lib/aarch64-linux-gnu/libpulse.so.0 $R/usr/lib/aarch64-linux-gnu/libpulse-simple.so.0 /out/lib/
  export LD_LIBRARY_PATH=$R/usr/lib/aarch64-linux-gnu:$R/usr/lib/aarch64-linux-gnu/pulseaudio:$M
  for f in /out/bin/* /out/modules/*.so /out/lib/*; do ldd "$f"; done | awk "/=>/{print \$3}" | sort -u | grep "^$R" |
    while read -r lib; do cp -L "$lib" /out/lib/; done
  for p in $PKGS; do cp -L $R/usr/share/doc/$p/copyright /out/licenses/$p.copyright; done
  chmod 755 /out/bin/* && chmod 644 /out/lib/* /out/modules/* /out/licenses/*
  cd /out && sha256sum bin/* lib/* modules/* > SHA256SUMS'
