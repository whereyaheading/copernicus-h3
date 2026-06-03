"""Measure real per-tile processing throughput on a multi-core box, and project the
full 26,450-tile run. Each worker does the true per-tile work: stream the COG from S3
(in-region) and aggregate composable stats at every resolution 2-9.

Run:  python throughput_bench.py <workers> [num_tiles]
e.g.  python throughput_bench.py 8 32
"""
import csv
import datetime as dt
import os
import sys
import time

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

import numpy as np
import rasterio
from h3ronpy.vector import coordinates_to_cells
from concurrent.futures import ProcessPoolExecutor

TOTAL_TILES = 26_450
BUCKET = "copernicus-dem-30m"
RESULTS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_results.csv")


def record(row):
    """Append a result row to disk immediately (survives a dropped SSH pipe)."""
    new = not os.path.exists(RESULTS_CSV)
    with open(RESULTS_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc", "workers", "tiles", "seconds", "tiles_per_s",
                        "mpx", "proj_hours"])
        w.writerow(row)
        f.flush()


def process_tile(name):
    """Real per-tile work: read + aggregate count/sum/sumsq/min/max at res 2-9."""
    url = f"/vsicurl/https://{BUCKET}.s3.amazonaws.com/{name}/{name}.tif"
    with rasterio.open(url) as ds:
        elev = ds.read(1).astype(np.float64)
        T, nodata = ds.transform, ds.nodata
        h, w = elev.shape
    lon = T.c + (np.arange(w) + 0.5) * T.a
    lat = T.f + (np.arange(h) + 0.5) * T.e
    lon2d, lat2d = np.meshgrid(lon, lat)
    elev = elev.ravel()
    m = np.isfinite(elev) if nodata is None else (np.isfinite(elev) & (elev != nodata))
    elev, la, lo = elev[m], lat2d.ravel()[m], lon2d.ravel()[m]
    for res in range(2, 10):
        cells = np.asarray(coordinates_to_cells(la, lo, res)).astype(np.uint64)
        # fast group-by: sort once, then C-speed reduceat for every stat
        order = np.argsort(cells, kind="stable")
        sc, se = cells[order], elev[order]
        bnd = np.concatenate(([0], np.nonzero(np.diff(sc))[0] + 1))   # group starts
        cnt = np.diff(np.append(bnd, sc.size))
        csum = np.add.reduceat(se, bnd)
        csumsq = np.add.reduceat(se * se, bnd)
        cmin = np.minimum.reduceat(se, bnd)
        cmax = np.maximum.reduceat(se, bnd)
    return elev.size


def land_tiles(n):
    out = []
    for lat in range(43, 35, -1):          # central US plains, all land
        for lon in range(104, 92, -1):
            out.append(f"Copernicus_DSM_COG_10_N{lat}_00_W{lon:03d}_00_DEM")
            if len(out) >= n:
                return out
    return out


if __name__ == "__main__":
    workers = int(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else workers * 4
    tiles = land_tiles(n)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        px = list(ex.map(process_tile, tiles))
    secs = time.time() - t0
    tps = len(tiles) / secs
    mpx = sum(px) / 1e6
    proj_h = TOTAL_TILES / tps / 3600
    record([dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            workers, len(tiles), round(secs, 1), round(tps, 3), round(mpx), round(proj_h, 1)])
    print(f"{len(tiles)} tiles | {workers} workers | {secs:.1f}s "
          f"| {tps:.2f} tiles/s | {mpx:.0f} Mpx total", flush=True)
    print(f"PROJECT: full {TOTAL_TILES:,} tiles at this rate = {proj_h:.1f} h "
          f"(this box; scales ~linearly with vCPUs)  [saved -> {RESULTS_CSV}]", flush=True)
