#!/usr/bin/env python3
"""Tests for the fixed vegan classifier benchmark harness."""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import benchmark_ocado_vegan as benchmark


class BenchmarkOcadoVeganTests(unittest.TestCase):
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
