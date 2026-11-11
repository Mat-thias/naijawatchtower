# NaijaWatchTower evaluation report

> **Read these caveats before any number below.**
> - The real sample is small (n is stated beside every figure), so confidence intervals are wide and the
>   figures do not generalise to all sheets, states or elections.
> - Labels were typed from the sheet by one person (Matthias). **13 of 13 sheets have not yet been reconciled** against the model's readings, so the labels may
>   still contain typing errors, and for some sheets whether an unlisted party row is empty or a written 0
>   is still being checked (see the blank = 0 column).
> - Most portal images are 1024-pixel-wide thumbnails; full-size images may read differently.
> - Discrepancy detection is measured on number-level injections and simulated records, not on physically
>   altered sheets, and arithmetic checks cannot catch a forgery that keeps all totals consistent.
> - The arithmetic rules have been checked on a small real sample only.
> - This proof of concept shows nothing about trust or turnout.

## Model and prompt selection

Chosen on R-dev only (field accuracy, then speed): **Qwen3-VL-8B-Instruct-0shot-none**. R-test was not used for any choice. R-dev holds only 4 sheets (fewer for few-shot runs, whose exemplars are excluded), so this choice is fragile; other configurations scored higher on R-test, which is reported but must not drive the choice.

**M1 threshold not met:** non-blank field accuracy on R-dev is 49.5% against a target of 80%. The reader is not good enough to run unattended; the system therefore sends every uncertain or rule-breaking sheet to a person (see risk-coverage below: at the default threshold no sheet is auto-accepted).


Generated 2026-11-11T13:30:26+00:00. All figures are aggregates over real sheets labelled by hand; no per-sheet results are published. 95% bootstrap intervals over sheets in brackets.

## Reader comparison

| run | dataset | n sheets | n fields | field acc. | non-blank field acc. | field acc., blank = 0 | sheet exact | JSON valid | s/sheet (median, p95) | peak VRAM MB |
|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3-VL-8B-Instruct-0shot-none-dev | R-dev | 4 | 108 | 0.481 [0.259, 0.722] | 0.495 [0.269, 0.750] (n=105) | 0.750 [0.630, 0.870] | 0.000 [0.000, 0.000] | 1.0 | 8.59, 11.42 | 6787.0 |
| Qwen3-VL-8B-Instruct-0shot-none-test | R-test | 9 | 243 | 0.481 [0.342, 0.650] | 0.485 [0.342, 0.660] (n=241) | 0.638 [0.469, 0.811] | 0.000 [0.000, 0.000] | 1.0 | 8.26, 9.6 | 6776.0 |
| Qwen3-VL-8B-Instruct-1shot-local-dev | R-dev | 3 | 81 | 0.309 [0.111, 0.518] | 0.317 [0.115, 0.518] (n=79) | 0.654 [0.518, 0.815] | 0.000 [0.000, 0.000] | 1.0 | 9.29, 9.55 | 7329.0 |
| Qwen3-VL-8B-Instruct-1shot-local-test | R-test | 9 | 243 | 0.679 [0.560, 0.786] | 0.680 [0.558, 0.792] (n=241) | 0.757 [0.663, 0.844] | 0.000 [0.000, 0.000] | 1.0 | 8.99, 9.84 | 7318.0 |
| Qwen3-VL-8B-Instruct-2shot-local-dev | R-dev | 2 | 54 | 0.463 [0.111, 0.815] | 0.481 [0.115, 0.846] (n=52) | 0.722 [0.630, 0.815] | 0.000 [0.000, 0.000] | 1.0 | 10.19, 10.39 | 7633.0 |
| Qwen3-VL-8B-Instruct-2shot-local-test | R-test | 9 | 243 | 0.700 [0.584, 0.807] | 0.701 [0.584, 0.809] (n=241) | 0.761 [0.691, 0.831] | 0.000 [0.000, 0.000] | 1.0 | 10.16, 10.89 | 7901.0 |
| Qwen3.5-9B-0shot-none-dev | R-dev | 4 | 108 | 0.463 [0.241, 0.685] | 0.476 [0.250, 0.698] (n=105) | 0.741 [0.639, 0.824] | 0.000 [0.000, 0.000] | 1.0 | 10.27, 11.6 | 8207.0 |
| Qwen3.5-9B-0shot-none-test | R-test | 9 | 243 | 0.597 [0.469, 0.749] | 0.602 [0.471, 0.760] (n=241) | 0.827 [0.745, 0.905] | 0.000 [0.000, 0.000] | 1.0 | 10.02, 10.89 | 8202.0 |

