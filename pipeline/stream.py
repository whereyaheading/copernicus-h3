"""Tile sourcing for the Copernicus->H3 pipeline.

Default is pure HTTP streaming via /vsicurl/ (no disk). With an LRU disk cache enabled
(`cache_gb > 0`) the COG is downloaded once and reused — a big speedup while iterating on
M1/M2, where the same tiles are re-read dozens of times. The cache is capped and evicts
least-recently-used tiles, so it never grows unbounded. OFF by default and for the M3
global run (which touches each tile exactly once, so caching only burns disk).
"""
from __future__ import annotations

import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator, Sequence

BUCKET = "copernicus-dem-30m"
CACHE_DIR = os.environ.get("COPERNICUS_TILE_CACHE_DIR",
                           os.path.join(os.path.dirname(__file__), ".tile_cache"))


def tile_https(name: str) -> str:
    return f"https://{BUCKET}.s3.amazonaws.com/{name}/{name}.tif"


def _evict_to_cap(cap_bytes: int) -> None:
    """Drop least-recently-used cached tiles until total size <= cap."""
    files = [(os.path.getatime(p), os.path.getsize(p), p)
             for p in (os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR))
             if p.endswith(".tif")]
    total = sum(sz for _, sz, _ in files)
    for _, sz, p in sorted(files):          # oldest access first
        if total <= cap_bytes:
            break
        os.remove(p)
        total -= sz


def tile_source(name: str, cache_gb: float = 0.0) -> str:
    """Return a rasterio-openable source for `name` — a local path if cached, else vsicurl."""
    if cache_gb <= 0:
        return f"/vsicurl/{tile_https(name)}"
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{name}.tif")
    if os.path.exists(path):
        os.utime(path, None)                # mark as recently used (LRU)
        return path
    tmp = path + ".part"
    urllib.request.urlretrieve(tile_https(name), tmp)
    os.replace(tmp, path)
    _evict_to_cap(int(cache_gb * 1e9))
    return path


def prefetched(names: Sequence[str], lookahead: int = 3,
               cache_gb: float = 5.0) -> Iterator[tuple[str, str]]:
    """Yield (name, local_path) in order while keeping `lookahead` tiles downloading
    ahead of the consumer, so the next tiles are on disk by the time we reach them.

    Prefetch requires a cache (somewhere to land the tiles); a small default is forced
    if caching is off. The consumer processes tile i while i+1..i+lookahead download.
    """
    cache_gb = max(cache_gb, 5.0)
    n = len(names)
    with ThreadPoolExecutor(max_workers=lookahead) as ex:
        inflight = {i: ex.submit(tile_source, names[i], cache_gb)
                    for i in range(min(lookahead, n))}
        for i in range(n):
            path = inflight.pop(i).result()         # block only if it's not ready yet
            j = i + lookahead                        # queue the tile `lookahead` ahead
            if j < n:
                inflight[j] = ex.submit(tile_source, names[j], cache_gb)
            yield names[i], path
