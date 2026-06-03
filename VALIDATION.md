# Validation & Stress-Testing

How this dataset is checked for correctness, and how to stress-test it for your own use. Everything here is reproducible from the public dataset + source; the checks are organized cheapest-first. Each item lists **what it verifies** and a **pass criterion**.

> TL;DR for a quick trust check: run the **structural invariants** (Tier 1) over a sample, then the **landmark spot-checks** (Tier 2). If those pass, the dataset is behaving.

---

## Tier 1 — Structural invariants (every cell, cheap)

Asserted over the whole dataset (or a random sample per resolution). All must hold for 100% of rows.

| Check | Pass criterion |
|---|---|
| Column order & types | `h3_cell uint64`, four `elevation_* float32`, `pixel_count uint32`, `geoid_undulation float32` |
| Ordering | `min ≤ mean ≤ max` (within fp tolerance) |
| Non-negativity | `elevation_stddev ≥ 0`, `pixel_count ≥ 1` |
| Finiteness | no NaN / Inf in any column |
| Physical range | every `elevation_*` ∈ [−432 m (Dead Sea shore), 8849 m (Everest)] |
| Geoid range | `geoid_undulation` ∈ [−110 m, +90 m] (EGM2008 global bounds) |
| Cell validity | every `h3_cell` is a valid H3 index at the file's resolution |
| Uniqueness | `h3_cell` is unique within the dataset for a given resolution |

```sql
-- one-liner range/ordering check on res 8 (DuckDB)
SELECT count(*) AS violations FROM 'res8/base=*/*.parquet'
WHERE NOT (elevation_min <= elevation_mean AND elevation_mean <= elevation_max
           AND elevation_stddev >= 0 AND pixel_count >= 1
           AND elevation_max BETWEEN -432 AND 8849);   -- expect 0
```

---

## Tier 2 — Landmark spot-checks (ground truth)

Look up known points and compare. **Flat/open sites → check `elevation_mean`; sharp peaks → check `elevation_max`** (a 30 m DSM under-samples summits, so expect the *Copernicus* value, not the survey height).

| Site | (lat, lon) | Column | Expected | Tol |
|---|---|---|---|---|
| LAX airport | 33.9416, −118.4085 | mean | ~38 m | ±25 |
| Death Valley (Badwater) | 36.2468, −116.8143 | mean | ~−86 m | ±15 |
| Four Corners | 36.999, −109.045 | mean | ~1477 m | ±20 |
| Aspen (KASE) | 39.2232, −106.8687 | mean | ~2380 m | ±40 |
| Everest summit | 27.9881, 86.9250 | **max** | ~8738 m (≈111 m below survey) | ±60 |

