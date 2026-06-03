"""Run the validation suite against the finished dataset and emit a report.

Executes the checks described in VALIDATION.md against `h3-terrain/` and writes the results to
VALIDATION_REPORT.md (and prints them). Reproducible: anyone can re-run on the public data.

  python pipeline/validate.py --dir h3-terrain
"""
import argparse
import datetime as dt
import glob
import os
import re
import time

import duckdb

# (name, lat, lon, res, column, expected, tol)
ELEV_LANDMARKS = [
    ("LAX",          33.9416, -118.4085, 8, "elevation_mean",   38, 25),
    ("Death Valley", 36.2468, -116.8143, 8, "elevation_mean",  -86, 15),
    ("Four Corners", 36.999,  -109.045,  8, "elevation_mean", 1477, 25),
    ("Everest",      27.9881,   86.9250, 8, "elevation_max",  8738, 60),
]
# (name, lat, lon, expected N), looked up at res 8
GEOID_PTS = [("KTEB", 40.8503, -74.0608, -32.8), ("LAX", 33.9416, -118.4085, -36.0),
             ("KDEN", 39.8617, -104.6731, -18.2), ("London", 51.4775, -0.4614, 46.5),
             ("Tokyo", 35.68, 139.69, 36.7)]
COLS = ["h3_cell", "elevation_mean", "elevation_max", "elevation_min",
        "elevation_stddev", "pixel_count", "geoid_undulation"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="h3-terrain")
    args = ap.parse_args()
    D = args.dir
    con = duckdb.connect()
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    con.execute("SET memory_limit='8GB'")            # streaming aggregates only — no big hashes

    rows = []   # (section, check, detail, ok)
    def rec(section, check, detail, ok):
        rows.append((section, check, detail, ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {section} · {check}: {detail}", flush=True)

    def src(res):
        single = f"{D}/res{res}/part-000.parquet"
        return single if os.path.exists(single) else f"{D}/res{res}/base=*/*.parquet"

    def cell(lat, lon, res):
        return con.execute(f"SELECT h3_latlng_to_cell({lat}, {lon}, {res})").fetchone()[0]

    def partition(res, c):
        base = (c >> 45) & 127            # H3 base cell by bit math — the published shard key
        return f"{D}/res{res}/base={base}/*.parquet", base

    # ---- Tier 1: structural invariants + per-resolution pixel totals (one scan each) ----
    px_total = {}
    for res in range(2, 10):
        q = con.execute(f"""
          SELECT count(*),
            count(*) FILTER (WHERE elevation_min > elevation_mean+0.01 OR elevation_mean > elevation_max+0.01),
            count(*) FILTER (WHERE elevation_stddev < 0 OR pixel_count < 1),
            count(*) FILTER (WHERE isnan(elevation_mean) OR isinf(elevation_mean)
                               OR isnan(geoid_undulation) OR isinf(geoid_undulation)),
            count(*) FILTER (WHERE elevation_max > 8849 OR elevation_min < -432),
            count(*) FILTER (WHERE geoid_undulation < -110 OR geoid_undulation > 90),
            sum(pixel_count::HUGEINT), min(elevation_min), max(elevation_max)
          FROM read_parquet('{src(res)}')""").fetchone()
        n, bord, bnn, bnan, brng, bgeo, px, gmn, gmx = q
        px_total[res] = int(px)
        ok = (bord == 0 and bnn == 0 and bnan == 0 and brng == 0 and bgeo == 0)
        rec("Tier1 invariants", f"res{res}",
            f"{n:,} rows; violations order={bord} nonneg={bnn} nan={bnan} range={brng} "
            f"geoid={bgeo}; elev {gmn:.0f}..{gmx:.0f} m", ok)

    # ---- Conservation: Σpixel_count is the total land-pixel count, equal at every resolution ----
    base = px_total[9]
    spread = max(abs(px_total[r] - base) / base for r in px_total)
    rec("Conservation", "Σpixel_count across resolutions",
        f"res2={px_total[2]:,} … res9={px_total[9]:,} (max spread {spread*100:.3f}%)", spread < 0.001)

    # ---- Tier 2: landmark elevations ----
    for name, lat, lon, res, col, exp, tol in ELEV_LANDMARKS:
        c = cell(lat, lon, res)
        path, _ = partition(res, c)
        r = con.execute(f"SELECT {col} FROM read_parquet('{path}') WHERE h3_cell = {c}").fetchone()
        if r is None:
            rec("Landmark elevation", name, "cell not found", False)
        else:
            rec("Landmark elevation", name, f"{col}={r[0]:.1f} m (exp {exp}±{tol})", abs(r[0]-exp) <= tol)

    # ---- Tier 2: geoid spot-checks (at res 8) ----
    for name, lat, lon, exp in GEOID_PTS:
        c = cell(lat, lon, 8)
        path, _ = partition(8, c)
        r = con.execute(f"SELECT geoid_undulation FROM read_parquet('{path}') WHERE h3_cell={c}").fetchone()
        rec("Geoid", name, f"N={r[0]:+.1f} m (exp {exp:+.1f})" if r else "not found",
            r is not None and abs(r[0]-exp) <= 2.0)

    # ---- AGL end-to-end (KTEB worked example) ----
    c = cell(40.8503, -74.0608, 8); path, _ = partition(8, c)
    r = con.execute(f"SELECT elevation_mean, geoid_undulation FROM read_parquet('{path}') WHERE h3_cell={c}").fetchone()
    agl = -29 - r[0] - r[1]
    rec("AGL", "KTEB on-ground fix", f"-29 - {r[0]:.0f} - ({r[1]:.0f}) = {agl:.1f} m (≈0 expected)", abs(agl) < 8)

    # ---- Tier 4: partition integrity — every cell's base-cell bits match its shard ----
    parts = sorted(glob.glob(f"{D}/res9/base=*"))[::10][:12]
    bad_total = 0
    for p in parts:
        base = int(p.split("base=")[1])
        bad = con.execute(f"SELECT count(*) FROM read_parquet('{p}/*.parquet') "
                          f"WHERE (h3_cell >> 45) & 127 <> {base}").fetchone()[0]
        bad_total += bad
    rec("Partition integrity", f"res9 ({len(parts)} base shards sampled)",
        f"{bad_total} cells in the wrong base-cell shard", bad_total == 0)

    # ---- Tier 5: hypsographic sanity (res 5, ~equal-area cells) ----
    h = con.execute(f"""SELECT avg(elevation_mean),
        count(*) FILTER (WHERE elevation_mean < 200)*1.0/count(*),
        count(*) FILTER (WHERE elevation_mean > 1000)*1.0/count(*)
        FROM read_parquet('{src(5)}')""").fetchone()
    rec("Hypsographic", "mean land elevation",
        f"{h[0]:.0f} m (expect ~600-1000); <200m={h[1]*100:.0f}%, >1000m={h[2]*100:.0f}%",
        600 <= h[0] <= 1100)

    # ---- Stress / performance ----
    c = cell(39.2232, -106.8687, 9); path, _ = partition(9, c)
    t0 = time.time(); con.execute(f"SELECT * FROM read_parquet('{path}') WHERE h3_cell={c}").fetchone()
    rec("Performance", "point lookup (res9, 1 partition)", f"{(time.time()-t0)*1000:.0f} ms", True)
    t0 = time.time(); tot = con.execute(f"SELECT count(*) FROM read_parquet('{src(9)}')").fetchone()[0]
    rec("Performance", "full res9 scan (count)", f"{tot:,} rows in {time.time()-t0:.1f}s", True)

    # ---- write the report ----
    npass = sum(1 for *_, ok in rows if ok)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Validation Report", "",
             f"Generated `{now}` by `validate.py` against `{D}/`. **{npass}/{len(rows)} checks passed.**",
             "", "| Section | Check | Result | |", "|---|---|---|---|"]
    for section, check, detail, ok in rows:
        lines.append(f"| {section} | {check} | {detail} | {'✅' if ok else '❌'} |")
    lines += ["", "See [VALIDATION.md](VALIDATION.md) for what each check verifies and its pass criterion."]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # pipeline/ -> repo root
    report = os.path.join(repo_root, "VALIDATION_REPORT.md")
    with open(report, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n{npass}/{len(rows)} passed — wrote {report}")


if __name__ == "__main__":
    main()