*Field acc., blank = 0* counts an empty cell and a written 0 as the same value, since both count as 0 in every sum; it shows how much of the error is the blank-versus-zero convention.

## Calibration (temperature scaling, fitted on R-dev)

| run | dataset | n fields | T | ECE before | ECE after |
|---|---|---|---|---|---|
| Qwen3-VL-8B-Instruct-0shot-none-dev | R-dev | 108 | 20.0 | 0.4135 | 0.1232 |
| Qwen3-VL-8B-Instruct-0shot-none-test | R-test | 243 | 20.0 | 0.4341 | 0.0968 |
| Qwen3-VL-8B-Instruct-1shot-local-dev | R-dev | 81 | 1.0 | 0.5935 | 0.5935 |
| Qwen3-VL-8B-Instruct-1shot-local-test | R-test | 243 | 1.0 | 0.2579 | 0.2579 |
| Qwen3-VL-8B-Instruct-2shot-local-dev | R-dev | 54 | 1.0 | 0.4751 | 0.4751 |
| Qwen3-VL-8B-Instruct-2shot-local-test | R-test | 243 | 1.0 | 0.2312 | 0.2312 |
| Qwen3.5-9B-0shot-none-dev | R-dev | 108 | 1.0 | 0.3635 | 0.3635 |
| Qwen3.5-9B-0shot-none-test | R-test | 243 | 1.0 | 0.2795 | 0.2795 |

## Constraint-aware decoding and routing

| run | dataset | sheet exact (top-1) | sheet exact (suggestions accepted) | suggestions | fully correct suggestions |
|---|---|---|---|---|---|
| Qwen3-VL-8B-Instruct-0shot-none-dev | R-dev | 0.0 | 0.0 | 0 | 0 |
| Qwen3-VL-8B-Instruct-0shot-none-test | R-test | 0.0 | 0.0 | 0 | 0 |
| Qwen3-VL-8B-Instruct-1shot-local-dev | R-dev | 0.0 | 0.0 | 1 | 0 |
| Qwen3-VL-8B-Instruct-1shot-local-test | R-test | 0.0 | 0.0 | 2 | 0 |
| Qwen3-VL-8B-Instruct-2shot-local-dev | R-dev | 0.0 | 0.0 | 0 | 0 |
| Qwen3-VL-8B-Instruct-2shot-local-test | R-test | 0.0 | 0.0 | 1 | 0 |
| Qwen3.5-9B-0shot-none-dev | R-dev | 0.0 | 0.0 | 0 | 0 |
| Qwen3.5-9B-0shot-none-test | R-test | 0.0 | 0.0 | 0 | 0 |

### Risk-coverage (calibrated confidence threshold sweep)

