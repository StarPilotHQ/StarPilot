# Bundled BlueALSA 5.0.0

Linux/ARM64 binaries and matching PCM/CTL plugins come from upstream commit
`1935d6dcb8975f2d7a51aaafe61538d157224623`, without decoder patches. Keep `bin/`,
`lib/`, version, source revision, checksums and license files together. Activation
validates checksums, ELF architecture and executable permissions.

## Integration and rollback

`phone_profile.py` temporarily selects the daemon through `bluealsa.service` using
`95-starpilot-bluealsa5.conf` under `/run`. Private ALSA configs select the bundled
plugins; v5 plugins must not run against the stock v4 daemon. Both paths preserve
software volume; playback opens at 100%, unmuted. Capture uses `hwcompat "silence"`
across transport interruptions. The launcher enables SBC and LDAC, keeps an output
transport alive for 5 s after its last client closes, and retains the existing
`Nice=-5` policy. Spotify Bluetooth output also advertises the A2DP receiver.

System binaries, plugins, bonds and v4 state remain intact. V5 state lives under
`/data/starpilot/bluealsa5/lib/bluealsa`. Activation failure removes the temporary
overrides and restores the prior profile before restarting the system daemon.
Unknown overrides are rejected.

To roll back, check out the old commit and reboot. Restarting openpilot instead
works when the old supervisor runs its profile cleanup: the compatible
`90-starpilot-phone.conf` triggers a restart, and the self-contained launcher copied
to `/run` removes the v5 override when the checkout's `VERSION` disappears. Checkout
alone cannot replace a running daemon. Reboot clears both overrides independently.

## Build and validation

Read [laptop/device build instructions](../../../../docs/how-to/laptop-device-build.md).

```sh
git clone --branch v5.0.0 https://github.com/arkq/bluez-alsa.git /tmp/bluealsa-5
openpilot/starpilot/soloist/bluealsa/build.sh --setup
openpilot/starpilot/soloist/bluealsa/build.sh /tmp/bluealsa-5
.venv/bin/python -m pytest openpilot/starpilot/soloist/tests -q
```

Setup downloads dependencies; the build exports the pinned upstream commit and
runs offline. `BUILD_IMAGE` and `BUILD_PACKAGES.txt` record inputs; apt package
availability is not pinned. `UPSTREAM_TEST_RESULTS.txt` records 19 passing programs.
Local integration tests cover integrity, configuration, activation failure and
old-checkout recovery with simulated service calls. They do not establish audible
quality or device ABI compatibility. Device acceptance must exercise phone
reconnects, pause/skip, Android Auto, Bluetooth output and rollback.

See upstream [release notes](https://github.com/arkq/bluez-alsa/blob/v5.0.0/NEWS.md)
and [plugin options](https://github.com/arkq/bluez-alsa/blob/v5.0.0/doc/bluealsa-plugins.7.rst).
