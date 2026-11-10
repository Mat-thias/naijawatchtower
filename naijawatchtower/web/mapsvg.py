"""Server-rendered national map (CLAUDE.md 9.1). No tiles, no JS map library, works offline.

- Equirectangular projection with a cosine-latitude correction centred on Nigeria.
- Layers colour states by coverage, verification, discrepancies or accreditation only;
  never by party (rule 12).
- Colour-blind-safe sequential scale (light to dark blue-green); "no data" is a grey
  diagonal hatch. Every region is a focusable link with an aria-label carrying the value
  in words, and a DEMO watermark crosses the map (rule 11).
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from html import escape
from typing import Any

from naijawatchtower.config import ROOT

GEO = ROOT / "data" / "geo" / "nigeria_admin1.geojson"
WIDTH = 760
PAD = 10
# 6-step sequential scale, colour-blind safe (light yellow-green to dark blue), checked for contrast steps.
SCALE = ["#edf8b1", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8"]
DARK_TEXT_STEPS = 3          # labels on the first three steps use dark text
NO_DATA = "url(#nodata)"
LAYER_TITLES = {"coverage": "Sheets digitised ÷ expected", "verified": "Share of digitised sheets verified or reviewed",
                "discrepancies": "Open discrepancies", "accreditation": "Accredited ÷ registered (unofficial)"}


@lru_cache(maxsize=1)
def _features() -> list[dict[str, Any]]:
    return json.loads(GEO.read_text())["features"]


@lru_cache(maxsize=1)
def _projection() -> tuple[float, float, float, float, float]:
    """(lon0, lat_max, kx, scale, height) for the equirectangular projection."""
    lons, lats = [], []
    for f in _features():
        for ring in _rings(f["geometry"]):
            for lon, lat in ring:
                lons.append(lon)
                lats.append(lat)
    lat0 = (min(lats) + max(lats)) / 2
    kx = math.cos(math.radians(lat0))
    w = (max(lons) - min(lons)) * kx
    h = max(lats) - min(lats)
    scale = (WIDTH - 2 * PAD) / w
    return min(lons), max(lats), kx, scale, h * scale + 2 * PAD


def _rings(geom: dict[str, Any]):
    if geom["type"] == "Polygon":
        yield from geom["coordinates"]
    else:
        for poly in geom["coordinates"]:
            yield from poly


def _path(geom: dict[str, Any]) -> str:
    lon0, lat_max, kx, scale, _ = _projection()
    parts = []
    for ring in _rings(geom):
        pts = [f"{PAD + (lon - lon0) * kx * scale:.1f},{PAD + (lat_max - lat) * scale:.1f}" for lon, lat in ring]
        parts.append("M" + "L".join(pts) + "Z")
    return "".join(parts)


def _centroid(geom: dict[str, Any]) -> tuple[float, float]:
    lon0, lat_max, kx, scale, _ = _projection()
    ring = max(_rings(geom), key=len)
    xs = [PAD + (lon - lon0) * kx * scale for lon, _ in ring]
    ys = [PAD + (lat_max - lat) * scale for _, lat in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def colour(value: float | None, layer: str, vmax: float) -> tuple[str, int | None]:
    """(fill, scale step) for a value; None means no data."""
    if value is None:
        return NO_DATA, None
    top = vmax if layer == "discrepancies" else 1.0
    frac = 0.0 if top <= 0 else min(1.0, max(0.0, value / top))
    step = min(len(SCALE) - 1, int(frac * len(SCALE)))
    return SCALE[step], step


def describe(rec: dict[str, Any], layer: str) -> str:
    """Plain-language value for aria-labels and the data table."""
    v = rec["value"]
    source = ("redacted real sheets" if rec["real_count"] else "simulated records" if rec["simulated_count"]
              else "no units")
    if v is None:
        return f"{rec['name']}: no data ({source})"
    if layer == "discrepancies":
        return f"{rec['name']}: {int(v)} open discrepancies ({source})"
    if layer == "accreditation":
        return f"{rec['name']}: {v:.0%} of registered voters accredited, unofficial ({source})"
    cov = rec["digitised"] / rec["expected"] if rec["expected"] else 0
    ver = (rec["verified"] + rec["reviewed"]) / rec["digitised"] if rec["digitised"] else 0
    return f"{rec['name']}: {cov:.0%} digitised, {ver:.0%} verified or reviewed ({source})"


def render_map(records: list[dict[str, Any]], layer: str, link_prefix: str = "/state/",
               watermark: str = "DEMO DATA", hx: bool = False, zone: str | None = None) -> str:
    """The national SVG for one layer. `records` come from services.state_records.

    With `hx`, clicking a state loads its panel in place (plain links still work without
    JavaScript). With `zone`, states outside that geopolitical zone are dimmed.
    """
    by_name = {r["map_name"]: r for r in records}
    vmax = max([r["value"] or 0 for r in records] + [1]) if layer == "discrepancies" else 1.0
    _, _, _, _, height = _projection()
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" role="img" '
           f'aria-labelledby="map-title" class="nga-map" data-layer="{layer}">',
           f'<title id="map-title">Map of Nigeria by state: {escape(LAYER_TITLES[layer])}. '
           f'{escape(watermark)}. Unofficial.</title>',
           '<defs><pattern id="nodata" width="8" height="8" patternUnits="userSpaceOnUse" '
           'patternTransform="rotate(45)"><rect width="8" height="8" fill="#e6e6e6"/>'
           '<line x1="0" y1="0" x2="0" y2="8" stroke="#9a9a9a" stroke-width="2"/></pattern></defs>',
           '<g class="regions">']
    for f in _features():
        rec = by_name[f["properties"]["map_name"]]
        fill, step = colour(rec["value"], layer, vmax)
        label = describe(rec, layer)
        hx_attrs = (f' hx-get="{link_prefix}{rec["code"]}/panel" hx-target="#state-panel" hx-swap="innerHTML"'
                    if hx else "")
        dim = ' opacity="0.25"' if zone and rec["zone"] != zone else ""
        out.append(f'<a href="{link_prefix}{rec["code"]}" aria-label="{escape(label)}" class="region" '
                   f'data-code="{rec["code"]}" data-step="{"none" if step is None else step}"{hx_attrs}>'
                   f'<path d="{_path(f["geometry"])}" fill="{fill}" stroke="#ffffff" stroke-width="0.8"{dim}>'
                   f'<title>{escape(label)}</title></path></a>')
    out.append("</g>")
    cx, cy = WIDTH / 2, height / 2
    out.append(f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle" dominant-baseline="middle" '
               f'transform="rotate(-24 {cx:.0f} {cy:.0f})" font-size="64" font-weight="700" '
               f'font-family="system-ui, sans-serif" fill="#000" fill-opacity="0.10" pointer-events="none" '
               f'class="watermark" aria-hidden="true">{escape(watermark)}</text>')
    out.append("</svg>")
    return "".join(out)


def legend(layer: str, records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Legend rows: colour, range text; plus the no-data swatch."""
    if layer == "discrepancies":
        vmax = max([r["value"] or 0 for r in records] + [1])
        bands = [(f"{vmax * i / len(SCALE):.0f}–{vmax * (i + 1) / len(SCALE):.0f}") for i in range(len(SCALE))]
    else:
        bands = [f"{100 * i // len(SCALE)}–{100 * (i + 1) // len(SCALE)}%" for i in range(len(SCALE))]
    return [{"colour": c, "label": b} for c, b in zip(SCALE, bands)] + [{"colour": "nodata", "label": "No data"}]
