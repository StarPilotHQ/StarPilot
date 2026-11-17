# Galaxy companion

Galaxy is StarPilot's Vue browser interface for device status, saved preferences,
and local tools. The managed service listens on port 8082. On the device's local
network, open the address and QR code shown by the native Galaxy page. Direct
local connections establish a browser session automatically. Remote or proxied
connections use the existing password-backed session; account pairing and a
managed remote tunnel are not available.

## Run and test

`./dev galaxy` starts a desktop server on loopback. The Python entry point is
`python -m openpilot.starpilot.galaxy.server`; its default host is `127.0.0.1`.
Use `--host 0.0.0.0` only when a LAN bind is intended. The managed service is
selected by the manager and can be disabled with `STARPILOT_GALAXY_DISABLE=1`
in the manager environment. A manual server cannot share its port with the
managed service. Static browser files are under `web/`, with no JavaScript
package installation required.

For frontend or backend work, `./dev galaxy --live [--port N]` builds the shared
host cache, releases its lock so `./c3`, `./c4` and `./onroad` can run alongside,
then follows edits under this package. An open page reloads itself after each
edit; other changes here restart the server first. Refresh a page opened before
the session once. Edits outside the package need `./dev sync` and a restart.
The reload script exists only in the host cache, never in this directory.
Unsaved drafts and pending saves defer automatic reload until they are resolved.
Add `--no-autoreload` to keep following edits but refresh by hand. Only one live
Galaxy session can own a shared host cache at a time, regardless of port.
Run the package tests with `./dev pytest openpilot/starpilot/galaxy/tests`.
Browser JavaScript tests use Node 24; set `STARPILOT_NODE` to an existing Node
24 executable when needed. A static preview can serve `web/` over loopback;
it labels its sample System Monitor data and cannot edit a device.

StarPort log uploads use the built-in developer Discord webhook.

## Pages and owners

| Page | Current source and operations |
| --- | --- |
| Home and Tools | Local status cards, navigation, theme, drawer, pinned tiles, and hash routes. |
| System Monitor and Plots | Bounded local process/device and driving-message samples. Unavailable data stays unknown; leaving a page stops its polling. |
| Logs and Routes | Crash-report previews and a shared route workspace for camera footage, driving UI replay, segment details, and scoped footage/replay/log downloads. |
| Software & Updates | Parked update controls and a newest-first version list. Installed and fully cached commits are marked; selecting a commit offers installation or download and installation. Initially shows up to two newer commits, the installed commit, and the previous installed commit. Collapsed gaps above and below the installed commit expand independently; older history remains available below the previous version. Each gap shows its own remaining count and reveals at most ten commits per click inside a scroll area bounded by the viewport. Release notes follow the selected installed or prepared version. |
| Driving and Device Preferences | Saved settings through typed owners shared with native controls, including SLC, lateral and longitudinal tuning, conditional modes, display, cameras, and sounds. |
| Vehicle Controls | One saved manual vehicle choice for the next start, or Auto for normal detection. |
| Models and Model Laboratory | Verified model catalog management and parked Laboratory configuration. Paired inference is unavailable in the production model runner. |
| Bluetooth | Local BlueZ adapter and device operations, including pairing confirmation. Bluetooth audio routing is unavailable. |
| Maps | Local map status and a parked catalog-region download, progress, or cancellation. A completed selection takes effect at the next map service start. |
| Cameras and Sentry | Saved PiP and V-ASM settings and bounded Sentry event metadata. The crop editor uses a browser-local still image; it does not upload that image. |
| Sound packs | Catalog status, verified download and cancellation through the local sound owner. |
| Galaxy Access & Install | Remote link pairing, Galaxy installation, and an opt-in Wi-Fi hotspot. Local addresses are on Home. |

`Refresh` and branch changes request filtered
history metadata in a separate Git cache and worker. They remain available during
background staging. `Check for updates` also wakes the background updater after the metadata check. When automatic downloads are enabled, a newer commit wakes
the separate download phase. Update and download actions retrieve the chosen
snapshot's missing file contents; version browsing uses `--filter=blob:none` with
bounded history depth. The branch selector lives in the version list and refreshes its metadata without changing the updater target. `Download only` prepares the selected commit without restarting; `Select previous version` selects the saved branch and commit without installing it. Downloads report Git object progress and finalization
reports copied files; Git garbage collection is outside the finalization path.

