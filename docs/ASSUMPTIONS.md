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
