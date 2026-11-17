# Galaxy phone bridge

Comma-side Bluetooth bridge and LAN discovery for the phone companion apps.
The apps are maintained separately, with their own licenses and build instructions:

- [Android companion](https://github.com/dirwin31/StarPilotAndroid/tree/Play-Dev).
- [iOS companion](https://github.com/natehuby76/StarPilotBT/tree/work/secretgood-companion-sync/openpilot/starpilot/companion/ios).

This bridge does not load source from either app repository.

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

## Troubleshooting on comma

```sh
cat /data/galaxy-ble/setup-status.json
journalctl -u galaxy-ble-fork.service -n 100 --no-pager
journalctl -u galaxy-lan-discovery.service -n 100 --no-pager
```

Pairing or forgetting a phone doesn't restart the bridge; it reloads the phone
list within a second, and forgetting one phone disconnects only that phone.
Tapping Retry restarts the bridge, which drops any connected phone.

## Tests

```sh
python -m unittest discover -s openpilot/starpilot/galaxy_bluetooth/tests -v
```
