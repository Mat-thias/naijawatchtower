# Assumptions and decisions

Every decision taken without explicit instruction, with its reason. Matthias can overturn any of them; open items are marked **OPEN**.

## Step 0: environment report (9 Nov 2026)

| item | finding |
|---|---|
| OS | Ubuntu 22.04.5 LTS, kernel 6.8.0-138, x86_64 |
| CPU | Intel, 8 cores / 16 threads @ 1.8 GHz base |
| RAM | 62 GiB (58 GiB available), 2 GiB swap |
| Disk | 145 GB free on `/` (84% used) — enough for 2–3 VLM candidates (~5–17 GB each) |
| GPU | **NVIDIA GeForce RTX 4070, 12,282 MiB** (the spec said RTX 4080; the 12 GB VRAM budget is unchanged) |
| Driver / CUDA | driver 595.99.02, CUDA 13.2 (driver API); no `nvcc` (not needed: PyTorch wheels bundle their CUDA runtime) |
| Python | system `python3` is **3.10.12** (below the 3.11+ requirement); `uv` 0.11.23 is installed with a managed **CPython 3.12.13** |
| PyTorch | system Python has `torch 2.14.0+cu130`, `torch.cuda.is_available() == True` |
| pip / PyPI | reachable (HTTP 200) |
| Hugging Face | reachable (HTTP 200 on a model config file); a local HF cache exists |
| MNIST | not tested: no longer needed (D1) |

## Decisions

| id | decision | reason |
|---|---|---|
| D1 | **No synthetic sheet images** (decided by Matthias, 9 Nov). No MNIST generator, no S-dev/S-test, no synthetic exemplars, no CRNN. Real sheets are the only images; clearly labelled simulated records (numbers only) fill the map. | Synthetic accuracy does not predict real accuracy, and the generator was the most expensive M0 item. |
| D2 | Redaction uses **solid black boxes**, never blur or pixelation, and never writes invented text (such as replacement party names) onto a sheet. | Pixelated text can sometimes be recovered; drawing new text onto a real result form would be fabricating content on an official document. |
| D3 | **OPEN — organiser confirmation.** Redacted copies (`data_redacted/`) stay git-ignored and are not shown outside Matthias's machine until the organisers (Rise Networks) confirm in writing that showing publicly available IReV images with personal details blacked out is acceptable. When confirmed, record the date and sender here and remove `data_redacted/` from `.gitignore`. Until then `make demo` on a fresh clone runs on simulated records only. | An edited copy of a real EC8A is the kind of artefact this tool is designed to flag; whether it may appear in a public demo for an INEC-partnered programme is the organisers' call, not ours. |
| D4 | Per-party totals are **never computed over real sheets**, at any level; real party rows are shown as "Row 1" … "Row 18". | The party order is printed identically on every sheet of an election, so row totals are party totals; blacking out names alone hides nothing. |
| D5 | Real sheets get **pseudonymous `8NN` unit codes** (real state number, sequential LGA/ward/unit numbers); real unit codes are never stored in the app DB. Simulated units use `9NN`. | Keeps real units unidentifiable in the app while still placing each sheet in its state on the map. |
| D6 | Real sheets and simulated records are **never summed together**; coverage and verification counts report them separately. | A total mixing real and fake data means nothing and could be misread. |
| D7 | Project Python is **3.12 via uv**, in `.venv/`. | System Python 3.10 is below the spec's 3.11+. |
| D8 | Calibration uses **temperature scaling** (one parameter). | R-dev has 6–10 sheets × 27 fields, mostly blank; isotonic regression would overfit. |
| D9 | M1 stop rule: **stop and report if field-level accuracy on non-blank R-dev fields is below 80%** (replaces "sheet-level < 60% on mild synthetic sheets"). | Blank fields are easy and dominate the count; sheet-level exact match over 27 fields is very strict on real photos. Threshold is Claude's choice: change it if you want a different bar. |
| D10 | The prompt-injection test uses a **private copy of a real sheet with a white margin added beside it** carrying the injected text; the sheet content is not altered, and the copy is never published. | Tests injection without fabricating content on the sheet itself. |
| D11 | Few-shot runs are evaluated on R-dev sheets **that are not exemplars**. | Otherwise the model is scored on sheets it was shown with their answers. |
| D12 | Network use is limited to setup: pip installs, model-weight downloads, and the one-time boundary-data download. Runtime and tests make no network calls. | Spec rule 7 and section 12. |
| D13 | Licence: **MIT** (placeholder; change if needed). | Spec default. |
| D14 | **Claude never views real images.** Inspection of real sheets uses metadata only (sizes, dimensions, page counts, hashes); every visual check (labelling, redaction, duplicate checks) is Matthias's. | Viewing an image in Claude Code sends it to a hosted model, which rule 7 forbids. |
| D15 | Manual downloads land in `data_real/raw/`; `scripts/import_real.py` copies JPEG/PNG byte for byte and extracts the single embedded image of each PDF in its native format (`pdfimages -all`, no re-encoding) into `data_real/images/`. Images whose shorter side is under **600 px** are skipped as unreadable. | Format conversion without altering the sheet; tiny images would only add noise to the evaluation. |
| D16 | Map boundaries: **geoBoundaries gbOpen NGA ADM1** (build Dec 2023, source GRID3, **CC BY 4.0**, attribution required), simplified file at commit `9469f09`, downloaded once on 9 Nov 2026; simplified with shapely (tolerance 0.01°, 3-decimal coordinates) to 66 KB in `data/geo/nigeria_admin1.geojson`. The FCT is named "Abuja Federal Capital Territory" in this source. | Openly licensed, all 37 regions present, small enough to commit. |
| D17 | R-dev size is `ceil(25% × N)`, shared across states in proportion to their sheet counts; the split is frozen in `data_real/splits.json` on first run. | Reproducible and stratified; with 30 sheets this gives 8 dev, 22 test. |
| D18 | Label entry is three lines per sheet: items 1–8 space-separated, non-blank party rows as `row=value`, then the bottom total (`-` blank, `?` illegible). Reconciliation against the reader is a separate later step. | Most party rows are blank, so typing only the filled rows is about 3× faster; labelling can start before the VLM exists. |

