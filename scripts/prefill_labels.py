"""Read every real sheet once with the VLM so the label tool can pre-fill its suggestions.

Readings are stored as validated JSON under data_real/runs/prefill/<sha256>.json (git-ignored).
Prints progress counts only, never values. Resumable: images already read are skipped.

Usage: python scripts/prefill_labels.py [--model Qwen/Qwen3-VL-8B-Instruct]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import DATA_REAL  # noqa: E402
from naijawatchtower.eval.labelling import IMAGE_SUFFIXES  # noqa: E402
from naijawatchtower.ocr.cache_reader import CacheReader, store_reading  # noqa: E402

PREFILL_DIR = DATA_REAL / "runs" / "prefill"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen3-VL-8B-Instruct")
    args = parser.parse_args()
    images = sorted(p for p in (DATA_REAL / "images").iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    cache = CacheReader([PREFILL_DIR])
    todo = [p for p in images if cache.path_for(p) is None]
    print(f"{len(images)} images, {len(todo)} to read", flush=True)
    if not todo:
        return 0
    from naijawatchtower.ocr.vlm_reader import VLMReader
    reader = VLMReader.from_config({"model_id": args.model, "n_shots": 0})
    for i, image in enumerate(todo, 1):
        reading = reader.read(image)
        store_reading(image, reading, PREFILL_DIR)
        print(f"[{i}/{len(todo)}] {image.name}: {reading.seconds:.1f}s valid={reading.json_valid}", flush=True)
    print("prefill done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
