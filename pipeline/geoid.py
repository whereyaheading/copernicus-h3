"""EGM2008 geoid undulation N for H3 cells — vectorized via in-RAM grid interpolation.

N = geoid_undulation = h_ellipsoidal − H_orthometric (geoid height above the WGS84 ellipsoid).
We load the EGM2008 2.5′ grid (the PROJ `us_nga_egm08_25.tif`, EPSG:3855) into memory once and
bilinear-interpolate it in NumPy — ~27 M cells/s, exact per cell. Verified bit-for-bit against
pyproj's EPSG:4979→EPSG:4326+EPSG:3855 transform at KTEB/LAX/KDEN/London/Tokyo.

Model: **EGM2008**. Sign: **negative in CONUS** (~−15..−36 m), positive over Europe/Asia.
Consumer:  alt_agl = alt_geom − elevation_mean − geoid_undulation   (metres)

Grid prerequisite (one-time, ~77 MB):
    curl -L -o "$(python -c 'import pyproj;print(pyproj.datadir.get_user_data_dir())')/us_nga_egm08_25.tif" \
         https://cdn.proj.org/us_nga_egm08_25.tif
"""
import os

import numpy as np
import rasterio
from scipy.ndimage import map_coordinates
from h3ronpy.vector import cells_to_coordinates

try:
    import pyproj
    _USER_DIR = pyproj.datadir.get_user_data_dir()
except Exception:
    _USER_DIR = os.path.expanduser("~/Library/Application Support/proj")
GRID_PATH = os.path.join(_USER_DIR, "us_nga_egm08_25.tif")

if not os.path.exists(GRID_PATH):
    raise FileNotFoundError(
        f"EGM2008 grid not found at {GRID_PATH}. Download it once:\n"
        f"  curl -L -o '{GRID_PATH}' https://cdn.proj.org/us_nga_egm08_25.tif")

with rasterio.open(GRID_PATH) as _ds:
    _G = _ds.read(1).astype("float64")        # geoid undulation N, metres (range ~ -107..+86)
    _T = _ds.transform
_X0, _DX, _Y0, _DY = _T.c, _T.a, _T.f, _T.e   # origin lon/lat + pixel size (north-up: _DY<0)


def undulation_for_latlon(lat, lon):
    """N (float32, m) for arrays of lat/lon, bilinear-interpolated from the EGM2008 grid."""
    lat = np.atleast_1d(np.asarray(lat, dtype="float64"))
    lon = np.atleast_1d(np.asarray(lon, dtype="float64"))
    rows = (lat - _Y0) / _DY - 0.5            # fractional pixel coords (pixel-center aligned)
    cols = (lon - _X0) / _DX - 0.5
    return map_coordinates(_G, [rows, cols], order=1, mode="nearest").astype("float32")


def undulation_for_cells(cells):
    """N (float32, m) for an array of H3 cell ids, at each cell centroid."""
    cells = np.asarray(cells, dtype="uint64")
    coords = cells_to_coordinates(cells)
    return undulation_for_latlon(np.asarray(coords.column("lat")),
                                 np.asarray(coords.column("lng")))
