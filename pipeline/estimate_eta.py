"""Estimate completion time for the Copernicus->H3 latitude-band sweep.

Reproducible ETA from two authoritative inputs — no SSH to the running box, survives
restarts, self-corrects each run as more bands finish:

  * tile_manifest.json    -> tiles per latitude band = the ACTUAL unit of work.
                             One tile per worker (run.py: ex.map(process_tile, tiles)).
  * S3 v1/<band>/_COMPLETE -> which bands are done + when (marker LastModified). Bands are
                             processed sequentially, so consecutive markers' time-delta IS
                             that band's wall time.

Why tiles, not land area: the Copernicus source bucket has NO tile for open ocean
(manifest.py), so ocean is already excluded from the work list — a band's tile count is
its land extent at 1 deg. Within a coastal tile, ocean pixels are nodata and masked before
the costly H3 step, so land-fraction is only a second-order effect; observed seconds/tile
is near-constant, which is why tile count is the right (and exactly-known) predictor.

Model: per-band wall seconds ~= rate * tiles, where `rate` (s/tile) is the robust median over
the most-recent `--window` bands (default 12) — so the CURRENT box drives the estimate and a
mid-run box swap/resize is reflected at once instead of being averaged away. Remaining time =
rate * remaining tiles. Re-run anytime; it self-corrects as the box and latitude regime change.

  python estimate_eta.py --bucket whereyaheading-copernicus-h3

Depends only on boto3 + stdlib (no numpy/pyarrow/rasterio), so it is immune to the iCloud
.venv native-lib eviction that breaks the heavier modules.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

import boto3

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "tile_manifest.json")


def band_label(lat: int) -> str:
    """Match run.py: tile N{lat} covers [lat, lat+1] -> 'lat_+37_+36'."""
    return f"lat_{lat + 1:+d}_{lat:+d}"


def tiles_by_band() -> dict[str, int]:
    with open(MANIFEST) as f:
        by_lat = json.load(f)                      # {"0": [names...], "-12": [...]}
    return {band_label(int(k)): len(v) for k, v in by_lat.items()}


def completed_markers(bucket: str, region: str) -> dict[str, dt.datetime]:
    """{band: marker-LastModified} for every v1/<band>/_COMPLETE (the resume signal)."""
    s3 = boto3.client("s3", region_name=region)
    out: dict[str, dt.datetime] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="v1/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/_COMPLETE"):
                out[obj["Key"].split("/")[1]] = obj["LastModified"]
    return out


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--region", default="eu-central-1")
    ap.add_argument("--window", type=int, default=12,
                    help="estimate the rate from the N most-recent bands (0 = all history). "
                         "A small window tracks the CURRENT box, so a mid-run box swap or "
                         "resize is reflected immediately instead of being averaged away.")
    args = ap.parse_args()

    per_band = tiles_by_band()
    total_bands = len(per_band)
    total_tiles = sum(per_band.values())

    markers = completed_markers(args.bucket, args.region)
    by_time = sorted((t, b) for b, t in markers.items())     # completion order

    # marker-to-marker delta = wall time for the LATER band (sequential processing).
    obs = []                                                 # (tiles, seconds, band)
    for (t_prev, _), (t_cur, band) in zip(by_time, by_time[1:]):
        tiles = per_band.get(band)
        secs = (t_cur - t_prev).total_seconds()
        if tiles and secs > 0:
            obs.append((tiles, secs, band))

    if len(obs) < 2:
        done = len(markers)
        print(f"Only {done} band(s) done — need >=3 to fit a rate. Check back shortly.")
        return

    # Per-tile rate from the most-recent `window` bands, so the CURRENT box's speed drives
    # the ETA. A mid-run box swap/resize shows up as a rate shift instead of being averaged
    # into oblivion. Median (not a linear fit) because recent bands cluster in tile-count,
    # which makes a 2-param fit's slope unstable; per-band overhead (~3s) is negligible here.
    recent = obs[-args.window:] if args.window > 0 else obs
    spt_recent = [s / t for t, s, _ in recent]
    med_recent = _median(spt_recent)
    kept = [r for r in spt_recent if 0.25 * med_recent <= r <= 4 * med_recent]  # drop swap gap/stall
    rate = _median(kept) if kept else med_recent            # s/tile, wall-clock on this box
    rate_all = _median([s / t for t, s, _ in obs])          # full history, for comparison

    done_bands = set(markers)
    remaining = [(bnd, n) for bnd, n in per_band.items() if bnd not in done_bands]
    rem_tiles = sum(n for _, n in remaining)
    rem_secs = rate * rem_tiles
    done_tiles = total_tiles - rem_tiles

    now = dt.datetime.now(dt.timezone.utc)
    eta = now + dt.timedelta(seconds=rem_secs)
    heavy = sorted(remaining, key=lambda x: -x[1])[:5]
    shift = (rate_all / rate) if rate else 1.0
    note = (f"  [{shift:.1f}x faster than older bands — box change?]" if shift > 1.3
            else f"  [{1/shift:.1f}x slower than older bands]" if shift < 0.77 else "")

    print(f"== Copernicus->H3 sweep ETA ==                  {now:%Y-%m-%d %H:%M UTC}")
    print(f"  bands : {len(done_bands):>4}/{total_bands}  ({len(done_bands)/total_bands:5.1%} of bands)")
    print(f"  tiles : {done_tiles:>7,}/{total_tiles:,}  ({done_tiles/total_tiles:5.1%})   <- real progress")
    print(f"  rate  : {rate:.3f} s/tile  (~{60/rate:.0f} tiles/min)  from last {len(kept)}/{len(recent)} "
          f"bands{note}")
    print(f"          all-history rate {rate_all:.3f} s/tile  ({rate_all/rate if rate else 1:.1f}x the window)")
    print(f"  left  : {len(remaining)} bands, {rem_tiles:,} tiles")
    print(f"  ETA   : ~{rem_secs/3600:.1f} h   ->   {eta:%Y-%m-%d %H:%M UTC}")
    print(f"  heavy : {', '.join(f'{b}({n})' for b, n in heavy)}  (most-expensive remaining)")


if __name__ == "__main__":
    main()
