"""VLM reader (CLAUDE.md 4.2-4.4): a local open-weight VLM reads the whole page once.

- Runs only on the local GPU; no image ever leaves the machine.
- Greedy decoding constrained by `OutputGrammar`, so the output always matches the schema.
- Per-field confidence and alternatives come from the token probabilities recorded
  at each constrained step. Confidences here are raw; calibration is applied later.
- Only the validated reading is returned; the raw model text is never stored.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from naijawatchtower.config import CONFIG_DIR, ROOT, field_names
from naijawatchtower.ocr.grammar import TOP_K, OutputGrammar, StepDist, readings_from_steps
from naijawatchtower.ocr.schema import to_output_json
from naijawatchtower.ocr.types import FieldReading, SheetReading
from naijawatchtower.redact.core import load_oriented


def load_vlm_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """`config/vlm.yaml` with optional overrides."""
    cfg = yaml.safe_load((CONFIG_DIR / "vlm.yaml").read_text())
    cfg.update(overrides or {})
    return cfg


def resolve_commit(model_id: str, revision: str | None) -> str:
    """Exact commit hash of the locally cached model snapshot (no network)."""
    try:
        from huggingface_hub import snapshot_download
        return Path(snapshot_download(model_id, revision=revision, local_files_only=True)).name
    except Exception:  # noqa: BLE001 - provenance only; never block reading
        return revision or "unknown"


def resize_to_budget(img, max_pixels: int):
    """Downscale (never upscale) so width*height <= max_pixels."""
    w, h = img.size
    if w * h <= max_pixels:
        return img
    scale = math.sqrt(max_pixels / (w * h))
    from PIL import Image
    return img.resize((max(28, int(w * scale)), max(28, int(h * scale))), Image.LANCZOS)


@dataclass
class Exemplar:
    """A few-shot example: an image and its human-labelled answer."""

    image: Path
    values: dict[str, int | None]
    unclear: list[str]


class _SchemaProcessor:
    """LogitsProcessor that masks everything the grammar forbids and records step distributions."""

    def __init__(self, grammar: OutputGrammar, prompt_len: int) -> None:
        import torch
        self.torch = torch
        self.machine = grammar.machine()
        self.prompt_len = prompt_len
        self.dists: list[StepDist] = []

    def __call__(self, input_ids, scores):
        torch = self.torch
        generated = input_ids.shape[1] - self.prompt_len
        if generated > len(self.machine.tokens):
            self.machine.advance(int(input_ids[0, -1]))
        allowed = self.machine.allowed()
        mask = torch.full_like(scores, float("-inf"))
        mask[:, allowed] = 0
        masked = scores + mask
        probs = torch.softmax(masked[0].float(), dim=-1)
        k = min(TOP_K, len(allowed))
        top = torch.topk(probs, k)
        self.dists.append(StepDist(int(top.indices[0]),
                                   [(int(i), float(p)) for i, p in zip(top.indices, top.values)]))
        return masked


class VLMReader:
    """`SheetReader` backed by a local VLM loaded once at start-up."""

    def __init__(self, cfg: dict[str, Any], exemplars: list[Exemplar] | None = None) -> None:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

        self.cfg = cfg
        self.torch = torch
        torch.manual_seed(cfg.get("seed", 0))
        quant = cfg.get("quantization", "nf4")
        kwargs: dict[str, Any] = {"device_map": "cuda:0", "dtype": torch.bfloat16}
        skip = cfg.get("keep_full_precision", ["visual", "lm_head"])
        if quant == "nf4":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16, llm_int8_skip_modules=skip)
        elif quant == "int8":
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True, llm_int8_skip_modules=skip)
        self.processor = AutoProcessor.from_pretrained(cfg["model_id"], revision=cfg.get("revision"))
        self.model = AutoModelForImageTextToText.from_pretrained(
            cfg["model_id"], revision=cfg.get("revision"), **kwargs).eval()
        tok = self.processor.tokenizer
        eos = tok.convert_tokens_to_ids(cfg.get("eos_token", "<|im_end|>"))
        self.grammar = OutputGrammar(lambda s: tok.encode(s, add_special_tokens=False), field_names(), eos)
        self.instruction = (ROOT / cfg["prompt"]).read_text()
        self.exemplars = exemplars or []
        commit = resolve_commit(cfg["model_id"], cfg.get("revision"))
        self.model_revision = f"{cfg['model_id']}@{commit}:{quant}"
        self.prompt_version = f"{cfg['prompt_version']}+{len(self.exemplars)}shot-{cfg.get('exemplar_config', 'none')}"

    @classmethod
    def from_config(cls, overrides: dict[str, Any] | None = None,
                    exemplars: list[Exemplar] | None = None) -> "VLMReader":
        return cls(load_vlm_config(overrides), exemplars)

    def _messages(self) -> tuple[list[dict], list]:
        """Chat messages with exemplar turns; returns (messages, exemplar images)."""
        messages: list[dict] = []
        images = []
        follow_up = "Next result sheet. Same instructions: answer with the JSON object only."
        for i, ex in enumerate(self.exemplars):
            images.append(resize_to_budget(load_oriented(ex.image), self.cfg["max_pixels"]))
            text = self.instruction if i == 0 else follow_up
            messages.append({"role": "user", "content": [{"type": "image"}, {"type": "text", "text": text}]})
            messages.append({"role": "assistant",
                             "content": [{"type": "text", "text": to_output_json(ex.values, ex.unclear)}]})
        text = self.instruction if not self.exemplars else follow_up
        messages.append({"role": "user", "content": [{"type": "image"}, {"type": "text", "text": text}]})
        return messages, images

    def read(self, image_path: Path) -> SheetReading:
        """Read all 27 fields from one image."""
        torch = self.torch
        start = time.perf_counter()
        torch.cuda.reset_peak_memory_stats()
        messages, images = self._messages()
        images = images + [resize_to_budget(load_oriented(image_path), self.cfg["max_pixels"])]
        prompt = self.processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                                    enable_thinking=False)
        inputs = self.processor(text=[prompt], images=images, return_tensors="pt").to(self.model.device)
        prompt_len = inputs["input_ids"].shape[1]
        proc = _SchemaProcessor(self.grammar, prompt_len)
        from transformers import LogitsProcessorList
        try:
            with torch.inference_mode():
                self.model.generate(**inputs, logits_processor=LogitsProcessorList([proc]),
                                    max_new_tokens=self.grammar.max_new_tokens, do_sample=False,
                                    temperature=None, top_p=None, top_k=None, repetition_penalty=1.0,
                                    eos_token_id=self.grammar.eos_id,
                                    pad_token_id=self.processor.tokenizer.pad_token_id or self.grammar.eos_id)
            fields = readings_from_steps(proc.machine, proc.dists, self.grammar)
            valid = True
        except (ValueError, RuntimeError):
            fields = {n: FieldReading.of(None, 0.0) for n in field_names()}
            valid = False
        seconds = time.perf_counter() - start
        vram = torch.cuda.max_memory_allocated() / 2**20
        return SheetReading(fields=fields, model_revision=self.model_revision,
                            prompt_version=self.prompt_version, seconds=seconds, vram_peak_mb=vram,
                            json_valid=valid, extra={"input_tokens": int(prompt_len)})
