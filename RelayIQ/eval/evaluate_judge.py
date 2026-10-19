"""Evaluate judge quality against curated or synthetic datasets.

Usage:
  python eval/evaluate_judge.py
    python eval/evaluate_judge.py --suite synthetic --force-fallback
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.placement import PlacementEngine

CHALLENGE_DATASET = Path("eval/data/judge_challenge.jsonl")
SYNTHETIC_DATASET = Path("eval/data/judge_synth_1000.jsonl")
REPORT_MD = Path("eval/reports/judge_eval_latest.md")
REPORT_JSON = Path("eval/reports/judge_eval_latest.json")


def load_dataset(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def accuracy(pred: list, exp: list) -> float:
    if not exp:
        return 0.0
    ok = sum(1 for p, e in zip(pred, exp) if p == e)
    return ok / len(exp)


def classification_metrics(pred: list, exp: list) -> dict:
    labels = sorted(set(pred) | set(exp), key=str)
    per_label = {}
    for label in labels:
        true_positive = sum(1 for p, e in zip(pred, exp) if p == label and e == label)
        false_positive = sum(1 for p, e in zip(pred, exp) if p == label and e != label)
        false_negative = sum(1 for p, e in zip(pred, exp) if p != label and e == label)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[str(label).lower()] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": sum(1 for expected in exp if expected == label),
        }

    return {
        "accuracy": round(accuracy(pred, exp), 4),
        "macro_precision": round(mean(item["precision"] for item in per_label.values()), 4),
        "macro_recall": round(mean(item["recall"] for item in per_label.values()), 4),
        "macro_f1": round(mean(item["f1"] for item in per_label.values()), 4),
        "per_label": per_label,
    }


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "mean": round(mean(values), 3),
        "p50": round(percentile(values, 0.5), 3),
        "p95": round(percentile(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("challenge", "synthetic"), default="challenge")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--force-fallback", action="store_true")
    args = parser.parse_args()

    dataset = args.dataset or (CHALLENGE_DATASET if args.suite == "challenge" else SYNTHETIC_DATASET)
    suite = "custom" if args.dataset else args.suite
    rows = load_dataset(dataset)
    if args.max_cases > 0:
        rows = rows[: args.max_cases]

    preds = defaultdict(list)
    exps = defaultdict(list)
    urgency_matrix = Counter()
    critical_misses = []
    unnecessary_cloud = []
    missed_cloud = []
    placement_latencies_ms = []
    evaluated_rows = []
    decision_failures = []
    fallback_decisions = 0
    engine = PlacementEngine()

    for row in rows:
        expected = row["expected"]
        try:
            decision = engine.decide(row["prompt"], force_fallback=args.force_fallback)
        except Exception as exc:
            decision_failures.append({"id": row["id"], "error": type(exc).__name__})
            continue
        predicted = decision.verdict
        evaluated_rows.append(row)
        placement_latencies_ms.append(decision.timings_ms["total"])
        if predicted.get("judge_mode") == "fallback":
            fallback_decisions += 1

        for field in ("urgency", "complexity", "needs_fresh", "route", "tier"):
            preds[field].append(predicted[field])
            exps[field].append(expected[field])

        urgency_matrix[(expected["urgency"], predicted["urgency"])] += 1
        if expected["urgency"] == "critical" and predicted["urgency"] != "critical":
            critical_misses.append({
                "id": row["id"],
                "prompt": row["prompt"],
                "predicted_urgency": predicted["urgency"],
            })
        if expected["route"] == "local" and predicted["route"] == "cloud":
            unnecessary_cloud.append(row["id"])
        if expected["route"] == "cloud" and predicted["route"] == "local":
            missed_cloud.append(row["id"])

    metrics = {
        field: classification_metrics(preds[field], exps[field])
        for field in preds.keys()
    }
    topline_accuracy = {field: result["accuracy"] for field, result in metrics.items()}

    by_combo = Counter()
    by_combo_ok = Counter()
    for row, p_u, p_c, p_f in zip(evaluated_rows, preds["urgency"], preds["complexity"], preds["needs_fresh"]):
        e = row["expected"]
        combo = (e["urgency"], e["complexity"], e["needs_fresh"])
        by_combo[combo] += 1
        if (p_u, p_c, p_f) == combo:
            by_combo_ok[combo] += 1

    combo_acc = {
        f"{u}|{c}|{f}": round(by_combo_ok[(u, c, f)] / by_combo[(u, c, f)], 4)
        for (u, c, f) in sorted(by_combo.keys())
    }

    critical_count = sum(1 for row in evaluated_rows if row["expected"]["urgency"] == "critical")
    local_count = sum(1 for row in evaluated_rows if row["expected"]["route"] == "local")
    cloud_count = len(evaluated_rows) - local_count
    decision_success_rate = len(evaluated_rows) / len(rows) if rows else 0.0
    fallback_decision_rate = fallback_decisions / len(evaluated_rows) if evaluated_rows else 0.0
    report = {
        "cases": len(rows),
        "evaluated_cases": len(evaluated_rows),
        "dataset": dataset.as_posix(),
        "suite": suite,
        "interpretation": (
            "Mixed candidate-label benchmark: 36 hand-authored adversarial seeds plus 164 seeded matrix cases; independent review pending"
            if suite == "challenge"
            else "Synthetic routing-matrix conformance only; not an independent quality claim"
        ),
        "force_fallback": args.force_fallback,
        "metrics": metrics,
        "placement_latency_ms": latency_summary(placement_latencies_ms),
        "availability": {
            "decision_success_rate": round(decision_success_rate, 4),
            "fallback_decision_rate": round(fallback_decision_rate, 4),
            "failures": decision_failures,
        },
        "critical_miss_count": len(critical_misses),
        "critical_miss_rate": round(len(critical_misses) / critical_count, 4) if critical_count else 0,
        "critical_misses": critical_misses,
        "unnecessary_cloud_count": len(unnecessary_cloud),
        "unnecessary_cloud_rate": round(len(unnecessary_cloud) / local_count, 4) if local_count else 0,
        "unnecessary_cloud_ids": unnecessary_cloud,
        "missed_cloud_count": len(missed_cloud),
        "missed_cloud_rate": round(len(missed_cloud) / cloud_count, 4) if cloud_count else 0,
        "missed_cloud_ids": missed_cloud,
        "matrix_accuracy": combo_acc,
        "urgency_confusion": {
            f"{e}->{p}": count for (e, p), count in sorted(urgency_matrix.items())
        },
    }

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "---",
        "title: Judge evaluation report",
        "description: Latest deterministic fallback evaluation and safety-critical misses.",
        f"ms.date: {date.today().isoformat()}",
        "ms.topic: reference",
        "---",
        "",
        "## Run Summary",
        "",
        f"* Cases: {len(rows)}",
        f"* Dataset: {dataset.as_posix()}",
        f"* Suite: {suite}",
        f"* Interpretation: {report['interpretation']}",
        f"* Mode: {'fallback-heuristic' if args.force_fallback else 'model-backed'}",
        f"* Decision success rate: {decision_success_rate:.2%} ({len(evaluated_rows)}/{len(rows)})",
        f"* Fallback decision rate: {fallback_decision_rate:.2%} ({fallback_decisions}/{len(evaluated_rows)})",
        "",
        "This report evaluates deterministic fallback behavior. It does not measure",
        "Foundry Local Judge model quality or model inference latency.",
        "",
        "## Topline Accuracy",
        "",
        f"* urgency: {topline_accuracy['urgency']:.2%}",
        f"* complexity: {topline_accuracy['complexity']:.2%}",
        f"* needs_fresh: {topline_accuracy['needs_fresh']:.2%}",
        f"* route: {topline_accuracy['route']:.2%}",
        f"* tier: {topline_accuracy['tier']:.2%}",
        f"* critical urgency miss rate: {report['critical_miss_rate']:.2%} ({len(critical_misses)}/{critical_count})",
        f"* unnecessary cloud rate: {report['unnecessary_cloud_rate']:.2%} ({len(unnecessary_cloud)}/{local_count})",
        f"* missed cloud rate: {report['missed_cloud_rate']:.2%} ({len(missed_cloud)}/{cloud_count})",
        "",
        "## Precision, Recall, and F1",
        "",
    ]
    for field, result in metrics.items():
        lines.append(
            f"* {field}: macro precision {result['macro_precision']:.2%}, "
            f"macro recall {result['macro_recall']:.2%}, macro F1 {result['macro_f1']:.2%}"
        )

    latency = report["placement_latency_ms"]
    lines += [
        "",
        "## Placement Latency",
        "",
        f"* mean: {latency['mean']:.3f} ms",
        f"* p50: {latency['p50']:.3f} ms",
        f"* p95: {latency['p95']:.3f} ms",
        f"* max: {latency['max']:.3f} ms",
        "",
        "## Exact Matrix Conformance",
        "",
        "Each slice requires urgency, complexity, freshness, route, and tier to all",
        "match the candidate labels. A zero here is not a critical-urgency miss.",
        "",
    ]
    for key, val in combo_acc.items():
        lines.append(f"* {key}: {val:.2%}")

    lines += ["", "## Urgency Confusion (expected->predicted)", ""]
    for k, count in sorted(report["urgency_confusion"].items()):
        lines.append(f"* {k}: {count}")

    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Saved {REPORT_JSON}")
    print(f"Saved {REPORT_MD}")
    print("Topline:", topline_accuracy)
    print("Placement latency (ms):", report["placement_latency_ms"])


if __name__ == "__main__":
    main()