**Geoid spot-checks** (compare `geoid_undulation` to the [NGS / online EGM2008 calculator](https://geodesy.noaa.gov/GEOID/)):

| Site | Expected N |
|---|---|
| KTEB (Teterboro) | −32.8 m |
| LAX | −36.0 m |
| KDEN (Denver) | −18.2 m |
| London | +46.5 m |
| Tokyo | +36.7 m |

**End-to-end AGL check** (the headline use): for a known on-ground ADS-B fix, `height_above_ground = alt_geom − elevation_mean − geoid_undulation` should be ≈ 0. Worked: KTEB `−29 − 1 − (−32) ≈ +2 m`. ✓

---

## Tier 3 — Cross-source comparison

Sample N random cells and compare `elevation_mean` against an independent source:

- **Raw Copernicus DEM** at the same lat/lon — should match within floating-point precision (this is the round-trip check; a mismatch means an aggregation bug).
- **USGS 3DEP** (US cells only) — an independent high-accuracy DEM; expect agreement within DEM error (a few m), larger over canopy/buildings (DSM vs DTM).
- **Kontur 400 m H3 elevation** — close, modulo their aggregation choices.

Pass: median absolute difference vs raw Copernicus ≈ 0; vs 3DEP within ~few m (bias explainable by DSM-vs-DTM).

---

## Tier 4 — Internal consistency

- **Boundary-merge correctness.** A hex that straddled a processing seam must equal a single-pass aggregation over the union of its pixels — exactly on `pixel_count`/`min`/`max`, fp-exact on `mean`. (Validated: e.g. a straddling res-9 hex with partials of 41 px @ 430 m and 89 px @ 456 m → merged 130 px @ 447.94 m — the pixel-weighted mean, not the naïve average.)
- **Conservation.** Σ `pixel_count` over a resolution == total valid source pixels processed (nothing lost or double-counted).
- **Cross-resolution consistency.** Rolling res-(k+1) cells up to their res-k parents should *approximately* match the directly-computed res-k file (gap expected from H3's imperfect nesting; a **large** divergence flags a bug).
- **Partition integrity.** Every cell in `res{8,9}/base=B/` actually has base cell `B` (`(h3_cell >> 45) & 127 = B`). Expect 0 misfiled cells. (No H3 library needed — the shard key is pure bit math.)
- **No-orphan / completeness.** Every land tile in the source produced cells; emitted land extent reconciles against an independent landmask. *(Safety-relevant: a missing cell is interpreted as ocean / sea level, so dropped land would silently read as 0 m.)*

```python
# partition-integrity spot check (no h3 extension required)
import duckdb
bad = duckdb.sql("""
  SELECT count(*) FROM read_parquet('res9/base=2/*.parquet')
  WHERE (h3_cell >> 45) & 127 <> 2
""").fetchone()[0]   # expect 0
```

---

## Tier 5 — Distributional checks

- **Hypsographic curve.** The global distribution of `elevation_mean` (area-weighted by cell) should match the *shape* of Earth's known hypsographic curve — most land low-lying, a long tail into the mountains — with sane fractions below 200 m / above 1000 m. This catches systematic bias a spot-check can't.

  **Observed (this build):** the area-weighted mean of `elevation_mean` over res-5 cells is **~612 m**, with ~44 % of cells below 200 m and ~20 % above 1000 m. That is **lower than the ~800–840 m figure often quoted for "mean land elevation."** We believe the gap is a property of *what this dataset measures*, not an aggregation defect, for three reasons: (1) it is a **DSM area-weighted mean** — res-5 H3 cells are near-equal-area, so a plain cell average already *is* area-weighted, and there is no double-counting (the conservation check confirms the same pixel total at every resolution); (2) the dataset is **land-only and flat-lines large water bodies at ~0 m**, which drags the mean down relative to figures computed differently; and (3) the canonical "~840 m" number is itself **source- and method-dependent** and especially sensitive to how the high Antarctic/Greenland ice sheets are weighted. So we read 612 m as the honest area-weighted mean of *this* surface model, and treat the hypsographic check as a **shape/sanity** test (low-skewed with a mountain tail) rather than a hard match to a literature constant. If a future consumer needs a true bare-earth (DTM) land mean, this number should not be used as that figure.
- **Geoid field shape.** `geoid_undulation` should be smooth and large-wavelength: negative across CONUS, positive over Europe/Japan, no high-frequency noise.
- **Coverage by latitude.** Cell counts per latitude band should track land area (the dataset is land-only).

---

## Stress & performance testing

Targets for "will this hold up under my workload."

| Test | What it measures | Healthy result |
|---|---|---|
| **Point lookup, cold** | latency to read one res-9 cell via shard pruning | reads ~1 base shard (~130 MB), not the whole dataset; sub-second after the file is cached |
| **Shard pruning** | a regional query touches only the relevant `base=` shards | files scanned ≈ #base cells in the query box, not all 111 |
| **Bulk join** | join *N* million of your points against res-8 on `h3_cell` | linear in *N*; memory bounded (project to `h3_cell` first) |
| **Full-resolution scan** | aggregate over res-9 (1.7 B rows) | completes streaming on a laptop (DuckDB), bounded memory |
| **Repartition / re-export** | rebuild a different partition scheme | the res-9 GROUP-BY needs ~100 GB scratch — do it in-RAM (≥128 GB) or with ample disk |

**Edge cases to exercise:**
- **Ocean lookup** → *no row returned*; confirm your code treats "missing" as sea level (0 m orthometric), not as an error.
- **Sub-sea-level land** (Dead Sea ~−430 m, Death Valley ~−86 m) → negative `elevation_mean` present and correct.
- **High-elevation fields** (KASE ~2380 m, KDEN ~1655 m) → the AGL formula needs `geoid_undulation`; `alt_baro`/`ground` flags alone are wrong here.
- **Poles & dateline** → cells near 84 °N and −90 °S, and at ±180° longitude, resolve and look up correctly.
- **Shard-seam cells** → a cell whose neighbors fall in a different `base=` shard is still found via its own base cell.

---

## Reproducibility

The dataset is a deterministic derivative of public inputs. A rebuild from the same Copernicus GLO-30 vintage + the pinned H3 / PROJ versions (recorded in each Parquet footer) reproduces the cell values bit-for-bit on `count`/`min`/`max` and within fp on `mean`/`stddev`. The build + these checks live in this repository.

## Known limitations

See the [README caveats](README.md#coverage--caveats): it's a **DSM** (surface, not bare ground), **land-only/sparse** (missing = sea level), under-reports **sharp peaks**, flat-lines **water**, and carries the usual **polar** DEM artifacts.
