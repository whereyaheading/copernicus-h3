# Building & Reproducing the Dataset

How the dataset in [`README.md`](README.md) was produced, and how to rebuild it from the public
source. The result is a deterministic derivative of the Copernicus GLO-30 DEM — same source vintage
+ pinned library versions reproduces the cell values (count/min/max bit-for-bit, mean/stddev within
floating-point).

## Repository layout

```
README.md  VALIDATION.md  VALIDATION_REPORT.md   # dataset card, validation method, latest results
LICENSE  DATA_LICENSE.md                          # code (MIT) and data (Copernicus) terms
requirements.txt                                  # pinned deps the pipeline was built with
pipeline/        # the production build, run in order (see below)
  compose.py       composable per-cell statistics — the core primitive
  manifest.py      list the source tiles, grouped into latitude bands
  stream.py        tile sourcing (vsicurl / LRU disk cache)
  run.py           the resumable parallel sweep -> S3
  publish.py       write a finalized band to S3 (atomic, with a _COMPLETE marker)
  estimate_eta.py  progress / ETA from the _COMPLETE markers
  refine.py        boundary merge (recombine cells straddling band seams)
  geoid.py         EGM2008 geoid undulation, vectorized
  add_geoid.py     add the geoid_undulation column + sort each file by h3_cell
  recompress.py    re-encode for size (delta + byte-stream-split + zstd)
  repartition.py   final published layout: base-cell shards + provenance footer
  validate.py      run the validation suite -> VALIDATION_REPORT.md
  prototypes/      milestone scripts kept for provenance (not part of the build)
benchmarks/      one-off throughput / download experiments that informed the run
docs/            planning doc, land-area band analysis, validation imagery
h3-terrain/      the published dataset — H3 terrain cells, res 2–9 (~23 GB; ships separately, not via git)
data/            local build scratch — per-band sweep output, pre-merge (~28 GB; never published)
```

## How it works

- **Source:** Copernicus DEM **GLO-30** (2021 release), the global 30 m DSM — ~26,450 1°×1°
  Cloud-Optimized GeoTIFFs in `s3://copernicus-dem-30m` (eu-central-1).
- **Aggregation:** every source pixel is assigned to the H3 cell containing its centroid, and folded
  into **composable statistics** (count, sum, sum-of-squares, min, max — see `compose.py`). These
  compose exactly across any partition of the pixels, which is what makes the streaming sweep and the
  boundary merge correct. Done **independently per resolution** (2–9), not rolled up.
- **Sweep direction:** north→south, one latitude band at a time, flushing cells once no
  unprocessed southern tile can touch them. Bands are written independently and resumably.
- **Boundary merge:** cells straddling a band seam get each band's pixels separately during the
  sweep, then `refine.py` recombines them with a GROUP-BY on `h3_cell` (the build uses res-1 `r1=`
  partitions for the parallel write).
- **Published layout:** `repartition.py` re-shards res8/res9 to **base-cell** shards
  (`base = (h3_cell >> 45) & 127`, == the H3 res-0 parent) — ~111 shards/resolution, routable by
  pure integer math with no H3 call — and stamps every file's footer with provenance.
- **Geoid:** `geoid_undulation` from the EGM2008 grid (EPSG:3855), bilinear-interpolated at each
  cell centroid (`geoid.py`).

## Reproducing the build

Install deps (`pip install -r requirements.txt`; DuckDB pulls the `h3` community extension at
runtime). The production path runs in order, from the repo root:

| Step | Command | What it does |
|---|---|---|
| 1 | `python pipeline/manifest.py` | List the 26,450 source tiles → `pipeline/tile_manifest.json`, grouped into latitude bands |
| 2 | `python pipeline/run.py --bucket <out>` | Resumable parallel sweep → per-band Parquet (res 2–9) on S3. `stream.py`/`publish.py`/`compose.py` are its libraries; `estimate_eta.py` tracks progress. Best run on an in-region EC2 box. |
| 3 | `python pipeline/refine.py --data-dir data --out-dir h3-terrain` | Boundary merge → `h3-terrain/` (res 8–9 in `r1=` partitions for the parallel write) |
| 4 | `python pipeline/add_geoid.py --dir h3-terrain` | Add `geoid_undulation` (EGM2008) and sort each file by `h3_cell` |
| 5 | `python pipeline/recompress.py --dir h3-terrain` | Re-encode (delta + byte-stream-split + zstd) — no data change |
| 6 | `python pipeline/repartition.py --dir h3-terrain` | Final layout: re-shard res8/9 to `base=` shards + stamp provenance footers |
| 7 | `python pipeline/validate.py --dir h3-terrain` | Run the validation suite → `VALIDATION_REPORT.md` |

`geoid.py` needs the EGM2008 grid once (~77 MB):

```bash
curl -L -o "$(python -c 'import pyproj;print(pyproj.datadir.get_user_data_dir())')/us_nga_egm08_25.tif" \
     https://cdn.proj.org/us_nga_egm08_25.tif
```

## Prototypes (provenance)

`pipeline/prototypes/` holds the incremental milestone scripts the production code grew out of —
kept so the design is auditable, **not** part of the build path:

- `single_tile_dev.py` — **M1**: one tile, streamed, aggregated to res 2–9, validated against landmarks.
- `band_dev.py` — **M2**: proved cross-tile merge exactness and finalize-and-flush (exercises `compose.py`).
- `sweep_dev.py` — **M3**: prefetched, row-by-row (latitude-band) sweep with flush.
- `build_band.py` — end-to-end single-band build into the published schema.
- `validate_elevation.py` — early cross-source elevation check (figure in `docs/elevation_validation.png`).

`benchmarks/` holds the throughput/download experiments (`download_bench.py`, `throughput_bench.py`)
that established the run was bandwidth-bound and sized the EC2 box.
