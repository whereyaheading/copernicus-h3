"""Lay out the final published tree: base-cell shards + provenance footer.

Published partition scheme for res8/res9 is the **base cell**:

    base = (h3_cell >> 45) & 127          # == the H3 res-0 parent, by pure bit math

so a consumer routes to a shard with integer arithmetic and no H3 library call — and it
prunes in plain SQL (`WHERE base = (h3_cell >> 45) & 127`) without the h3 extension. Folders
are `base=0` … `base=121`, ~111 per resolution. Same grouping as res-0, far fewer files than
the build-time res-1 (`r1=`) layout, and no sub-1 MB slivers.

Re-shards from the already-merged `r1=` data **per base** (memory-bounded — no global sort), and
stamps every file's Parquet footer with source/attribution/datum metadata. res2–res7 (single
files) are rewritten in place just to carry the same footer. Idempotent if already base-sharded.

  python pipeline/repartition.py --dir h3-terrain
"""
import argparse
import glob
import os
import re
import shutil

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

FLOATS = ["elevation_mean", "elevation_max", "elevation_min", "elevation_stddev", "geoid_undulation"]
FOOTER = {
    b"source": b"Copernicus DEM GLO-30 (2021 release), s3://copernicus-dem-30m",
    b"attribution": (b"Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and "
                     b"(c) Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS "
                     b"by the European Union and ESA; all rights reserved."),
    b"vertical_datum": b"EGM2008 orthometric (metres)",
    b"horizontal_datum": b"WGS84",
    b"partition_key": b"base = (h3_cell >> 45) & 127  (H3 base cell / res-0 parent)",
    b"schema_version": b"1",
}
EXPECTED = {2: 2956, 3: 17297, 4: 109738, 5: 736665, 6: 5072613,
            7: 35285394, 8: 246405861, 9: 1723282383}


def write(table, path):
    pq.write_table(table.replace_schema_metadata(FOOTER), path,
                   compression="zstd", compression_level=9,
                   use_byte_stream_split=FLOATS,
                   column_encoding={"h3_cell": "DELTA_BINARY_PACKED"},
                   use_dictionary=False, row_group_size=2_000_000)


def read_clean(files):
    """Read literally (no Hive column inference), concat to one table."""
    return pa.concat_tables([pq.ParquetFile(f).read() for f in files])


def reshard(d, res):
    """res8/res9: r1= partitions -> base= shards, sorted, in a temp dir; verify; swap in."""
    r1dirs = glob.glob(f"{d}/res{res}/r1=*")
    if not r1dirs:
        print(f"res{res}: no r1= dirs (already re-sharded?) — skipping"); return
    by_base = {}
    for dd in r1dirs:
        r1 = int(re.search(r"r1=(\d+)", dd).group(1))
        by_base.setdefault((r1 >> 45) & 127, []).extend(glob.glob(dd + "/*.parquet"))
    tmp = f"{d}/res{res}__new"
    shutil.rmtree(tmp, ignore_errors=True)
    total = 0
    for i, b in enumerate(sorted(by_base), 1):
        t = read_clean(by_base[b])
        t = t.take(pc.sort_indices(t, sort_keys=[("h3_cell", "ascending")]))
        os.makedirs(f"{tmp}/base={b}", exist_ok=True)
        write(t, f"{tmp}/base={b}/part-000.parquet")
        total += t.num_rows
        if i % 25 == 0:
            print(f"  res{res}: {i}/{len(by_base)} shards, {total:,} rows", flush=True)
    assert total == EXPECTED[res], f"res{res} ROW COUNT MISMATCH: {total:,} != {EXPECTED[res]:,}"
    shutil.rmtree(f"{d}/res{res}")
    os.rename(tmp, f"{d}/res{res}")
    print(f"res{res}: {len(by_base)} base shards, {total:,} rows  ✓ (swapped in)", flush=True)


def restamp(d, res):
    """res2-7: single file rewritten in place to carry the footer metadata."""
    src = f"{d}/res{res}/part-000.parquet"
    t = pq.ParquetFile(src).read()
    if t.schema.metadata and b"attribution" in t.schema.metadata:
        print(f"res{res}: footer already stamped — skipping"); return
    t = t.take(pc.sort_indices(t, sort_keys=[("h3_cell", "ascending")]))
    write(t, src + ".tmp"); os.replace(src + ".tmp", src)
    assert t.num_rows == EXPECTED[res], f"res{res} count {t.num_rows} != {EXPECTED[res]}"
    print(f"res{res}: {t.num_rows:,} rows, footer stamped  ✓", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="h3-terrain")
    args = ap.parse_args()
    for res in range(2, 8):
        restamp(args.dir, res)
    for res in (8, 9):
        reshard(args.dir, res)
    print("\nrepartition complete.")


if __name__ == "__main__":
    main()
