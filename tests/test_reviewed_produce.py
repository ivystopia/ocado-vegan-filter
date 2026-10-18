"""Frozen source-backed produce additions to the published baseline."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import published_baseline as policy


class RetainedReviewsTests(unittest.TestCase):
    def test_all_additions_are_new_ids_and_have_verifiable_snapshots(self):
        root = Path(__file__).resolve().parents[1]
        reviews = []
        for filename in ("produce-reviews.json", "new-produce-reviews.json"):
            reviews.extend(json.loads((root / "audit/published-baseline-restart" / filename).read_text())["reviews"])
        baseline = policy.baseline_decisions()
        ids = [r["product_id"] for r in reviews]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("647140011", ids)
        self.assertFalse(set(ids) & baseline.keys())
        for r in reviews:
            self.assertEqual(r["context_hash"], policy.digest(r["context"]))
            for q in r["support"]:
                self.assertIn(q["quote"], r["context"]["product"][q["field"]])
