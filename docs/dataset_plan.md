# Copernicus DEM → H3 Dataset — Planning Doc

> **Historical design doc — kept for the rationale, not as the spec.** This is the original
> planning document written while scoping the dataset. It records *why* the key decisions were
> made (resolution ceiling, dropping res 0–1, streaming aggregation, banding). The authoritative
> description of what actually shipped is **[../README.md](../README.md)** + **[../VALIDATION.md](../VALIDATION.md)**.
> Where this doc and the README disagree, the README wins — e.g. this doc explores a 0–10 range, but
> the dataset shipped at **resolutions 2–9** (see the rationale below for how that ceiling was chosen).

**One-line:** Take the Copernicus GLO-30 global DEM (~580 GB of COGs) and re-publish it as a series of H3-indexed Parquet files at every practically-useful resolution (shipped: 2–9) so anyone using H3 can do terrain lookups without managing raster tiles themselves.

**Scope:** Single Mac Studio, local processing, incremental — start with the resolution we need for the flight_data project (res 8) and expand outward at our own pace. No cloud spend.

## Why publish this

There's no comprehensive H3-indexed global DEM published as open data today. The closest existing thing is:

- **Kontur's 400m H3 elevation dataset** — single fixed resolution (~res 8), part of a commercial humanitarian-data product, license terms favor non-commercial use
- **OpenTopography** — serves Copernicus DEM but in raster form, no H3 indexing
- **Various GitHub one-off scripts** — using `h3ronpy` etc. but no published derivative datasets

So the gap is real. Anyone wanting H3-indexed terrain currently has to either:
1. Pay Kontur for commercial use of their 400m data, OR
2. Download Copernicus tiles, run h3ronpy themselves at their chosen resolution(s), eat the compute

Publishing this once-for-everyone saves the world thousands of hours of redundant compute, while giving us a high-quality precomputed AGL lookup we can use directly.

### Who would use it

