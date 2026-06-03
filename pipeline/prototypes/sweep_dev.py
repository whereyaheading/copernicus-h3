"""M3-streaming prototype: prefetched, ROW-BY-ROW (latitude-band) sweep with flush.

The real pipeline processes a *row* = a latitude band (all longitudes at one latitude),
then steps south to the next row, flushing cells that no tile further south can touch.
Here we sweep a small grid (a few longitudes x several latitudes) row by row, north->south:

  - within a row: tiles across longitudes are merged (east-west cross-tile boundaries),
  - across rows: after each latitude band we flush cells whose south extent >= the band edge,
  - the prefetcher stays `--lookahead` tiles ahead in row-major order, overlapping download
    with compute so the sweep rarely blocks on the network.

Run:  python pipeline/prototypes/sweep_dev.py --lookahead 3 --tile-cache-gb 5
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import rasterio
from h3ronpy.vector import cells_bounds_arrays

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # pipeline/ on path
import stream
from compose import partial, merge

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

RES = 9
LONS = [112, 111, 110]                 # degrees West  (a 3-tile-wide row)
LATS = list(range(39, 34, -1))         # N39..N35, swept north -> south
# row-major order: outer latitude (the band), inner longitude (tiles within the band)
TILES = [f"Copernicus_DSM_COG_10_N{lat}_00_W{lon}_00_DEM" for lat in LATS for lon in LONS]


def read_path(path):
    with rasterio.open(path) as ds:
        elev = ds.read(1).astype(np.float64)
        T, nodata = ds.transform, ds.nodata
        h, w = elev.shape
    lon = T.c + (np.arange(w) + 0.5) * T.a
    lat = T.f + (np.arange(h) + 0.5) * T.e
    lon2d, lat2d = np.meshgrid(lon, lat)
    elev = elev.ravel()
    mask = np.isfinite(elev) if nodata is None else (np.isfinite(elev) & (elev != nodata))
    return lat2d.ravel()[mask], lon2d.ravel()[mask], elev[mask]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookahead", type=int, default=3)
    ap.add_argument("--tile-cache-gb", type=float, default=5.0)
    args = ap.parse_args()

    print(f"Sweeping {len(LATS)} rows x {len(LONS)} tiles at res {RES}, "
          f"lookahead={args.lookahead}, cache={args.tile_cache_gb} GB\n")
    print(f"{'row band':<12} {'tiles':>5} {'dl wait':>8} {'wall':>7} {'active':>8} {'flushed':>10}")

    state = None
    flushed = 0
    cur_lat = None
    row_wait = row_n = 0
    row_t0 = time.time()
    prev = time.time()

    def flush(south_edge):
        nonlocal state, flushed
        miny = np.asarray(cells_bounds_arrays(state["cell"]).column("miny"))
        done = miny >= south_edge                      # no tile further south can touch these
        flushed += int(done.sum())                     # -> would be appended to the band parquet
        state = {k: v[~done] for k, v in state.items()}

    for name, path in stream.prefetched(TILES, args.lookahead, args.tile_cache_gb):
        wait = time.time() - prev
        lat = int(name.split("_N")[1][:2])
        if cur_lat is not None and lat != cur_lat:     # finished the previous latitude band
            flush(cur_lat)
            print(f"N{cur_lat}..N{cur_lat+1:<7} {row_n:>5} {row_wait:>7.1f}s "
                  f"{time.time()-row_t0:>6.1f}s {state['cell'].size:>8,} {flushed:>10,}")
            row_wait = row_n = 0
            row_t0 = time.time()
        cur_lat = lat
        lat_a, lon_a, elev = read_path(path)
        tp = partial(lat_a, lon_a, elev, RES)
        state = tp if state is None else merge([state, tp])
        row_wait += wait
        row_n += 1
        prev = time.time()

    flush(cur_lat)                                      # last band
    print(f"N{cur_lat}..N{cur_lat+1:<7} {row_n:>5} {row_wait:>7.1f}s "
          f"{time.time()-row_t0:>6.1f}s {state['cell'].size:>8,} {flushed:>10,}")
    flushed += state["cell"].size                       # tail (no further south in this grid)
    print(f"\n[OK] {len(TILES)} tiles in {len(LATS)} rows, {flushed:,} cells finalized; "
          f"active set stays ~one row's straddle set, not growing with rows already swept.")


if __name__ == "__main__":
    main()
