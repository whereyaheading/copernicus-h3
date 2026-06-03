"""Re-encode every Parquet file in the dataset for much smaller size — no data change.

The cells are sorted by h3_cell, so DELTA_BINARY_PACKED on h3_cell + byte-stream-split on the
float columns roughly halves the files vs pyarrow's defaults (and beats DuckDB's encoding).
In place on `h3-terrain/`; idempotent. Reads each file literally (no Hive inference) so the schema
stays the clean 7 columns.

  python pipeline/recompress.py --dir h3-terrain
"""
import argparse
import glob
import os
import time

import pyarrow.parquet as pq

FLOATS = ["elevation_mean", "elevation_max", "elevation_min", "elevation_stddev", "geoid_undulation"]


def recompress(path):
    t = pq.ParquetFile(path).read()              # literal file -> clean 7 columns, no Hive r1
    tmp = path + ".tmp"
    pq.write_table(
        t, tmp,
        compression="zstd", compression_level=9,
        use_byte_stream_split=FLOATS,            # float columns
        column_encoding={"h3_cell": "DELTA_BINARY_PACKED"},   # sorted ints -> tiny
        use_dictionary=False,
        row_group_size=2_000_000,
    )
    os.replace(tmp, path)
    return t.num_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="h3-terrain")
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.dir, "**", "*.parquet"), recursive=True))
    print(f"recompressing {len(files)} files in {args.dir}/")
    t0 = time.time()
    before = sum(os.path.getsize(f) for f in files)
    for i, f in enumerate(files, 1):
        recompress(f)
        if i % 200 == 0:
            print(f"  {i}/{len(files)} ({(time.time()-t0)/60:.1f} min)", flush=True)
    after = sum(os.path.getsize(f) for f in files)
    print(f"done: {before/1e9:.1f} GB -> {after/1e9:.1f} GB "
          f"({100*(1-after/before):.0f}% smaller), {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
