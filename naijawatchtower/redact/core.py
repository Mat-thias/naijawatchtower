"""Redacted display copies of real sheets (CLAUDE.md 3.3, decisions D2 and D10).

- Solid black boxes only; never blur or pixelate.
- Never write invented text onto the sheet. The only added text sits on a white
  banner strip *above* the image, or in a margin *beside* it for the private
  prompt-injection test copy.
- Always writes a new file. The original is opened read-only and never modified.
- Output is re-encoded with no EXIF or other metadata.

Box coordinates are in pixels of the original after EXIF orientation is applied,
which is also how the interactive tool displays the image.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

BANNER_TEXT = "REDACTED COPY FOR DEMO – NOT AN OFFICIAL DOCUMENT"
Box = tuple[int, int, int, int]  # x0, y0, x1, y1


def load_oriented(path: Path) -> Image.Image:
    """Open an image as RGB with its EXIF orientation applied."""
    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).convert("RGB")


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def normalise_box(box: Iterable[float], width: int, height: int) -> Box:
    """Order corners and clip a box to the image."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, x1 = sorted((max(0, min(width, x0)), max(0, min(width, x1))))
    y0, y1 = sorted((max(0, min(height, y0)), max(0, min(height, y1))))
    return x0, y0, x1, y1


def banner_height(image_height: int) -> int:
    """Height of the banner strip for an image of this height."""
    return max(36, image_height // 25)


def redact_image(src: Path, boxes: Iterable[Iterable[float]], dst: Path,
                 banner: str = BANNER_TEXT, quality: int = 90) -> str:
    """Write a redacted copy of `src` to `dst` and return the copy's SHA-256.

    Boxes are filled solid black; a white banner strip carrying `banner` is added
    above the image. `dst` must differ from `src`.
    """
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if src == dst:
        raise ValueError("refusing to overwrite the original")
    img = load_oriented(src)
    draw = ImageDraw.Draw(img)
    for box in boxes:
        draw.rectangle(normalise_box(box, img.width, img.height), fill=(0, 0, 0))

    strip = banner_height(img.height)
    out = Image.new("RGB", (img.width, img.height + strip), (255, 255, 255))
    out.paste(img, (0, strip))
    text_draw = ImageDraw.Draw(out)
    size = max(10, int(strip * 0.55))
    font = _font(size)
    while size > 8 and text_draw.textlength(banner, font=font) > img.width - 16:
        size -= 1
        font = _font(size)
    text_draw.text((8, (strip - size) // 2), banner, fill=(0, 0, 0), font=font)

    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, format="JPEG", quality=quality, optimize=True)  # no exif= -> no metadata
    return hashlib.sha256(dst.read_bytes()).hexdigest()


def make_injection_copy(src: Path, dst: Path, text: str) -> Path:
    """Private prompt-injection test copy (D10): the sheet with a white margin beside it carrying `text`.

    The sheet itself is not altered. The copy stays under `data_real/` and is never published.
    """
    img = load_oriented(src)
    margin = max(240, img.width // 3)
    out = Image.new("RGB", (img.width + margin, img.height), (255, 255, 255))
    out.paste(img, (0, 0))
    draw = ImageDraw.Draw(out)
    size = max(14, img.height // 60)
    words, lines, line = text.split(), [], ""   # empty text -> blank margin (control copy)
    font = _font(size)
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) > margin - 20 and line:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    for i, ln in enumerate(lines):
        draw.text((img.width + 10, img.height // 3 + i * int(size * 1.3)), ln, fill=(20, 20, 160), font=font)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, format="JPEG", quality=92)
    return Path(dst)


class BoxStore:
    """`data_real/redaction_boxes.json`: the boxes for every sheet, so redaction is reproducible."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, dict] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, sheet_id: str) -> list[Box]:
        return [tuple(b) for b in self.data.get(sheet_id, {}).get("boxes", [])]  # type: ignore[misc]

    def version(self, sheet_id: str) -> int:
        return int(self.data.get(sheet_id, {}).get("boxes_version", 0))

    def put(self, sheet_id: str, file: str, boxes: list[Box]) -> int:
        """Store boxes for a sheet, bumping its version. Returns the new version."""
        version = self.version(sheet_id) + 1
        self.data[sheet_id] = {"file": file, "boxes": [list(b) for b in boxes],
                               "boxes_version": version,
                               "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.save()
        return version

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)


class Manifest:
    """`data_redacted/manifest.json`: one entry per redacted copy, with Matthias's review flag."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, dict] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def record(self, sheet_id: str, file: str, sha256: str, boxes_version: int) -> None:
        """Record a (re)written copy. Any rewrite clears the review flag."""
        self.data[sheet_id] = {"file": file, "sha256": sha256, "boxes_version": boxes_version,
                               "reviewed_by_matthias": False}
        self.save()

    def mark_reviewed(self, sheet_id: str, ok: bool) -> None:
        self.data[sheet_id]["reviewed_by_matthias"] = ok
        self.save()

    def reviewed(self) -> list[str]:
        """Sheet ids whose copies Matthias has approved."""
        return sorted(s for s, e in self.data.items() if e.get("reviewed_by_matthias"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)
