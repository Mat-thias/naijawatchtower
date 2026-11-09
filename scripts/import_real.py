"""Import manually downloaded real sheets from data_real/raw/ into data_real/images/.

- JPEG/PNG files are copied byte for byte.
- Single-image PDFs: the embedded image is extracted in its native format with
  `pdfimages -all` (no re-encoding), so nothing about the sheet is altered.
- Images whose shorter side is below --min-side pixels are skipped as too small to read.
- Possible duplicates (near-identical small grayscale hashes) are reported, not removed.
- Writes data_real/import_log.csv. Never views or uploads anything; runs offline.

Usage: python -I scripts/import_real.py [--min-side 600]
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RAW, IMAGES, LOG = ROOT / "data_real/raw", ROOT / "data_real/images", ROOT / "data_real/import_log.csv"


def ahash(path: Path, size: int = 16) -> np.ndarray:
    """Average hash of an image, orientation-normalised to portrait."""
    with Image.open(path) as img:
        g = img.convert("L")
        if g.width > g.height:
            g = g.rotate(90, expand=True)
        arr = np.asarray(g.resize((size, size)), dtype=float)
    return arr > arr.mean()


def extract_pdf(pdf: Path, workdir: Path) -> Path | None:
    """Extract the single embedded image of a one-page PDF; None if not exactly one image."""
    prefix = workdir / pdf.stem
    subprocess.run(["pdfimages", "-all", "-f", "1", "-l", "1", str(pdf), str(prefix)],
                   check=True, capture_output=True, timeout=60)
    found = sorted(workdir.glob(f"{pdf.stem}-*"))
    return found[0] if len(found) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-side", type=int, default=600)
    args = parser.parse_args()
    IMAGES.mkdir(parents=True, exist_ok=True)
    rows, imported = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for src in sorted(RAW.iterdir()):
            suffix = src.suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png"):
                candidate = src
            elif suffix == ".pdf":
                candidate = extract_pdf(src, Path(tmp))
                if candidate is None:
                    rows.append([src.name, "", "", "skipped: not exactly one embedded image"])
                    continue
            else:
                rows.append([src.name, "", "", "ignored: not an image or PDF"])
                continue
            with Image.open(candidate) as img:
                w, h = img.size
            if min(w, h) < args.min_side:
                rows.append([src.name, "", f"{w}x{h}", f"skipped: shorter side < {args.min_side}px"])
                continue
            dst = IMAGES / f"{src.stem}{candidate.suffix.lower()}"
            shutil.copyfile(candidate, dst)
            rows.append([src.name, dst.name, f"{w}x{h}", "imported"])
            imported.append(dst)

    hashes = {p: ahash(p) for p in imported}
    dupes = [(a.name, b.name, int((hashes[a] != hashes[b]).sum()))
             for a, b in combinations(imported, 2) if (hashes[a] != hashes[b]).sum() <= 20]
    with LOG.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "imported_as", "size", "status"])
        writer.writerows(rows)
    for row in rows:
        print(f"{row[3]:45s} {row[0]:45s} {row[2]}")
    print(f"\n{len(imported)} imported into {IMAGES}")
    for a, b, d in dupes:
        print(f"possible duplicate: {a} ~ {b} (hash distance {d}/256)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
