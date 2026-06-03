"""Does parallel tile download actually scale? Measures aggregate throughput at several
concurrency levels and projects the full 26,450-tile wall time.

Each level uses a DISJOINT batch of fresh (uncached) land tiles so S3/CDN caching can't
skew later runs. Downloads to memory and discards — we only care about bytes/sec.
"""
from __future__ import annotations

import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import stream

TOTAL_TILES = 26_450
AVG_TILE_MB = 22.5                                   # mean compressed COG size (doc probe)
# Central US: contiguous all-land tiles (lon 3-digit ZERO-PADDED -> W094 not W94).
LEVELS = [1, 4, 8, 16]
LON_COLS = list(range(109, 93, -1))                 # 16 longitudes -> 16 tiles/batch
BATCHES = {k: [f"Copernicus_DSM_COG_10_N{lat}_00_W{lon:03d}_00_DEM" for lon in LON_COLS]
           for k, lat in zip(LEVELS, [46, 45, 44, 43])}


def fetch(name):
    try:
        with urllib.request.urlopen(stream.tile_https(name), timeout=120) as r:
            return len(r.read())
    except Exception as e:
        print(f"  ! {name}: {e}")
        return 0


def main():
    print(f"Projecting wall time for {TOTAL_TILES:,} tiles.\n")
    print(f"{'threads':>7} {'tiles':>5} {'wall':>7} {'MB/s':>7} {'tiles/s':>8} "
          f"{'s/tile':>7} {'-> full run':>12}")
    for k in LEVELS:
        batch = BATCHES[k]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=k) as ex:
            sizes = list(ex.map(fetch, batch))
        wall = time.time() - t0
        ok = [s for s in sizes if s > 0]
        mbps = (sum(ok) / 1e6) / wall
        # full-run floor is bandwidth-bound: total bytes / sustained MB/s
        proj_h = (TOTAL_TILES * AVG_TILE_MB) / mbps / 3600
        print(f"{k:>7} {len(ok):>5} {wall:>6.1f}s {mbps:>7.1f} {len(ok)/wall:>8.2f} "
              f"{wall/len(ok):>6.1f}s {proj_h:>9.1f} h")
    print(f"\nFull run = {TOTAL_TILES:,} tiles x {AVG_TILE_MB} MB = "
          f"{TOTAL_TILES*AVG_TILE_MB/1000:.0f} GB; wall floor = that / sustained MB/s.")
    print("Download only (no decode). With prefetch, compute overlaps download.")


if __name__ == "__main__":
    main()
