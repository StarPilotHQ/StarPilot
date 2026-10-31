# Third-party notices

## Jetlink

Jetlink's comma client is copyright (c) 2026 Zeph Leggett and distributed under the MIT License. Its complete license and permission notice are retained in [jetlink_repo/LICENSE](jetlink_repo/LICENSE); source and revision are recorded in [CREDITS.md](CREDITS.md#jetlink-external-inference) and `upstream-sync.json`.

## MRR35 Hyundai angle and radar

The MRR35 radar generator and related Hyundai angle integration derive from the frozen StarPilot snapshot `678af783`, with relevant sunnypilot/opendbc angle-branch ancestry at `cc4b08625a98e94b318cab15e45e05dad58042bd`. Both upstream `LICENSE` and `LICENSE.md` were published for that source. Their scope is not unambiguous, so both applicable notices and the extension-file attribution are retained here without choosing between them. See [CREDITS.md](CREDITS.md) for the narrow source map.

### Upstream `opendbc` `LICENSE` notice (MIT)

Copyright (c) 2020, Comma.ai, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### Upstream extension file notice

> Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
>
> This file is part of sunnypilot and is licensed under the MIT License.
> See the LICENSE.md file in the root directory for more details.

### Upstream `LICENSE.md` notice (published as “Custom MIT License”)

The following notice is reproduced verbatim from the reference branch:

> # Custom MIT License
>
> Copyright (c) 2024, Haibin Wen, SUNNYPILOT LLC
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to view and modify the Software, subject to the following conditions:
>
> 1. **Permission Required**: Permission Required for Commercial, For-Profit, or Closed Source Use: Use of the Software, in whole or in part, for any commercial purposes, for-profit projects, or in closed source projects requires explicit written permission from the original author(s).
>
> 2. **Redistribution**: Any redistribution of the Software, modified or unmodified, must retain this license notice and the following acknowledgment:
>    "This software is licensed under a custom license requiring permission for use."
>
> 3. **Visibility**: Any project that uses the Software must visibly mention the following acknowledgment:
>    "This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use."
>
> 4. **No Warranty**: THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
>
> Contact sunnypilot Support <support@sunnypilot.ai> for permission requests.
>
> ---
>
> Haibin Wen, SUNNYPILOT LLC

Required upstream acknowledgments:

> This software is licensed under a custom license requiring permission for use.
>
> This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.

For commercial, for-profit, or closed-source use, consult the upstream notice and obtain any
permission it requires. The upstream repository's simultaneous publication of two differently
scoped notices should be clarified with the relevant copyright holders before relying on one to
the exclusion of the other.

## BluePilot Ford support

The Ford CAN FD camera-radar filtering includes work adapted from BluePilot's `bp-7.0` source, through StarPilot `2efe6017bfe14cee4807833e05f9a7f6ba255cdb`. See [CREDITS.md](CREDITS.md#ford-support-adapted-from-bluepilot) for contributors and source revisions. The upstream extension-file and `LICENSE.md` notices reproduced above also apply to that source; they are retained without resolving the difference between their published license descriptions.

## Continental ARS510 radar

The optional RAV4 radar decoders and `toyota_tss2_ars510.dbc` are adapted from
<https://github.com/ds-sebastian/ars510-radar> at
`fb5b29c2b5999f34afeb6f7ac494a73949d66c67`.

MIT License

Copyright (c) 2026 ARS510 radar contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## PulseAudio server for Spotify

`openpilot/starpilot/soloist/pulseaudio/` contains unmodified AArch64 binaries from Ubuntu 24.04 packages: PulseAudio 16.1 (LGPL-2.1-or-later), libltdl (LGPL-2.1-or-later), libtdb (LGPL-3.0-or-later), libsoxr (LGPL-2.1-or-later), liborc and speexdsp (BSD-style). The complete copyright and license notices are in that directory's `licenses/`, package hashes in `PACKAGES.sha512`, and the corresponding source is available from Ubuntu's archive for the exact versions listed in its README. The libraries are dynamically linked and may be replaced with `update.sh`.

## Galaxy Android companion tunnel

`openpilot/starpilot/galaxy/bin/hev-socks5-tunnel_linux_arm64` is the upstream
[hev-socks5-tunnel 2.18.0](https://github.com/heiher/hev-socks5-tunnel/releases/tag/2.18.0)
static Linux ARM64 release, under the MIT license. Its notice is retained in
`openpilot/starpilot/galaxy/bin/LICENSE.hev-socks5-tunnel`; the adjacent SHA-256
file pins the downloaded artifact. The Android companion uses the Kotlin
standard library (Apache-2.0) and the Gradle wrapper (Apache-2.0); no third-party
UI framework is bundled.

## Galaxy Bluetooth companion source snapshot

The Galaxy Bluetooth phone bridge and iOS app from https://github.com/StarPilotHQ/StarPilot
(natehuby76, PRs #224/#225) are included under `openpilot/starpilot/galaxy_bluetooth/`
and `openpilot/starpilot/companion/ios/`, with their license files (`UPSTREAM-LICENSE`, `openpilot/starpilot/companion/ios/UPSTREAM-GALAXY-LICENSE`).
The bridge runs on openpilot's Python with PyCryptodome 3.23.0 (BSD/public-domain
components, from opendbc). Its other dependencies are vendored unmodified, as
pure-Python sources, in `openpilot/starpilot/galaxy_bluetooth/vendor/`:
dbus-next 0.2.3 (MIT), ifaddr 0.2.0 (MIT) and Zeroconf 0.151.3
(LGPL-2.1-or-later). Their license texts are in `vendor/licenses/`, and
`vendor/VENDOR.json` records each PyPI source file and its SHA-256. Zeroconf is
kept as separate, unmodified source files that can be replaced with another
compatible version, as the LGPL requires.

## Subaru angle support from jacobw

The Subaru angle implementation includes work from Jacob Waller's `jul-angle-based` openpilot/opendbc branches and their upstream contributors. See [CREDITS.md](CREDITS.md#subaru-angle-steering-from-jacob-wallers-fork) and `opendbc_repo/opendbc/car/subaru/ORIGIN_HISTORY.tsv` for pinned sources, original authors and titles. The source repositories publish the comma MIT license; the complete applicable notice remains in the project `LICENSE` and `opendbc_repo/LICENSE`. StarPilot's adaptations preserve additional native and caller safety guards.