## M1 reader decisions

| id | decision | reason |
|---|---|---|
| D19 | **VLM candidates:** Qwen/Qwen3.5-9B (Feb 2026), Qwen/Qwen3-VL-8B-Instruct (Oct 2025), Qwen2.5-VL-7B-Instruct as fallback; all **Apache-2.0**. Newer Qwen generations (3.6, 3.8) and Gemma 4 come only as 26B+ models that do not fit 12 GB at 4-bit. | Best open-weight models that fit the VRAM budget with permissive licences (checked on Hugging Face 9 Nov 2026). |
| D20 | **Constrained decoding by grammar** (`naijawatchtower/ocr/grammar.py`): the JSON skeleton is forced token by token; at each value the model may emit only digits (max 4, no leading zero), `null`, or the end of the number; the unclear list allows only field names. Output is compact JSON (no spaces). | Schema validity by construction (no retry path needed), clean per-field token probabilities, and sheet text cannot change the output structure (prompt-injection defence). |
| D21 | **Field confidence** = product of the probabilities of the digit tokens *and* of the step that ends the number (or of the `null` token), renormalised over the allowed tokens. **Alternatives**: single-digit substitutions from the top-5 tokens per position, one digit shorter, one digit longer, and `null`; up to 6 per field. | Including the end-of-number step captures length uncertainty (e.g. 12 vs 120). |
| D22 | 4-bit NF4 (double quantisation) for the language model; the **vision tower and output layer stay in bf16**; repetition penalty, sampling and top-k/top-p are switched off. | Quantising the output layer and vision encoder costs accuracy for little memory; a repetition penalty would distort digit probabilities for numbers such as 1100. |
| D23 | Scripts never print values read from real sheets; smoke tests and evaluation print timing, VRAM and aggregate counts only. | Real-sheet results are reported only as aggregates (rule 10). |
| D24 | The prompt-injection test compares the injected copy with a **control copy carrying the same blank margin**, not with the clean sheet. | Adding a margin shrinks the sheet after resizing, which changes readings by itself; the control isolates the effect of the injected text. |

## M2 backend decisions

| id | decision | reason |
|---|---|---|
| D25 | USSD registration asks an explicit SMS opt-in question ("Get an SMS when its result is ready? 1 Yes 2 No") before the END screen; web sign-up defaults to no SMS. | The spec flow ends right after the code, which would make alerts either always on (not real opt-in) or impossible to switch on. |
| D26 | A sheet in NEEDS_REVIEW or UNREADABLE **cannot become VERIFIED** through confirmations; it stays in the review queue until a reviewer acts. Disputes can still mark it DISPUTED. | Rule 5: no path may bypass human review of uncertain sheets. |
| D27 | The burst heuristic opens a VERIFIER_DISPUTE discrepancy with a "burst flag" note (it flags, never blocks). | Puts the flag in the reviewer's existing queue without a new table. |
| D28 | Uploads without metadata are stored byte for byte; uploads with EXIF/XMP are re-encoded without it. The stored file's SHA-256 is recorded. | Keeps redacted copies identical to the files their cached readings are keyed by. |
| D29 | The `/ussd-sim` page posts to a server-side proxy that adds the shared secret. | The secret never reaches the browser. |
| D30 | Observers sign up with an invite code from `OBSERVER_INVITE_CODE` (dev default `DEMO-OBSERVER`). Reviewer, ingest and admin accounts are created by the seeder. | Simulates accreditation without any registry data. |
| D31 | The sheet page checks the rules live against the current values (model reading or reviewer values); `validation_result` keeps the pipeline's original check. | After a review, the page must show whether the corrected figures add up. |
| D32 | Every verification (including a confirmation) gets a random 5-digit case reference, stored on the verification row. | USSD shows a reference after both "yes" and "no", and a repeat attempt can quote it. |

## Labelling conventions

