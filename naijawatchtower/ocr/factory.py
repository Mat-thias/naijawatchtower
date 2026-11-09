"""Construct a `SheetReader` by name (`VLM_BACKEND=local|cache`)."""

from __future__ import annotations

from naijawatchtower.ocr.types import SheetReader


def make_reader(name: str) -> SheetReader:
    """Return the reader for `name` ('local' = VLM on the GPU, 'cache' = replay stored outputs)."""
    if name == "local":
        from naijawatchtower.ocr.vlm_reader import VLMReader  # type: ignore[import-not-found]
        return VLMReader.from_config()
    if name == "cache":
        from naijawatchtower.ocr.cache_reader import CacheReader  # type: ignore[import-not-found]
        return CacheReader.from_config()
    raise ValueError(f"unknown reader {name!r}; use 'local' or 'cache'")
