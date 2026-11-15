#!/usr/bin/env bash
# Explicit, isolated ARM64 build. Does not install or restart anything on a comma.
set -euo pipefail
cd "$(dirname "$0")"
image=starpilot-bluealsa-builder:24.04
source_commit=1935d6dcb8975f2d7a51aaafe61538d157224623
if [[ ${1:-} == --setup ]]; then
  docker build --platform linux/arm64 -t "$image" .
  exit
fi
if [[ $# != 1 || ! -d $1/.git ]]; then
  echo 'Usage: build.sh --setup | build.sh /path/to/bluez-alsa-v5.0.0' >&2
  exit 2
fi
source_dir=$(cd "$1" && pwd)
[[ $(git -C "$source_dir" rev-parse HEAD) == "$source_commit" ]] || { echo 'Wrong BlueALSA source revision' >&2; exit 1; }
# Export only committed upstream files plus the reviewed patches in patches/. Local changes are never silently bundled.
mkdir -p build
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT
git -C "$source_dir" archive "$source_commit" > "$archive"
cp "$archive" build/upstream.tar
docker run --rm --platform linux/arm64 --pull never --network none \
  --mount "type=bind,src=$PWD,dst=/package" "$image" bash -euc '
  mkdir /tmp/source
  cd /tmp/source
  tar xf /package/build/upstream.tar
  for patch in /package/patches/*.patch; do git apply "$patch"; done
  autoreconf --install >/tmp/autoreconf.log 2>&1 || { cat /tmp/autoreconf.log; exit 1; }
  ./configure --localstatedir=/data/starpilot/bluealsa5 --disable-debug --disable-manpages --enable-ldac --enable-ofono --enable-test --with-libsamplerate > /tmp/configure.log 2>&1 || { cat /tmp/configure.log; exit 1; }
  make -j4 >/tmp/make.log 2>&1 || { cat /tmp/make.log; exit 1; }
  make -j4 check >/tmp/check.log 2>&1 || { cat test/test-suite.log /tmp/check.log; exit 1; }
  mkdir -p /package/bin /package/lib /package/licenses
  cp src/bluealsad src/bluealsactl/bluealsactl utils/aplay/bluealsa-aplay /package/bin/
  cp src/asound/.libs/libasound_module_{pcm,ctl}_bluealsa.so /package/lib/
  strip /package/bin/* /package/lib/*
  /package/bin/bluealsad --version
  /package/bin/bluealsad --help > /package/build/help.txt
  : > /package/build/dependencies.txt
  for artifact in /package/bin/* /package/lib/*; do
    readelf -h "$artifact" | grep "Machine:.*AArch64"
    ldd "$artifact" >> /package/build/dependencies.txt
  done
  ! grep -q "not found" /package/build/dependencies.txt
  dpkg-query -W > /package/build/build-packages.txt
  cp /tmp/configure.log /tmp/check.log /package/build/
  cp test/test-suite.log /package/build/
  cp test/test-suite.log /package/UPSTREAM_TEST_RESULTS.txt
  cp /package/build/build-packages.txt /package/BUILD_PACKAGES.txt
  cp LICENSE /package/licenses/BlueALSA.txt
  cp LICENSES/* /package/licenses/
  cd /package
  sha256sum bin/* lib/* > SHA256SUMS
'
printf '%s\n' "$source_commit" > UPSTREAM_COMMIT
printf '%s\n' '5.0.0' > VERSION
docker image inspect --format '{{.Id}}' "$image" > BUILD_IMAGE
