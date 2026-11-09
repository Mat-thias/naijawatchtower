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
