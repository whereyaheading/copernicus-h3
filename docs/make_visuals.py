"""Generate the README/card visuals straight from the published dataset.

  python docs/make_visuals.py --dir h3-terrain --out docs/visuals
"""
import argparse
import glob
import os

import h3
import numpy as np
import pyarrow.parquet as pq
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import LinearSegmentedColormap
from h3ronpy.vector import cells_to_coordinates

# hypsometric land tint: green lowlands -> tan -> brown -> snow
LAND = LinearSegmentedColormap.from_list("land", [
    (0.00, "#27604a"), (0.12, "#3f8a4f"), (0.32, "#a7c163"),
    (0.52, "#e2c879"), (0.70, "#b07a43"), (0.86, "#7a5230"), (1.00, "#ffffff")])
OCEAN = "#0a1722"


def centroids(cells):
    c = cells_to_coordinates(np.asarray(cells, dtype="uint64"))
    return np.asarray(c.column("lat")), np.asarray(c.column("lng"))


def load(path, cols):
    t = pq.ParquetFile(path).read(columns=cols)
    return {c: t.column(c).to_numpy() for c in cols}


def global_elevation(D, out):
    d = load(f"{D}/res5/part-000.parquet", ["h3_cell", "elevation_mean"])
    lat, lng = centroids(d["h3_cell"])
    fig, ax = plt.subplots(figsize=(16, 8)); fig.set_facecolor(OCEAN); ax.set_facecolor(OCEAN)
    sc = ax.scatter(lng, lat, c=d["elevation_mean"], s=1.1, cmap=LAND, vmin=0, vmax=6000,
                    linewidths=0, rasterized=True)
    ax.set_xlim(-180, 180); ax.set_ylim(-58, 84); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    cb = fig.colorbar(sc, ax=ax, shrink=0.55, pad=0.01); cb.set_label("mean elevation (m)", color="w")
    cb.ax.yaxis.set_tick_params(color="w"); plt.setp(cb.ax.get_yticklabels(), color="w")
    cb.outline.set_edgecolor("none")
    ax.set_title("Copernicus GLO-30 Terrain, H3-Indexed — mean elevation (res 5, ~740k cells)",
                 color="w", fontsize=15, pad=12)
    fig.savefig(f"{out}/global_elevation.png", dpi=140, bbox_inches="tight", facecolor=OCEAN)
    plt.close(fig); print("  global_elevation.png")


def geoid_map(D, out):
    d = load(f"{D}/res5/part-000.parquet", ["h3_cell", "geoid_undulation"])
    lat, lng = centroids(d["h3_cell"])
    fig, ax = plt.subplots(figsize=(16, 8)); fig.set_facecolor("#10131a"); ax.set_facecolor("#10131a")
    sc = ax.scatter(lng, lat, c=d["geoid_undulation"], s=1.1, cmap="Spectral_r",
                    vmin=-90, vmax=90, linewidths=0, rasterized=True)
    ax.set_xlim(-180, 180); ax.set_ylim(-58, 84); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    cb = fig.colorbar(sc, ax=ax, shrink=0.55, pad=0.01)
    cb.set_label("geoid undulation N (m)", color="w")
    cb.ax.yaxis.set_tick_params(color="w"); plt.setp(cb.ax.get_yticklabels(), color="w")
    cb.outline.set_edgecolor("none")
    ax.set_title("EGM2008 geoid undulation — the shipped `geoid_undulation` column (res 5)",
                 color="w", fontsize=15, pad=12)
    fig.savefig(f"{out}/geoid.png", dpi=140, bbox_inches="tight", facecolor="#10131a")
    plt.close(fig); print("  geoid.png")


def hypsographic(D, out):
    d = load(f"{D}/res5/part-000.parquet", ["elevation_mean"])
    e = np.sort(d["elevation_mean"])[::-1]                 # high -> low
    area = np.linspace(0, 100, e.size)                     # cumulative % of land
    mean = d["elevation_mean"].mean()
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.fill_between(area, e, e.min(), color="#a7c163", alpha=0.5)
    ax.plot(area, e, color="#7a5230", lw=1.6)
    ax.axhline(mean, color="#b03030", lw=1.2, ls="--")
    ax.text(99, mean + 80, f"area-weighted mean ≈ {mean:.0f} m", ha="right", color="#b03030", fontsize=10)
    ax.set_xlabel("cumulative share of land area (%)"); ax.set_ylabel("elevation (m)")
    ax.set_xlim(0, 100); ax.set_ylim(e.min() - 50, e.max() * 1.02)
    ax.set_title("Hypsographic curve — land elevation distribution (res 5)", fontsize=13)
    ax.grid(alpha=0.25)
    fig.savefig(f"{out}/hypsographic.png", dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig); print("  hypsographic.png")


