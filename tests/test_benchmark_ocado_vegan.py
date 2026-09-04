#!/usr/bin/env python3
"""Tests for the fixed vegan classifier benchmark harness."""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import benchmark_ocado_vegan as benchmark


class BenchmarkOcadoVeganTests(unittest.TestCase):
    def test_frozen_fixture_runs_without_opening_live_database(self) -> None:
        fixture = benchmark.load_fixture(benchmark.DEFAULT_FIXTURE)
        expected = {p["product_id"]: p["expected_status"] for p in fixture["products"]}
        args = benchmark.build_parser().parse_args(["--db", "/nonexistent/catalogue.sqlite"])

        def classify(contexts, **kwargs):
            return [benchmark.classifier.ClassificationResult(
                product_id=context["product"]["id"],
                vegan_status=expected[context["product"]["id"]],
                vegan_reason="ingredients" if expected[context["product"]["id"]] == "vegan" else None,
                confidence="certain", summary="Fixture result", evidence={}, source="codex",
            ) for context in contexts]

        with mock.patch.object(benchmark.sqlite3, "connect", side_effect=AssertionError("Live database opened")):
            with mock.patch.object(benchmark.classifier, "classify_codex_contexts", side_effect=classify):
                result = benchmark.run_benchmark(args)
        self.assertEqual(result["exact_matches"], 48)
        self.assertTrue(result["frozen_contexts"])
        self.assertEqual(len(result["fixture_sha256"]), 64)
        self.assertEqual(fixture["contexts"]["677770011"]["product"]["dietary_information"], "Suitable for Vegetarians")

    def test_fixture_contains_two_balanced_24_product_cohorts(self) -> None:
        fixture = benchmark.load_fixture(benchmark.DEFAULT_FIXTURE)
        products = fixture["products"]

        self.assertEqual(len(products), 48)
        self.assertEqual(Counter(product["cohort"] for product in products), {"challenge": 24, "holdout": 24})
        self.assertEqual(
            Counter(product["expected_status"] for product in products if product["cohort"] == "holdout"),
            {"vegan": 8, "nonvegan": 8, "unknown": 8},
        )

    def test_defaults_use_luna_high_with_two_passes(self) -> None:
        args = benchmark.build_parser().parse_args([])

        self.assertEqual(args.model, "gpt-5.6-luna")
        self.assertEqual(args.reasoning_effort, "high")
        self.assertEqual(args.passes, 2)
        self.assertEqual(args.batch_size, 10)


if __name__ == "__main__":
    unittest.main()
