"""Evaluate the reader on labelled real sheets (CLAUDE.md 10). Runs locally; publishes aggregates only.

  python scripts/evaluate.py run --split dev [--n-shots 0] [--exemplar-config local] [--model ...] [--name RUN]
  python scripts/evaluate.py calibrate --name RUN     # fit temperature on an R-dev run
  python scripts/evaluate.py report                   # results/metrics.json + results/REPORT.md

Readings are cached per run under data_real/runs/<RUN>/ (validated JSON only), so runs resume.
Few-shot runs never score their own exemplar sheets.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import DATA_REAL, DATA_REDACTED, ROOT  # noqa: E402
from naijawatchtower.eval.calibration import CALIBRATION_FILE, fit_temperature  # noqa: E402
from naijawatchtower.eval.labelling import ILLEGIBLE, LabelStore  # noqa: E402
from naijawatchtower.eval.metrics import (calibration_metrics, compare, reader_metrics,  # noqa: E402
                                          system_metrics)
from naijawatchtower.ocr.cache_reader import CacheReader, store_reading  # noqa: E402
from naijawatchtower.ocr.vlm_reader import Exemplar, load_vlm_config  # noqa: E402

RUNS = DATA_REAL / "runs"
RESULTS = ROOT / "results"
SWEEP = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99]
CAVEATS = """> **Read these caveats before any number below.**
> - The real sample is small (n is stated beside every figure), so confidence intervals are wide and the
>   figures do not generalise to all sheets, states or elections.
> - Labels were typed from the sheet by one person (Matthias). {reconciliation}
> - Most portal images are 1024-pixel-wide thumbnails; full-size images may read differently.
> - Discrepancy detection is measured on number-level injections and simulated records, not on physically
>   altered sheets, and arithmetic checks cannot catch a forgery that keeps all totals consistent.
> - The arithmetic rules have been checked on a small real sample only.
> - This proof of concept shows nothing about trust or turnout.
"""


def load_splits() -> dict:
    path = DATA_REAL / "splits.json"
    if not path.exists():
        sys.exit("no data_real/splits.json: label sheets, then run `make split`")
    return json.loads(path.read_text())


def exemplars_for(ids: list[str], config: str, store: LabelStore) -> list[Exemplar]:
    out = []
    for sid in ids:
        labels = store.labels[sid]
        if ILLEGIBLE in labels.values():
            sys.exit(f"{sid} has illegible labels; pick another exemplar")
        image = (store.images_dir / store.sheets[sid]["file"] if config == "local"
                 else DATA_REDACTED / f"{sid}.jpg")
        out.append(Exemplar(image, dict(labels), []))  # type: ignore[arg-type]
    return out


def cmd_run(args) -> None:
    from naijawatchtower.ocr.vlm_reader import VLMReader
    store, splits = LabelStore.load(DATA_REAL), load_splits()
    cfg = load_vlm_config({k: v for k, v in {"model_id": args.model, "max_pixels": args.max_pixels,
                                            "quantization": args.quantization}.items() if v is not None})
    n_shots = cfg["n_shots"] if args.n_shots is None else args.n_shots
    ex_config = args.exemplar_config or cfg.get("exemplar_config", "local")
    ex_ids = (args.exemplars.split(",") if args.exemplars else cfg.get("exemplars", []))[:n_shots]
    if n_shots and len(ex_ids) < n_shots:
        sys.exit(f"need {n_shots} exemplar ids (from R-dev): --exemplars R00a,R00b")
    if any(e not in splits["dev"] for e in ex_ids):
        sys.exit("exemplars must come from R-dev")
    sheets = [s for s in (splits["dev"] + splits["test"] if args.split == "all" else splits[args.split])
              if s not in ex_ids]
    name = args.name or f"{cfg['model_id'].split('/')[-1]}-{n_shots}shot-{ex_config if n_shots else 'none'}-{args.split}"
    run_dir = RUNS / name
    meta = {"model_id": cfg["model_id"], "quantization": cfg["quantization"], "max_pixels": cfg["max_pixels"],
            "n_shots": n_shots, "exemplar_config": ex_config if n_shots else "none", "exemplars": ex_ids,
            "split": args.split, "sheets": sheets,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cache = CacheReader([run_dir])
    todo = [s for s in sheets if cache.path_for(store.images_dir / store.sheets[s]["file"]) is None]
    print(f"run {name}: {len(sheets)} sheets, {len(todo)} to read")
    if todo:
        reader = VLMReader(cfg, exemplars_for(ex_ids, ex_config, store))
        meta["model_revision"], meta["prompt_version"] = reader.model_revision, reader.prompt_version
        for i, sid in enumerate(todo, 1):
            image = store.images_dir / store.sheets[sid]["file"]
            r = reader.read(image)
            store_reading(image, r, run_dir)
            print(f"  [{i}/{len(todo)}] {sid}: {r.seconds:.1f}s valid={r.json_valid}")
    old = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    (run_dir / "meta.json").write_text(json.dumps({**old, **meta}, indent=1))


def evals_for(name: str, store: LabelStore):
    run_dir = RUNS / name
    meta = json.loads((run_dir / "meta.json").read_text())
    cache = CacheReader([run_dir])
    evals = []
    for sid in meta["sheets"]:
        image = store.images_dir / store.sheets[sid]["file"]
        if cache.path_for(image) is not None:
            evals.append(compare(cache.read(image), store.labels[sid]))
    return meta, evals


def cmd_calibrate(args) -> None:
    store = LabelStore.load(DATA_REAL)
    meta, evals = evals_for(args.name, store)
    if meta["split"] != "dev":
        sys.exit("calibrate on an R-dev run only")
    confs = [c for e in evals for c in e.confs]
    correct = [c for e in evals for c in e.correct]
    t = fit_temperature(confs, correct)
    key = f"{evals[0].reading.model_revision}|{evals[0].reading.prompt_version}"
    data = json.loads(CALIBRATION_FILE.read_text()) if CALIBRATION_FILE.exists() else {}
    data[key] = {"temperature": t, "fitted_on": f"R-dev run {args.name}", "n_fields": len(confs),
                 **calibration_metrics(evals, t)}
    CALIBRATION_FILE.write_text(json.dumps(data, indent=1))
    print(json.dumps(data[key], indent=1))


def cmd_report(args) -> None:
    from naijawatchtower.eval.calibration import load_temperature
    store = LabelStore.load(DATA_REAL)
    metrics: dict = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "runs": {},
                     "labels": {"n_labelled": len(store.labels),
                                "n_reconciled": sum(1 for s in store.labels if store.sheets[s].get("reconciled_at"))}}
    for run_dir in sorted(p for p in RUNS.iterdir() if (p / "meta.json").exists() and p.name != "smoke"):
        meta, evals = evals_for(run_dir.name, store)
        if not evals:
            continue
        t = load_temperature(evals[0].reading.model_revision, evals[0].reading.prompt_version)
        dataset = {"dev": "R-dev", "test": "R-test", "all": "R-dev+R-test"}[meta["split"]]
        metrics["runs"][run_dir.name] = {
            "dataset": dataset, "model": meta.get("model_revision", meta["model_id"]),
            "prompt": meta.get("prompt_version"), "n_shots": meta["n_shots"],
            "exemplar_config": meta["exemplar_config"],
            "reader": reader_metrics(evals), "calibration": calibration_metrics(evals, t),
            "system": system_metrics(evals, t, SWEEP)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "metrics.json").write_text(json.dumps(metrics, indent=1))
    system_path = RESULTS / "system_metrics.json"
    system = json.loads(system_path.read_text()) if system_path.exists() else None
    (RESULTS / "REPORT.md").write_text(render_report(metrics) + (render_system(system) if system else ""))
    print(f"wrote {RESULTS / 'metrics.json'} and {RESULTS / 'REPORT.md'} ({len(metrics['runs'])} runs)")


def fmt(m: dict) -> str:
    return f"{m['value']:.3f} [{m['ci_low']:.3f}, {m['ci_high']:.3f}]"


def reconciliation_text(labels: dict) -> str:
    n, r = labels["n_labelled"], labels["n_reconciled"]
    if r == n:
        return ("Every field where the model (Qwen3-VL-8B, zero-shot) read a different value was then re-checked\n"
                ">   against the image.")
    return (f"**{n - r} of {n} sheets have not yet been reconciled** against the model's readings, so the labels may\n"
            ">   still contain typing errors, and for some sheets whether an unlisted party row is empty or a written 0\n"
            ">   is still being checked (see the blank = 0 column).")


def selection_text(metrics: dict) -> str:
    dev = {k: v for k, v in metrics["runs"].items() if v["dataset"] == "R-dev"}
    if not dev:
        return ""
    best = max(dev, key=lambda k: dev[k]["reader"]["field_accuracy"]["value"])
    nb = dev[best]["reader"]["nonblank_field_accuracy"]["value"]
    return ("## Model and prompt selection\n\n"
            f"Chosen on R-dev only (field accuracy, then speed): **{best.removesuffix('-dev')}**. R-test was not used "
            f"for any choice. R-dev holds only {dev[best]['reader']['n_sheets']} sheets (fewer for few-shot runs, whose "
            "exemplars are excluded), so this choice is fragile; other configurations scored higher on R-test, which "
            "is reported but must not drive the choice.\n\n"
            f"**M1 threshold not met:** non-blank field accuracy on R-dev is {nb:.1%} against a target of 80%. The "
            "reader is not good enough to run unattended; the system therefore sends every uncertain or "
            "rule-breaking sheet to a person (see risk-coverage below: at the default threshold no sheet is "
            "auto-accepted).\n\n")


def render_report(metrics: dict) -> str:
    lines = ["# NaijaWatchTower evaluation report", "",
             CAVEATS.replace("{reconciliation}", reconciliation_text(metrics["labels"])),
             selection_text(metrics),
             f"Generated {metrics['generated']}. All figures are aggregates over real sheets labelled by hand; "
             "no per-sheet results are published. 95% bootstrap intervals over sheets in brackets.", "",
             "## Reader comparison", "",
             "| run | dataset | n sheets | n fields | field acc. | non-blank field acc. | field acc., blank = 0 | "
             "sheet exact | JSON valid | s/sheet (median, p95) | peak VRAM MB |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in metrics["runs"].items():
        m = r["reader"]
        lines.append(f"| {name} | {r['dataset']} | {m['n_sheets']} | {m['n_fields_scored']} | "
                     f"{fmt(m['field_accuracy'])} | {fmt(m['nonblank_field_accuracy'])} "
                     f"(n={m['n_fields_nonblank']}) | {fmt(m['value_equivalent_field_accuracy'])} | "
                     f"{fmt(m['sheet_exact_match'])} | {m['json_valid_rate']} | "
                     f"{m['seconds_median']}, {m['seconds_p95']} | {m['vram_peak_mb']} |")
    lines += ["", "*Field acc., blank = 0* counts an empty cell and a written 0 as the same value, since both "
              "count as 0 in every sum; it shows how much of the error is the blank-versus-zero convention."]
    lines += ["", "## Calibration (temperature scaling, fitted on R-dev)", "",
              "| run | dataset | n fields | T | ECE before | ECE after |", "|---|---|---|---|---|---|"]
    for name, r in metrics["runs"].items():
        c = r["calibration"]
        lines.append(f"| {name} | {r['dataset']} | {c['n_fields']} | {c['temperature']} | "
                     f"{c['ece_before']} | {c['ece_after']} |")
    lines += ["", "## Constraint-aware decoding and routing", "",
              "| run | dataset | sheet exact (top-1) | sheet exact (suggestions accepted) | suggestions | "
              "fully correct suggestions |", "|---|---|---|---|---|---|"]
    for name, r in metrics["runs"].items():
        s = r["system"]
        lines.append(f"| {name} | {r['dataset']} | {s['sheet_exact_top1']} | "
                     f"{s['sheet_exact_if_suggestions_accepted']} | {s['repair_suggestions']} | "
                     f"{s['repair_suggestions_fully_correct']} |")
    lines += ["", "### Risk-coverage (calibrated confidence threshold sweep)", ""]
    for name, r in metrics["runs"].items():
        lines += [f"**{name}** ({r['dataset']}, n={r['reader']['n_sheets']} sheets)", "",
                  "| field_conf_min | auto-routed share | error rate among auto-routed | wrong-but-accepted | "
                  "human review share |", "|---|---|---|---|---|"]
        for row in r["system"]["risk_coverage"]:
            lines.append(f"| {row['field_conf_min']} | {row['auto_ok_share']} | {row['error_rate_among_auto_ok']} | "
                         f"{row['wrong_but_accepted_share']} | {row['human_review_share']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_system(s: dict) -> str:
    """Report sections from scripts/evaluate_system.py (no real labels needed)."""
    d = s["discrepancy_detection_simulated"]
    lines = ["## Discrepancy detection on injected defects (simulated records)", "",
             f"Dataset: {d['dataset']}, n = {d['n_records']} records ({d['n_records'] // 7} per type). "
             "Defects are injected into the numbers, never into images.", "",
             "| defect | n | failed a hard rule | discrepancy opened (no consistent reading) | sent to human review |",
             "|---|---|---|---|---|"]
    for defect, r in d["per_defect"].items():
        lines.append(f"| {defect} | {r['n']} | {r['flagged_by_rules']:.1%} | {r['discrepancy_opened']:.1%} | "
                     f"{r['sent_to_human_review']:.1%} |")
    lines += ["", f"Precision of the rule flag (planted arithmetic defects vs all flagged, illegible readings counted "
              f"as false flags): {d['rule_flag_precision']:.1%}.",
              "**Limits:** number-level injection does not test whether the reader sees a physically altered digit; "
              "a forgery that keeps every total consistent is not caught by arithmetic. Where a tampered digit opens "
              "no discrepancy, the decoder found a plausible alternative reading and offers it only as a suggestion; "
              "the sheet still goes to a person.", "",
              "## Verification simulation (simulated unit, real status rules)", "",
              "| scenario | without an observer | with one honest observer |", "|---|---|---|"]
    sy = s["sybil_simulation"]
    for key, label in [("attackers_to_mark_correct_sheet_disputed", "attackers needed to mark a correct sheet disputed"),
                       ("attackers_to_verify_wrong_sheet", "attackers needed to verify a wrong sheet"),
                       ("final_status_wrong_sheet_after_10_attackers", "status of the wrong sheet after 10 attackers"),
                       ("burst_flag_raised_within_10_attackers", "burst flag raised")]:
        lines.append(f"| {label} | {sy['without_observer'][key]} | {sy['with_observer'][key]} |")
    u, g = s["ussd"], s["geo"]
    lines += ["", sy["note"], "", "## USSD", "",
              f"- Check my unit: **{u['inputs_check']} input** (target ≤ 3). Verify and confirm: "
              f"**{u['inputs_verify_yes']} inputs** on a sheet with {u['verify_lines_on_example_sheet']} lines "
              f"(target ≤ 8).",
              f"- {u['screens_walked']} reachable screens walked in English and Pidgin: "
              f"{u['share_screens_within_160']:.0%} within 160 characters (longest {u['max_screen_chars']}).",
              "- Limits: no images over USSD (figures only), session timeouts, a cost per session, and a telecom "
              "aggregator, shortcode and regulatory approval are needed; none is connected in the PoC.", "",
              "| channel | read figures | confirm / dispute | see the image | needs data / smartphone |",
              "|---|---|---|---|---|",
              "| Web | yes | yes, with per-field corrections | yes (redacted) | yes |",
              "| USSD | yes (≤ 160 chars per screen) | yes, one correction per report | no | no |",
              "| SMS (stub, nothing sent) | no figures, only \"your unit's result is ready\" | no | no | no |", "",
              "## Geographic view", "",
              f"National SVG: {g.get('regions')} regions, {g.get('svg_bytes', 0) / 1000:.0f} KB "
              f"({g.get('svg_gzip_bytes', 0) / 1000:.0f} KB gzipped; budget 300 KB), median render "
              f"{g.get('render_ms_median')} ms. Layer values are checked against hand-computed fixtures in the "
              "test suite.", "",
              "## Trust indicators and a pilot design", "",
              "The PoC measures nothing about trust or turnout. It builds the features a pilot would test: the "
              "per-sheet journey timeline, report references with public status and reason codes, and the "
              "explainer. A real pilot would need an independent evaluator, informed consent, comparison wards "
              "without the tool, and pre-registered indicators: share of reports answered, median time to "
              "resolution, verification coverage by ward, and an optional anonymous before/after confidence "
              "question (shown only where at least 20 responses exist). It must never target or nudge anyone to vote.", ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    run.add_argument("--n-shots", type=int)
    run.add_argument("--exemplar-config", choices=["local", "public"])
    run.add_argument("--exemplars", help="comma-separated R-dev sheet ids")
    run.add_argument("--model")
    run.add_argument("--max-pixels", type=int)
    run.add_argument("--quantization")
    run.add_argument("--name")
    cal = sub.add_parser("calibrate")
    cal.add_argument("--name", required=True)
    sub.add_parser("report")
    args = parser.parse_args()
    {"run": cmd_run, "calibrate": cmd_calibrate, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
