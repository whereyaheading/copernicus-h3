# Data License — Copernicus DEM Derivative

This dataset (the H3-indexed Parquet files under `h3-terrain/`) is a **derivative work of the Copernicus DEM GLO-30**. The **code** in this repository is MIT-licensed (see the README); **this file governs the data.**

## Mandatory attribution

If you use, redistribute, or build on this dataset you **must** carry the following attribution, and the same obligation passes to your downstream users:

> Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

## What the Copernicus DEM licence permits (plain-language summary)

The Copernicus DEM is **free and open data**. Under its licence you may reproduce, distribute, adapt, and use it — including commercially — **provided the attribution above is preserved**. This redistribution and the derivation performed here (per-cell aggregate statistics; no change to the underlying elevation values beyond aggregation) are made under those terms.

> This paragraph is a convenience summary, **not** the licence itself. The authoritative terms are the Copernicus DEM licence / EULA linked below — consult it for the binding conditions and attach its full text if you redistribute.

## Authoritative terms & sources

- Copernicus DEM on the AWS Open Data Registry: https://registry.opendata.aws/copernicus-dem/
- Copernicus DEM product page & licence (ESA / Airbus EULA): https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model

## Disclaimer

The data is provided **"as is", without warranty of any kind**, express or implied, including but not limited to accuracy, completeness, or fitness for a particular purpose. It is a **surface model (DSM)** and carries the caveats documented in the [README](README.md#coverage--caveats) (canopy/buildings included, sharp peaks under-sampled, water flat-lined, polar artifacts, land-only/sparse). Neither the original data providers nor the producer of this derivative is liable for any use of it. **Do not use this dataset as the sole source for safety-of-life navigation or obstacle clearance.**

By downloading the data you accept the Copernicus DEM licence terms and this disclaimer.
