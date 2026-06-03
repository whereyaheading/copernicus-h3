"""Plan latitude bands for the Copernicus->H3 dataset.

File size is driven by hex COUNT (fixed-width row per hex), not terrain ruggedness,
and H3 cells are ~equal-area, so output bytes per band scale with LAND AREA per band.
We size each banded resolution to ~TARGET_MB per file and snap cuts to round latitudes.

Land-area-by-latitude comes from analysis/land_area_by_latitude.csv (computed from the
global_land_mask 1.8km landmask; total 147.4 Mkm2, ~1% of the canonical 148.9).
"""
import csv
import numpy as np

TARGET_MB = 500
BYTES_PER_ROW = 30            # compressed parquet, per doc
CELL_AREA_KM2 = {             # avg H3 cell area, per planning doc table
    2: 86746, 3: 12393, 4: 1770, 5: 253, 6: 36, 7: 5.16, 8: 0.74, 9: 0.105,
}

# --- load land area per 1-degree band (north -> south) ---
lat_north, land = [], []
with open("analysis/land_area_by_latitude.csv") as f:
    for row in csv.DictReader(f):
        lat_north.append(int(row["lat_north"]))
        land.append(float(row["land_km2"]))
lat_north = np.array(lat_north)
land = np.array(land)
total_land = land.sum()
south_edge = lat_north - 1                 # 89 .. -90
cum = np.cumsum(land)                       # cum[i] = land from 90N down to south_edge[i]


def cum_to(lat):
    """Cumulative land (km2) from 90N down to integer boundary `lat`."""
    if lat >= 90:
        return 0.0
    if lat <= -90:
        return total_land
    return float(cum[np.where(south_edge == lat)[0][0]])


def plan(res, step, fixed_cuts=None):
    """Greedy walk N->S, cutting only at multiples of `step` degrees. Cut when the
    band so far is closer to the target than it would be after one more step (so each
    file lands as near TARGET_MB as the rounding allows). `fixed_cuts` overrides the
    greedy search with an explicit list of cut latitudes (e.g. hemispheres = [90,0,-90])."""
    cell = CELL_AREA_KM2[res]
    size_total_mb = (total_land / cell) * BYTES_PER_ROW / 1e6
    if fixed_cuts is not None:
        cuts = list(fixed_cuts)
    else:
        target_land = (TARGET_MB * 1e6 / BYTES_PER_ROW) * cell  # km2 per target-size file
        cuts, acc = [90], 0.0
        for i in range(len(land)):
            ls = south_edge[i]
            acc += land[i]
            if ls % step and ls != -90:                         # not a legal cut latitude
                continue
            nb = next((c for c in range(ls - 1, -91, -1) if c % step == 0 or c == -90), -90)
            next_land = cum_to(nb) - cum_to(ls)                 # land in the following step
            if ls == -90 or acc >= target_land - next_land / 2:
                cuts.append(ls)
                acc = 0.0
        if cuts[-1] != -90:
            cuts.append(-90)
    rows = []
    for ln, ls in zip(cuts[:-1], cuts[1:]):
        la = cum_to(ls) - cum_to(ln)
        rows.append((ln, ls, la, la / cell, la / cell * BYTES_PER_ROW / 1e6))
    return len(rows), size_total_mb, rows


print(f"Total land {total_land/1e6:.2f} Mkm2 | target {TARGET_MB} MB/file | {BYTES_PER_ROW} B/row\n")
print(f"{'res':>3} {'total':>9} {'files':>6}  banding")
for res in range(2, 10):
    cell = CELL_AREA_KM2[res]
    size_mb = (total_land / cell) * BYTES_PER_ROW / 1e6
    if size_mb <= TARGET_MB:
        sz = f"{size_mb*1000:.0f} KB" if size_mb < 1 else f"{size_mb:.1f} MB"
        print(f"{res:>3} {sz:>9} {1:>6}  single file (under target)")
print()

PLANS = [
    (7, 5, [90, 0, -90]),   # res 7: two files, N and S hemispheres
    (8, 5, None),           # res 8: greedy at 5-deg boundaries
    (9, 1, None),           # res 9: greedy at whole-degree boundaries
]
for res, step, fixed in PLANS:
    n, size_mb, rows = plan(res, step, fixed)
    unit = f"{size_mb/1000:.1f} GB" if size_mb >= 1000 else f"{size_mb:.0f} MB"
    mbs = [r[4] for r in rows]
    how = "fixed hemispheres" if fixed else f"snap {step} deg"
    print(f"=== res {res}: {unit} total -> {len(rows)} files ({how}) "
          f"| size MB min/mean/max = {min(mbs):.0f}/{sum(mbs)/len(mbs):.0f}/{max(mbs):.0f} ===")
    print(f"   {'north':>6} {'south':>6} {'width':>6} {'land Mkm2':>10} {'rows':>12} {'size MB':>8}")
    for ln, ls, la, r, mb in rows:
        print(f"   {ln:>+6} {ls:>+6} {ln-ls:>5}d {la/1e6:>10.2f} {r:>12,.0f} {mb:>8.0f}")
    with open(f"analysis/bands_res{res}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["band", "lat_north", "lat_south", "land_km2", "est_rows", "est_mb"])
        for idx, (ln, ls, la, r, mb) in enumerate(rows):
            w.writerow([idx, ln, ls, f"{la:.1f}", f"{r:.0f}", f"{mb:.1f}"])
    print(f"   -> wrote analysis/bands_res{res}.csv\n")
