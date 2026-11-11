# Model card: NaijaWatchTower sheet reader

> **Caveats first.** The real sample is small (13 labelled sheets: 4 in R-dev, 9 in R-test), so every interval is wide and nothing here generalises to all sheets, states or elections. Labels were typed by one person and **have not yet been reconciled** against the model, so they may contain typing errors. Whether some unlisted party rows are empty or a written 0 is still being checked. The arithmetic rules are validated on this small sample only. The PoC shows nothing about trust or turnout.

## Model

| | |
|---|---|
| Base model | **Qwen/Qwen3-VL-8B-Instruct**, revision `0c351dd01ed87e9c1b53cbc748cba10e6187ff3b`, licence **Apache-2.0** |
| Quantisation | 4-bit NF4 (bitsandbytes, double quantisation) for the language model; vision tower and output layer kept in bf16 |
| Hardware | One NVIDIA RTX 4070, 12 GB; peak 6.8 GB VRAM; median 8.3 s per sheet (R-test, n = 9) |
| Prompt | `prompts/read_sheet_v1.md`, zero-shot (`read_sheet_v1+0shot-none`) |
| Decoding | Greedy, **grammar-constrained**: the JSON skeleton is forced; values may only be digits (max 4), `null`, or the end of the number; "unclear" may only list field names. 100% schema-valid output on every run. |
| Confidence | Product of the probabilities of the deciding tokens, then temperature scaling fitted on R-dev (T = 20, the upper bound of the search: the raw model is strongly overconfident). |
| Alternatives | Up to 6 readings per field from the top tokens at each digit position; used by the constraint-aware decoder. |
| Candidates compared | Qwen3-VL-8B-Instruct and Qwen3.5-9B (both Apache-2.0), zero-shot; Qwen3-VL-8B with 1 and 2 local few-shot examples. Selected on R-dev only. |

## Intended use

Reading the 27 handwritten figures of a photographed Form EC 8A (items 1–8, 18 party rows, bottom total) **as a first pass for human checking**. Every reading is shown next to its source image, checked against the form's arithmetic, routed to a reviewer when uncertain or inconsistent, and open to confirmation or dispute by people registered to the unit.

## Out of scope

- Declaring, projecting or ranking results. The system never does this.
- Any unattended use: at the current accuracy every sheet must be checked by a person.
- Finding fraud. A discrepancy is a flag for review, not a finding.
- Reading names, signatures, stamps, codes or the "in words" column; the output schema has no place for them.
- Other forms (EC 8B and above), or form versions with a different number of party rows, without re-validation.

## Data

- **Real sheets:** 30 publicly available result-sheet images downloaded by hand from the public portal (no automated access); 13 labelled so far. Originals stay private; nothing derived from them is published except aggregate figures.
- **Split:** by sheet, stratified by state, frozen before any tuning: R-dev 4 sheets, R-test 9 sheets.
- **Image quality:** most are 1024-pixel-wide portal thumbnails; a few are full-resolution phone photos.

## Results (aggregate; 95% bootstrap intervals over sheets)

| Configuration | Dataset (n sheets) | Field accuracy | Field accuracy, blank = 0 | Sheets fully correct |
|---|---|---|---|---|
| **Qwen3-VL-8B, zero-shot (shipped)** | R-dev (4) | 0.48 [0.26, 0.72] | 0.75 [0.63, 0.87] | 0 of 4 |
| **Qwen3-VL-8B, zero-shot (shipped)** | R-test (9) | 0.48 [0.34, 0.65] | 0.64 [0.47, 0.81] | 0 of 9 |
| Qwen3.5-9B, zero-shot | R-test (9) | 0.60 [0.47, 0.75] | 0.83 [0.75, 0.91] | 0 of 9 |
| Qwen3-VL-8B, 2-shot (local examples) | R-test (9) | 0.70 [0.58, 0.81] | 0.76 [0.69, 0.83] | 0 of 9 |

"Blank = 0" counts an empty cell and a written 0 as the same value; both count as 0 in every sum. The full tables, calibration and risk-coverage are in `results/REPORT.md`.

**The M1 target (80% non-blank field accuracy on R-dev) is not met: 49.5%.** The configurations that scored higher on R-test were not selected, because R-test must not drive choices. With more labelled development sheets, few-shot prompting and Qwen3.5-9B should be re-tested.

## Known failure modes

- **Layout confusion between the numbered items.** The most common error copies item 1 (voters on the register) into item 2 (accredited voters), and swaps unused, spoiled and rejected ballots.
- **Blank versus written zero.** The model often reads cells holding a 0, "NIL" or a dash as empty, or the reverse.
- **Spurious figures in empty party rows** on some sheets.
- **Written sums.** A cell such as "5+74=79" must be reduced to one number; the prompt asks for the total.
- **Overconfidence.** Raw confidences are far too high; calibration reduces the expected calibration error from 0.41 to 0.12 (R-dev) and from 0.43 to 0.10 (R-test).

## Human oversight design

- A sheet is accepted automatically only if every calibrated field confidence is ≥ 0.90, nothing is marked unclear and every hard rule holds. **At the current accuracy no sheet in R-dev or R-test qualifies, so the human-review load is 100%.** That is the intended safe behaviour while the reader is weak.
- When a hard rule fails, the constraint-aware decoder proposes the most probable reading that adds up. It is shown as a "possible alternative reading" and **never applied automatically**, because a tampered digit can look like a misreading.
- Reviewers must choose a reason code; every decision is audit-logged and every change to a model value is recorded as a model-versus-human mismatch for later evaluation.
- Accepted corrections can be exported as field-level training examples (`scripts/export_feedback.py`) for prompt improvement or a later fine-tune.

## Safety

- Runs locally; no image is ever sent to a hosted API; only validated figures are stored, never the raw model text.
- Prompt injection: text written beside a real sheet asking for zeros left the output schema-valid and did not move any reading to zero; it perturbed one field.
