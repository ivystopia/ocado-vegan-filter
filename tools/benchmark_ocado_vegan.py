#!/usr/bin/env python3
"""Run the fixed, DB-backed vegan classifier regression benchmark."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import classify_ocado_vegan as classifier


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "ocado_products.sqlite"
DEFAULT_FIXTURE = REPO_ROOT / "benchmarks/vegan-classifier-v1.json"
VALID_STATUSES = {"vegan", "nonvegan", "unknown"}


def load_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    products = payload.get("products")
    if payload.get("schema_version") != 1 or not isinstance(products, list) or not products:
        raise ValueError("Benchmark fixture must contain a non-empty schema-version-1 products array")

    seen: set[str] = set()
    for product in products:
        product_id = str(product.get("product_id") or "")
        cohort = product.get("cohort")
        expected_status = product.get("expected_status")
        if not product_id.isdigit() or not isinstance(cohort, str) or not cohort:
            raise ValueError(f"Invalid benchmark product entry: {product!r}")
        if expected_status not in VALID_STATUSES:
            raise ValueError(f"{product_id}: invalid expected_status {expected_status!r}")
        if product_id in seen:
            raise ValueError(f"Duplicate benchmark product ID: {product_id}")
        seen.add(product_id)
    if payload.get("contexts_file"):
        snapshots = json.loads((path.parent / payload["contexts_file"]).read_text(encoding="utf-8"))
        contexts = snapshots.get("contexts", {})
        if set(contexts) != seen:
            raise ValueError("Frozen benchmark contexts must exactly match the fixture product IDs")
        for product_id, snapshot in contexts.items():
            context = snapshot["context"]
            if context.get("product", {}).get("id") != product_id or classifier.context_hash(context) != snapshot["sha256"]:
                raise ValueError(f"Invalid frozen benchmark context: {product_id}")
        payload["contexts"] = {product_id: row["context"] for product_id, row in contexts.items()}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Database for legacy fixtures without frozen contexts")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--model", default=classifier.DEFAULT_CODEX_MODEL)
    parser.add_argument("--reasoning-effort", default=classifier.DEFAULT_REASONING_EFFORT)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--json-output", type=Path)
    return parser


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    if "/" in args.model:
        raise ValueError("Pass the model and reasoning effort separately; model IDs must not contain '/'")
    if args.passes < 2:
        raise ValueError("The safety benchmark requires at least two independent passes")
    if args.batch_size < 1:
        raise ValueError("batch-size must be positive")

    fixture = load_fixture(args.fixture)
    products = fixture["products"]
    expected = {str(product["product_id"]): product["expected_status"] for product in products}
    cohorts: dict[str, list[str]] = defaultdict(list)
    for product in products:
        cohorts[product["cohort"]].append(str(product["product_id"]))

    db_uri = args.db.resolve().as_uri() + "?mode=ro"
    conn = None
    if not fixture.get("contexts"):
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
    decisions: dict[str, classifier.ClassificationResult] = {}
    started = time.perf_counter()
    try:
        for cohort, product_ids in cohorts.items():
            print(f"Benchmark cohort {cohort}: {len(product_ids)} products", flush=True)
            for offset in range(0, len(product_ids), args.batch_size):
                batch_ids = product_ids[offset : offset + args.batch_size]
                contexts = (
                    [fixture["contexts"][product_id] for product_id in batch_ids]
                    if fixture.get("contexts") else classifier.load_product_contexts(conn, batch_ids)
                )
                results = classifier.classify_codex_contexts(
                    contexts,
                    passes=args.passes,
                    retries=args.retries,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    codex_bin=args.codex_bin,
                )
                decisions.update((result.product_id, result) for result in results)
    finally:
        if conn is not None:
            conn.close()

    elapsed = time.perf_counter() - started
    missing = sorted(set(expected) - set(decisions), key=int)
    errors = sorted(
        product_id
        for product_id, result in decisions.items()
        if result.source == "codex_error" or result.validation_error
    )
    disagreements = sorted(
        product_id
        for product_id, result in decisions.items()
        if "independent_codex_disagreement" in result.evidence.get("ambiguity_notes", [])
    )
    mismatches = sorted(
        product_id
        for product_id, result in decisions.items()
        if result.vegan_status != expected[product_id]
    )
    false_vegan = sorted(
        product_id
        for product_id, result in decisions.items()
        if result.vegan_status == "vegan" and expected[product_id] != "vegan"
    )

    result = {
        "benchmark": fixture["name"],
        "fixture_date": fixture.get("benchmark_date"),
        "fixture_sha256": classifier.context_hash(fixture),
        "frozen_contexts": bool(fixture.get("contexts")),
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "passes": args.passes,
        "batch_size": args.batch_size,
        "duration_seconds": round(elapsed, 3),
        "product_count": len(products),
        "exact_matches": len(products) - len(mismatches) - len(missing),
        "mismatch_ids": mismatches,
        "false_vegan_ids": false_vegan,
        "disagreement_ids": disagreements,
        "error_ids": errors,
        "missing_ids": missing,
        "decisions": {
            product_id: {
                "expected_status": expected[product_id],
                "actual_status": decision.vegan_status,
                "actual_reason": decision.vegan_reason,
                "source": decision.source,
            }
            for product_id, decision in sorted(decisions.items(), key=lambda item: int(item[0]))
        },
    }
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_benchmark(args)
    print(
        "Benchmark result: "
        f"{result['exact_matches']}/{result['product_count']} exact, "
        f"false-vegan={len(result['false_vegan_ids'])}, "
        f"disagreements={len(result['disagreement_ids'])}, "
        f"errors={len(result['error_ids'])}, "
        f"duration={result['duration_seconds']:.1f}s"
    )
    if result["mismatch_ids"]:
        print("Mismatches: " + " ".join(result["mismatch_ids"]))
    if result["disagreement_ids"]:
        print("Disagreements: " + " ".join(result["disagreement_ids"]))
    if result["error_ids"] or result["missing_ids"]:
        print("Errors/missing: " + " ".join(result["error_ids"] + result["missing_ids"]))

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {args.json_output}")

    return int(bool(result["mismatch_ids"] or result["error_ids"] or result["missing_ids"]))


if __name__ == "__main__":
    raise SystemExit(main())
