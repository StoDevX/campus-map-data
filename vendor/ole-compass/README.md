# ole-compass, vendored

The pedestrian graph these three files describe is the only survey of St. Olaf's
walkable network that knows about **indoor connections, stairs and building
entrances**. Nothing in the college's ArcGIS services or in OpenStreetMap
carries any of it, which is why it is worth keeping alive.

Copied verbatim from [`StoDevX/ole-compass`][oc] `data/`, at commit
`e07ac3b` (`e07ac3bd3f8fd3990573723cff61a1585eb14cf3`), the tip of its default branch, dated 2016-09-19.

[oc]: https://github.com/StoDevX/ole-compass

## Why vendored rather than fetched

**ole-compass is an archive, not an upstream.** It is a 2016 C++/OpenGL course
project whose last commit is dated 2016-09-19 — roughly a decade before this
was written. There is no release, no tag, and no maintainer to track. Fetching
it at build time would mean a scheduled job depending on a dormant repository
staying reachable, in exchange for updates that are never coming.

Copying it makes this repo the owner of the data, which is the honest position:
nobody else is maintaining it.

## What the files are

| File | |
| --- | --- |
| `entireMapCalc.txt` | The graph. 228 nodes, then an adjacency list. |
| `buttonList.txt` | 33 building entrance buttons — the node id for each building's door. |
| `specialButtons.txt` | UI buttons of the original program (Reset, pan arrows). Unused here; kept so the set is complete. |

### The coordinates are pixels, not geography

```
228
0	535 369	0
1	406 328	1
```

Each node row is `id  x  y  indoor`, where `x`/`y` are **pixels on the original
program's campus bitmap** (`StoMap.png` in ole-compass), not longitude and
latitude. Turning them into coordinates is the whole job of
`scripts/build_routing.py`, which fits them against this repo's own scraped
buildings and walkways.

The adjacency rows that follow are `id  degree  (neighbour, edgeType)…`, where
the edge types are 0 sidewalk, 1 indoor, 2 stairs, 4 path.

## Licence

ole-compass carries **no LICENSE file**. It is a StoDevX repository and this is
a StoDevX repository, so this is a copy within the same organisation rather than
a redistribution — but the absence is recorded here rather than papered over. If
this data is ever published under an explicit licence, that should be settled
first.

## Provenance of the georeferencing

The two-stage fit implemented in `scripts/build_routing.py` is adapted from
`scripts/build_path_graph.py` in [`StoDevX/course-data-visualization`][cdv],
which did that work first. See the header of that script for what changed.

[cdv]: https://github.com/StoDevX/course-data-visualization