- **Geospatial analysts already using H3** (Kontur datasets ecosystem, Foursquare's Studio, Carto, Snowflake H3 users) who want elevation as just another H3-indexed feature
- **Aviation / drone projects** doing AGL computation (us, but also others)
- **Climate / agriculture / environmental research** wanting elevation as a feature in H3 pipelines
- **Game / sim / mapping projects** that want terrain data in a non-raster format
- **General-purpose lookups** — anyone who wants "elevation at this (lat, lon)" with a hash lookup rather than a raster query

### Why this is good for the flight_data project

- It's the precomputed terrain lookup table our `api_field_definitions.md` `alt_agl` derivation needs
- The compute + workflow figure out how to do raster → H3 efficiently on a single machine
- It's a publishable artifact — establishes credibility / open-source presence
- Forces us to think hard about H3 resolution storage trade-offs in a controlled context separate from the live flight data

## Source data (actual numbers)

Probed the AWS Open Data S3 bucket to get current real numbers:

| Stat | Value |
|---|---|
| Tile count | **26,450** (1°×1° tiles) |
| Mean per-tile size | 22.5 MB (compressed COG) |
| Median | 23.8 MB |
| Min (mid-Pacific) | 0.22 MB |
| Max (Andes, Himalayas) | 45.8 MB |
| **Total source size** | **~580 GB** |

Available as COGs at `https://copernicus-dem-30m.s3.amazonaws.com/{tile}/{tile}.tif`. Public, no auth, supports HTTP range requests so we can stream pixels for any tile without downloading the full file if we want.

**The 580 GB number is dramatically smaller than my earlier 22 TB guess** — COG compression on elevation data is very effective. The whole project is single-machine-feasible.

## Scope decisions

### Resolutions to publish — res 2 through res 9

H3 has resolutions 0-15. Source is 30m. Going finer than ~30m equivalent is oversampling.

| H3 Res | Cell edge | Cell area | Approx land cells globally | Notes |
|---|---|---|---|---|
| ~~0~~ | ~~1,107 km~~ | ~~4.25M km²~~ | ~~~30~~ | **Dropped — continent-scale, useless toy** |
| ~~1~~ | ~~419 km~~ | ~~607k km²~~ | ~~~250~~ | **Dropped — subcontinental, no real use** |
| 2 | 158 km | 86,746 km² | ~1,800 | Coarse regional |
| 3 | 60 km | 12,393 km² | ~12k | Sub-state |
| 4 | 22.6 km | 1,770 km² | ~85k | Metro region |
| **5** | **8.5 km** | **253 km²** | **~600k** | County-ish; home-turf for our project |
| 6 | 3.2 km | 36 km² | ~4M | City-district; flight_data default storage |
| **7** | **1.22 km** | **5.16 km²** | **~30M** | Airport-area |
| **8** | **460 m** | **0.74 km²** | **~210M** | **Project's finest committed resolution** |
| 9 | 174 m | 0.105 km² | ~1.5B | Block / small parcel — finer than we need but cheap to include |
| ~~10~~ | ~~66 m~~ | ~~0.015 km²~~ | ~~~10B~~ | **Deferred — 300 GB output, can be added later if demand warrants** |
| 11 | 25 m | 0.0021 km² | ~70B | At source resolution — diminishing returns |
| 12 | 9.4 m | — | ~500B | Below source — pure oversampling |

**Publish res 2 through res 9.** Res 10 is deferred — if anyone wants sub-block precision, we can produce it later as a single-resolution run; the savings in memory, disk, and wall time on this run aren't worth the marginal utility for our needs.

Bonus from dropping res 0-1: their hexes would stay in memory for the entire global run (continent-spanning hexes are never "done" until every tile has been processed), which conflicts with the streaming-aggregation memory model below. Res 2 is the largest hex size that still gets finalized within a reasonable distance.

### Independent raw → H3 aggregation per resolution (not rollup)

Per Frances's call: **for the published dataset, do it right** — compute each resolution independently from the source pixels, not by rolling up from a finer resolution. Reasons:

- **Aggregation correctness:** mean rolls trivially; max / min / stddev compose correctly with care but it's easy to get subtly wrong. Doing each resolution from raw avoids that risk.
- **Edge effects:** each resolution has its own boundary behavior (hex perimeter / pixel grid intersection). Rolling up can compound those.
- **Cost is small enough not to matter:** this is still a *single* streaming walk over the source — each tile is downloaded once. "Independent" means that while a tile's pixels are in memory we compute the cell ID at each of the 8 resolutions directly (rather than computing res 9 and rolling up). Those extra `latlng_to_cell` calls are cheap vectorized scans over a pixel array we already have, so the whole run stays hours-to-days, not weeks.

For our **internal flight_data use** (powering the `alt_agl` lookup), rollup is fine — speeds up iteration during prototype, accuracy doesn't really matter for that. But given we're doing it the proper way for the published data, we might as well have nice things.

### Aggregation columns

Per H3 cell at each resolution:

- **`elevation_mean`** (m) — mean over all source pixels in the cell
- **`elevation_max`** (m) — for aviation clearance / obstacle avoidance
- **`elevation_min`** (m) — for water-body detection, valleys
- **`elevation_stddev`** (m) — terrain ruggedness
- **`pixel_count`** (int) — source pixels contributing; confidence weighting

5 columns. Schema is the same at every resolution so a user picking one file gets a consistent shape.

### Per-resolution file sizes & latitude banding

Because every hex is **one fixed-width row regardless of terrain**, output size tracks **land area**, not source-tile bytes — a glassy-ocean hex and a Himalayan hex cost the same. (The 0.22 → 45.8 MB spread in the *source* COGs is input compute/ruggedness; it vanishes after aggregation.) So we don't bin by tile bytes — we partition the large resolutions into **latitude bands sized to ~500 MB each**, cuts snapped to round latitudes.

Bands are computed by `analysis/copernicus_band_planner.py` from a land-area-by-latitude curve (`analysis/land_area_by_latitude.csv`, derived from the `global_land_mask` 1.8 km landmask — 147.4 Mkm² total, within 1% of the canonical 148.9, 68/32 N–S split).

| Res | Rows | Total size | Files | Hosting |
|---|---|---|---|---|
| 2 | ~1.7k | ~51 KB | 1 | repo (CSV+Parquet) |
| 3 | ~12k | ~360 KB | 1 | repo (CSV+Parquet) |
| 4 | ~83k | ~2.5 MB | 1 | repo (CSV+Parquet) |
| 5 | ~580k | ~18 MB | 1 | repo / Release |
| 6 | ~4.1M | ~123 MB | 1 | Release |
| 7 | ~29M | ~857 MB | **2** — N/S hemispheres (584 / 273 MB) | Release |
| 8 | ~199M | **~6.0 GB** | **12** — 5°/10° bands (~140–670 MB) | Release |
| 9 | ~1.4B | **~42 GB** | **90** — whole-degree bands (~180–660 MB) | Release |

**Total: ~49 GB published**, all on GitHub — small resolutions committed in the repo, res 6+ as Release assets (every band file is <700 MB, comfortably under GitHub's 2 GB/file Release cap); S3 / Hugging Face mirror for bulk/programmatic pulls. Band definitions are checked in as `analysis/bands_res{7,8,9}.csv`.

Bands are narrow where land is dense (1° apart through the 40–55°N belt) and wide over empty ocean (one res-9 band spans −42° to −68°). Per Frances, res 7 is split at the **equator into N/S hemispheres** rather than by equal size — a clean semantic boundary even though it's lopsided (the north holds 68% of land). Small single-file resolutions (2–4) also go up as **CSV** alongside Parquet so they open/grep with no tooling.

**Layout:** `{res}/{lat_band}/part-*.parquet` — e.g. `res9/lat_+50_+49/part-000.parquet`; single-file resolutions skip the band dir. The `lat_band` directory is the band's north_south range, so a regional consumer pulls only the bands covering their latitudes.

> **Caveat:** the ~30 bytes/row compression figure is still an estimate; M1 gives the real number and re-running the planner shifts boundaries slightly. Treat the bands as final-*shape*, not final-to-the-degree.


## Technical approach

### Pipeline architecture — latitude-band streaming on 32 GB Mac Studio

**Hardware constraint:** 32 GB RAM, ~500 GB SSD. We can't keep global aggregations in memory; we can't keep ~600 GB of working disk for a naive global merge.

**Strategy:** process tiles in latitude order from one pole toward the other. Within each latitude band, fully aggregate the band's contribution to every resolution, finalize and flush hexes that won't see further contributions, then move south. This bounds memory to "active hex set for ~1 band" and produces naturally-partitioned outputs by latitude.

The reason res 0-1 are dropped: their hexes are continent-spanning and would never finalize during the run — they'd stay in memory until the very end. Res 2 (158 km / ~1.4° latitude span) is the largest hex that gets retired within a reasonable distance of the active band.

```
For each latitude band B (say 1° wide), processed pole-to-equator-to-other-pole:
    For each tile T in band B's row IN PARALLEL (8-12 workers):
        Per-worker, in worker memory:
        1. HTTP GET COG, decompress to ndarray
        2. h3ronpy: for each resolution R in 2..9, compute the H3 res-R cell
           per pixel directly via latlng_to_cell (vectorized, one pass per R)
        3. Aggregate each resolution R in its own worker-local hashtable
           (every pixel updates 8 hashtables, one per resolution — computed
            from raw pixels at each level, NOT rolled up from a finer level)
        4. Discard tile from memory
    
    Reduce worker hashtables → row-shared hashtables (per resolution):
        For each resolution R 2..9:
            row_shared_hash[R] += worker_hashes[R]   (compose count/sum/sum_sq/min/max)
    
    After the row is fully aggregated, identify finalized hexes:
        For each resolution R, walk row_shared_hash[R]:
            A hex is "final" if its northernmost extent ≤ current band's southern edge
            (i.e. no tile we'll process from here south can contribute to it)
        Append finalized hexes to per-resolution per-latitude-band Parquet output
        Remove them from row_shared_hash[R] (free memory)
    
    Move to next latitude band.

After the global walk:
    For each resolution, the per-band Parquets ARE the final dataset
    (small post-pass to merge boundary hexes that straddle band boundaries)
    Optional final concatenation per resolution if a single file is desired,
    or keep latitude-partitioned for efficient regional queries.
```

**How a hex's "northernmost extent" is known (the finalize trigger):** it's a lookup in the H3 library. `h3.cell_to_boundary(cell)` returns the cell's vertex lat/lngs; the max latitude over those vertices (plus a small epsilon for the arc between vertices) is its northernmost reach. A hex is safe to finalize and flush once the active band's southern edge has passed that latitude — no tile processed further south can contribute another pixel to it. It's a cheap per-cell call, run only on the currently-active hexes at flush time, not on every cell globally.

**Each resolution is computed independently from raw pixels — not rolled up from a finer one.**

H3 cells are *not* perfectly hierarchically nested: a res-(k+1) cell's centroid falls inside its res-k parent, but the seven children don't tile the parent exactly (hexagons can't perfectly subdivide into hexagons). So grouping res-(k+1) cells by parent yields slightly different pixel membership than assigning pixels to res-k cells directly, and that boundary mismatch differs at — and compounds across — every level. The cost of doing all 8 resolutions as independent passes is modest (see compute estimates), so we do it right once: every published resolution is aggregated straight from the source pixels via `latlng_to_cell` at that resolution.

**The cell statistics are still composable** — and that's what keeps the per-worker reduction and cross-tile merge exact (a hex whose pixels are split across workers or tiles recombines without error):

- `mean = sum / count` is exact when both are summed correctly
- `variance = sum_sq / count − mean²` (Welford-equivalent formulation), exact given exact `sum_sq` and `count`
- `min`, `max` trivially compose under min / max operations
- `count` composes under addition

Composition combines partial aggregates of the *same* hex at the *same* resolution seen across different tiles/workers — it is never used to derive one resolution from another.

### Memory budget on 32 GB

Active state at any point:

| Component | Peak |
|---|---|
| Per-worker tile state × 8-12 workers | 5-8 GB |
| Row-shared hashtable, res 9 (~1 active row) | 1.5-2 GB |
| Row-shared res 8 | ~150 MB |
| Row-shared res 2-7 | <50 MB combined |
| OS, Python, libraries overhead | 4-6 GB |
| **Total peak** | **~12-16 GB** |

Comfortably fits 32 GB with ~16 GB headroom. Frees us to run more concurrent workers if we want to push wall time down, or to keep things conservative.

Dropping res 10 was the right call — it would have been the dominant memory and disk consumer, and res 9 (174m hexes, 0.105 km² area) is already finer than any flight_data use case needs.

### Disk budget during processing

| Component | Peak |
|---|---|
| Per-band finalized outputs accumulating locally | <10 GB during processing |
| Final outputs (res 2-9) | ~49 GB |
| Streaming COG inputs (HTTP, no disk caching) | 0 GB (prod) / up to ~5 GB (dev cache, see below) |
| **Working space total** | **~55 GB on 500 GB SSD — plenty of room** |

No need to ship anything off-machine during processing. The entire dataset can sit locally throughout, and we just upload to S3 / publish to GitHub Releases after the run completes.

**Dev tile cache (`--tile-cache-gb N`, default 0 = off).** While iterating on M1/M2 we re-run the same handful of tiles dozens of times; re-streaming them from S3 each time is the slowest part of the loop. With the flag set, `stream.py` keeps an **LRU on-disk cache of decoded/raw COGs capped at N GB** (~5 GB is plenty for a dev band) and evicts least-recently-used tiles past the cap. It's a pure speed optimization — a cache hit is byte-identical to a fresh download. **Off by default and explicitly off for the M3 global run:** that run touches each tile exactly once, so a cache would only burn disk for zero reuse. The cap means the cache never grows unbounded even if left on.

### Resumability

Checkpoint is per-latitude-band (all-resolutions-together). If the run stops mid-band, we re-process that band. If we stop between bands, we continue from the next one.

Each band's Parquet output is atomic: either all resolutions for that band wrote successfully, or we redo the band.

### Square-pixel-to-hexagon coverage — getting it right

This is the key correctness concern: source data is on a regular square grid (DEM pixels); H3 cells are hexagons. Pixels near hex boundaries can be ambiguous. Wrong-way implementations:

- **Naive bbox query** — "for each hex, find pixels within its bounding rectangle" → misses pixels near non-vertical hex edges; double-counts pixels in overlapping bboxes
- **Centroid-only sampling** — "take the pixel under each hex's centroid" → throws away all the other pixels in the hex

**Right-way: pixel-iteration.** For each pixel, compute the H3 cell that contains its centroid via `h3.latlng_to_cell(lat, lon, res)` — this is a closed-form mathematical function that gives exactly one hex per (lat, lon) point. Every pixel is assigned to exactly one cell; every pixel is processed once; no boundary ambiguity.

**h3ronpy implements this correctly** — its raster→H3 conversion is pixel-iteration with multi-threaded SIMD. We use h3ronpy and trust it.

**Edge effect at our finest published resolution (res 9, ~174m cells vs 30m pixels):** each res-9 hex contains ~30 source pixels, so aggregation is well-supported. Pixel-iteration is independent of hex size, so h3ronpy handles it correctly regardless; finer resolutions just mean fewer contributing pixels per hex, and the `pixel_count` column tells the consumer how much data is behind each cell.

**At res 10 and below (deferred / unpublished):** res 10 (~66m cells) still holds ~5 pixels per hex — defensible, just deferred for cost reasons rather than correctness. Beyond res 10 (res 11+) the hexes get smaller than the source pixels: pixel-iteration still assigns each pixel to exactly one hex, but many fine hexes would have `pixel_count = 0` or 1 (no pixel center happened to land there). **That's the correctness floor — which is why res 9 is our published ceiling and res 11+ is never worth publishing from this source.**

### Cross-tile-boundary cells

H3 hexes don't align with 1°×1° tile boundaries. Some hexes straddle tiles — meaning the same hex sees pixels from 2 (or, at corners, up to 4) different tiles. **No re-download is required to handle this** — composable aggregations let us correctly combine partial contributions.

```
Tile A: hex_X gets pixels [p1, p2, p3]
        → emits (hex_X, count=3, sum=Σ₁₋₃ elev, sum_sq=Σ₁₋₃ elev², min=…, max=…)

Tile B: hex_X gets pixels [p4, p5]
        → emits (hex_X, count=2, sum=Σ₄₋₅ elev, sum_sq=Σ₄₋₅ elev², min=…, max=…)

Merge step (GROUP BY h3_cell across all per-tile outputs):
  hex_X → count = 3+2 = 5
          sum = Σ₁₋₅ elev
          sum_sq = Σ₁₋₅ elev²
          min = min over all 5 partial-mins
          max = max over all 5 partial-maxes
          mean = sum / count    (final-derived)
          stddev = sqrt(sum_sq/count − mean²)   (final-derived)
```

Result is mathematically identical to loading every pixel of hex_X at once. Each hex is touched by at most 4 per-tile outputs. The merge is a simple `GROUP BY h3_cell` over all tile outputs at that resolution — DuckDB / pyarrow handles this trivially across ~26k inputs.

Every published cell has its full pixel coverage after the merge, so there's no need to mark cells that straddled tile boundaries — the merge makes that distinction invisible by construction.

## Compute estimates for 32 GB Mac Studio

**Per-tile work** (within a worker, isolated):
- Download + decompress: ~0.5-3 seconds (HTTP latency + COG decode)
- Compute H3 cells at each resolution 2-9 for all pixels (h3ronpy, one vectorized pass per resolution): ~5-15 seconds depending on tile complexity
- Aggregate each resolution in worker-local hashtables: ~1 second
- Reduce into row-shared hashtables: ~1 second
- **Total per tile: ~8-20 seconds**

**Per-row wall time** (one 1° latitude band):
- ~360 tiles per row at the equator, fewer near poles
- 8-12 parallel workers × ~12 sec/tile avg = ~6-12 minutes per row
- Plus per-row finalize-and-flush: ~10 seconds

**Total global walk:** ~180 latitude bands × ~6 minutes avg = **~12-18 hours wall time on a 32 GB / 24-core Mac Studio.**

Dropping res 10 saved roughly:
- 5-10 hours of wall time (the res-10 work was dominating)
- ~15 GB of peak memory
- ~300 GB of output disk + the headache of streaming res-10 bands off-machine

**Cost:** $0 cloud (everything local). Mac Studio electricity for ~12 hours ≈ a dollar.

## Output format & schema

Per resolution, one Parquet file (or partitioned set for the large ones):

```
h3_cell           uint64       # H3 cell index
elevation_mean    float32      # meters, source DEM reference
elevation_max     float32      # meters
elevation_min     float32      # meters
elevation_stddev  float32      # meters
pixel_count       uint32       # source DEM pixels aggregated into this cell
```

5 columns. `pixel_count` implicitly serves the "how much data is behind this cell" purpose — low counts at fine resolutions indicate sparse source coverage. Every cell in the merged output has its full pixel coverage by construction.

**Sparse / land-only: a missing hex means ocean.** We emit a row only where Copernicus has source pixels (~41% of the globe — land, coast, and the ocean tiles that exist), *not* for open ocean Copernicus never measured. A consumer that looks up a hex and finds **nothing interprets it as ocean ⇒ 0 m (sea level)**. This is what lets aircraft over water get a valid AGL (altitude − 0) without us storing billions of ocean-zero rows. Inland sub-sea-level land (Dead Sea, Death Valley) is still present with real negative values — only open sea is absent. **This makes complete land coverage a safety-critical invariant** (see Validation): any land hex we *fail* to emit reads as 0 m, making a plane look higher above ground than it is.

Sorted by `h3_cell` ascending; partitioned into latitude bands per the file-sizing scheme above (`{res}/{lat_band}/part-*.parquet`).

Embed metadata in the Parquet file footer:
- Source DEM version + retrieval date (vintage tracking)
- h3 library version
- Conversion script git commit
- The required Copernicus attribution string
- Schema version

## License & compliance

The Copernicus license requires us to:

1. **Pass through attribution** — required text in every distributed artifact
2. **Pass through liability disclaimer** — required text included
3. **Pass through viral clause** — downstream users must agree to same terms

Concrete plan:

- **Code:** MIT (our original work) — the permissive "do basically anything: use, modify, sell, relicense, just keep the copyright + license notice; no warranty" license. (Apache 2.0 is the same spirit plus an explicit patent grant; MIT is simpler and fine here since there's nothing patentable.)
- **Data:** `DATA_LICENSE.md` pass-throughs the Copernicus terms verbatim. Attribution embedded in each Parquet file's metadata so it travels with the data.
- **README** prominently states the data license and downstream obligations
- We're publishing a *derivative* (H3 aggregated statistics), so Article 6(b) requires the "produced using Copernicus WorldDEM-30 ..." attribution everywhere

The viral clause is standard "downloading constitutes acceptance" with prominent terms.

## Repo structure

```
copernicus-h3/
├── README.md                      # main intro, usage examples, links to data
├── LICENSE                        # MIT/Apache for the code
├── DATA_LICENSE.md                # Copernicus pass-through terms
├── ATTRIBUTION.md                 # required attribution strings + citation
├── docs/
│   ├── methodology.md             # algorithm details, aggregation formulas
│   ├── usage.md                   # how to query the dataset (Python, SQL, JS)
│   ├── reproducibility.md         # exact versions, retrieval dates, how to rebuild
│   └── faq.md                     # license questions, accuracy questions, etc.
├── src/
│   └── copernicus_h3/
│       ├── stream.py              # tile-by-tile S3 streaming + optional LRU dev cache (--tile-cache-gb)
│       ├── aggregate.py           # per-tile raster → H3 (h3ronpy wrapper)
│       ├── merge.py               # cross-tile merge + cross-boundary aggregation
│       ├── publish.py             # Parquet writing, metadata embedding
│       ├── verify.py              # validation suite
│       └── checkpoint.py          # resumability state
├── scripts/
│   ├── run_resolution.py          # run a single resolution end-to-end
│   ├── single_tile_dev.py         # dev tool for testing on one tile
│   └── upload_to_s3.py            # publish to AWS Open Data Registry
├── tests/
│   ├── test_aggregate.py          # unit tests on synthetic raster
│   ├── test_merge.py              # cross-tile-boundary correctness
│   └── test_landmarks.py          # validate vs known elevations
├── data/
│   ├── res2_through_res4/         # small enough to check into repo
│   └── README.md                  # pointers to GitHub Releases / S3 for res 5+
├── notebooks/
│   ├── 01_validation.ipynb        # cross-check against ground truth
│   ├── 02_kontur_comparison.ipynb # benchmark against Kontur's 400m
│   ├── 03_aviation_use_case.ipynb # the AGL-lookup example
│   └── 04_terrain_visualization.ipynb
└── pyproject.toml
```

## Validation strategy — assume the output is wrong until a check says otherwise

A 12-18 hour unattended run is exactly where silent corruption hides: an off-by-one in nodata handling, a hex retired one band too early, a unit slip, and you discover it only after publishing. So the operating stance is militant: **every number is presumed wrong until an independent check passes.** Principles:

- **Quantitative gates, not eyeballing.** Every check has a numeric tolerance; exceeding it fails the build. No "looks about right."
- **Fail loud, halt fast.** A violated invariant aborts the run and names the offending tile/band — it never warns-and-continues into 18 hours of garbage.
- **Independent ground truth.** Validate against sources that don't share our code path: raw Copernicus pixels, USGS 3DEP (the US national lidar-derived DEM — far more accurate than Copernicus over the US; see Open questions), Kontur's 400m H3 set, surveyed landmark elevations, the global hypsographic curve.
- **No silent loss.** Coverage, nodata, and skipped inputs are logged and reconciled every band — truncation is never invisible.

### Tier 1 — always-on invariants (asserted in code, every tile, every run)

Cheap enough to run on every cell; these catch most bugs the instant they happen:

- **Structural:** `min ≤ mean ≤ max`, `stddev ≥ 0`, `pixel_count ≥ 1`, no NaN/inf in any column.
- **Physical range:** every `elevation_*` within [−432 m (Dead Sea shore), 8849 m (Everest)]. Anything outside flags a unit / projection / nodata bug. The Copernicus nodata sentinel must be stripped, never aggregated as if it were 0 m.
- **Conservation (the big one):** Σ `pixel_count` over all cells at a resolution == valid source pixels processed (after nodata removal). Nothing lost, nothing double-counted. Checked per tile and globally per resolution.
- **Merge exactness:** the composable-stats invariant from the cross-tile section — a hex's recombined per-tile/per-worker partials must equal a single-pass aggregation of its pixels, exactly on count/sum/min/max.
- **Land completeness (safety-critical):** because a *missing* hex is read as ocean = 0 m, dropped land is a silent, dangerous error. Per band, reconcile emitted-hex extent against an independent landmask (`analysis/land_area_by_latitude.csv` / Natural Earth coastlines): emitted land area must match expected land area for that latitude within tolerance, and **every Copernicus land tile must have produced hexes**. A land tile that yields zero hexes aborts the run.

### Tier 2 — per-milestone gates (must pass before moving on)

| When | Gate — proceed only if this passes |
|---|---|
| **M1 single tile** | Landmark spot-checks, picking the right statistic — flat/airport sites vs `mean` (LAX = 38 m, Death Valley = −86 m), **sharp peaks vs `max`** against the value Copernicus actually reports (GLO-30 under-reports knife-edge summits — Everest reads ~8738 m, *not* 8849); every cell's `elevation_mean` matches a direct raw-DEM resample within ε; conservation holds on the tile. **(Done — all three pass; see `copernicus-h3/single_tile_dev.py`.)** |
| **M2 band** | Peak memory stays under budget (asserted, not hoped); **finalize-and-flush correctness** — re-process an overlapping band and confirm flushed hexes are *identical* (no hex retired too early or too late); cross-tile and band-boundary straddle cells merge correctly. |
| **M3 global run** | Live monitoring per band: cell counts, elevation histogram, and % nodata logged each band; a fixed set of **canary cells** re-checked every band, run aborts on drift; global conservation; **resumability test** — kill mid-band, resume, confirm output is identical to an uninterrupted run. |
| **M4 suite** | Full cross-source compare — Kontur 400m (close, modulo their aggregation), USGS 3DEP (US, independent high-accuracy); landmark battery across all continents; **hypsographic check** — our global land-elevation distribution must match the known hypsographic curve (mean land elevation ≈ 800 m; documented fractions below 200 m / above 1000 m) to catch systematic bias a spot-check can't; **cross-resolution sanity** — rolling res-(k+1) up to res-k parents should *approximately* match the directly-computed res-k file (gap expected from H3's imperfect nesting; a *large* divergence is a bug); coastline/water behavior (ocean ≈ 0 m), polar DEM artifacts, and sharp-peak under-reporting (GLO-30 reads ~111 m low at Everest) documented as caveats. |

### Tier 3 — regression baseline

Freeze a golden set of known cells (the landmarks plus a few random cells) with their expected values checked into the repo. Any code change must reproduce them within tolerance before merge — prevents a "harmless cleanup" from silently shifting every elevation by a meter.

### On failure

A failed check is a stop, not a note. Halt, bisect to the offending tile/band (the asserts name it), reproduce and fix in `notebooks/01_validation.ipynb`, then resume. A failed gate blocks the next milestone — we do not publish past a red check.

## Distribution plan

Multi-tier:

| Tier | What | Hosted | Access |
|---|---|---|---|
| Repo | Code + docs + res 2-4 data | GitHub | `git clone` |
| Releases | Res 5-7 data | GitHub Releases (up to 2 GB per file) | Download URLs |
| Mirror | All resolutions | AWS Open Data Registry (free) | S3 direct |
| Mirror | All resolutions | Hugging Face Datasets | `datasets.load_dataset()` |

### Announcement / awareness (after launch)

- Blog post explaining methodology + license caveats
- Twitter / Bluesky thread with visual examples (rendered H3 elevations as heatmaps)
- Post on H3 Discord / Slack channels
- Submit to Awesome-DEM repo
- Submit to Awesome-H3 (create if doesn't exist)
- HackerNews submission

## Roadmap & milestones

Streaming-by-latitude on a 32 GB Mac Studio, res 2-9:

| Milestone | Effort | Deliverable |
|---|---|---|
| **M1: Single-tile prototype** | 1 evening | Stream one tile from S3, run h3ronpy at each resolution 2-9 independently from the pixels, validate vs known reference points (LAX = 38m), write `single_tile_dev.py` |
| **M2: Multi-tile band prototype** | 1-2 days | Process a small latitude band (~10° wide, ~600 tiles) using the streaming model. Verify memory stays bounded, finalize-and-flush works, landmark elevations come out right. Unit tests on per-resolution aggregation correctness + cross-tile merging. |
| **M3: Global streaming run** | ~12-18 hours wall time | One end-to-end run: pole-to-pole walk, all 26,450 tiles × res 2-9, each resolution aggregated independently from raw pixels. Tier-1 invariants + per-band canary/conservation checks run live and abort on anomaly (see Validation strategy). Outputs ~52 GB total, all local. **Includes the res-8 file flight_data needs for AGL lookups.** |
| **M4: Validation suite** | 2-3 days | Landmark checks across all continents; Kontur cross-compare; cross-band-boundary spot checks; document accuracy bounds; iterate on any bugs found |
| **M5: Wire flight_data to use res-8 output** | 1 day | Add `terrain_h3_res8` lookup to the ingestion pipeline; verify alt_agl values look right |
| **M6: Publication infrastructure** | 1 week | Repo polish, README, AWS Open Data Registry submission, Hugging Face Datasets submission |
| **M7: Announcement** | 2-3 days | Blog post, social, HackerNews, community submissions |

**Total elapsed:** ~2-3 weeks part-time, of which only ~12-18 hours is actively compute-bound (the M3 walk-away run).

**Total compute cost:** $0 cloud (everything local). Mac Studio electricity for ~12-15 hours of work ≈ a dollar.

If demand for res 10 turns out to be real after launch, we can do a separate run later — it'd take its own ~10-15 hours and ~300 GB of output, but by then we'd know if it's worth the cost.

## Connection back to flight_data

Once M3 is done, the flight_data project gets its terrain lookup:

- `alt_agl` derivation in `api_field_definitions.md` → H3 cell lookup against the res-8 Parquet
- Terrain column in per-fix `fixes_per_hex_minimal` from `table_registry.md` → same H3 lookup
- The hybrid res-6/res-8 storage in `per_plane_profile.md` gets matching terrain coverage once res-6 also lands (M6)

This side-quest IS our terrain pipeline, just productized.

## Open questions

1. **Update cadence after publication.** Copernicus issues occasional updates. **Decision:** pin to a snapshot and document the vintage in every Parquet footer (already in the schema) and in `docs/reproducibility.md`; users opt into newer vintages, we don't promise continuous tracking. **TODO:** before v1, design the concrete update-incorporation plan — how a new Copernicus vintage triggers a re-run, how outputs are versioned, and how consumers learn about it (changelog / release tags).

### Decided

- **Output partitioning.** Bucket = **latitude band**, sized to ~500 MB/file with round-latitude cuts (see [Per-resolution file sizes & latitude banding](#per-resolution-file-sizes--latitude-banding)). res 2–6 single file; res 7 two hemispheres; res 8 twelve 5°/10° bands; res 9 ninety whole-degree bands. Layout `{res}/{lat_band}/part-*.parquet`.
- **Resolution ceiling.** Publish res 2-9; defer res 10; never publish res 11+ (below source resolution). Res 10+ → Future work (wait for demand; the code lets anyone run it and submit the output).
- **3DEP layer for US.** Keep the published dataset pure-Copernicus for global consistency; consumers wanting the US accuracy boost layer USGS 3DEP themselves. *(3DEP = the USGS **3D Elevation Program**, the US national lidar-derived elevation dataset — ~1 m / ~10 m DEMs, far more accurate than Copernicus over US territory. We use it only as an independent validation reference, never as source.)*
- **License acceptance UX.** Standard "downloading constitutes acceptance," no click-through (we're not hosting a portal). Terms, attribution, and downstream obligations live in `README.md`, `DATA_LICENSE.md`, and `docs/faq.md` — all on GitHub.

## Future work & outside contributions

Deferred by design — none of these block v1, and because the conversion code is published, each is something we *or an outside contributor* can run and submit:

- **Res 10 (and finer) on demand.** We publish res 2-9; the code runs at any resolution, so anyone can do a single-resolution res-10 pass (~10-15 h, ~300 GB) and submit it. We run it ourselves only if demand shows up.
- **GLO-90 tier.** Same methodology on the 90 m product — ~9× less source data, much smaller output. An easy lightweight companion tier once GLO-30 is done.
- **Bathymetry merge (GEBCO).** Copernicus is land-only (oceans flatlined to 0 m); merging GEBCO would make a true global land+ocean terrain set. Not needed for our use, but an obvious community value-add.
- **DTM (bare-ground) variant.** Copernicus is a DSM (canopy / building tops); a DTM needs ground/non-ground classification — substantial extra work, good v2 / contribution target.

## Cross-references

- `api_field_definitions.md` — defines `alt_agl` derivation that consumes this
- `per_plane_profile.md` — hybrid H3 storage that this terrain data sits underneath
- `geographic_priors.md` — uses the resulting `terrain_h3` lookup for ingestion-time AGL
- `table_registry.md` — should add `terrain_h3` as a new reference table
