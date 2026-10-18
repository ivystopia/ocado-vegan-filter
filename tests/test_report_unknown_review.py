import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import report_unknown_review as reporting
import review_current_unknowns as review


class AccountingTests(unittest.TestCase):
    def fixture(self, root, complete=True):
        context = {
            "product": {"id": "1", "name": "Plain apples", "official_vegan": 0},
            "flags": [],
            "categories": [],
        }
        d = {
            "product_id": "1",
            "status": "vegan",
            "reason": "ingredients",
            "rationale": "Plain apples.",
            "support": [{"field": "name", "quote": "Plain apples"}],
            "concerns": [],
            "assessment": dict.fromkeys(review.DIMENSIONS, "clear"),
        }
        result = {"decision": d, "primary": [d, d], "arbitration": None, "call_ids": ["one", "two"]}
        hashed = review.baseline.digest(context)
        with closing(sqlite3.connect(root / "review.sqlite")) as conn, conn:
            conn.execute(
                "CREATE TABLE products(id TEXT,context_json TEXT,context_hash TEXT,result_json TEXT)"
            )
            conn.execute(
                "INSERT INTO products VALUES(?,?,?,?)",
                ("1", json.dumps(context), hashed, json.dumps(result) if complete else None),
            )
        review.write_json(root / "manifest.json", {"cohort_hash": review.baseline.digest([["1", hashed]])})
        folder = root / "passes" / "00000"
        folder.mkdir(parents=True)
        if complete:
            for call_id, stage in [("one", "primary-1"), ("two", "primary-2")]:
                binding = review.baseline.digest(
                    {
                        "version": review.VERSION,
                        "prompt": review.PROMPT + review.baseline.encoded([context]),
                        "model": review.PRIMARY[0],
                        "effort": review.PRIMARY[1],
                    }
                )
                record = {
                    "call_id": call_id,
                    "stage": stage,
                    "model": review.PRIMARY[0],
                    "reasoning_effort": review.PRIMARY[1],
                    "product_ids": ["1"],
                    "context_hash": binding,
                    "invocation_started": True,
                    "exit_status": 0,
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                    "parsed_response": {"decisions": [d]},
                }
                (folder / f"{stage}.attempts.jsonl").write_text(json.dumps(record) + "\n")
        return folder

    def test_complete_means_every_reading_is_traceable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            with mock.patch("builtins.print"):
                result = reporting.report(root, root / "report")
            self.assertTrue(result["complete"])
            self.assertEqual(result["traceable_fresh_readings"], 2)
            self.assertEqual(result["calls_by_model"]["gpt-6-luna/high"]["input_tokens"], 20)

    def test_missing_second_independent_call_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = self.fixture(root)
            p = folder / "primary-2.attempts.jsonl"
            r = json.loads(p.read_text())
            r["stage"] = "primary-1"
            p.write_text(json.dumps(r) + "\n")
            with self.assertRaisesRegex(ValueError, "Untraceable"):
                reporting.report(root, root / "report")

    def test_partial_review_is_explicitly_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root, False)
            with mock.patch("builtins.print"):
                result = reporting.report(root, root / "report")
            self.assertFalse(result["complete"])
            self.assertEqual(result["pending_ids"], ["1"])