**Qwen3-VL-8B-Instruct-0shot-none-dev** (R-dev, n=4 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.0 | None | 0.0 | 1.0 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

**Qwen3-VL-8B-Instruct-0shot-none-test** (R-test, n=9 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.0 | None | 0.0 | 1.0 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

**Qwen3-VL-8B-Instruct-1shot-local-dev** (R-dev, n=3 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.0 | None | 0.0 | 1.0 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

**Qwen3-VL-8B-Instruct-1shot-local-test** (R-test, n=9 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.0 | None | 0.0 | 1.0 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

**Qwen3-VL-8B-Instruct-2shot-local-dev** (R-dev, n=2 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.0 | None | 0.0 | 1.0 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

**Qwen3-VL-8B-Instruct-2shot-local-test** (R-test, n=9 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.0 | None | 0.0 | 1.0 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

**Qwen3.5-9B-0shot-none-dev** (R-dev, n=4 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.0 | None | 0.0 | 1.0 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

**Qwen3.5-9B-0shot-none-test** (R-test, n=9 sheets)

| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | human review share |
|---|---|---|---|---|
| 0.5 | 0.1111 | 1.0 | 0.1111 | 0.8889 |
| 0.6 | 0.0 | None | 0.0 | 1.0 |
| 0.7 | 0.0 | None | 0.0 | 1.0 |
| 0.8 | 0.0 | None | 0.0 | 1.0 |
| 0.85 | 0.0 | None | 0.0 | 1.0 |
| 0.9 | 0.0 | None | 0.0 | 1.0 |
| 0.95 | 0.0 | None | 0.0 | 1.0 |
| 0.98 | 0.0 | None | 0.0 | 1.0 |
| 0.99 | 0.0 | None | 0.0 | 1.0 |

## Discrepancy detection on injected defects (simulated records)

Dataset: simulated records (numbers only), n = 3500 records (500 per type). Defects are injected into the numbers, never into images.

| defect | n | failed a hard rule | discrepancy opened (no consistent reading) | sent to human review |
|---|---|---|---|---|
| none | 500 | 0.0% | 0.0% | 0.0% |
| tampered_digit | 500 | 100.0% | 27.4% | 100.0% |
| sum_mismatch_entry | 500 | 100.0% | 90.4% | 100.0% |
| overvote | 500 | 100.0% | 51.2% | 100.0% |
| illegible | 500 | 43.0% | 0.0% | 100.0% |
| low_confidence | 500 | 0.0% | 0.0% | 100.0% |
| close_alternative | 500 | 0.0% | 0.0% | 100.0% |

Precision of the rule flag (planted arithmetic defects vs all flagged, illegible readings counted as false flags): 87.5%.
**Limits:** number-level injection does not test whether the reader sees a physically altered digit; a forgery that keeps every total consistent is not caught by arithmetic. Where a tampered digit opens no discrepancy, the decoder found a plausible alternative reading and offers it only as a suggestion; the sheet still goes to a person.

## Verification simulation (simulated unit, real status rules)

| scenario | without an observer | with one honest observer |
|---|---|---|
| attackers needed to mark a correct sheet disputed | 2 | 2 |
| attackers needed to verify a wrong sheet | 3 | None |
| status of the wrong sheet after 10 attackers | VERIFIED | DISPUTED |
| burst flag raised | True | True |

Disputes only send a sheet to human review; attackers cannot change figures. Citizen registration is self-declared, so this is a signal, not proof.

## USSD

- Check my unit: **1 input** (target ≤ 3). Verify and confirm: **5 inputs** on a sheet with 10 lines (target ≤ 8).
- 342 reachable screens walked in English and Pidgin: 100% within 160 characters (longest 157).
- Limits: no images over USSD (figures only), session timeouts, a cost per session, and a telecom aggregator, shortcode and regulatory approval are needed; none is connected in the PoC.

| channel | read figures | confirm / dispute | see the image | needs data / smartphone |
|---|---|---|---|---|
| Web | yes | yes, with per-field corrections | yes (redacted) | yes |
| USSD | yes (≤ 160 chars per screen) | yes, one correction per report | no | no |
| SMS (stub, nothing sent) | no figures, only "your unit's result is ready" | no | no | no |

## Geographic view

National SVG: 37 regions, 63 KB (20 KB gzipped; budget 300 KB), median render 1.8 ms. Layer values are checked against hand-computed fixtures in the test suite.

## Trust indicators and a pilot design

The PoC measures nothing about trust or turnout. It builds the features a pilot would test: the per-sheet journey timeline, report references with public status and reason codes, and the explainer. A real pilot would need an independent evaluator, informed consent, comparison wards without the tool, and pre-registered indicators: share of reports answered, median time to resolution, verification coverage by ward, and an optional anonymous before/after confidence question (shown only where at least 20 responses exist). It must never target or nudge anyone to vote.