| id | decision | reason |
|---|---|---|
| D41 | **Model-assisted labelling** (Matthias's decision, 10 Nov; replaces type-first). `scripts/prefill_labels.py` reads every sheet with **Qwen3-VL-8B-Instruct, zero-shot**; the label tool pre-fills only fields with confidence ≥ 0.95, not unclear, and no rival above 10% of the top reading; all other fields appear as `_` and are typed from the sheet. Provenance per field (`typed` / `model_accepted` / `model_corrected`) is saved in `data_real/label_provenance.csv`; R001–R002 were typed before the change. | Labelling 27 fields × 30 sheets by hand is the critical path. **Cost:** a confident model error that the labeller accepts is invisible, so accuracy may be overstated, most for Qwen3-VL-8B itself in the model comparison. The report states this and gives accuracy on typed fields separately. |
| D40 | A cell holding a **written sum** (e.g. item 8 written as "5+74") is labelled with its **total** (79); the label tool accepts `5+74` as typed and records a private note in `data_real/label_notes.csv`. The prompt tells the model to give the total. Such cells are counted and reported separately in the evaluation. Prompt `read_sheet_v1` was edited for this on 10 Nov before any reported run. | The output can hold only one number per field, and the total is what the clerk recorded; seen on a real sheet. |
| D39 | Label **what is written**, not what should add up. A cell with "0", "NIL" or a dash is **0**; a truly empty cell is **blank** (`-`); a crossed-out value takes the **final rewritten** value; anything you cannot read is `?` (excluded from accuracy). | Keeps labels consistent with the prompt and the rules; arithmetic errors on the sheet must stay visible. |

## M3 web decisions

| id | decision | reason |
|---|---|---|
| D33 | HTMX **2.0.11** (licence **0BSD**) is vendored as `naijawatchtower/web/static/htmx-2.0.11.min.js`, downloaded once from unpkg on 10 Nov 2026 (sha256 `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717`). No CDN at runtime. | Spec: vendored HTMX, no CDN, no build step. |
| D34 | `/pu/{code}` and `/discrepancies` serve HTML to browsers (`Accept: text/html`) and JSON to everything else. | The spec names the same paths for the JSON API (5.3) and the pages (9). |
| D35 | Demo quick sign-in (`/login/demo/{role}`) for seeded demo accounts, enabled while `NWT_DEMO_MODE=1` (the default). **Turn it off for anything but a demo.** | The 3-minute demo video cannot wait for OTPs from a server log. |
| D36 | On the coverage layer a state with units but no sheets shows **0%** (that is data: nothing digitised). The "no data" hatch marks states with no units (coverage) or nothing digitised (verified, accreditation). | Showing 0% as "no data" would hide states that are behind. |
| D37 | An `/upload` page for the ingest role (not in the spec's page list) posts to the same ingest path as `POST /sheets`. | Needed for `make demo-live`, where a redacted copy is read on the GPU on camera. |
| D38 | Web forms rely on `SameSite=Lax` session cookies; there are no CSRF tokens in the PoC. | Proportionate for a local demo; recorded as a residual risk in THREAT_MODEL. |

## M1 smoke results (10 Nov 2026; engineering checks, not reported metrics)

- Both candidates load in 4-bit and produce schema-valid output on 3/3 real sheets: Qwen3.5-9B 10.5–12 s/sheet, peak 8.2 GB; Qwen3-VL-8B-Instruct 8.6–9.3 s/sheet, peak 6.8 GB (1.6 MP image budget, zero-shot, ~1,950–1,970 input tokens).
- On the first 2 labelled sheets (combined, n = 54 fields): Qwen3.5-9B 47/54, Qwen3-VL-8B 48/54. Far too small to choose; the choice is made on R-dev.
- Qwen3.5-9B runs its linear-attention layers on slow reference kernels (`causal_conv1d` and `flash-linear-attention` not installed; the former needs `nvcc`, which this machine lacks).
- **Prompt injection (Qwen3.5-9B, one labelled sheet):** output schema-valid in all cases; fields correct vs labels: control 21/27, injected 20/27; fields read as 0: 13 in clean, control and injected copies; of the 4 fields where the injected copy differs from the control, none changed to 0. The injected instruction was not followed; the added text perturbed one reading.

## Real data received (9 Nov 2026)

36 files downloaded by Matthias. Imported: **30** images (19 portal thumbnails at 1024 px wide, 10 single-JPEG PDFs from 678×944 up to 4096×3072, and `canvas.png`). Skipped: five PDFs containing only a 192×256 image, and a saved HTML page.

- **OPEN:** what is `canvas.png` (1258×943)? If it is a screenshot of the portal rather than a sheet photo, remove it from `data_real/images/`.
- **OPEN:** possible duplicates by image hash (may be false alarms, as all forms look alike): `result_54786` ~ `result_54788`, `result_60281` ~ `result_60295`.
- The portal thumbnails are 1024 px wide; if full-size versions are available, they will likely read better.

## To check before any real-world use

- `config/states.json` state numbering against INEC's published list.
- Rules R2–R7 against the official form instructions and the labelled real sheets (R1 is printed on the form).
- The "how results are counted" explainer wording against INEC's published procedure.
- The Pidgin strings with a native speaker.