def hex_closeup(D, out, res=8):
    # Aspen / Pitkin County Airport (KASE) and the Elk Mountains
    KASE = (39.2232, -106.8687)
    S, N, W, E = 39.02, 39.42, -107.10, -106.55
    corners = [(S, W), (S, E), (N, W), (N, E), KASE]
    bases = sorted({(h3.str_to_int(h3.latlng_to_cell(la, lo, res)) >> 45) & 127 for la, lo in corners})
    files = [f"{D}/res{res}/base={b}/part-000.parquet" for b in bases
             if os.path.exists(f"{D}/res{res}/base={b}")]
    cells, elev = [], []
    for f in files:
        d = load(f, ["h3_cell", "elevation_mean"]); cells.append(d["h3_cell"]); elev.append(d["elevation_mean"])
    cells = np.concatenate(cells); elev = np.concatenate(elev)
    lat, lng = centroids(cells)
    m = (lat >= S) & (lat <= N) & (lng >= W) & (lng <= E)
    cells, elev = cells[m], elev[m]
    polys = [[(lo, la) for la, lo in h3.cell_to_boundary(h3.int_to_str(int(c)))] for c in cells]
    fig, ax = plt.subplots(figsize=(11, 8))
    edge = (1, 1, 1, 0.12) if res <= 8 else "none"          # show the hex grid at coarser res
    pc = PolyCollection(polys, array=elev, cmap=LAND, edgecolors=edge, linewidths=0.2)
    ax.add_collection(pc); ax.set_xlim(W, E); ax.set_ylim(S, N); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    ax.plot(*KASE[::-1], marker="*", ms=20, color="white", mec="black", mew=0.8, zorder=5)
    ax.annotate("KASE — Aspen airport", KASE[::-1], textcoords="offset points", xytext=(12, -4),
                color="white", fontsize=11, fontweight="bold",
                path_effects=[__import__("matplotlib.patheffects", fromlist=["withStroke"]).withStroke(linewidth=2, foreground="black")])
    cb = fig.colorbar(pc, ax=ax, shrink=0.75, pad=0.01); cb.set_label("mean elevation (m)")
    edge_m = elev.min(); peak = elev.max()
    ax.set_title(f"H3 close-up — Aspen (KASE) & the Elk Mountains, res {res} "
                 f"({len(cells):,} hexagons; valley {edge_m:.0f} m → peaks {peak:.0f} m)", fontsize=12)
    fig.savefig(f"{out}/aspen_closeup.png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig); print("  aspen_closeup.png")


KASE = (39.2232, -106.8687)
ASPEN = (39.02, 39.42, -107.10, -106.55)          # S, N, W, E


def _region(D, res):
    """Cells + elevation inside the Aspen box at a given resolution."""
    S, N, W, E = ASPEN
    single = f"{D}/res{res}/part-000.parquet"
    if os.path.exists(single):
        files = [single]
    else:
        corners = [(S, W), (S, E), (N, W), (N, E), KASE]
        bases = sorted({(h3.str_to_int(h3.latlng_to_cell(la, lo, res)) >> 45) & 127 for la, lo in corners})
        files = [f"{D}/res{res}/base={b}/part-000.parquet" for b in bases
                 if os.path.exists(f"{D}/res{res}/base={b}")]
    cells, elev = [], []
    for p in files:
        d = load(p, ["h3_cell", "elevation_mean"]); cells.append(d["h3_cell"]); elev.append(d["elevation_mean"])
    cells = np.concatenate(cells); elev = np.concatenate(elev)
    lat, lng = centroids(cells)
    m = (lat >= S) & (lat <= N) & (lng >= W) & (lng <= E)
    return cells[m], elev[m]


def resolution_gif(D, out, resolutions=(5, 6, 7, 8, 9), exag=8, azim=168, elev_angle=28):
    """Boomerang GIF sweeping H3 resolution over Aspen with a fixed camera."""
    import math
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from matplotlib.colors import Normalize
    from PIL import Image
    S, N, W, E = ASPEN
    lat0, lon0 = (S + N) / 2, (W + E) / 2
    mlat, mlon = 110540.0, 111320.0 * math.cos(math.radians(lat0))
    xr = [(W - lon0) * mlon, (E - lon0) * mlon]; yr = [(S - lat0) * mlat, (N - lat0) * mlat]

    regions = {r: _region(D, r) for r in resolutions}
    cmin = min(float(e.min()) for _, e in regions.values())
    cmax = max(float(e.max()) for _, e in regions.values())
    norm = Normalize(cmin, cmax); z0 = cmin - 40
    fdir = f"{out}/frames"; os.makedirs(fdir, exist_ok=True)
    paths = []
    for r in resolutions:
        cells, elev = regions[r]
        faces, fcolors = [], []
        for c, ev in zip(cells, elev):
            b = h3.cell_to_boundary(h3.int_to_str(int(c)))
            xy = [((lo - lon0) * mlon, (la - lat0) * mlat) for la, lo in b]
            top = LAND(norm(ev)); side = tuple(0.62 * v for v in top[:3]) + (1.0,)
            faces.append([(x, y, ev) for x, y in xy]); fcolors.append(top)
            for i in range(len(xy)):
                x1, y1 = xy[i]; x2, y2 = xy[(i + 1) % len(xy)]
                faces.append([(x1, y1, z0), (x2, y2, z0), (x2, y2, ev), (x1, y1, ev)]); fcolors.append(side)
        fig = plt.figure(figsize=(8, 6.5)); fig.set_facecolor("white")
        ax = fig.add_axes([0, 0, 1, 1], projection="3d")
        ax.add_collection3d(Poly3DCollection(faces, facecolors=fcolors, edgecolors="none", linewidths=0))
        ax.set_xlim(xr); ax.set_ylim(yr); ax.set_zlim(z0, cmax)
        ax.set_box_aspect((xr[1] - xr[0], yr[1] - yr[0], (cmax - z0) * exag))
        ax.view_init(elev=elev_angle, azim=azim); ax.set_axis_off()
        p = f"{fdir}/res{r}.png"; fig.savefig(p, dpi=100, facecolor="white"); plt.close(fig)
        paths.append(p); print(f"  frame res{r} ({len(cells):,} cells)", flush=True)

    from PIL import ImageChops, ImageDraw, ImageFont
    raw = {r: Image.open(f"{fdir}/res{r}.png").convert("RGB") for r in resolutions}
    ref = raw[resolutions[-1]]                                    # res 9 = reference for the crop
    rb = ImageChops.difference(ref, Image.new("RGB", ref.size, (255, 255, 255))).getbbox()
    W0, H0 = ref.size; pad = 22
    box = (max(0, rb[0] - pad), max(0, rb[1] - pad), min(W0, rb[2] + pad), min(H0, rb[3] + pad))
    font = ImageFont.truetype(os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf"), 22)
    def frame(r):                                                 # label painted AFTER crop (corner-locked)
        im = raw[r].crop(box).copy(); d = ImageDraw.Draw(im); t = f"res {r}"
        tb = d.textbbox((0, 0), t, font=font)
        d.text((im.width - (tb[2] - tb[0]) - 18, im.height - (tb[3] - tb[1]) - 16), t,
               fill=(154, 154, 154), font=font)
        return im
    order = list(resolutions) + list(resolutions[-2:0:-1])        # 4..9..5 -> seamless loop
    imgs = [frame(r) for r in order]
    dur = [1000 if r in (resolutions[0], resolutions[-1]) else 380 for r in order]   # hold the extremes
    imgs[0].save(f"{out}/aspen_resolution.gif", save_all=True, append_images=imgs[1:],
                 duration=dur, loop=0, optimize=True)
    print(f"  wrote aspen_resolution.gif ({len(order)} frames, {box[2]-box[0]}x{box[3]-box[1]}px, "
          f"{os.path.getsize(out + '/aspen_resolution.gif')/1e6:.1f} MB)")


def aspen_3d(D, out, res=8, exag=3.2, azim=-62, elev_angle=30):
    """Oblique 3D render — each H3 cell placed at its elevation, camera tilted."""
    import math
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    KASE = (39.2232, -106.8687)
    S, N, W, E = 39.02, 39.42, -107.10, -106.55
    single = f"{D}/res{res}/part-000.parquet"
    if os.path.exists(single):                                  # res 2-7: one file
        files = [single]
    else:                                                       # res 8-9: base shards
        corners = [(S, W), (S, E), (N, W), (N, E), KASE]
        bases = sorted({(h3.str_to_int(h3.latlng_to_cell(la, lo, res)) >> 45) & 127 for la, lo in corners})
        files = [f"{D}/res{res}/base={b}/part-000.parquet" for b in bases
                 if os.path.exists(f"{D}/res{res}/base={b}")]
    cells, elev = [], []
    for p in files:
        d = load(p, ["h3_cell", "elevation_mean"]); cells.append(d["h3_cell"]); elev.append(d["elevation_mean"])
    cells = np.concatenate(cells); elev = np.concatenate(elev)
    lat, lng = centroids(cells)
    m = (lat >= S) & (lat <= N) & (lng >= W) & (lng <= E)
    cells, elev = cells[m], elev[m]

    lat0, lon0 = (S + N) / 2, (W + E) / 2
    mlat, mlon = 110540.0, 111320.0 * math.cos(math.radians(lat0))     # deg -> metres (local)
    polys = []
    for c, ev in zip(cells, elev):
        b = h3.cell_to_boundary(h3.int_to_str(int(c)))
        polys.append([((lo - lon0) * mlon, (la - lat0) * mlat, ev) for la, lo in b])

    # extrude each cell into a solid hexagonal column (floor -> elevation)
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    norm = Normalize(float(elev.min()), float(elev.max())); sm = ScalarMappable(norm, LAND)
    z0 = float(elev.min()) - 40.0
    faces, fcolors = [], []
    for poly, ev in zip(polys, elev):
        xy = [(x, y) for x, y, _ in poly]
        top_rgba = LAND(norm(ev)); side_rgba = tuple(0.62 * v for v in top_rgba[:3]) + (1.0,)
        faces.append([(x, y, ev) for x, y in xy]); fcolors.append(top_rgba)          # top
        for i in range(len(xy)):                                                      # walls
            x1, y1 = xy[i]; x2, y2 = xy[(i + 1) % len(xy)]
            faces.append([(x1, y1, z0), (x2, y2, z0), (x2, y2, ev), (x1, y1, ev)])
            fcolors.append(side_rgba)

    fig = plt.figure(figsize=(13, 9)); fig.set_facecolor("white")
    ax = fig.add_subplot(111, projection="3d")
    pc = Poly3DCollection(faces, facecolors=fcolors, edgecolors="none", linewidths=0)
    ax.add_collection3d(pc)
    xr = [(W - lon0) * mlon, (E - lon0) * mlon]; yr = [(S - lat0) * mlat, (N - lat0) * mlat]
    ax.set_xlim(xr); ax.set_ylim(yr); ax.set_zlim(z0, elev.max())
    ax.set_box_aspect((xr[1] - xr[0], yr[1] - yr[0], (elev.max() - z0) * exag))
    ax.view_init(elev=elev_angle, azim=azim)

    # airport star at its cell's elevation
    kcell = h3.latlng_to_cell(*KASE, res); ki = np.where(cells == h3.str_to_int(kcell))[0]
    kz = float(elev[ki[0]]) if len(ki) else float(np.percentile(elev, 5))
    ax.scatter([(KASE[1] - lon0) * mlon], [(KASE[0] - lat0) * mlat], [kz + 120],
               marker="*", s=240, color="white", edgecolor="black", linewidths=0.8, zorder=20)
    ax.set_axis_off()
    cb = fig.colorbar(sm, ax=ax, shrink=0.5, pad=0.0); cb.set_label("mean elevation (m)")
    ax.set_title(f"Aspen (KASE) in 3D — res {res} H3 cells extruded by elevation "
                 f"(valley {elev.min():.0f} m → peaks {elev.max():.0f} m, {exag}× vertical)",
                 fontsize=12, y=0.92)
    fig.savefig(f"{out}/aspen_3d_res{res}.png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig); print(f"  aspen_3d_res{res}.png ({len(cells):,} cells)")


def _children_elev(D, res, parents):
    """Cells = all res-`res` descendants of the res-4 `parents`, with their elevation."""
    if res == 4:
        cells = np.array([h3.str_to_int(c) for c in parents], dtype="uint64")
    else:
        cells = np.array([h3.str_to_int(c) for p in parents for c in h3.cell_to_children(p, res)],
                         dtype="uint64")
    single = f"{D}/res{res}/part-000.parquet"
    if os.path.exists(single):
        groups = {0: cells}; path = lambda b: single
    else:
        base = (cells >> 45) & 127
        groups = {int(b): cells[base == b] for b in np.unique(base)}
        path = lambda b: f"{D}/res{res}/base={b}/part-000.parquet"
    oc, oe = [], []
    for b, tc in groups.items():
        t = pq.ParquetFile(path(b)).read(columns=["h3_cell", "elevation_mean"])
        sc = t.column("h3_cell").to_numpy(); se = t.column("elevation_mean").to_numpy()
        o = np.argsort(sc); sc, se = sc[o], se[o]
        idx = np.clip(np.searchsorted(sc, tc), 0, len(sc) - 1)
        ok = sc[idx] == tc
        oc.append(tc[ok]); oe.append(se[idx[ok]])
    return np.concatenate(oc), np.concatenate(oe)


def subdivision_gif(D, out, ring=1, resolutions=(4, 5, 6, 7, 8), exag=8, azim=168, elev_angle=28):
    """Boomerang GIF subdividing a cluster of res-4 hexes down the H3 hierarchy (true hex footprint)."""
    import math
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from matplotlib.colors import Normalize
    from PIL import Image, ImageChops, ImageDraw, ImageFont
    parents = h3.grid_disk(h3.latlng_to_cell(*KASE, 4), ring)
    regions = {r: _children_elev(D, r, parents) for r in resolutions}
    cmin = min(float(e.min()) for _, e in regions.values())
    cmax = max(float(e.max()) for _, e in regions.values())
    fcells, _ = regions[resolutions[-1]]
    flat, flng = centroids(fcells)
    lat0, lon0 = (flat.min() + flat.max()) / 2, (flng.min() + flng.max()) / 2
    mlat, mlon = 110540.0, 111320.0 * math.cos(math.radians(lat0))
    xs = (flng - lon0) * mlon; ys = (flat - lat0) * mlat
    xr = [xs.min() * 1.02, xs.max() * 1.02]; yr = [ys.min() * 1.02, ys.max() * 1.02]
    norm = Normalize(cmin, cmax); z0 = cmin - 60
    fdir = f"{out}/frames_sub"; os.makedirs(fdir, exist_ok=True)
    for r in resolutions:
        cells, elev = regions[r]
        faces, fcolors = [], []
        for c, ev in zip(cells, elev):
            b = h3.cell_to_boundary(h3.int_to_str(int(c)))
            xy = [((lo - lon0) * mlon, (la - lat0) * mlat) for la, lo in b]
            top = LAND(norm(ev)); side = tuple(0.62 * v for v in top[:3]) + (1.0,)
            faces.append([(x, y, ev) for x, y in xy]); fcolors.append(top)
            for i in range(len(xy)):
                x1, y1 = xy[i]; x2, y2 = xy[(i + 1) % len(xy)]
                faces.append([(x1, y1, z0), (x2, y2, z0), (x2, y2, ev), (x1, y1, ev)]); fcolors.append(side)
        fig = plt.figure(figsize=(8, 6.5)); ax = fig.add_axes([0, 0, 1, 1], projection="3d")
        ax.add_collection3d(Poly3DCollection(faces, facecolors=fcolors, edgecolors="none", linewidths=0))
        ax.set_xlim(xr); ax.set_ylim(yr); ax.set_zlim(z0, cmax)
        ax.set_box_aspect((xr[1] - xr[0], yr[1] - yr[0], (cmax - z0) * exag))
        ax.view_init(elev=elev_angle, azim=azim); ax.set_axis_off()
        fig.savefig(f"{fdir}/res{r}.png", dpi=100, facecolor="white"); plt.close(fig)
        print(f"  frame res{r} ({len(cells):,} cells)", flush=True)
    raw = {r: Image.open(f"{fdir}/res{r}.png").convert("RGB") for r in resolutions}
    ref = raw[resolutions[-1]]
    rb = ImageChops.difference(ref, Image.new("RGB", ref.size, (255, 255, 255))).getbbox()
    W0, H0 = ref.size; pad = 22
    box = (max(0, rb[0] - pad), max(0, rb[1] - pad), min(W0, rb[2] + pad), min(H0, rb[3] + pad))
    font = ImageFont.truetype(os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf"), 22)
    def frame(r):
        im = raw[r].crop(box).copy(); d = ImageDraw.Draw(im); t = f"res {r}"
        tb = d.textbbox((0, 0), t, font=font)
        d.text((im.width - (tb[2] - tb[0]) - 18, im.height - (tb[3] - tb[1]) - 16), t,
               fill=(154, 154, 154), font=font)
        return im
    order = list(resolutions) + list(resolutions[-2:0:-1])
    imgs = [frame(r) for r in order]
    dur = [1000 if r in (resolutions[0], resolutions[-1]) else 420 for r in order]
    imgs[0].save(f"{out}/aspen_subdivision.gif", save_all=True, append_images=imgs[1:],
                 duration=dur, loop=0, optimize=True)
    print(f"  wrote aspen_subdivision.gif ({len(order)} frames, {box[2]-box[0]}x{box[3]-box[1]}px, "
          f"{os.path.getsize(out + '/aspen_subdivision.gif')/1e6:.1f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="h3-terrain")
    ap.add_argument("--out", default="docs/visuals")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    global_elevation(a.dir, a.out)
    geoid_map(a.dir, a.out)
    hypsographic(a.dir, a.out)
    hex_closeup(a.dir, a.out)
    print("done.")


if __name__ == "__main__":
    main()
