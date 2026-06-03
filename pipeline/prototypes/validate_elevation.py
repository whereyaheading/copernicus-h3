"""Validate the Copernicus->H3 OUTPUT against real-world terrain.

Pulls the finished bands' H3 parquet from S3 (or local ./output), decodes each cell back to
lat/lon, and renders a relief map of our `elevation_mean` — so you can eyeball whether the
DATA is right (ice sheets & mountain ranges high, plains low, known depressions negative),
not just whether the right tiles ran. Adds a quantitative spot-check: at a handful of famous
locations it reports our value next to the real-world expectation.

Ground truth = physical geography. The sweep runs north->south, so until it passes the
equator the map covers the northern hemisphere; re-run as more bands land to see it grow.

  python validate_elevation.py --bucket whereyaheading-copernicus-h3 --res 5
  python validate_elevation.py --local --res 4        # use ./output (dev sample only)

Deps: numpy, pyarrow, h3ronpy, matplotlib, boto3 (all already in the venv). Coastline
overlay is fetched once from Natural Earth and cached; it's optional and skipped if offline.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import urllib.request

import numpy as np
import pyarrow.parquet as pq
import matplotlib
matplotlib.use("Agg")                       # headless: write a PNG, no display needed
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from h3ronpy.vector import cells_to_coordinates

HERE = os.path.dirname(os.path.abspath(__file__))
COAST_CACHE = os.path.join(HERE, "ne_110m_land.geojson")
COAST_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
             "master/geojson/ne_110m_land.geojson")

# (name, lat, lon, expected_low_m, expected_high_m) — all within the done range (lat >= ~42)
GROUND_TRUTH = [
    ("Greenland interior ice", 72.0, -40.0, 2200, 3300),
    ("Jotunheimen, Norway",    61.6,   8.3, 1000, 2000),
    ("Swiss Alps",             46.6,   8.0, 1200, 3000),
    ("N. European Plain (PL)", 52.2,  19.0,   50,  250),
    ("West Siberian Plain",    60.0,  75.0,   20,  160),
    ("Ural Mountains",         60.0,  59.5,  350, 1100),
    ("Caspian depression",     42.5,  50.5,  -40,   10),
    ("Hudson Bay lowland",     55.0, -83.0,  -10,  160),
]


def band_label(lat):
    return f"lat_{lat + 1:+d}_{lat:+d}"


def completed_bands_s3(s3, bucket):
    done = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="v1/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/_COMPLETE"):
                done.append(obj["Key"].split("/")[1])
    return sorted(set(done))


def read_band(res, band, s3=None, bucket=None):
    """Return (cells uint64, elev float, pixels float) for one band/res, or None if absent."""
    key = f"res{res}/{band}/part-000.parquet" if s3 is None else f"v1/{band}/res{res}/part-000.parquet"
    try:
        if s3 is None:
            table = pq.read_table(os.path.join(HERE, "output", f"res{res}", band, "part-000.parquet"))
        else:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            table = pq.read_table(io.BytesIO(body))
    except Exception:
        return None
    cells = np.asarray(table.column("h3_cell")).astype(np.uint64)
    elev = np.asarray(table.column("elevation_mean")).astype(np.float64)
    pix = np.asarray(table.column("pixel_count")).astype(np.float64)
    return cells, elev, pix


def decode(cells):
    rb = cells_to_coordinates(cells)
    return np.asarray(rb.column("lat")), np.asarray(rb.column("lng"))


def load_all(res, use_local, bucket):
    s3 = None
    if not use_local:
        import boto3
        s3 = boto3.client("s3", region_name="eu-central-1")
        bands = completed_bands_s3(s3, bucket)
    else:
        outdir = os.path.join(HERE, "output", f"res{res}")
        bands = sorted(os.listdir(outdir)) if os.path.isdir(outdir) else []
    lat, lon, elev, pix = [], [], [], []
    n_bands = 0
    for b in bands:
        got = read_band(res, b, s3, bucket)
        if not got:
            continue
        cells, e, p = got
        la, lo = decode(cells)
        lat.append(la); lon.append(lo); elev.append(e); pix.append(p)
        n_bands += 1
    if not lat:
        return None
    return (np.concatenate(lat), np.concatenate(lon),
            np.concatenate(elev), np.concatenate(pix), n_bands)


def coastline_segments():
    """Natural Earth 110m land outlines as line segments, or [] if unavailable."""
    try:
        if not os.path.exists(COAST_CACHE):
            urllib.request.urlretrieve(COAST_URL, COAST_CACHE)
        gj = json.load(open(COAST_CACHE))
    except Exception:
        return []
    segs = []
    for feat in gj.get("features", []):
        geom = feat.get("geometry", {})
        polys = geom.get("coordinates", [])
        if geom.get("type") == "Polygon":
            polys = [polys]
        for poly in polys:
            for ring in poly:
                segs.append(np.asarray(ring))
    return segs


def spot_check(lat, lon, elev, pix):
    print("\nGround-truth spot-check (our pixel-weighted mean vs real-world):")
    print(f"  {'location':24} {'our mean':>9}  {'expected':>14}   verdict")
    for name, plat, plon, lo, hi in GROUND_TRUTH:
        m = (np.abs(lat - plat) < 0.4) & (np.abs(lon - plon) < 0.4)
        if not m.any():
            print(f"  {name:24} {'--':>9}  {f'{lo}..{hi} m':>14}   not yet processed")
            continue
        w = pix[m]
        val = float(np.average(elev[m], weights=w if w.sum() else None))
        # generous band: hex-mean smooths away peaks, so allow below `lo` and well above it
        ok = (lo - 250) <= val <= (hi + 400)
        mark = "OK" if ok else "CHECK"
        print(f"  {name:24} {val:8.0f}m  {f'{lo}..{hi} m':>14}   {mark}  ({m.sum()} cells)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", default="whereyaheading-copernicus-h3")
    ap.add_argument("--res", type=int, default=5)
    ap.add_argument("--local", action="store_true", help="use ./output instead of S3")
    ap.add_argument("--out", default=os.path.join(HERE, "elevation_validation.png"))
    args = ap.parse_args()

    print(f"loading res{args.res} cells from {'local ./output' if args.local else 'S3 '+args.bucket} ...")
    got = load_all(args.res, args.local, args.bucket)
    if not got:
        print("No data found — are any bands complete at this resolution?")
        return
    lat, lon, elev, pix, n_bands = got
    print(f"{len(lat):,} H3 cells across {n_bands} bands "
          f"(lat {lat.min():.0f}..{lat.max():.0f}, elev {elev.min():.0f}..{elev.max():.0f} m)")

    # marker size shrinks with finer resolution so cells tile without huge overdraw
    msize = {2: 60, 3: 22, 4: 8, 5: 3, 6: 1.2}.get(args.res, 4)
    fig, ax = plt.subplots(figsize=(16, 8))
    for seg in coastline_segments():
        ax.add_collection(LineCollection([seg], colors="0.6", linewidths=0.4, zorder=1))
    sc = ax.scatter(lon, lat, c=elev, s=msize, cmap="terrain",
                    vmin=-200, vmax=3500, linewidths=0, zorder=2)
    # mark + label the spot-check sites
    for name, plat, plon, *_ in GROUND_TRUTH:
        ax.plot(plon, plat, "o", mfc="none", mec="red", ms=8, mew=1.3, zorder=3)
        ax.annotate(name, (plon, plat), textcoords="offset points", xytext=(6, 4),
                    fontsize=7, color="red", zorder=4)
    ax.set_xlim(-180, 180); ax.set_ylim(min(0, lat.min()) - 2, 90)
    ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    ax.set_title(f"Copernicus->H3 output relief  |  res{args.res}, {n_bands} bands, "
                 f"{len(lat):,} cells  |  validate vs real terrain")
    ax.set_aspect("equal", adjustable="box")
    fig.colorbar(sc, ax=ax, shrink=0.7, label="mean elevation (m)")
    fig.tight_layout()
    fig.savefig(args.out, dpi=130)
    print(f"wrote {args.out}")

    spot_check(lat, lon, elev, pix)


if __name__ == "__main__":
    main()