Automatic installation is opt-in and defaults off. Its five numeric cron fields
(minute, hour, day, month, weekday) default to `0 0 * * *`: daily at midnight in
the saved local timezone shown in Advanced options. The timezone selector saves an IANA timezone with the schedule, including daylight-saving rules. The updater only restarts for a
validated, prepared update while idle and freshly confirmed offroad. It skips
onroad occurrences and reserves each scheduled minute before restarting to avoid
reboot loops. These preferences live in the StarPilot software state directory.

The Vue router in `web/js/app.js` and the route handlers in `server.py` are the
current route map. API groups include `/api/auth`, `/api/system/monitor`,
`/api/software/status`, `/api/settings`, `/api/models`, `/api/maps`,
`/api/sounds`, `/api/bluetooth`, `/api/vehicle-selection`, `/api/recordings`, `/api/replay`,
`/api/sentry/events`, and `/api/flm`. An unknown API route returns an error;
there is no arbitrary Params or shell-command endpoint. The retired
`POST /api/companion` cross-origin bridge is no longer supported. Local hotspot
access uses the same authenticated, same-origin HTTP API as local Wi-Fi.

## Access and operation rules

Home's outside temperature is an Open-Meteo weather estimate near the last
saved navigation location, not a vehicle sensor reading. The authenticated
weather endpoint fetches asynchronously, rounds coordinates to two decimal
places before sending them to Open-Meteo, caches successful requests for ten
minutes and backs off failed requests for one minute. Changed locations may
refresh after one minute. Data older than thirty minutes is unavailable;
no saved location means no weather request. The Home page respects Use Metric
and stops weather polling when hidden. Device thermal sensors are not cabin-air
thermometers, so no cabin temperature is inferred from CPU, modem or board heat.

### Optional reverse proxy

While offroad, open **Toggles → Developer → Reverse proxy**. Enter the proxy IP
address (for example `192.168.1.2`) and public HTTPS origin (for example
`https://galaxy.example.com`), enable reverse proxy, and save with confirmation.
Settings persist beside Galaxy's access credentials and apply immediately;
no startup-script edits or restart are needed. Keep direct IP access available
when changing or disabling a proxy you are currently using.

The address must be the actual proxy socket peer as seen by the comma, not the
browser address. The origin must be the exact external HTTPS origin (include
a nonstandard port if used), without a trailing slash or path. The proxy must
preserve the external Host and browser Origin headers and forward to Galaxy's
HTTP port. Forwarded client-IP headers are not trusted. Only this peer and
hostname gain the exception; all proxy requests require an existing Galaxy
password-backed session, even without forwarding headers. Configure a Galaxy
password through the existing device controls before using the proxy. Direct
IP-and-port access from other clients remains unchanged. Proxy access is off
by default. Invalid configuration never grants proxy access.

The hotspot is off by default. While parked, open **Galaxy Access &
Install → Galaxy Wi-Fi Hotspot**, enable automatic broadcast, and save a password
(12–63 printable ASCII characters without spaces). Join `TheGalaxy-<last four
characters of dongle ID>` and bookmark `http://172.31.254.1:8082/`; stay connected
if your phone warns of no Internet. Saved settings survive restarts. It runs
without Android Auto or another Wi-Fi connection, requires an available Wi-Fi
radio, and follows connected Wi-Fi/Android Auto's channel. Network changes may
interrupt access; changing the password or disabling it disconnects clients.
It provides no Internet gateway; the public Galaxy link still needs Internet
and its tunnel. Sustained projection and standalone reconnects still need
hardware validation.

The server requires the actual peer and socket destination to be local for
passwordless access. Proxy headers select the password-backed path. The Host
must match the destination address or an independently discovered local DNS
identity, including the listening port. DNS identities come from the device's
OS hostname (with its local resolver search domains) or the socket destination's
reverse DNS records. Each name must forward-resolve only to that exact IPv4
destination. A request-selected name merely resolving to a private address is
not trusted, because that would allow DNS rebinding. This trusts the local
OS/DHCP/DNS infrastructure; arbitrary aliases without that identity metadata
are not admitted as API/session origins. For those aliases, direct local
GET/HEAD of exactly `/` without Origin redirects to the actual socket's
IP-and-port address. Browsers follow this automatically without a password or
hostname registration, but the address bar changes to the IP. Curl to the DNS
URL needs `-L`; with a manually overridden Host, request the returned Location
without that override instead. The redirect sends no cookies and never applies to API paths,
writes, forwarded requests, or remote transport. DNS discovery has a one-second
bound and a five-second destination-specific cache. Literal IP access remains available
when discovery fails. A manually overridden portless Host is accepted only for
GET/HEAD of the landing document without Origin, never API/session requests.
JSON writes require the same exact origin.
Sessions use opaque HttpOnly, SameSite=Strict cookies; a local session cannot
authorize a remote request. A session is checked again after slower reads and
before sensitive results are returned. The server bounds connections, request
bodies, file reads, and operation concurrency.

