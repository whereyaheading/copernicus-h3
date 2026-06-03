"""End-to-end: build one latitude-band's published Parquet files from cached tiles.

Stream -> aggregate res 2-9 independently -> merge across the row's tiles -> finalize
(mean/stddev) -> write the real published schema with a metadata footer, partitioned as
output/res{R}/{lat_band}/part-000.parquet -> read back and validate.

This is a standalone single-row fragment, so cells on the row's N/S/E/W edges only see
this row's pixels (in the global run they'd merge with neighbouring bands/longitudes).

Run:  python pipeline/prototypes/build_band.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # pipeline/ on path
import stream
from compose import partial, merge

OUT = os.path.join(os.path.dirname(__file__), "output")
RESOLUTIONS = range(2, 10)
ROW_LAT = 36                                        # band covers [36, 37]
LONS = [112, 111, 110]                              # W; contiguous, all cached
TILES = [f"Copernicus_DSM_COG_10_N{ROW_LAT}_00_W{lon}_00_DEM" for lon in LONS]
LAT_BAND = f"lat_+{ROW_LAT+1}_+{ROW_LAT}"           # north_south, per doc convention

SCHEMA_FIELDS = [
    ("h3_cell", pa.uint64()),
    ("elevation_mean", pa.float32()),
    ("elevation_max", pa.float32()),
    ("elevation_min", pa.float32()),
    ("elevation_stddev", pa.float32()),
    ("pixel_count", pa.uint32()),
]
FOOTER = {
    b"source": b"Copernicus DEM GLO-30 (COG), s3://copernicus-dem-30m",
    b"attribution": b"Produced using Copernicus WorldDEM-30 (c) DLR e.V. / Airbus DS GmbH",
    b"h3_version": b"4.4.2",
    b"schema_version": b"1",
    b"generated_at": dt.datetime.now(dt.timezone.utc).isoformat().encode(),
}


def read_tile(name):
    with rasterio.open(stream.tile_source(name, cache_gb=5)) as ds:
        elev = ds.read(1).astype(np.float64)
        T, nodata = ds.transform, ds.nodata
        h, w = elev.shape
    lon = T.c + (np.arange(w) + 0.5) * T.a
    lat = T.f + (np.arange(h) + 0.5) * T.e
    lon2d, lat2d = np.meshgrid(lon, lat)
    elev = elev.ravel()
    mask = np.isfinite(elev) if nodata is None else (np.isfinite(elev) & (elev != nodata))
    return lat2d.ravel()[mask], lon2d.ravel()[mask], elev[mask]


def finalize(m):
    """Composable partials -> published columns (mean, stddev derived last)."""
    count = m["count"]
    mean = m["sum"] / count
    var = np.maximum(m["sumsq"] / count - mean * mean, 0.0)
    order = np.argsort(m["cell"])                   # sort by h3_cell ascending
    return {
        "h3_cell": m["cell"][order].astype(np.uint64),
        "elevation_mean": mean[order].astype(np.float32),
        "elevation_max": m["max"][order].astype(np.float32),
        "elevation_min": m["min"][order].astype(np.float32),
        "elevation_stddev": np.sqrt(var)[order].astype(np.float32),
        "pixel_count": count[order].astype(np.uint32),
    }


def write_parquet(res, cols):
    schema = pa.schema(SCHEMA_FIELDS, metadata={
        **FOOTER, b"resolution": str(res).encode(), b"lat_band": LAT_BAND.encode()})
    table = pa.table({n: cols[n] for n, _ in SCHEMA_FIELDS}, schema=schema)
    d = os.path.join(OUT, f"res{res}", LAT_BAND)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "part-000.parquet")
    pq.write_table(table, path, compression="zstd")
    return path, os.path.getsize(path)


def main():
    print(f"Building band {LAT_BAND} from {len(TILES)} cached tiles -> {OUT}\n")
    tiles = [read_tile(t) for t in TILES]
    npx = sum(t[0].size for t in tiles)
    lat_all = np.concatenate([t[0] for t in tiles])
    lon_all = np.concatenate([t[1] for t in tiles])
    elev_all = np.concatenate([t[2] for t in tiles])
    print(f"{npx:,} valid px; elevation {elev_all.min():.0f}..{elev_all.max():.0f} m\n")

    print(f"{'res':>3} {'cells':>9} {'bytes/row':>9} {'file':>9}")
    written = {}
    for res in RESOLUTIONS:
        m = merge([partial(*t, res) for t in tiles])
        assert int(m["count"].sum()) == npx, f"res{res} conservation failed"
        cols = finalize(m)
        # Tier-1 invariants on the finished columns
        assert (cols["elevation_min"] <= cols["elevation_mean"] + 1e-3).all()
        assert (cols["elevation_mean"] <= cols["elevation_max"] + 1e-3).all()
        assert (cols["pixel_count"] >= 1).all()
        path, size = write_parquet(res, cols)
        written[res] = (path, size, len(cols["h3_cell"]))
        print(f"{res:>3} {len(cols['h3_cell']):>9,} "
              f"{size/max(len(cols['h3_cell']),1):>8.1f}B {size/1e3:>7.1f}KB")

    total = sum(s for _, s, _ in written.values())
    print(f"\n[OK] wrote {len(written)} parquet files, {total/1e6:.2f} MB total, "
          f"conservation + invariants pass on every resolution")

    # ---- read back & validate against the raw pixels (Four Corners area) ----
    from h3ronpy.vector import coordinates_to_cells
    res = 8
    t = pq.read_table(written[res][0])
    print(f"\nRead back res{res}: {t.num_rows:,} rows, schema {[f.name for f in t.schema]}")
    print(f"footer: {dict((k.decode(), v.decode()) for k, v in t.schema.metadata.items())}")

    lat0, lon0, exp = 36.9990, -109.0450, None      # Four Corners monument ~1512 m
    cell = int(np.asarray(coordinates_to_cells(np.array([lat0]), np.array([lon0]), res))[0])
    h3col = t.column("h3_cell").to_numpy()
    i = np.searchsorted(h3col, cell)
    if i < len(h3col) and h3col[i] == cell:
        mean = float(t.column("elevation_mean").to_numpy()[i])
        # ground truth: aggregate that cell's raw pixels directly
        allcells = np.asarray(coordinates_to_cells(lat_all, lon_all, res)).astype(np.uint64)
        truth = elev_all[allcells == cell].mean()
        ok = abs(mean - truth) < 1e-2
        print(f"Four Corners res{res} cell {cell:#x}: parquet mean={mean:.2f} m, "
              f"raw-pixel mean={truth:.2f} m -> {'MATCH' if ok else 'MISMATCH'}")


if __name__ == "__main__":
    main()
