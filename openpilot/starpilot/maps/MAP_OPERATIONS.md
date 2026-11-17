# Parked offline maps

The Linux manager runs one `map_snapshot_operations` child while offroad.
Galaxy and native UI use its private local socket; neither launches a map
downloader. Native System/Software currently shows status only. Authenticated
Galaxy offers region review, start, progress and cancellation. The existing
map observation and driving-control boundaries are unchanged.

The owner uses only the generated, source-checked Mapd package and its bundled
region catalog. A request identifies one named region, a transfer budget, a
new-storage budget and the expected current generation. The Go backend first
prepares a complete generation containing the new region and the currently
selected offline regions, then atomically selects it if the expected selection
still matches. Overlapping groups use the new download; unchanged validated
tiles are hard-linked without duplicating their payload on disk. Separate
regions do not require downloading the area between them. Combined coverage
retains the existing 65,536-tile bound. Previous generations remain stored. Selection
applies at the next shadow start; it does not replace a running map provider
or qualify its candidate speed for control. Deletion,
scheduled refresh and full navigation remain outside this workflow.

Fresh manager, device and Panda offroad evidence is required before a download
and through preparation/selection. Authority loss or cancellation terminates
and reaps the owned child. Process output, request sizes, transfer, new storage
and operation time have explicit limits. An interrupted operation is reported
without silently retrying or treating partial files as selected. Conflicting
requests cannot start competing downloads or overwrite a newer selection.

The socket and interruption marker live under the writable, namespace-aware
StarPilot storage root, with private directory/socket permissions and same-user
peer verification. HTTP requests require the existing local Galaxy session and
same-origin checks. Source URLs, arbitrary filesystem paths and raw geographic
bounds are never accepted from either UI. An `OPENPILOT_PREFIX` uses its own
`/data/media/0/starpilot-<prefix>/maps/offline` store for downloads and provider
startup, keeping normal map selection independent. Missing provider packages or
offroad evidence leave management unavailable.

Tests cover HTTP/session behavior, the Vue request lifecycle, first-use storage,
selection conflicts, interruption, malformed/slow/disconnected clients and
child cleanup. Synthetic data and process tests do not establish real-region
coverage, dataset freshness, download-server availability or road suitability.
