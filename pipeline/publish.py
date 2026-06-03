"""Write a finalized latitude band to S3, and report which bands are already done.

Layout: s3://{bucket}/v1/{band}/res{R}/part-000.parquet, plus a s3://.../v1/{band}/_COMPLETE
marker written LAST. The marker is the atomic "this band is fully done" signal — if a run
dies mid-band there's no marker, so a resume re-does that band (overwriting is idempotent).
This is what makes the run resumable and lets a faster box pick up from a slower one.
"""
from __future__ import annotations

import io

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

_s3 = None

SCHEMA_FIELDS = [
    ("h3_cell", pa.uint64()),
    ("elevation_mean", pa.float32()),
    ("elevation_max", pa.float32()),
    ("elevation_min", pa.float32()),
    ("elevation_stddev", pa.float32()),
    ("pixel_count", pa.uint32()),
]


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name="eu-central-1")
    return _s3


def res_key(band, res):
    return f"v1/{band}/res{res}/part-000.parquet"


def marker_key(band):
    return f"v1/{band}/_COMPLETE"


def completed_bands(bucket) -> set[str]:
    """Bands that have a _COMPLETE marker — skipped on resume."""
    done = set()
    pages = s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="v1/")
    for page in pages:
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/_COMPLETE"):
                done.add(obj["Key"].split("/")[1])
    return done


def write_band(bucket, band, per_res_cols, footer):
    """Upload every resolution's Parquet, then the atomic _COMPLETE marker."""
    for res, cols in per_res_cols.items():
        schema = pa.schema(SCHEMA_FIELDS, metadata={
            **footer, b"resolution": str(res).encode(), b"lat_band": band.encode()})
        table = pa.table({n: cols[n] for n, _ in SCHEMA_FIELDS}, schema=schema)
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="zstd")
        s3().put_object(Bucket=bucket, Key=res_key(band, res), Body=buf.getvalue())
    s3().put_object(Bucket=bucket, Key=marker_key(band), Body=b"")   # LAST = atomic done
