"""M2 multi-tile band prototype: cross-tile merge + finalize-and-flush.

Historical milestone, kept for provenance — this is the script that *proved* the streaming model
before the production sweep (`../run.py`) was written. It exercises the production primitives in
`../compose.py` directly. Streams a 2x2 block of adjacent Copernicus tiles sharing the corner
(36N, 116W), so hexes straddle tile edges (up to 4 tiles at the corner), and verifies the two
correctness risks of the streaming model:

  1. Cross-tile merge exactness — composing per-tile partial stats is bit-identical
     (count/min/max) and fp-identical (sum/mean) to aggregating the union of pixels once.
  2. Finalize-and-flush — after the northern band is done, a hex is safe to flush iff it
     cannot receive pixels from any unprocessed southern tile (its south extent >= the line).
     We verify flushed hexes never change when the southern tiles are later added.

Run:  python pipeline/prototypes/band_dev.py
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rasterio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # pipeline/ on path
import stream
from compose import partial, merge, min_latitude, assert_exact

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

CACHE_GB = 0.0   # set by --tile-cache-gb
NORTH = ["Copernicus_DSM_COG_10_N36_00_W117_00_DEM",
         "Copernicus_DSM_COG_10_N36_00_W116_00_DEM"]   # lat 36..37
SOUTH = ["Copernicus_DSM_COG_10_N35_00_W117_00_DEM",
         "Copernicus_DSM_COG_10_N35_00_W116_00_DEM"]   # lat 35..36
BAND_LINE = 36.0   # boundary between the northern band and the unprocessed south


def read_tile(name):
    with rasterio.open(stream.tile_source(name, CACHE_GB)) as ds:
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
    global CACHE_GB
    ap = argparse.ArgumentParser()
    ap.add_argument("--tile-cache-gb", type=float, default=0.0,
                    help="LRU disk cache cap in GB (0 = stream, no cache)")
    CACHE_GB = ap.parse_args().tile_cache_gb

    names = NORTH + SOUTH
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        tiles = list(ex.map(read_tile, names))
    npx = sum(t[0].size for t in tiles)
    print(f"Streamed {len(names)} tiles, {npx:,} valid px in {time.time()-t0:.1f}s\n")

    # ---- 1. cross-tile merge exactness, per resolution ----
    print("Cross-tile merge exactness (merged partials == single-pass over the union):")
    for res in (7, 8, 9):
        per_tile = [partial(*t, res) for t in tiles]
        merged = merge(per_tile)
        direct = partial(np.concatenate([t[0] for t in tiles]),
                         np.concatenate([t[1] for t in tiles]),
                         np.concatenate([t[2] for t in tiles]), res)
        assert_exact(merged, direct, f"res{res}")
        # boundary hexes = cells that appear in >1 tile
        seen = np.concatenate([p["cell"] for p in per_tile])
        _, c = np.unique(seen, return_counts=True)
        straddle = int((c > 1).sum())
        print(f"  res{res}: {merged['cell'].size:>8,} cells, "
              f"{straddle:>6,} straddle >1 tile -> merge EXACT")

    # ---- 2. finalize-and-flush correctness at res 9 ----
    print("\nFinalize-and-flush (process northern band [36,37], flush, then add south):")
    north_state = merge([partial(*t, 9) for t in tiles[:2]])
    miny = min_latitude(north_state["cell"])
    flushable = miny >= BAND_LINE            # cannot be touched by any tile south of 36N
    held = ~flushable
    print(f"  after north band: {north_state['cell'].size:,} cells -> "
          f"{flushable.sum():,} flushable, {held.sum():,} held (straddle {BAND_LINE}N)")

    # now add the southern tiles and confirm the model held:
    full_state = merge([partial(*t, 9) for t in tiles])
    idx = {int(c): i for i, c in enumerate(full_state["cell"])}
    # flushed cells must be byte-identical in the full run (no southern contribution)
    f_cells = north_state["cell"][flushable]
    f_counts_north = north_state["count"][flushable]
    f_counts_full = np.array([full_state["count"][idx[int(c)]] for c in f_cells])
    assert np.array_equal(f_counts_north, f_counts_full), "FLUSH BUG: a flushed cell changed!"
    # held cells should have gained pixels from the south
    h_cells = north_state["cell"][held]
    h_north = north_state["count"][held]
    h_full = np.array([full_state["count"][idx[int(c)]] for c in h_cells])
    gained = int((h_full > h_north).sum())
    print(f"  [OK] all {flushable.sum():,} flushed cells unchanged by southern tiles")
    print(f"  [OK] {gained:,}/{held.sum():,} held cells gained pixels from the south "
          f"(the merge we'd have lost by flushing early)")


if __name__ == "__main__":
    main()
