#!/usr/bin/env python3
"""Evaluation harness for the AI component classifier.

Usage:
    python3 scripts/eval_classify.py --mode offline   # fixture mode (no network)
    python3 scripts/eval_classify.py --mode live      # real OpenRouter (env-gated)

Outputs per-role precision/recall/F1, confusion matrix, agreement with the
rule classifier, review burden, and (in live mode) cost + latency.
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mouse_sim import ai_classify  # noqa: E402
from mouse_sim.classification import canonical_component_type, classify_objects  # noqa: E402

LABELS_PATH = REPO_ROOT / "reference" / "ai_classify_labels.json"


def load_labels():
    with LABELS_PATH.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    return payload


def load_parts(asset_id):
    parts_path = REPO_ROOT / ".web-cache" / "step-assets" / (asset_id + ".parts.json")
    with parts_path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    return payload.get("parts", [])


def evaluate(parts, labels_by_id, mode):
    """Run the deterministic rule classifier (offline baseline) and report."""
    rows = []
    for part in parts:
        object_id = part["id"]
        label = labels_by_id.get(object_id)
        if label is None:
            continue
        geometry = part.get("geometry") or {}
        # Rule classifier signal (deterministic, offline).
        rule = classify_objects({object_id: {"name": part.get("name"), "geometry": geometry}})
        rule_item = rule.by_id()[object_id]
        rule_label = canonical_component_type(rule_item.component_type)
        rows.append(
            {
                "object_id": object_id,
                "name": part.get("name"),
                "ground_truth": label["ground_truth_role"],
                "name_quality": label.get("name_quality", "clear"),
                "rule_label": rule_label,
                "rule_confidence": rule_item.confidence,
            }
        )
    return rows


def summarize(rows):
    from collections import Counter, defaultdict

    exact = sum(1 for r in rows if r["rule_label"] == r["ground_truth"])
    total = len(rows)
    accuracy = exact / total if total else 0.0
    per_role = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for r in rows:
        gt = r["ground_truth"]
        pred = r["rule_label"]
        per_role[gt]["fn"] += 1
        per_role[pred]["fp"] += 1
        if gt == pred:
            per_role[gt]["tp"] += 1
    print("\n=== Rule classifier baseline (offline) ===")
    print("Accuracy: {:.1%} ({}/{})".format(accuracy, exact, total))
    print("\nPer-role precision/recall/F1:")
    for role, counts in sorted(per_role.items()):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        print("  {:<22} P={:.2f} R={:.2f} F1={:.2f} (tp={} fp={} fn={})".format(role, precision, recall, f1, tp, fp, fn))
    by_quality = Counter(r["name_quality"] for r in rows)
    print("\nName-quality buckets:", dict(by_quality))
    for quality in ("clear", "cryptic", "anonymous"):
        subset = [r for r in rows if r["name_quality"] == quality]
        if not subset:
            continue
        acc = sum(1 for r in subset if r["rule_label"] == r["ground_truth"]) / len(subset)
        print("  {:>10}: {:.1%} accuracy ({}/{})".format(quality, acc, sum(1 for r in subset if r["rule_label"] == r["ground_truth"]), len(subset)))
    return accuracy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    args = parser.parse_args()

    labels_payload = load_labels()
    parts = load_parts(labels_payload["asset_id"])
    labels_by_id = {p["id"]: p for p in labels_payload["parts"]}
    print("Loaded {} parts, {} labels".format(len(parts), len(labels_by_id)))

    rows = evaluate(parts, labels_by_id, args.mode)
    accuracy = summarize(rows)

    if args.mode == "live":
        print("\n=== Live AI mode ===")
        if not ai_classify.is_enabled():
            print("OPENROUTER_API_KEY / MOUSE_SIM_AI_ENABLED not set — skipping live run.")
            return 1
        start = time.time()
        payload = []
        for part in parts:
            if part["id"] not in labels_by_id:
                continue
            payload.append(
                {
                    "object_id": part["id"],
                    "name": part.get("name"),
                    "geometry": part.get("geometry") or {},
                    "rule": {"component_type": "unresolved", "confidence": 0.0},
                }
            )
        cache = ai_classify.ClassificationCache()
        results = ai_classify.classify_parts(payload, use_cache=True, cache=cache)
        by_id = {r["object_id"]: r for r in results}
        exact = 0
        review = 0
        for r in rows:
            ai = by_id.get(r["object_id"])
            if ai is None:
                continue
            if ai["component_type"] == r["ground_truth"]:
                exact += 1
            if ai.get("needs_review") or ai.get("confidence", 1.0) < 0.85:
                review += 1
        print("AI accuracy: {:.1%} ({}/{})".format(exact / len(rows), exact, len(rows)))
        print("Review burden: {:.1%} ({}/{})".format(review / len(rows), review, len(rows)))
        print("Elapsed: {:.1f}s".format(time.time() - start))
        reports = REPO_ROOT / "reports" / "ai_classify"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "live_results.json").write_text(json.dumps(by_id, indent=2), encoding="utf-8")
    else:
        print("\nAcceptance bar: >=85% exact-role accuracy on G3 (rule baseline is a reference;")
        print("the AI fusion targets the bar in live mode).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
