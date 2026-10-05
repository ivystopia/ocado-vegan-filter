#!/usr/bin/env python3
"""Replay frozen historical Luna disagreements through live Sol candidate arbitration only."""
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[2] / "tools"))
import classify_ocado_vegan as classifier

# Candidate override in this replay process only; never writes catalogue/defaults.
classifier.ARBITRATION_MODEL = "gpt-6.1-sol"


def main():
    path = ROOT / "result.json"
    if path.exists():
        raise RuntimeError("Preserve the existing result; run a copy in a new directory")
    cohort = json.loads((ROOT / "fixture.json").read_text())
    original = classifier.classify_codex_contexts
    results = []
    started = time.perf_counter()
    for offset in range(0, len(cohort["products"]), 10):
        batch = cohort["products"][offset:offset + 10]
        for row in batch:
            assert classifier.context_hash(row["context"]) == row["sha256"]
        contexts = [row["context"] for row in batch]
        def replay(contexts, **kwargs):
            if kwargs.get("arbitration_policy"):
                return original(contexts, **kwargs)
            return [classifier.ClassificationResult(**row["primary"]) for row in batch]
        with patch.object(classifier, "classify_codex_contexts", side_effect=replay):
            results.extend(classifier.classify_with_arbitration(
                contexts, passes=2, retries=2, model=classifier.DEFAULT_CODEX_MODEL,
                reasoning_effort="high", codex_bin="codex"))
    expected = {r["product_id"]: r["expected_status"] for r in cohort["products"]}
    mismatches = [r.product_id for r in results if r.vegan_status != expected[r.product_id]]
    errors = [r.product_id for r in results if r.validation_error]
    result = {"fixture_sha256": classifier.context_hash(cohort),
              "arbitration_prompt_version": classifier.ARBITRATION_PROMPT_VERSION,
              "model": classifier.ARBITRATION_MODEL, "reasoning_effort": classifier.ARBITRATION_REASONING_EFFORT,
              "passes": 2, "batch_size": 10, "duration_seconds": round(time.perf_counter() - started, 3),
              "mismatch_ids": mismatches, "error_ids": errors,
              "decisions": [asdict(r) for r in results]}
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Arbitration replay: {len(results) - len(mismatches)}/{len(expected)} exact, errors={len(errors)}")
    return int(bool(mismatches or errors))


if __name__ == "__main__":
    raise SystemExit(main())
