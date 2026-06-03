# Validation Report

Generated `2026-06-02 21:49 UTC` by `validate.py` against `h3-terrain/`. **23/23 checks passed.**

| Section | Check | Result | |
|---|---|---|---|
| Tier1 invariants | res2 | 2,956 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Tier1 invariants | res3 | 17,297 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Tier1 invariants | res4 | 109,738 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Tier1 invariants | res5 | 736,665 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Tier1 invariants | res6 | 5,072,613 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Tier1 invariants | res7 | 35,285,394 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Tier1 invariants | res8 | 246,405,861 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Tier1 invariants | res9 | 1,723,282,383 rows; violations order=0 nonneg=0 nan=0 range=0 geoid=0; elev -428..8738 m | ✅ |
| Conservation | Σpixel_count across resolutions | res2=225,865,152,000 … res9=225,865,152,000 (max spread 0.000%) | ✅ |
| Landmark elevation | LAX | elevation_mean=38.0 m (exp 38±25) | ✅ |
| Landmark elevation | Death Valley | elevation_mean=-85.6 m (exp -86±15) | ✅ |
| Landmark elevation | Four Corners | elevation_mean=1477.2 m (exp 1477±25) | ✅ |
| Landmark elevation | Everest | elevation_max=8737.8 m (exp 8738±60) | ✅ |
| Geoid | KTEB | N=-32.8 m (exp -32.8) | ✅ |
| Geoid | LAX | N=-36.0 m (exp -36.0) | ✅ |
| Geoid | KDEN | N=-18.2 m (exp -18.2) | ✅ |
| Geoid | London | N=+46.5 m (exp +46.5) | ✅ |
| Geoid | Tokyo | N=+36.7 m (exp +36.7) | ✅ |
| AGL | KTEB on-ground fix | -29 - 1 - (-33) = 2.4 m (≈0 expected) | ✅ |
| Partition integrity | res9 (12 base shards sampled) | 0 cells in the wrong base-cell shard | ✅ |
| Hypsographic | mean land elevation | 612 m (expect ~600-1000); <200m=44%, >1000m=20% | ✅ |
| Performance | point lookup (res9, 1 partition) | 22 ms | ✅ |
| Performance | full res9 scan (count) | 1,723,282,383 rows in 0.1s | ✅ |

See [VALIDATION.md](VALIDATION.md) for what each check verifies and its pass criterion.
