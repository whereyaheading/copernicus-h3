# Copernicus GLO-30 Terrain — H3-Indexed Global Elevation

**Status: draft.** A global terrain-elevation dataset derived from the [Copernicus GLO-30 Digital Elevation Model](https://registry.opendata.aws/copernicus-dem/), re-published as **[H3](https://h3geo.org)-indexed Parquet** so you can get terrain statistics with a hash lookup instead of managing raster tiles.

For any H3 cell you get the mean/min/max/stddev elevation of the ground inside it, how many source pixels backed that estimate, and the geoid undulation needed to convert to height-above-ground. Available at H3 resolutions **2 through 9** (continent-scale down to ~170 m hexes).

---

## Get the data

Hosted in two places — use whichever fits:

- **Hugging Face** — browse and query in place: **[`whereyaheading/copernicus-glo30-h3-terrain`](https://huggingface.co/datasets/whereyaheading/copernicus-glo30-h3-terrain)**. Read directly with DuckDB over `hf://…`, or `snapshot_download(...)` the whole thing (see [Quick start](#quick-start)).
- **AWS S3** — `s3://copernicus-glo30-h3-terrain` (us-east-1), **public but requester-pays** (you pay egress, so it needs AWS credentials and `--request-payer requester`):
  ```bash
  aws s3 cp s3://copernicus-glo30-h3-terrain/res8/base=2/part-000.parquet . --request-payer requester
  ```

~23 GB total, 228 Parquet files. See [How it's organized](#how-its-organized) for the layout.

---

## What's in it

One row per H3 cell, at each published resolution. Schema is identical at every resolution:

| Column | Type | Units | Description |
|---|---|---|---|
| `h3_cell` | `uint64` | — | H3 cell index (the primary key) |
| `elevation_mean` | `float32` | metres | Mean of all source pixels in the cell |
| `elevation_max` | `float32` | metres | Maximum source pixel (obstacle clearance) |
| `elevation_min` | `float32` | metres | Minimum source pixel (valleys, water) |
| `elevation_stddev` | `float32` | metres | Std-dev of source pixels (terrain ruggedness) |
| `pixel_count` | `uint32` | — | Source pixels aggregated into this cell (confidence weight) |
| `geoid_undulation` | `float32` | metres | EGM2008 geoid height above the WGS84 ellipsoid (see [Vertical datum](#vertical-datum--height-above-ground)) |

- **Elevations are orthometric** (height above the EGM2008 geoid — Copernicus GLO-30's native vertical datum), in metres.
- **Horizontal datum is WGS84.** Cell geometry is standard H3.
- Statistics are computed **independently at each resolution directly from the ~30 m source pixels** (not rolled up from a finer level), so each resolution is internally exact.

### Resolutions

| Res | Hex edge | Hex area | Rows (land) | Typical use |
|---|---|---|---|---|
| 2 | 158 km | 86,700 km² | ~3.0 k | Coarse regional |
| 3 | 60 km | 12,400 km² | ~17 k | Sub-state |
| 4 | 22.6 km | 1,770 km² | ~110 k | Metro region |
| 5 | 8.5 km | 253 km² | ~740 k | County-scale |
| 6 | 3.2 km | 36 km² | ~5.1 M | City district |
| 7 | 1.22 km | 5.16 km² | ~35 M | Airport area |
| 8 | 460 m | 0.74 km² | ~246 M | Within-airport / fine terrain |
| 9 | 174 m | 0.105 km² | ~1.72 B | Block / small parcel |

---

## How it's organized

```
res2/part-000.parquet                      # res 2–7: one file per resolution
res3/part-000.parquet
…
res7/part-000.parquet
res8/base=<0–121>/part-000.parquet         # res 8–9: sharded by H3 base cell
res9/base=<0–121>/part-000.parquet
```

- **One directory per resolution** (`res2/` … `res9/`).
- **Res 2–7** are a **single Parquet file** each.
- **Res 8 and 9** are **sharded by H3 base cell** (`base=<0–121>/`, ~111 shards) — the top of the H3 hierarchy. The shard key is recoverable from any cell index by **pure integer math, no H3 library call**: `base = (h3_cell >> 45) & 127`. To read only a region, compute that for your cells and read just those shards — and because the data is sorted by `h3_cell`, it also prunes by row-group statistics *inside* each shard. (You can even prune in plain SQL with no H3 extension: `WHERE base = (h3_cell >> 45) & 127`.)
- Rows within a file are ordered by `h3_cell`; files are **Zstandard-compressed Parquet**.
- Provenance (source, Copernicus attribution, vertical/horizontal datum, partition key, schema version) is embedded in each Parquet **footer**.

---

## Quick start

**Look up the elevation at a point** (Python, `h3` + `duckdb`):

```python
import h3, duckdb

lat, lon, res = 39.2232, -106.8687, 8        # Aspen, CO
cell = h3.latlng_to_cell(lat, lon, res)
base = (cell >> 45) & 127                     # which shard — pure bit math, no H3 call

row = duckdb.sql(f"""
    SELECT * FROM 'res8/base={base}/*.parquet'
    WHERE h3_cell = {int(cell)}
""").fetchone()
print(row)   # (h3_cell, mean, max, min, stddev, pixel_count, geoid_undulation)
```

**Bulk join against your own points** — compute the H3 cell for each point at your chosen resolution, then join on `h3_cell`. For res 8/9, filter on `base = (h3_cell >> 45) & 127` first so DuckDB skips irrelevant shards.

**A whole resolution at once** (small resolutions):

```sql
SELECT * FROM 'res6/part-000.parquet' WHERE elevation_mean > 3000;   -- high terrain
```

---

## Vertical datum & height-above-ground

Elevations here are **orthometric** (referenced to the EGM2008 geoid). GNSS / ADS-B devices report **ellipsoidal** height (above the WGS84 ellipsoid). The two differ by the **geoid undulation `N`**, shipped here as `geoid_undulation` so you don't have to carry a geoid model:

```
geoid_undulation = N = h_ellipsoidal − H_orthometric   (negative in CONUS, ~ −15 to −36 m)
```

**To compute height above the terrain** from an ellipsoidal-height source:

```
height_above_ground = h_ellipsoidal − elevation_mean − geoid_undulation     (all metres)
```

Worked example (an aircraft on the ground at Teterboro, KTEB): `h_ellipsoidal ≈ −29 m`, `elevation_mean = 1 m`, `geoid_undulation ≈ −32 m` → `−29 − 1 − (−32) ≈ +2 m` above ground. ✓

`geoid_undulation` is evaluated at each cell's centroid from the EGM2008 5′ model (EPSG:3855).

---

## Coverage & caveats

- **Land only / sparse.** Cells exist only where Copernicus has data (land + coast). **A missing cell means open ocean → treat as sea level (0 m orthometric).** Don't expect a row for a mid-ocean point.
- **It's a surface model (DSM), not bare ground (DTM).** Elevations include tree canopy and buildings. For most terrain use this is fine; for true ground height it is an upper bound in vegetated/built areas.
- **Sharp peaks read low.** A 30 m DSM under-samples knife-edge summits — e.g. Everest reads ~8,738 m vs. the true 8,849 m. Validate mountain peaks against `elevation_max`, expecting the Copernicus value, not the survey height.
- **Water is flat-lined.** Oceans and large water bodies read ~0 m; inland water sits at its surface elevation.
- **Polar latitudes** carry the usual high-latitude DEM artifacts; treat the far poles with caution.
- These are **aggregate statistics**, not the original raster — for sub-cell precision use the source DEM directly.

---

## How it was built

- **Source:** Copernicus DEM **GLO-30** (2021 release), the global 30 m DSM, ~26,450 1°×1° Cloud-Optimized GeoTIFF tiles, from the [AWS Open Data registry](https://registry.opendata.aws/copernicus-dem/).
- **Aggregation:** every source pixel is assigned to the H3 cell containing its centroid (`latlng_to_cell`) and folded into composable statistics (count, sum, sum-of-squares, min, max) — done **independently per resolution**. Cells straddling processing-tile seams are recombined exactly, so seam hexes carry their full pixel coverage.
- **Geoid:** `geoid_undulation` from EGM2008 (EPSG:3855) via PROJ, per-cell centroid.

The build code and a step-by-step **[reproduction guide are in BUILD.md](BUILD.md)**. The result is reproducible from the public source.

---

## Validation

- **Method** — what each check verifies and its pass criterion: **[VALIDATION.md](VALIDATION.md)**.
- **Results** — the latest run of the suite against this dataset: **[VALIDATION_REPORT.md](VALIDATION_REPORT.md)** (currently **23/23 passing**).
- **Reproduce** — `python pipeline/validate.py --dir h3-terrain` regenerates the report from the public data.

Checks span structural invariants, landmark spot-checks, cross-source comparison, internal consistency (boundary merge, conservation, partition integrity), distributional sanity, and stress/performance. The headline correctness signal: `Σpixel_count` is **identical at every resolution** (225,865,152,000) — each resolution independently aggregated the exact same source land pixels.

---

## License & attribution

The **code** in this repository is released under the MIT License.

The **data** is a derivative of the Copernicus DEM and is redistributed under the Copernicus DEM licence. **If you use this dataset you must carry the following attribution**, and the same obligation passes to your downstream users:

> Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

See `DATA_LICENSE.md` for the full Copernicus DEM terms and disclaimer. By downloading the data you accept those terms.

## Citation

> Copernicus GLO-30 Terrain, H3-Indexed. Derived from Copernicus DEM GLO-30. https://huggingface.co/datasets/whereyaheading/copernicus-glo30-h3-terrain

Also mirrored at `s3://copernicus-glo30-h3-terrain` (us-east-1, requester-pays).
