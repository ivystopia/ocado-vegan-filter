#!/usr/bin/env python3
"""Reconcile a frozen Unknown-review cohort, raw attempts and proposed additions."""
import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import published_baseline as baseline
import review_current_unknowns as review


def report(root, output):
    manifest = json.loads((root / "manifest.json").read_text())
    with closing(sqlite3.connect(f'file:{root / "review.sqlite"}?mode=ro', uri=True)) as conn:
        rows = conn.execute(
            "SELECT id,context_json,context_hash,result_json FROM products ORDER BY id"
        ).fetchall()
    contexts = {pid: json.loads(raw) for pid, raw, _, _ in rows}
    calls = {}
    admissions = {}
    for path in (root / "passes").glob("*/*.attempts.jsonl"):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record["call_id"] in calls:
                raise ValueError("Duplicate attempt record")
            calls[record["call_id"]] = record
    for path in (root / "passes").glob("*/*.admissions.jsonl"):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            admissions[record["call_id"]] = record
    unresolved = sorted(set(admissions) - set(calls))
    by_model = defaultdict(Counter)
    for record in calls.values():
        group = by_model[f'{record["model"]}/{record["reasoning_effort"]}']
        group["attempts"] += 1
        group["invoked"] += bool(record.get("invocation_started"))
        group["failed_attempts"] += bool(record.get("error"))
        group["missing_usage"] += record.get("invocation_started", True) and record.get("usage") is None
        for key, value in (record.get("usage") or {}).items():
            group[key] += value
        if record.get("parsed_response"):
            subset = [contexts[pid] for pid in record["product_ids"]]
            binding = baseline.digest(
                {
                    "version": review.VERSION,
                    "prompt": review.PROMPT + baseline.encoded(subset),
                    "model": record["model"],
                    "effort": record["reasoning_effort"],
                }
            )
            if binding != record["context_hash"]:
                raise ValueError("Attempt input binding mismatch")
    outcomes = Counter()
    reasons = Counter()
    completed = 0
    arbitrated = 0
    verified_readings = 0
    pending = []
    candidates = []
    negative_tags = []
    for pid, raw, hashed, result_raw in rows:
        if baseline.digest(contexts[pid]) != hashed:
            raise ValueError("Frozen context hash mismatch")
        if result_raw is None:
            pending.append(pid)
            continue
        result = json.loads(result_raw)
        if len(result["primary"]) != 2:
            raise ValueError("Invalid primary count")
        disagreement = len({(d["status"], d["reason"]) for d in result["primary"]}) != 1
        if disagreement != bool(result["arbitration"]):
            raise ValueError("Invalid arbitration admission")
        if disagreement and len(result["arbitration"]) != 2:
            raise ValueError("Invalid arbitration count")
        stage_readings = list(zip(("primary-1", "primary-2"), result["primary"]))
        if disagreement:
            stage_readings += list(zip(("arbitration-1", "arbitration-2"), result["arbitration"]))
        for stage, reading in stage_readings:
            grounded = []
            for call_id in result["call_ids"]:
                record = calls[call_id]
                if record["stage"] != stage or pid not in record["product_ids"]:
                    continue
                for d in (record.get("parsed_response") or {}).get("decisions", []):
                    if d.get("product_id") != pid:
                        continue
                    try:
                        normalized = review.validate_payload({"decisions": [d]}, [contexts[pid]])[pid]
                    except (ValueError, TypeError):
                        continue
                    if normalized == reading and record.get("exit_status") == 0 and record.get("usage"):
                        grounded.append(call_id)
            if not grounded:
                raise ValueError(f"Untraceable fresh reading: {pid} {stage}")
            verified_readings += 1
        if result["decision"] != review.merge(result["primary"], result["arbitration"]):
            raise ValueError("Changed merge")
        decision = result["decision"]
        completed += 1
        arbitrated += disagreement
        outcomes[decision["status"]] += 1
        product = contexts[pid]["product"]
        summary = [
            pid,
            product["name"],
            decision["reason"],
            decision["rationale"],
            f"https://www.ocado.com/products/{pid}",
        ]
        if decision["status"] == "vegan":
            candidates.append(summary)
            reasons[decision["reason"]] += 1
        if decision["status"] == "nonvegan" and (
            product["official_vegan"] == 1 or "vegan" in contexts[pid]["flags"]
        ):
            negative_tags.append(summary)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in [("vegan-candidates.csv", candidates), ("ocado-tag-contradictions.csv", negative_tags)]:
        with (output / name).open("w", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["product_id", "name", "reason", "rationale", "url"])
            writer.writerows(data)
    result = {
        "cohort": len(rows),
        "reviewed": completed,
        "remaining": len(pending),
        "pending_ids": pending,
        "cohort_hash": manifest["cohort_hash"],
        "model_outcomes": dict(outcomes),
        "candidate_reasons": dict(reasons),
        "arbitrated_products": arbitrated,
        "traceable_fresh_readings": verified_readings,
        "calls_by_model": {k: dict(v) for k, v in by_model.items()},
        "unfinished_admissions": unresolved,
        "complete": completed == len(rows)
        and not unresolved
        and not any(v["missing_usage"] for v in by_model.values()),
        "method": "Fresh independent readings of frozen SQLite source evidence; exact references and call provenance validated. This is not a fresh web scrape or independent external ground truth.",
    }
    review.write_json(output / "accounting.json", result)
    print(baseline.encoded({k: v for k, v in result.items() if k != "pending_ids"}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report(args.root, args.output)
