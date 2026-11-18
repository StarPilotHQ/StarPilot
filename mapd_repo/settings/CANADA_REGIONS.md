# Canadian download regions

The `ca_province` catalog covers all ten provinces and three territories.
Bounds are the envelopes of Geofabrik's Canada subregion extract polygons,
retrieved from https://download.geofabrik.de/index-v1.json on 2026-11-18.
The index SHA-256 was `076fb6d5d0df3efae47af91a8e24ff69d84da05872107958f99341a16a04da22`.
These are deliberately conservative extract envelopes (including coastal
coverage), not claims of precise administrative borders. Mapd rounds them
outward onto its existing two-degree archive grid from
https://map-data.pfeifer.dev/offline/. This uses existing provider archives,
not Geofabrik PBF downloads. Availability in the catalog means bounded, valid
coverage; it does not guarantee every live archive is currently reachable.

`nation.CA` and its existing bounds are unchanged for saved-selection
compatibility. Its legacy northern extent is 73.23 degrees; northern territory
choices cover their own full extract envelopes beyond that legacy extent.
Existing downloaded generations are not removed or migrated by this change.
The usual parked, transfer-budget, verification and atomic selection rules
remain in force. A missing archive leaves the previous selection intact.

The new embedded catalog requires a rebuilt Mapd provider and manifest. The
Galaxy UI only presents province choices returned by that installed provider,
so an older package continues to present its old catalog without invented
or unusable province buttons.
