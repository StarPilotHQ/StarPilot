# Galaxy phone bridge

Comma side of the Android and iPhone companion apps (`openpilot/starpilot/companion/`).

- `bootstrap.py` — the `galaxy_phone` process. Once a phone has been paired (or
  Retry is tapped), it starts the Bluetooth bridge and LAN discovery as systemd
  units. It leaves Bluetooth off if it's off in comma settings. Nothing is
  downloaded: `bridge/run.sh` runs the bridge on openpilot's Python with the
  vendored packages in `vendor/` (see `vendor/VENDOR.json`).
- `bridge/server.py` — Bluetooth GATT server. Requests are forwarded to the local
  Galaxy server by `bridge/proxy.py`, which blocks Wi-Fi-only features.
- `bridge/lan_discovery.py` — advertises `_sp-galaxy._tcp` on local Wi-Fi/Ethernet.
- `bridge/pairing.py` — the shared pairing key, `/data/galaxy-ble/pairing.json`.
- `bridge/claim.py` — the one-shot pairing window that hands a phone its own key.
- `bridge/ancs.py`, `bridge/ancs_dbus.py`, `bridge/bond.py` — iPhone notifications (below).
- `phone_ui.py` — the Pair phone button and screen in comma Settings → Bluetooth.

## Per-phone keys

Every phone has its own key in `/data/galaxy-ble/pairing.json`, so a phone can be
forgotten on its own (Settings → Bluetooth). Two optional app fields keep the list
tidy; older apps without them still work:

- The claim reply includes `"id"` (8 hex characters), comma's id for that phone's
  key. Apps should store it with the key.
- With its first Bluetooth request (the `/_bridge/health` check), an app may add
  `"phone": {"name": "Danny's iPhone", "replaces": "<id of its previous key>"}`
  inside the encrypted request. comma shows the name in its phone list and removes
  the replaced entry.

## Pairing a phone (one step for the app and music)

Settings → Bluetooth → **Pair phone** opens comma's two-minute pairing window. It is
the same window that lets a phone pair for music (`accept_pair`), and it also lets
one app claim its key. Nothing else is accepted without a key.

1. Comma writes `/dev/shm/starpilot-phone-claim.json` (mode 0600) with the deadline,
   its Bluetooth address and name. The bridge reads it on every request.
2. The app connects to the Galaxy GATT service and reads INFO (`0x01` + 16-byte challenge).
3. The app writes one unsealed frame, in the normal packet framing
   (`0x01 | seq u32 | 4-byte length + body`, body below):
   `"GCL1" | 65-byte uncompressed P-256 public key | UTF-8 phone name (at most 160 bytes)`.
4. Comma checks that the point is on the curve, issues a new key for this phone, and
   returns one frame by the **read-stream** flow (the app reads TX until it has the whole
   frame, then writes the ACK `0x02 | last seq u32`):
   `"GCR1" | 65-byte comma public key | 12-byte nonce | AES-256-GCM ciphertext | 16-byte tag`.
5. The shared key is `HKDF-SHA256(salt = the INFO challenge, ikm = ECDH x-coordinate (32 bytes),
   info = "galaxy-claim-v1", length 32)`; the GCM additional data is `"galaxy-claim-v1/reply"`.
   The plaintext is JSON: `{"key": <64 hex>, "id": <8 hex>, "adapterAddress", "adapterName"}`.
6. The window closes on the first claim; a second phone gets no reply. The app stores the
   key and id, then connects normally. This session already belongs to the new key.
7. For music, the app then bonds with `adapterAddress` (Android: `createBond`), or on iPhone
   asks the person to tap `adapterName` in Settings → Bluetooth. The same window accepts it.

## iPhone notifications (ANCS)

The iPhone app can let comma read the phone's notifications through Apple's ANCS. iOS
only offers ANCS on a bonded (encrypted) LE link, so the app first asks for a short bond window.

1. With a normal authenticated request, the app sends `POST /_bridge/notification-bond`
   (empty body). The bridge answers it itself (it never reaches Galaxy):
   - `200 {"ok": true, "seconds": 60}`: for 60 seconds comma accepts LE pairing from this
     phone's Bluetooth connection only (`"seconds": 0` when it is already bonded; a repeat
     request from the same phone returns the seconds left).
   - `409 {"error": <message to show>, "code": ...}`, where `code` is:
     - `pairing_window`: comma's Pair phone window is open; finish it first (until its two
       minutes are up).
     - `busy`: another phone holds the bond window.
     - `disconnected`: the phone isn't connected.
     - a code from Galaxy's `GET /api/companion/bluetooth-ready` (`park_required`,
       `bluetooth_off`, `bluetooth_unavailable`, `starting`, `bridge_starting`), or
       `not_ready` when that check can't be read. Like the Pair phone window, a bond window
       only opens while parked. An already bonded phone skips this check because no window opens.
   - `503 {"error": ..., "code": "unavailable"}`: Bluetooth refused the agent or the pairable change.
2. The app then reads the bond characteristic `bd490006-6dc1-4de7-a7d0-6cdb441f7650`
   (`encrypt-read`, value `0x01`). On an unbonded link iOS starts pairing and shows its own
   pairing prompt. comma registers a `NoInputNoOutput` agent that approves only that
   phone, sets the adapter pairable, and puts everything back when the phone bonds,
   disconnects or the minute runs out. If the Pair phone window opens meanwhile, comma
   steps aside immediately.
3. Once bonded, iOS asks the person to allow notifications for comma (Settings →
   Bluetooth → comma → Share System Notifications). The bridge looks for the phone's
   ANCS service every few seconds, subscribes, and posts each new, non-silent
   notification to Galaxy's `POST /api/companion/notification`. Notification text is
   never logged.

`/_bridge/health` reports `"notificationBond": true` when this is supported.

Known limitation: Android Auto's car pairing (`system/starport/bluetooth_bridge.py`) registers
its own default BlueZ agent. While a bond window holds the default agent, a car pairing started
in that minute may be rejected; retry it once the minute is over. The bridge can't detect this
reliably: Galaxy's `/api/starport/pairing/status` only reports pairing that the asking session
started itself.

## Troubleshooting on comma

```sh
cat /data/galaxy-ble/setup-status.json
journalctl -u galaxy-ble-fork.service -n 100 --no-pager
journalctl -u galaxy-lan-discovery.service -n 100 --no-pager
```

To check that an iPhone has an LE bond for notifications, look for `[LongTermKey]` or
`[PeripheralLongTermKey]` and `[IdentityResolvingKey]` in
`/var/lib/bluetooth/<adapter address>/<phone address>/info`. The journal logs
`Notification bond window closed: paired` and `ANCS client attached`. A close reported as
`disconnected` right after the iOS prompt can still be a good bond, because BlueZ may fold the
phone's random-address object into its existing music device. Check the `info` file.

Pairing or forgetting a phone doesn't restart the bridge; it reloads the phone
list within a second, and forgetting one phone disconnects only that phone.
Tapping Retry restarts the bridge, which drops any connected phone.

## Tests

```sh
python -m unittest discover -s openpilot/starpilot/galaxy_bluetooth/tests -v
```
