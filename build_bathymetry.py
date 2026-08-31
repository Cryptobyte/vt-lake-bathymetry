#!/usr/bin/env python3
"""Build Vermont lake bathymetry contour lines from the VT ANR Biobase point cloud.

Downloads the Vermont Agency of Natural Resources "Bathymetric Data" export (a CSV of
lake-bottom depth soundings), interpolates each lake's points to a grid, extracts
depth contour lines, and writes one simplified GeoJSON of LineString features with
`depth` (whole feet, positive) and `lake` properties -- the same shape the Scout app
already uses for New Hampshire bathymetry.

Contours are drawn at depth-scaled intervals (dense near the surface, wider in deep
water). A flat 5 ft interval produced thousands of contours for the deepest lakes
(Willoughby alone hit ~600), which is far more geometry than a phone's map renderer
will keep in one tile: it silently drops the over-dense lakes on device even though a
desktop renderer draws them fine. Scaling the interval with depth keeps near-shore
detail while holding every lake to a tile budget the app can actually render.

Requires GDAL command-line tools (gdal_grid, gdal_contour, ogr2ogr) on PATH.
Set VT_LOCAL_CSV to a local CSV path to skip the download (for testing).
"""
import csv, json, math, os, subprocess, sys, tempfile, urllib.request, zipfile
from collections import defaultdict

SRC_URL = "https://anrmaps.vermont.gov/websites/OpenData/Items/BathymetricData/BiobaseLakeBathymetry_08122020.zip"
OUT = os.environ.get("VT_OUT", "vt-lake-bathymetry.geojson")
# Depth-scaled contour levels in feet: every 5 ft down to 30, every 10-20 ft through
# the mid water, every 50 ft in the deeps. gdal_contour only emits the levels a given
# lake actually reaches, so shallow lakes keep their 5 ft near-shore detail while a
# 300 ft lake gets ~15 contours instead of ~60. Positive values on purpose: the grid
# stores depth as a positive magnitude (see main), which keeps every gdal_contour
# argument non-negative and clear of GDAL's "negative number looks like a flag" bug.
LEVELS_FT = [5, 10, 15, 20, 25, 30,
             40, 50, 60, 80, 100,
             150, 200, 250, 300, 350]
GRID = 300               # interpolation grid cells per side (smoother than 500)
SIMPLIFY_DEG = 0.00006   # ~6 m line simplification tolerance
MIN_POINTS = 200         # skip lakes with too few soundings to contour meaningfully
MIN_LEN_M = 90           # drop contour slivers shorter than this (interpolation noise)


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def length_m(coords):
    """Rough length of a lon/lat line in metres."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        dx = (x2 - x1) * 111000 * math.cos(math.radians((y1 + y2) / 2))
        dy = (y2 - y1) * 111000
        total += math.hypot(dx, dy)
    return total


def source_csv(work):
    local = os.environ.get("VT_LOCAL_CSV")
    if local:
        return local
    zpath = os.path.join(work, "src.zip")
    print("Downloading", SRC_URL, flush=True)
    urllib.request.urlretrieve(SRC_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        z.extract(name, work)
    return os.path.join(work, name)


def main():
    work = tempfile.mkdtemp()
    csv_path = source_csv(work)

    lakes = defaultdict(list)
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            lake = (row["LakeName"] or "").strip()
            if lake:
                lakes[lake].append((row["Longitude"], row["Latitude"], row["DepthInFeet"]))
    print(f"{len(lakes)} lakes, {sum(len(v) for v in lakes.values())} points", flush=True)

    features = []
    for lake, pts in sorted(lakes.items()):
        if len(pts) < MIN_POINTS:
            print(f"  skip {lake} ({len(pts)} pts)", flush=True)
            continue
        pcsv = os.path.join(work, "pts.csv")
        with open(pcsv, "w") as g:
            g.write("X,Y,Z\n")
            for x, y, z in pts:
                # Store depth as a positive magnitude below the surface. The source
                # records depth as negative, but positive Z keeps the contour levels
                # (and every gdal argument) non-negative.
                try:
                    zf = abs(float(z))
                except (TypeError, ValueError):
                    continue
                g.write(f"{x},{y},{zf}\n")
        vrt = os.path.join(work, "pts.vrt")
        with open(vrt, "w") as g:
            g.write(
                '<OGRVRTDataSource><OGRVRTLayer name="pts">'
                f'<SrcDataSource relativeToVRT="0">{pcsv}</SrcDataSource>'
                "<GeometryType>wkbPoint25D</GeometryType><LayerSRS>EPSG:4326</LayerSRS>"
                '<GeometryField encoding="PointFromColumns" x="X" y="Y" z="Z"/>'
                "</OGRVRTLayer></OGRVRTDataSource>"
            )
        tif = os.path.join(work, "g.tif")
        cont = os.path.join(work, "c.geojson")
        for p in (tif, cont):
            if os.path.exists(p):
                os.remove(p)
        try:
            run(["gdal_grid", "-q", "-a", "linear:nodata=-9999", "-zfield", "Z",
                 "-outsize", str(GRID), str(GRID), "-a_srs", "EPSG:4326", "-of", "GTiff", vrt, tif])
            run(["gdal_contour", "-q", "-a", "depth", "-fl", *[str(l) for l in LEVELS_FT], tif, cont])
        except subprocess.CalledProcessError:
            print(f"  contour failed for {lake}", flush=True)
            continue
        n = 0
        for feat in json.load(open(cont))["features"]:
            g = feat.get("geometry")
            if not g or g.get("type") != "LineString":   # drop degenerate Point contours
                continue
            if length_m(g["coordinates"]) < MIN_LEN_M:    # drop interpolation-noise slivers
                continue
            depth = round(feat["properties"]["depth"])    # positive ft below surface
            if depth <= 0:
                continue
            feat["properties"] = {"lake": lake.title(), "depth": depth}
            features.append(feat)
            n += 1
        print(f"  {lake}: {len(pts)} pts -> {n} contours", flush=True)

    merged = os.path.join(work, "merged.geojson")
    json.dump({"type": "FeatureCollection", "features": features}, open(merged, "w"))
    if os.path.exists(OUT):
        os.remove(OUT)
    run(["ogr2ogr", "-q", "-f", "GeoJSON", "-simplify", str(SIMPLIFY_DEG),
         "-lco", "COORDINATE_PRECISION=5", "-lco", "RFC7946=YES", OUT, merged])
    lakes_out = len({f["properties"]["lake"] for f in features})
    print(f"Wrote {OUT}: {os.path.getsize(OUT)} bytes, {len(features)} contours across {lakes_out} lakes", flush=True)


if __name__ == "__main__":
    main()
