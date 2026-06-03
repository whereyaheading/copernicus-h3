"""3D 'geoid potato' — the EGM2008 field rendered as radius deviation from a sphere.

The geoid undulation N (the dataset's `geoid_undulation` column) is the height of the geoid
above the reference ellipsoid, ±~100 m. Here it's drawn as deviation from a sphere, hugely
exaggerated, with a ghost wireframe sphere as the 'perfect' reference so you can see where the
geoid bulges out (red) and dips in (blue).

  python docs/make_geoid_3d.py --exag 8000 --azim -55 --elev 16
"""
import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.colors import Normalize

sys.path.insert(0, "pipeline")
import geoid as G

R = 6371000.0


def make(out, exag=8000, azim=-55, elev=16, light=(0.55, 0.3, 0.78), nlat=160, nlon=320, ghost=True):
    lat = np.linspace(-89.5, 89.5, nlat)
    lon = np.linspace(-180, 180, nlon)
    LON, LAT = np.meshgrid(lon, lat)
    N = G.undulation_for_latlon(LAT.ravel(), LON.ravel()).reshape(LAT.shape).astype(float)
    r = 1.0 + (N * exag) / R
    la, lo = np.radians(LAT), np.radians(LON)
    X = r * np.cos(la) * np.cos(lo); Y = r * np.cos(la) * np.sin(lo); Z = r * np.sin(la)

    norm = Normalize(N.min(), N.max())
    base = cm.Spectral_r(norm(N))
    L = np.array(light, float); L /= np.linalg.norm(L)
    nx, ny, nz = X / r, Y / r, Z / r                       # ~radial normals for spherical shading
    illum = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
    rgb = base.copy(); rgb[..., :3] = np.clip(rgb[..., :3] * (0.5 + 0.6 * illum)[..., None], 0, 1)

    fig = plt.figure(figsize=(9, 9)); fig.set_facecolor("white")
    ax = fig.add_subplot(111, projection="3d")
    if ghost:                                              # subtle reference rings (equator + meridians)
        t = np.linspace(0, 2 * np.pi, 120)
        for lon0 in np.radians(np.arange(0, 180, 30)):     # meridian great circles
            ax.plot(np.cos(t) * np.cos(lon0), np.cos(t) * np.sin(lon0), np.sin(t),
                    color="#9aa3ad", lw=0.4, alpha=0.3)
        for lat0 in np.radians([-60, -30, 0, 30, 60]):     # parallels
            ax.plot(np.cos(lat0) * np.cos(t), np.cos(lat0) * np.sin(t),
                    np.full_like(t, np.sin(lat0)), color="#9aa3ad", lw=0.4, alpha=0.3)
    ax.plot_surface(X, Y, Z, facecolors=rgb, rstride=1, cstride=1, linewidth=0,
                    antialiased=False, shade=False)
    ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=elev, azim=azim); ax.set_axis_off()
    lim = 1.13; ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_zlim(-lim, lim)
    sm = cm.ScalarMappable(norm, cm.Spectral_r)
    cb = fig.colorbar(sm, ax=ax, shrink=0.5, pad=-0.02)
    cb.set_label("geoid undulation N (m) — geoid height above the ellipsoid")
    fig.text(0.5, 0.88, f"EGM2008 geoid — deviation from a sphere, exaggerated {exag:,}×",
             ha="center", fontsize=13, color="#333")
    p = f"{out}/geoid_3d.png"
    fig.savefig(p, dpi=150, bbox_inches="tight", facecolor="white"); plt.close(fig)
    print(f"wrote {p}  (N range {N.min():.0f}..{N.max():.0f} m)")


def spin(out, n=16, exag=13000, elev=18, light=(0.62, 0.0, 0.85), nlat=120, nlon=240, dur=90):
    """Seamless-loop GIF of the geoid potato spinning 360° (no title, tight crop)."""
    from PIL import Image, ImageChops
    lat = np.linspace(-89.5, 89.5, nlat); lon = np.linspace(-180, 180, nlon)
    LON, LAT = np.meshgrid(lon, lat)
    N = G.undulation_for_latlon(LAT.ravel(), LON.ravel()).reshape(LAT.shape).astype(float)
    r = 1.0 + (N * exag) / R
    la, lo = np.radians(LAT), np.radians(LON)
    X = r * np.cos(la) * np.cos(lo); Y = r * np.cos(la) * np.sin(lo); Z = r * np.sin(la)
    base = cm.Spectral_r(Normalize(N.min(), N.max())(N))
    nx, ny, nz = X / r, Y / r, Z / r
    L0 = np.array(light, float); L0 /= np.linalg.norm(L0)
    fdir = f"{out}/geoid_frames"; os.makedirs(fdir, exist_ok=True)
    paths = []
    for k in range(n):
        th = 2 * np.pi * k / n                              # camera + light co-rotate -> globe spins, lighting screen-fixed
        Lx = L0[0] * np.cos(th) - L0[1] * np.sin(th); Ly = L0[0] * np.sin(th) + L0[1] * np.cos(th)
        illum = np.clip(nx * Lx + ny * Ly + nz * L0[2], 0, 1)
        rgb = base.copy(); rgb[..., :3] = np.clip(rgb[..., :3] * (0.5 + 0.6 * illum)[..., None], 0, 1)
        fig = plt.figure(figsize=(6, 6)); ax = fig.add_axes([0, 0, 1, 1], projection="3d")
        ax.plot_surface(X, Y, Z, facecolors=rgb, rstride=1, cstride=1, linewidth=0,
                        antialiased=False, shade=False)
        lim = 1.12; ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_zlim(-lim, lim)
        ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=elev, azim=np.degrees(th)); ax.set_axis_off()
        p = f"{fdir}/f{k:02d}.png"; fig.savefig(p, dpi=110, facecolor="white"); plt.close(fig)
        paths.append(p); print(f"  frame {k+1}/{n}", flush=True)
    raw = [Image.open(p).convert("RGB") for p in paths]
    cb = lambda im: ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    bs = [cb(im) for im in raw]; W0, H0 = raw[0].size; pad = 6
    box = (max(0, min(b[0] for b in bs) - pad), max(0, min(b[1] for b in bs) - pad),
           min(W0, max(b[2] for b in bs) + pad), min(H0, max(b[3] for b in bs) + pad))
    imgs = [im.crop(box) for im in raw]
    imgs[0].save(f"{out}/geoid_spin.gif", save_all=True, append_images=imgs[1:], duration=dur, loop=0, optimize=True)
    print(f"  wrote geoid_spin.gif ({n} frames, {box[2]-box[0]}x{box[3]-box[1]}px, "
          f"{os.path.getsize(out + '/geoid_spin.gif')/1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/visuals")
    ap.add_argument("--exag", type=int, default=8000)
    ap.add_argument("--azim", type=float, default=-55)
    ap.add_argument("--elev", type=float, default=16)
    ap.add_argument("--no-ghost", action="store_true")
    ap.add_argument("--spin", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.spin:
        spin(a.out, exag=a.exag if a.exag != 8000 else 13000, elev=a.elev)
    else:
        make(a.out, exag=a.exag, azim=a.azim, elev=a.elev, ghost=not a.no_ghost)
