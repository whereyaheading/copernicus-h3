"""Which Copernicus tiles actually exist, grouped by latitude band.

Lists the public bucket once (tile names only — no downloads) and caches the result.
Not every (lat, lon) has a tile (open ocean has none), so the sweep needs this to know
which longitude tiles make up each latitude band.
"""
from __future__ import annotations

import json
import os
import re

import boto3
from botocore import UNSIGNED
from botocore.client import Config

SRC_BUCKET = "copernicus-dem-30m"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tile_manifest.json")
_NAME_RE = re.compile(r"Copernicus_DSM_COG_10_([NS])(\d+)_00_([EW])(\d+)_00_DEM")


def parse(name: str):
    """'Copernicus_DSM_COG_10_N33_00_W119_00_DEM' -> (lat, lon) signed ints, or None."""
    m = _NAME_RE.search(name)
    if not m:
        return None
    ns, lat, ew, lon = m.groups()
    return int(lat) * (1 if ns == "N" else -1), int(lon) * (1 if ew == "E" else -1)


def build() -> dict[int, list[str]]:
    s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED), region_name="eu-central-1")
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=SRC_BUCKET, Delimiter="/")
    by_lat: dict[int, list[tuple[int, str]]] = {}
    for page in pages:
        for cp in page.get("CommonPrefixes", []):
            name = cp["Prefix"].rstrip("/")
            ll = parse(name)
            if ll:
                by_lat.setdefault(ll[0], []).append((ll[1], name))
    return {lat: [n for _, n in sorted(v)] for lat, v in by_lat.items()}   # west->east


def load(rebuild: bool = False) -> dict[int, list[str]]:
    if not rebuild and os.path.exists(CACHE):
        with open(CACHE) as f:
            return {int(k): v for k, v in json.load(f).items()}
    by_lat = build()
    with open(CACHE, "w") as f:
        json.dump(by_lat, f)
    return by_lat


if __name__ == "__main__":   # build/refresh the cache and print a summary
    m = load(rebuild=True)
    tiles = sum(len(v) for v in m.values())
    print(f"{tiles:,} tiles across {len(m)} latitude bands "
          f"(lat {min(m)}..{max(m)}) -> {CACHE}")
