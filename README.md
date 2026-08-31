# Vermont lake bathymetry contours

A once-a-year GitHub Action that turns Vermont's raw lake-depth soundings into clean
depth-contour lines, published here as a single GeoJSON.

## Why this exists

The [Scout](https://scout.cryptobyte.dev) app draws lake depth ("bathymetry") as
contour lines. New Hampshire publishes its contours as a live map service the app can
query directly. Vermont does not: the state's
[Bathymetric Data](https://geodata.vermont.gov/documents/VTANR::bathymetric-data-1)
is only a static CSV of ~2.4 million raw depth soundings (`Longitude, Latitude,
DepthInFeet, LakeName`) for ~60 lakes. This repo does the conversion Vermont didn't,
on a schedule, so the app has an NH-style contour layer to load.

## What the Action does

`build_bathymetry.py` (run by [.github/workflows/build.yml](.github/workflows/build.yml)):

1. Downloads the VT ANR Biobase soundings ZIP.
2. For each lake, interpolates the scattered points to a grid (`gdal_grid`, Delaunay
   linear) and extracts contour lines at depth-scaled intervals (`gdal_contour`):
   every 5 ft near the surface, widening to 10-20 ft in mid water and 50 ft in the
   deeps. A flat 5 ft interval made the deepest lakes so dense the app's map renderer
   dropped them on device, so the interval scales with depth to keep every lake within
   a tile budget a phone can draw.
3. Merges every lake into one GeoJSON of `LineString` features, each tagged with
   `lake` (name) and `depth` (whole feet, positive), then simplifies the lines.
4. Commits the result, `vt-lake-bathymetry.geojson`, back to this repo if it changed.

Output feature shape (matches how the app already reads NH bathymetry):

```json
{ "type": "Feature",
  "properties": { "lake": "Maidstone", "depth": 30 },
  "geometry": { "type": "LineString", "coordinates": [[-71.64, 44.66], ...] } }
```

## Schedule

Yearly (cron `0 6 1 4 *`, April 1) plus a manual **Run workflow** button. The source
data changes rarely, so yearly is plenty; run it by hand whenever Vermont updates.

## Consuming it

The committed file is served raw at:

```
https://raw.githubusercontent.com/Cryptobyte/vt-lake-bathymetry/main/vt-lake-bathymetry.geojson
```

It is a plain static GeoJSON (no bbox querying), so a client fetches the whole file
once. It is not for navigation.

## Tuning

In `build_bathymetry.py`: `LEVELS_FT` (the depth-scaled contour levels), `GRID`
(interpolation resolution), `SIMPLIFY_DEG` (line smoothing / file size), `MIN_POINTS`
(skip tiny lakes), `MIN_LEN_M` (drop short interpolation-noise slivers).

## Data source and license

Depth soundings: Vermont Agency of Natural Resources / VT DEC (Biobase lake surveys),
via the [Vermont Open Geodata Portal](https://geodata.vermont.gov). Not for
navigation. Check the source terms before redistribution.