Saved-setting writes use an authenticated preview/confirm transaction. Their
owners recheck current saved-source identity, vehicle eligibility, and fresh
offroad evidence before committing. A saved choice is not proof that a driving
feature is active. Map, model, Bluetooth, sound, and other operations each have
their own authority checks; browser text is never a command to execute. A lost
save response has an unknown outcome: refresh before repeating an action.

Status endpoints distinguish missing, invalid, stale, or changed inputs from
valid zero values. Crash and recording readers recheck file identity and skip
active or symlinked sources. Camera footage is remuxed from closed segments in
a bounded temporary cache. Combined footage and log archives are limited to
512 MB; larger routes use individual segment downloads. Plots and System Monitor stop or retire
requests when hidden, signed out, expired, or superseded. Sample rates and
message ages describe observed data, not driving-control timing.

Routes keep one selected segment across Footage and Replay with UI. Raw camera
and log downloads use a complete selected-route inventory, bounded at 512 segments;
the route list uses a smaller summary. Replay uses the existing parked-only owner,
so native replay, browser preview, and export cannot run competing renderers.

Replay exports render camera and UI together at normal speed, then stream-copy
into a validated MP4. Segment exports end at the engine's recorded segment boundary;
route exports concatenate replayable recorded ranges and omit gaps. An export
freezes the last preview's display settings for the same browser and route, or
snapshots the current display settings when no preview exists. Closing or hiding
Galaxy stops its preview but leaves its export running; explicit Cancel stops it.
The browser receipt survives a page refresh within the same authenticated session while Galaxy remains running.
Authority loss, source changes, time limits, or insufficient storage stop the job.

Only one completed export is cached, for 30 minutes or until replaced/deleted.
An active download holds the file against eviction. The owner reserves the larger
of 5 GiB and 10% of storage, budgets for spool plus final MP4, and caps exports at
32 GiB. Completed exports are private, authenticated, range-capable downloads;
owner restart clears the cache. Device replay binaries must be rebuilt with the
matching source. GPU rendering and the hardware encoder require device validation.

Galaxy does not provide cloud drive history, remote account setup, or updater actions. See
[model contracts](../models/README.md) for Laboratory runtime eligibility and
[map operations](../maps/MAP_OPERATIONS.md) for offline-region ownership.

## Source and notices

The UI adapts StarPilot's earlier Vue Galaxy design; the adapted StarPilot
source remains under the repository [LICENSE](../../../LICENSE). The bundled
Vue and Bootstrap Icons assets retain their respective MIT notices in
`web/vendor/notices/`. Other asset notices remain beside their files.

## Shared display language

Galaxy → Toggles → Localization selects the persistent openpilot `LanguageSetting`.
Galaxy reads the authenticated `/api/localization` endpoint and applies the same
`selfdrive/ui/translations/app_*.po` catalogs used by the device. Language changes
apply to an open Galaxy page within two seconds. Translation changes presentation
text and accessibility labels; request values, parameter keys, input values and
technical logs remain unchanged. The native large/compact renderer translates
measurements and drawing together and loads bundled Unicode fonts as needed.

`starpilot/ui/translation_strings.py` and `translation_messages.json` retain the
shared UI sources for the existing catalog update tool. The six requested
languages each contain 5,465 catalog entries: stock openpilot labels, Galaxy
pages and settings descriptions, native UI labels, formatted status messages
and driving alerts. Formatted translations retain the original values and
identifiers. Unlisted runtime diagnostics and external content retain English
fallback; user names, addresses, media metadata and technical logs are literal.
Boot/login initially uses English until the authenticated language response.
Native text is translated before measurement, wrapping and elision; translated
values carry a marker so they cannot be translated twice.
