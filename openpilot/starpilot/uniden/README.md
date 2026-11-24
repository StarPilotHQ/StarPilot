# Uniden R4/R8 integration

## Setup

Pair an R4/R8 in Galaxy → Bluetooth → Add a device → Search, then open the
**Uniden** tab, select it and choose **Use this detector**. The model is
inferred from the paired device name. The Uniden page (`#/bluetooth/uniden`)
shows connection status, **Change detector**, preferences, colors, frequency
rules and lockouts. Settings require the selected detector to remain paired;
edits require Park. Settings are stored in `UnidenConfig`. Widget size,
position, background transparency and *Hide when no alert* are in the theme
maker layout.

While the large driving UI is active, the feed polls every 100 ms after the
previous exchange completes when the integration is enabled (one second when
off), even with the widget hidden or covered by an openpilot alert. Leaving the
driving UI stops it. Both local Bluetooth and the StarPort owner run the same
exchange.

## Rules

- **Frequency rules.** Up to 32 rules with a label, X/K/KA band, frequency in GHz
  and tolerance in MHz. For a 34.313 GHz head unit use KA / 34.313 / 0 MHz; 2 MHz
  would match 34.311–34.315 GHz. Rules apply everywhere and cannot tell apart
  transmitters on the same frequency. They are stored on the comma, not in the
  detector's mute memory.
- **Mute delivery.** The detector has one general mute command (`BTreqMUTE:1`),
  not a per-signal mute. Automatic mute (rules, lockouts, Quiet Ride, timed mute)
  is sent in the same exchange as the snapshot, only when every active alert
  qualifies, and retried at most every 500 ms. A write is never treated as
  confirmation; only a later alert packet confirms a mute. Tap-to-mute requires
  a fresh snapshot of the same alerts.
- **Hold to lock out.** Holding a live alert for about a second locks it out
  within 300 m of where you are (at the pass point if the car just passed it).
  Remove lockouts under Uniden → Lockouts, including while driving.
- **Learned lockouts** (off by default). A pass of a fixed source (the R8's
  front → side → rear flip, or an R4's strength peak) passed again on separate
  trips at least 10 minutes apart becomes a lockout: 3 passes for X and K, 5 for
  Ka and Laser, for the chosen bands. Drive-bys where the signal is missing count
  as misses; a source is learned only if misses are at most half its passes, and
  a learned lockout whose misses outnumber its passes is removed. A signal more
  than two levels stronger than any seen there still alerts. Unseen learned
  lockouts expire after 90 days.
- **Quiet Ride** (off by default). Mutes the chosen bands while at or under the
  posted limit plus a margin. Needs a fresh GPS fix and a valid limit. A signal
  that appears at strength 5 or more is never quieted.
- **Timed mute** (off by default). Each new X, K or Ka signal beeps for its
  band's delay, then is muted. Laser and suddenly strong signals are not. If the
  signal reaches the band's warn-again strength it stays loud until it clears,
  and StarPilot sends `BTreqMUTE:0` to undo a mute the timer sent (never the
  driver's own), at most three times per set of alerts. *Never* keeps it muted.
- **Full-screen warning.** A segmented line inside the engagement border, with
  optional chevrons toward the signal, glow and veil, for live alerts in the
  chosen bands (Ka and Laser by default), each in its own color. Muted alerts dim
  it to the chosen brightness.
- **Offline badge.** If the selected detector is not connected for 10 seconds
  while driving, a DETECTOR OFFLINE badge replaces the card, or shows at the top
  of the view when the widget is off or covered.

Lockouts and learned sources stay on the comma in
`/data/starpilot/uniden/locks.json`. Nothing goes into route logs or cereal;
cloudlog records only failures, never positions.

## Checks

- `./dev pytest openpilot/starpilot/uniden/tests openpilot/starpilot/galaxy/tests/test_uniden_http.py -q`
- `node --test openpilot/starpilot/galaxy/tests/test_uniden.mjs`
- `node openpilot/starpilot/galaxy/tests/browser/uniden.cjs` (Playwright; supports
  `PLAYWRIGHT_MODULE` and `PLAYWRIGHT_EXECUTABLE_PATH` overrides).
