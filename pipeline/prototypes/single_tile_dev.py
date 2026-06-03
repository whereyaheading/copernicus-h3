"""M1 single-tile prototype for the Copernicus DEM -> H3 dataset.

Streams one Copernicus GLO-30 COG tile from the public AWS bucket, computes H3 cells
at each resolution 2-9 *independently from the pixels* (not rolled up), aggregates the
composable stats, and validates against a known landmark elevation.

Run:  python pipeline/prototypes/single_tile_dev.py            # defaults to the LAX tile
      python pipeline/prototypes/single_tile_dev.py 27.99 86.93 --name Everest
"""
from __future__ import annotations

import argparse
import math
import os
import time

import numpy as np
import rasterio
from h3ronpy.vector import coordinates_to_cells

# vsicurl tuning: COGs support range requests; don't scan the "directory".
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

BUCKET = "copernicus-dem-30m"
RESOLUTIONS = range(2, 10)
ELEV_MIN, ELEV_MAX = -432.0, 8849.0   # Dead Sea shore .. Everest (Tier-1 range gate)

# Landmarks for validation: lat, lon, expected elevation (m), tolerance, which stat.
# Flat/airport sites check `mean`; sharp peaks check `max` against the value Copernicus
# actually reports (GLO-30 under-reports knife-edge summits — Everest reads ~8738, not 8849).
LANDMARKS = {
    "LAX":          (33.9416, -118.4085, 38.0, 25.0, "mean"),
    "Death Valley": (36.2468, -116.8143, -85.0, 15.0, "mean"),
    "Everest":      (27.9881, 86.9250, 8738.0, 30.0, "max"),
}


def tile_name(lat: float, lon: float) -> str:
    """Copernicus 1x1 deg tile name, keyed by the tile's SW (floor) corner."""
    la, lo = math.floor(lat), math.floor(lon)
    ns, ew = ("N" if la >= 0 else "S"), ("E" if lo >= 0 else "W")
    return f"Copernicus_DSM_COG_10_{ns}{abs(la):02d}_00_{ew}{abs(lo):03d}_00_DEM"


def tile_url(name: str) -> str:
    return f"/vsicurl/https://{BUCKET}.s3.amazonaws.com/{name}/{name}.tif"


def read_tile(name: str):
    """Return (elev float64 1-D, lat 1-D, lon 1-D) for valid (non-nodata) pixels."""
    with rasterio.open(tile_url(name)) as ds:
        elev = ds.read(1).astype(np.float64)
        T, nodata = ds.transform, ds.nodata
        h, w = elev.shape
    # pixel-center lon/lat (tiles are EPSG:4326, north-up)
    lon = T.c + (np.arange(w) + 0.5) * T.a
    lat = T.f + (np.arange(h) + 0.5) * T.e
    lon2d, lat2d = np.meshgrid(lon, lat)
    elev = elev.ravel()
    mask = np.isfinite(elev)
    if nodata is not None:
        mask &= elev != nodata
    return elev[mask], lat2d.ravel()[mask], lon2d.ravel()[mask], (h, w)


def aggregate(lat: np.ndarray, lon: np.ndarray, elev: np.ndarray, res: int):
    """Per-hex composable stats at one resolution. Returns dict of parallel arrays."""
    cells = np.asarray(coordinates_to_cells(lat, lon, res)).astype(np.uint64)
    uniq, inv = np.unique(cells, return_inverse=True)
    count = np.bincount(inv).astype(np.int64)
    csum = np.bincount(inv, weights=elev)
    csumsq = np.bincount(inv, weights=elev * elev)
    cmin = np.full(uniq.size, np.inf)
    cmax = np.full(uniq.size, -np.inf)
    np.minimum.at(cmin, inv, elev)
    np.maximum.at(cmax, inv, elev)
    mean = csum / count
    var = np.maximum(csumsq / count - mean * mean, 0.0)   # clamp fp noise >= 0
    return {
        "cell": uniq,
        "mean": mean.astype(np.float32),
        "max": cmax.astype(np.float32),
        "min": cmin.astype(np.float32),
        "stddev": np.sqrt(var).astype(np.float32),
        "pixel_count": count.astype(np.uint32),
    }


def check_invariants(agg: dict, res: int) -> None:
    """Tier-1 always-on invariants from the planning doc."""
    assert (agg["pixel_count"] >= 1).all(), f"res{res}: empty cell"
    assert (agg["min"] <= agg["mean"] + 1e-3).all(), f"res{res}: min>mean"
    assert (agg["mean"] <= agg["max"] + 1e-3).all(), f"res{res}: mean>max"
    assert (agg["stddev"] >= 0).all(), f"res{res}: negative stddev"
    assert np.isfinite(agg["mean"]).all(), f"res{res}: non-finite mean"
    lo, hi = agg["min"].min(), agg["max"].max()
    assert ELEV_MIN <= lo and hi <= ELEV_MAX, f"res{res}: elevation {lo}..{hi} out of range"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("lat", nargs="?", type=float, default=LANDMARKS["LAX"][0])
    ap.add_argument("lon", nargs="?", type=float, default=LANDMARKS["LAX"][1])
    ap.add_argument("--name", default="LAX")
    args = ap.parse_args()

    name = tile_name(args.lat, args.lon)
    print(f"Tile: {name}")
    t0 = time.time()
    elev, lat, lon, (h, w) = read_tile(name)
    print(f"Read {h}x{w} = {h*w:,} px, {elev.size:,} valid "
          f"({elev.size/(h*w):.0%}) in {time.time()-t0:.1f}s")
    print(f"  elevation range: {elev.min():.1f} .. {elev.max():.1f} m\n")

    aggs = {}
    print(f"{'res':>3} {'cells':>10} {'px/cell':>8} {'agg s':>6}")
    for res in RESOLUTIONS:
        t = time.time()
        agg = aggregate(lat, lon, elev, res)
        check_invariants(agg, res)
        aggs[res] = agg
        print(f"{res:>3} {agg['cell'].size:>10,} "
              f"{elev.size/agg['cell'].size:>8.0f} {time.time()-t:>6.1f}")

    # --- conservation: every pixel lands in exactly one cell at each resolution ---
    for res, agg in aggs.items():
        total = int(agg["pixel_count"].sum())
        assert total == elev.size, f"res{res} conservation: {total} != {elev.size}"
    print(f"\n[OK] conservation holds (all {elev.size:,} px accounted for at every res)")

    # --- landmark validation at res 8 ---
    exp = LANDMARKS.get(args.name)
    cell = int(np.asarray(coordinates_to_cells(
        np.array([args.lat]), np.array([args.lon]), 8))[0])
    a8 = aggs[8]
    i = np.searchsorted(a8["cell"], np.uint64(cell))
    if i < a8["cell"].size and a8["cell"][i] == cell:
        stat = exp[4] if exp else "mean"
        got = float(a8[stat][i])
        line = (f"\n{args.name} res8 cell {cell:#x}: mean={a8['mean'][i]:.1f} "
                f"min={a8['min'][i]:.1f} max={a8['max'][i]:.1f} m  "
                f"px={int(a8['pixel_count'][i])}")
        if exp:
            ok = abs(got - exp[2]) <= exp[3]
            line += (f"\n  check {stat}={got:.1f} vs expected ~{exp[2]:.0f}±{exp[3]:.0f} "
                     f"-> {'PASS' if ok else 'FAIL'}")
        print(line)
    else:
        print(f"\n{args.name}: cell not found in tile (point outside tile?)")


if __name__ == "__main__":
    main()
