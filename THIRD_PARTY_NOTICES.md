# Third-Party Notices

This file supplements StarPilot's root `LICENSE`. StarPilot-original contributions are offered
under that MIT license. Third-party material remains subject to its original terms; inclusion here
does not relicense it or imply endorsement by an upstream project or contributor.

## BluePilot `bp-7.0` Ford support

Portions of StarPilot's Ford lateral control, CAN integration, panda safety logic, vehicle data,
and related tests are adapted from the public BluePilot repository:

- Source: <https://github.com/BluePilotDev/bluepilot/tree/bp-7.0>
- Audited `bp-7.0` snapshot: [`e1d051d7ba270261b4455068bd68f1a58db15a4a`](https://github.com/BluePilotDev/bluepilot/commit/e1d051d7ba270261b4455068bd68f1a58db15a4a)
- Historical reconstruction: [CREDITS.md](CREDITS.md#ford-support-adapted-from-bluepilot)
- Provenance and contributors: [CREDITS.md](CREDITS.md)

## sunnypilot Hyundai, Kia, and Genesis support

Portions of StarPilot's HKG angle steering, CAN integration, panda safety enforcement and tests,
firmware fingerprints, platform data, and cruise-button management are adapted directly from the
public sunnypilot repositories:

- Source: <https://github.com/sunnypilot/sunnypilot/tree/hkg-angle-steering-2025>
- Audited superproject snapshot: [`cfb38312db33779f4727c983d372474a56ccb5d8`](https://github.com/sunnypilot/sunnypilot/commit/cfb38312db33779f4727c983d372474a56ccb5d8)
- Source: <https://github.com/sunnypilot/opendbc/tree/hkg-angle-steering-2025>
- Audited angle-branch snapshot: [`cc4b08625a98e94b318cab15e45e05dad58042bd`](https://github.com/sunnypilot/opendbc/commit/cc4b08625a98e94b318cab15e45e05dad58042bd)
- Later HKG history was audited against `sunnypilot/opendbc` `master` at
  [`f95f996f5917dcbbf2e32fe51b606a24cf836af6`](https://github.com/sunnypilot/opendbc/commit/f95f996f5917dcbbf2e32fe51b606a24cf836af6).
- Historical reconstruction and contributors: [CREDITS.md](CREDITS.md#hyundai-kia-and-genesis-support-adapted-from-sunnypilot)

## Continental ARS510 radar

The optional RAV4 radar decoders and `toyota_tss2_ars510.dbc` are adapted from
<https://github.com/ds-sebastian/ars510-radar> at
`7ff9485a454130bc5765c9f715046d2694c655a6`.

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

## Applicable upstream license notices

The upstream repositories and branches identified above publish both `LICENSE` and `LICENSE.md`.
Their READMEs point to `LICENSE` for openpilot licensing, while some extension files point to
`LICENSE.md`. Because the scope of those two notices is not unambiguous, StarPilot preserves both
and treats the more restrictive notice conservatively for upstream-derived material. This is a
record of the published notices, not a legal conclusion about their scope.

### Upstream `LICENSE` notice (MIT)

Copyright (c) 2018, Comma.ai, Inc.

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

Several upstream Ford and HKG extension files carry this file-level notice:

> Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
>
> This file is part of sunnypilot and is licensed under the MIT License.
> See the LICENSE.md file in the root directory for more details.

StarPilot's Ford and HKG integrations were informed by extension files carrying this notice. The
reference to “MIT License” conflicts with the nonstandard restrictions in the referenced upstream
`LICENSE.md`; both texts are retained here rather than silently choosing between them.

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
