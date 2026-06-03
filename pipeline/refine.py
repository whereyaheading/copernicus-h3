"""Boundary-merge refinement for the Copernicus->H3 dataset.

Each latitude band was aggregated independently from its own tiles, so a hex that straddles
a band boundary appears in two adjacent bands with PARTIAL pixels. This recombines them.

The published columns (mean/max/min/stddev/pixel_count) reconstruct the composable stats:
    sum    = mean * pixel_count
    sumsq  = (stddev^2 + mean^2) * pixel_count
    merged: count=Σcount, sum=Σsum, sumsq=Σsumsq, min=min, max=max
    -> mean = Σsum/Σcount,  stddev = sqrt(Σsumsq/Σcount - mean^2)
A cell present in only one band passes through unchanged (the formula is identity on one row).

Output is SORTED by h3_cell (compresses well + region-queryable via predicate pushdown) and,
for the large resolutions, partitioned by **H3 res-1 parent** (NOT latitude — a merged
straddling cell has no single latitude band; its res-1 parent is well-defined). Because the
output is sorted by h3_cell and res-1 parents are contiguous in that order, each partition
writes as ONE file. Raw `data/` is never touched; merged output goes to a separate `--out-dir`.

  python pipeline/refine.py --data-dir data --out-dir h3-terrain
  python pipeline/refine.py --data-dir data --out-dir h3-terrain --res 8,9   # just the partitioned ones
"""
import argparse
import glob
import os

import duckdb

MERGE_SQL = """
SELECT
  h3_cell,
  (SUM(elevation_mean::DOUBLE * pixel_count) / SUM(pixel_count))::FLOAT          AS elevation_mean,
  MAX(elevation_max)::FLOAT                                                      AS elevation_max,
  MIN(elevation_min)::FLOAT                                                      AS elevation_min,
  sqrt(GREATEST(
      SUM((elevation_stddev::DOUBLE*elevation_stddev + elevation_mean::DOUBLE*elevation_mean) * pixel_count)
        / SUM(pixel_count)
      - pow(SUM(elevation_mean::DOUBLE*pixel_count)/SUM(pixel_count), 2), 0.0))::FLOAT AS elevation_stddev,
  SUM(pixel_count)::UINTEGER                                                     AS pixel_count
FROM read_parquet({files})
GROUP BY h3_cell
"""

PARTITION_RES = {8, 9}   # big enough to exceed the 2GB single-file (distribution) limit
PARENT_RES = 1           # bin by H3 res-1 parent (~250 land partitions)


def files_for_res(data_dir, res, bands=None):
    files = sorted(glob.glob(os.path.join(data_dir, "lat_*", f"res{res}", "part-*.parquet")))
    if bands:
        files = [f for f in files if any(f"/{b}/" in f for b in bands)]
    return files


def merge_res(con, files, out_dir, res, parent_res):
    flist = "[" + ",".join(f"'{f}'" for f in files) + "]"
    merge = MERGE_SQL.format(files=flist)
    rows_in = con.execute(f"SELECT count(*) FROM read_parquet({flist})").fetchone()[0]
    if res in PARTITION_RES:
        out = os.path.join(out_dir, f"res{res}")
        os.makedirs(out, exist_ok=True)
        # No global ORDER BY here: sorting 1.7B rows (res 9) blows the temp-dir spill.
        # res-1 partitioning + max_open_files keeps the file count sane; per-partition
        # sort+compaction happens later in the (additive) geoid pass, where it's cheap.
        con.execute(f"""COPY (SELECT *, h3_cell_to_parent(h3_cell, {parent_res}) AS r{parent_res}
                              FROM ({merge}))
                        TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (r{parent_res}),
                                    OVERWRITE_OR_IGNORE)""")
        rows_out = con.execute(f"SELECT count(*) FROM read_parquet('{out}/**/*.parquet')").fetchone()[0]
        nfiles = len(glob.glob(os.path.join(out, "**", "*.parquet"), recursive=True))
        nparts = len(glob.glob(os.path.join(out, f"r{parent_res}=*")))
        layout = f"{nparts} res-{parent_res} partitions, {nfiles} files"
    else:
        out = os.path.join(out_dir, f"res{res}", "part-000.parquet")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        con.execute(f"COPY ({merge} ORDER BY h3_cell) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        rows_out = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
        layout = "single file"
    return rows_in, rows_out, layout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--res", default="2,3,4,5,6,7,8,9")
    ap.add_argument("--parent-res", type=int, default=PARENT_RES)
    ap.add_argument("--bands", default=None, help="comma list to restrict (testing)")
    ap.add_argument("--mem", default="12GB")
    args = ap.parse_args()

    bands = args.bands.split(",") if args.bands else None
    spill = os.path.join(args.out_dir, "_spill")
    os.makedirs(spill, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    con.execute(f"SET memory_limit='{args.mem}'")
    con.execute(f"SET temp_directory='{spill}'")
    con.execute("SET partitioned_write_max_open_files=1024")
    con.execute("SET preserve_insertion_order=false")   # lighter spill on the big merges
    con.execute("SET max_temp_directory_size='106GB'")  # use available headroom for res-9 spill
    con.execute("SET threads=4")                        # fewer parallel spill buffers

    print(f"{'res':>3} {'files':>6} {'rows_in':>15} {'rows_out':>15} {'merged':>10}  layout")
    for res in [int(r) for r in args.res.split(",")]:
        files = files_for_res(args.data_dir, res, bands)
        if not files:
            print(f"{res:>3}   (no files)")
            continue
        rin, rout, layout = merge_res(con, files, args.out_dir, res, args.parent_res)
        print(f"{res:>3} {len(files):>6} {rin:>15,} {rout:>15,} {rin-rout:>10,}  {layout}", flush=True)


if __name__ == "__main__":
    main()
