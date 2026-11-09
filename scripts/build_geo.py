"""One-time setup: simplify downloaded state boundaries into data/geo/nigeria_admin1.geojson.

The download itself is a manual setup step (the only network use besides pip and model
weights), recorded in docs/ASSUMPTIONS.md:
  curl -sL -o <dir>/nga_adm1_simplified.geojson \
    https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/NGA/ADM1/geoBoundaries-NGA-ADM1_simplified.geojson
Source: geoBoundaries (wmgeolab), data from GRID3, CC BY 4.0.

Usage: python -I scripts/build_geo.py <downloaded.geojson> [--tolerance 0.01]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/geo/nigeria_admin1.geojson"
ATTRIBUTION = ("State boundaries: geoBoundaries ADM1 for Nigeria (gbOpen, build Dec 2023, data from GRID3), "
               "CC BY 4.0, https://www.geoboundaries.org. Simplified for this project.")


def _round(coords, ndigits: int):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], ndigits), round(coords[1], ndigits)]
    return [_round(c, ndigits) for c in coords]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.01, help="degrees")
    args = parser.parse_args()
    states = json.loads((ROOT / "config/states.json").read_text())["states"]
    by_map_name = {s["map_name"]: s for s in states}
    src = json.loads(args.source.read_text())

    features, seen = [], set()
    for feat in src["features"]:
        name = feat["properties"]["shapeName"]
        state = by_map_name.get(name)
        if state is None:
            sys.exit(f"boundary name {name!r} not in config/states.json")
        geom = shape(feat["geometry"]).simplify(args.tolerance, preserve_topology=True)
        gj = mapping(geom)
        features.append({"type": "Feature",
                         "properties": {"number": state["number"], "name": state["name"],
                                        "map_name": name, "zone": state["zone"]},
                         "geometry": {"type": gj["type"], "coordinates": _round(gj["coordinates"], 3)}})
        seen.add(state["number"])
    missing = sorted({s["number"] for s in states} - seen)
    if missing or len(features) != 37:
        sys.exit(f"expected 37 regions; missing state numbers {missing}")
    features.sort(key=lambda f: f["properties"]["number"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "attribution": ATTRIBUTION,
                               "features": features}, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(features)} regions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
