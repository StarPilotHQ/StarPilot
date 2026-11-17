# Usage statistics

The manager worker reports to `stats.firestar.link` after a drive and
once after startup with a valid clock and a previously verified model.
Fresh installs wait for an observed running model before reporting; a saved
model selection is not evidence that the model ran. It starts requests only while offroad.
Reporting failures do not affect manager process health or engagement.

Reports contain the branch and commit, device type, vehicle fingerprint,
last verified running model, cumulative usage, regional location, and settings.
They exclude VIN, recordings, passwords, pairing credentials, and exact GPS.
Location lookup receives whole-degree coordinates and reports a nearby city
center when available, otherwise the coarse coordinates.

Settings come from the existing typed UI owners. Ordinary Boolean, bounded
numeric, and fixed-choice rows are collected automatically. Raw Params dumps,
source documents, actions, free text, and user-defined sound-pack names are
excluded. Each field records the saved value and its owner-defined default where
available; it does not imply that the feature was actively controlling the car.

The Influx write API uses organization and bucket `StarPilot` with built-in
client authentication. Operators can override it with `STARPILOT_STATS_TOKEN`
or `/data/starpilot/analytics/token` (a regular file with mode `0600`, owned by
root or the manager user). `STARPILOT_STATS_TOKEN_FILE` selects another file.
`UsageStatsStatus` records the local reporting state without secrets or response
bodies. `UsageStatsState` retains counters and sanitized last-drive context.

Run the mocked network, privacy, owner, and lifecycle checks with:

```sh
./dev python -m pytest -q openpilot/starpilot/analytics/tests
```

These checks also run in `./test`. No test sends statistics to production.
