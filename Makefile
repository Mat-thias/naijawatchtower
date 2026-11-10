PY := .venv/bin/python

.PHONY: setup setup-gpu test import label reconcile label-status redact redact-review redact-apply split sim

setup:            ## create the venv (Python 3.12 via uv) and install CPU dependencies
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -r requirements.txt

setup-gpu: setup  ## add the VLM stack (needs an NVIDIA GPU)
	uv pip install --python $(PY) -r requirements-gpu.txt

test:
	$(PY) -m pytest -q

import:           ## data_real/raw -> data_real/images (byte copies / native PDF extraction)
	$(PY) -I scripts/import_real.py

label:            ## type labels for every unlabelled real sheet (resumable)
	$(PY) scripts/label_real.py

reconcile:        ## after M1: re-check each label the reader disagrees with
	$(PY) scripts/label_real.py reconcile

label-status:
	$(PY) scripts/label_real.py status

redact:           ## draw black boxes on each real sheet -> data_redacted/
	$(PY) scripts/redact.py

redact-review:    ## approve each redacted copy at full size
	$(PY) scripts/redact.py review

redact-apply:     ## re-render every redacted copy from the saved boxes
	$(PY) scripts/redact.py apply

split:            ## freeze the R-dev / R-test split (after labelling, before tuning)
	$(PY) scripts/split_real.py

sim:              ## simulated records for the map (excludes states with real sheets)
	$(PY) scripts/simulate_election.py

GPU_ENV := PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

smoke:            ## GPU smoke test + prompt-injection check (prints aggregates only)
	$(GPU_ENV) $(PY) scripts/smoke_vlm.py --n 3 --injection

eval-models:      ## zero-shot model comparison on R-dev (needs labels + split)
	$(GPU_ENV) $(PY) scripts/evaluate.py run --split dev --n-shots 0 --model Qwen/Qwen3.5-9B
	$(GPU_ENV) $(PY) scripts/evaluate.py run --split dev --n-shots 0 --model Qwen/Qwen3-VL-8B-Instruct

eval:             ## write results/metrics.json and results/REPORT.md from all runs
	$(PY) scripts/evaluate.py report

DEMO_DB := data/demo.db
PORT ?= 8000

demo:             ## seed the demo DB and serve it in cache mode (no GPU needed): http://127.0.0.1:8000
	$(PY) scripts/seed_demo.py --db $(DEMO_DB)
	NWT_DB=sqlite:///$(DEMO_DB) VLM_BACKEND=cache $(PY) -m uvicorn naijawatchtower.api.main:app --host 127.0.0.1 --port $(PORT)

demo-live:        ## serve the demo with the VLM on the GPU; upload a redacted copy at /upload (ingest account)
	NWT_DB=sqlite:///$(DEMO_DB) VLM_BACKEND=local $(GPU_ENV) $(PY) -m uvicorn naijawatchtower.api.main:app --host 127.0.0.1 --port $(PORT)
