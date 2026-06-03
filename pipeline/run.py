"""Resumable, parallel latitude-band sweep -> S3.

Sweeps latitude bands north->south. For each band the tiles are processed across a worker
pool (one tile per worker: stream from S3 in-region + aggregate res 2-9 via sort+reduceat),
then merged across the band, finalized, and written to S3. Bands already marked _COMPLETE
are skipped, so the run resumes after any stop and a faster box can take over (see publish.py).

  # full run on the EC2 box (in-region, no cache, all cores):
  nohup python pipeline/run.py --bucket whereyaheading-copernicus-h3 > ~/run.log 2>&1 &
  # bounded test on cached tiles:
  python pipeline/run.py --bucket whereyaheading-copernicus-h3 --bbox 36,38,-112.5,-109.5 --tile-cache-gb 5

Note: bands are aggregated independently (resumable); hexes straddling a band boundary get
each band's pixels separately and need a small boundary-merge post-pass before final publish.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import time
from concurrent.futures import ProcessPoolExecutor

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

import numpy as np
import rasterio
from h3ronpy.vector import coordinates_to_cells

import manifest
import publish
import stream
from compose import merge

RESOLUTIONS = range(2, 10)
FOOTER = {
    b"source": b"Copernicus DEM GLO-30, s3://copernicus-dem-30m",
    b"attribution": b"Produced using Copernicus WorldDEM-30 (c) DLR e.V. / Airbus DS GmbH",
    b"schema_version": b"1",
}


def band_label(lat):
    """Tile N{lat} covers [lat, lat+1]; label by north_south edges, e.g. lat_+37_+36."""
    return f"lat_{lat + 1:+d}_{lat:+d}"


def _agg(la, lo, elev, res):
    """Composable per-hex stats at one resolution via fast sort + reduceat."""
    cells = np.asarray(coordinates_to_cells(la, lo, res)).astype(np.uint64)
    order = np.argsort(cells, kind="stable")
    sc, se = cells[order], elev[order]
    bnd = np.concatenate(([0], np.nonzero(np.diff(sc))[0] + 1))
    return {
        "cell": sc[bnd],
        "count": np.diff(np.append(bnd, sc.size)).astype(np.int64),
        "sum": np.add.reduceat(se, bnd),
        "sumsq": np.add.reduceat(se * se, bnd),
        "min": np.minimum.reduceat(se, bnd),
        "max": np.maximum.reduceat(se, bnd),
    }


def process_tile(args):
    """Worker: stream one tile + return {res: partial}. Runs in a separate process."""
    name, cache_gb = args
    with rasterio.open(stream.tile_source(name, cache_gb)) as ds:
        elev = ds.read(1).astype(np.float64)
        T, nodata = ds.transform, ds.nodata
        h, w = elev.shape
    lon = T.c + (np.arange(w) + 0.5) * T.a
    lat = T.f + (np.arange(h) + 0.5) * T.e
    lon2d, lat2d = np.meshgrid(lon, lat)
    elev = elev.ravel()
    m = np.isfinite(elev) if nodata is None else (np.isfinite(elev) & (elev != nodata))
    la, lo, elev = lat2d.ravel()[m], lon2d.ravel()[m], elev[m]
    return {r: _agg(la, lo, elev, r) for r in RESOLUTIONS}


def finalize(m):
    c = m["count"]
    mean = m["sum"] / c
    var = np.maximum(m["sumsq"] / c - mean * mean, 0.0)
    o = np.argsort(m["cell"])
    return {
        "h3_cell": m["cell"][o].astype(np.uint64),
        "elevation_mean": mean[o].astype(np.float32),
        "elevation_max": m["max"][o].astype(np.float32),
        "elevation_min": m["min"][o].astype(np.float32),
        "elevation_stddev": np.sqrt(var)[o].astype(np.float32),
        "pixel_count": c[o].astype(np.uint32),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--bbox", help="lat_min,lat_max,lon_min,lon_max — limit the run (testing)")
    ap.add_argument("--tile-cache-gb", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    args = ap.parse_args()

    bbox = [float(x) for x in args.bbox.split(",")] if args.bbox else None
    tiles_by_lat = manifest.load()
    lats = sorted((l for l in tiles_by_lat if not bbox or bbox[0] <= l < bbox[1]), reverse=True)

    done = publish.completed_bands(args.bucket)
    todo = [l for l in lats if band_label(l) not in done]
    print(f"{len(lats)} bands in scope | {len(done)} complete | {len(todo)} to do "
          f"| {args.workers} workers -> s3://{args.bucket}/v1/", flush=True)

    footer = {**FOOTER, b"generated_at": dt.datetime.now(dt.timezone.utc).isoformat().encode()}
    cache = max(args.tile_cache_gb, 0.0)
    swept = failed = 0
    t_start = time.time()
    ex = ProcessPoolExecutor(max_workers=args.workers)
    for lat in todo:
        band = band_label(lat)
        tiles = tiles_by_lat[lat]
        if bbox:
            tiles = [t for t in tiles if bbox[2] <= manifest.parse(t)[1] < bbox[3]]
        if not tiles:
            continue
        t0 = time.time()
        try:
            results = list(ex.map(process_tile, [(t, cache) for t in tiles]))
            cols = {r: finalize(merge([res[r] for res in results])) for r in RESOLUTIONS}
            publish.write_band(args.bucket, band, cols, footer)
            swept += 1
            print(f"  [{swept}/{len(todo)}] {band}: {len(tiles)} tiles, "
                  f"{cols[9]['h3_cell'].size:,} res9 cells, {time.time() - t0:.1f}s "
                  f"(elapsed {(time.time() - t_start)/60:.1f} min)", flush=True)
        except Exception as e:
            # log and keep going — the band has no _COMPLETE marker, so a later run retries it
            failed += 1
            print(f"  !! {band} FAILED ({type(e).__name__}: {e}) — skipping, will retry on "
                  f"resume", flush=True)
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            ex = ProcessPoolExecutor(max_workers=args.workers)   # recreate in case pool broke
    ex.shutdown()
    print(f"sweep done: {swept} bands written, {failed} failed/skipped, "
          f"{(time.time() - t_start)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
