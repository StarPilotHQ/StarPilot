# Tracked dependency sources

Dependencies are ordinary folders in this repository. Clone normally, run
`tools/op.sh setup`, and commit application and dependency edits together.

`upstream-sync.json` records the last integrated upstream commit and its pristine
tree for each dependency. A recorded tree identifies the merge base; it does not
claim that the current folder is unchanged. Optional import exclusions are
explicit, and an optional `include` list imports only the listed upstream paths. Required third-party license notices remain in the source folders.

Run `python3 tools/vendor/check.py` to validate the tracked working tree, or add
`--revision REV` to validate a fetched commit before checking it out. Validation
checks layout and provenance fields; it does not establish runtime compatibility.

Maintainers can preview a dependency update with:

```sh
python3 tools/vendor/sync.py panda FULL_UPSTREAM_COMMIT_HASH
```

The base is the previously integrated snapshot, ours is the committed local
folder, and theirs is the selected upstream snapshot. The command requires a
clean checkout. It downloads objects into an external cache, reports conflicts
without changing the checkout, and preserves local edits through a three-way
merge. Use `--source-repo /path/to/upstream/repo` for an existing local object
cache. Add `--apply` to stage a conflict-free result and updated manifest. Review
the diff, run the affected tests, and create one ordinary local commit. The tool
does not commit, push, or resolve conflicts by discarding local changes.

Updating the main openpilot base is a separate integration task. Review the
upstream changes and dependency pins together, resolve any dependency tree
replacements explicitly, and update the main manifest revision only after
integration. Never accept a gitlink in place of a source folder. Updater and CI
checks reject that layout.

Dependency provenance checks do not replace runtime, firmware, or vehicle
qualification for an update.

## Local Mapd integration

`mapd_repo/` contains tracked source with StarPilot changes. The static Linux
ARM64 executable and its source/hash manifest are also tracked, under
`openpilot/starpilot/maps/provider/`. The upstream pin identifies the original
merge base; it does not require this source folder to match upstream exactly.

The local `limit_preview.go` helper publishes bounded, data-only speed-limit
previews from the provider's qualified road evidence. Python SLC owns advance
timing, offsets, confirmations, overrides and vehicle commands. Preserve the
preview lifecycle hooks, matching/schema changes and Go/Python regression tests
together when updating the provider.

Fixed one-way generation and closed-loop entry are backported from
[pfeiferj/mapd PR #105](https://github.com/pfeiferj/mapd/pull/105), merged as
`92a22aaac0396330d0db40432407f011ff723553`. Reverse one-way roads reverse their
nodes and both directional speed-tag pairs. Closed two-way loops cannot qualify
an early preview because their entry direction is ambiguous. Generator changes
apply to newly generated tiles; existing downloaded tiles are not rewritten.

Use the sync command above with dependency `mapd_repo` for upstream updates.
It preserves committed local changes through a three-way merge and stops on
conflicts; a normal update does not require manually reapplying this feature.
Review the merged behavior and run the provider and host SLC/wire tests. Follow
`mapd_repo/INTEGRATION.md` if changed schemas require regenerating Go bindings.

After changing provider source or its upstream pin, run `./build --mapd`, then
`python3 tools/release/stage_mapd_provider.py --source . --require-tracked`.
Commit the source, bindings, tests, executable and manifest together. Existing
package checks reject a binary whose recorded source or upstream pin is stale.
