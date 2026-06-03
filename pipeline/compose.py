"""Composable per-cell statistics — the heart of the build.

Each source pixel is assigned to the H3 cell containing its centroid, then folded into
**composable** aggregates: count, sum, sum-of-squares, min, max. These compose exactly across
any partition of the pixels — per tile, per worker, per latitude band — so a cell's statistics
can be built up incrementally and reduced at the end with no loss:

    merge([partial(A), partial(B)])  ==  partial(A ∪ B)

(bit-identical on count/min/max; floating-point-identical on sum/sumsq up to non-associativity).
This is what makes the streaming sweep and the boundary merge correct. The published columns are
reconstructed from these: mean = sum/count, std = sqrt(sumsq/count − mean²), plus min/max/count.

Used by the production sweep (`run.py`) and the prototypes that proved the model
(`prototypes/band_dev.py`).
"""
from __future__ import annotations

import numpy as np
from h3ronpy.vector import coordinates_to_cells, cells_bounds_arrays


def partial(lat, lon, elev, res):
    """Composable per-group stats for a set of pixels (one tile or a concatenated union)."""
    cells = np.asarray(coordinates_to_cells(lat, lon, res)).astype(np.uint64)
    uniq, inv = np.unique(cells, return_inverse=True)
    cmin = np.full(uniq.size, np.inf); np.minimum.at(cmin, inv, elev)
    cmax = np.full(uniq.size, -np.inf); np.maximum.at(cmax, inv, elev)
    return {"cell": uniq,
            "count": np.bincount(inv).astype(np.int64),
            "sum": np.bincount(inv, weights=elev),
            "sumsq": np.bincount(inv, weights=elev * elev),
            "min": cmin, "max": cmax}


def merge(parts):
    """Compose partials by GROUP BY cell — the cross-tile / cross-worker / cross-band reduction."""
    cat = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    uniq, inv = np.unique(cat["cell"], return_inverse=True)
    cmin = np.full(uniq.size, np.inf); np.minimum.at(cmin, inv, cat["min"])
    cmax = np.full(uniq.size, -np.inf); np.maximum.at(cmax, inv, cat["max"])
    return {"cell": uniq,
            "count": np.bincount(inv, weights=cat["count"]).astype(np.int64),
            "sum": np.bincount(inv, weights=cat["sum"]),
            "sumsq": np.bincount(inv, weights=cat["sumsq"]),
            "min": cmin, "max": cmax}


def min_latitude(cells):
    """Southernmost extent (deg) of each cell — the finalize/flush trigger for the N→S sweep."""
    b = cells_bounds_arrays(cells)
    return np.asarray(b.column("miny"))


def assert_exact(a, b, label):
    """Assert two composed results are identical (count/min/max exact; sum/sumsq within fp)."""
    assert np.array_equal(a["cell"], b["cell"]), f"{label}: cell sets differ"
    assert np.array_equal(a["count"], b["count"]), f"{label}: counts differ"
    assert np.array_equal(a["min"], b["min"]), f"{label}: mins differ"
    assert np.array_equal(a["max"], b["max"]), f"{label}: maxes differ"
    assert np.allclose(a["sum"], b["sum"], rtol=0, atol=1e-3), f"{label}: sums differ"
