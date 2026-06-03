"""Additive geoid pass: add `geoid_undulation` to every cell, and sort each file by h3_cell.

Purely additive — elevation columns are untouched. For every parquet file in the dataset:
  1. compute N = EGM2008 geoid undulation at each cell's centroid (geoid.py, vectorized),
  2. append it as `geoid_undulation` (float32, m),
  3. sort rows by h3_cell, and write back as ONE compacted file per (res / partition).

In place on `h3-terrain/` (raw `data/` is untouched). Idempotent: a file that already has the
column is skipped, so the pass is resumable.

  python pipeline/add_geoid.py --dir h3-terrain
"""
import argparse
import glob
import os
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

import geoid

PARTITION_RES = {8, 9}
OUT_ORDER = ["h3_cell", "elevation_mean", "elevation_max", "elevation_min",
             "elevation_stddev", "pixel_count", "geoid_undulation"]


def process(files, out_path):
    """Read parquet file(s) -> add geoid_undulation -> sort by h3_cell -> write one file."""
    t = pq.read_table(files, columns=OUT_ORDER[:-1])      # the 6 source columns
    cells = t.column("h3_cell").to_numpy()
    N = geoid.undulation_for_cells(cells)                  # float32, m
    t = t.append_column("geoid_undulation", pa.array(N, type=pa.float32()))
    t = t.take(pa.array(np.argsort(cells, kind="stable")))  # sort by h3_cell
    t = t.select(OUT_ORDER)
    tmp = out_path + ".tmp"
    pq.write_table(t, tmp, compression="zstd")
    os.replace(tmp, out_path)
    return t.num_rows


def has_geoid(path):
    return "geoid_undulation" in pq.read_schema(path).names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="h3-terrain")
    args = ap.parse_args()

    for res in range(2, 10):
        rdir = os.path.join(args.dir, f"res{res}")
        if not os.path.isdir(rdir):
            continue
        t0 = time.time()
        if res not in PARTITION_RES:
            f = os.path.join(rdir, "part-000.parquet")
            if has_geoid(f):
                print(f"res{res}: already has geoid — skip", flush=True)
                continue
            n = process([f], f)
            print(f"res{res}: single file, {n:,} rows, +geoid, {time.time()-t0:.1f}s", flush=True)
        else:
            parts = sorted(glob.glob(os.path.join(rdir, "r1=*")))
            done = rows = 0
            for p in parts:
                files = glob.glob(os.path.join(p, "*.parquet"))
                out = os.path.join(p, "part-000.parquet")
                if len(files) == 1 and files[0] == out and has_geoid(out):
                    continue                                  # already processed
                rows += process(files, out)
                for old in files:                             # drop the pre-compaction files
                    if old != out and os.path.exists(old):
                        os.remove(old)
                done += 1
                if done % 100 == 0:
                    print(f"  res{res}: {done}/{len(parts)} partitions "
                          f"({(time.time()-t0)/60:.1f} min)", flush=True)
            print(f"res{res}: {len(parts)} partitions, {rows:,} rows, +geoid+sorted, "
                  f"{(time.time()-t0)/60:.1f} min", flush=True)
    print("geoid pass complete.", flush=True)


if __name__ == "__main__":
    main()
