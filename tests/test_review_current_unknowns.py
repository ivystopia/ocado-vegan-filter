import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import review_current_unknowns as review


def context(pid="1", name="Plain apples"):
    return {"product": {"id": pid, "name": name, "official_vegan": 0}, "flags": [], "categories": []}


def decision(pid="1", status="vegan", reason="ingredients"):
    return {
        "product_id": pid,
        "status": status,
        "reason": reason,
        "rationale": "Plain fruit.",
        "support": [{"field": "name", "quote": "Plain apples"}],
        "concerns": [],
        "assessment": dict.fromkeys(review.DIMENSIONS, "clear"),
    }


class ReviewTests(unittest.TestCase):
    def test_missing_duplicate_and_unexpected_ids(self):
        for decisions in ([], [decision(), decision()], [decision("2")]):
            with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                review.validate_payload({"decisions": decisions}, [context()])

    def test_positive_needs_complete_assessment_and_no_concerns(self):
        for change in (
            {"concerns": ["Unspecified coating"]},
            {"assessment": dict.fromkeys(review.DIMENSIONS, "unresolved")},
        ):
            d = decision()
            d.update(change)
            with self.assertRaises(ValueError):
                review.validate_payload({"decisions": [d]}, [context()])

    def test_quotes_must_exist_and_claim_must_be_quoted(self):
        for d in (decision(reason="name"), decision(reason="manufacturer")):
            raw = review.validate_payload({"decisions": [d]}, [context()])["1"]
            self.assertEqual(raw["reason"], d["reason"], "preserve raw reason for disagreement checks")
            self.assertIsNotNone(review.positive_basis_issue(raw), "unsupported reason cannot promote")
        d = decision()
        d["support"][0]["quote"] = "Vegan apples"
        with self.assertRaises(ValueError):
            review.validate_payload({"decisions": [d]}, [context()])

    def test_tag_precedence(self):
        c = context()
        c["flags"] = ["vegan"]
        with self.assertRaises(ValueError):
            review.validate_payload({"decisions": [decision()]}, [c])
        self.assertEqual(
            review.validate_payload({"decisions": [decision(reason="tagged")]}, [c])["1"]["reason"], "tagged"
        )

    def test_nonpositive_cannot_have_positive_reason(self):
        with self.assertRaises(ValueError):
            review.validate_payload({"decisions": [decision(status="unknown")]}, [context()])

    def test_arbitration_disagreement_remains_unknown(self):
        yes = decision()
        no = decision(status="unknown", reason=None)
        self.assertEqual(review.merge([yes, no], [yes, no])["status"], "unknown")

    def test_real_pipeline_contract_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = review.connect(root)
            conn.execute(
                "CREATE TABLE products(id TEXT PRIMARY KEY,context_json TEXT,batch INTEGER,result_json TEXT)"
            )
            conn.executemany(
                "INSERT INTO products VALUES(?,?,0,NULL)",
                [(pid, json.dumps(context(pid))) for pid in ["1", "2"]],
            )
            conn.commit()
            conn.close()
            calls = []

            def fake(prompt, schema, **kw):
                supplied = json.loads(prompt.split("PRODUCTS_JSON:\n")[1])
                calls.append((kw["stage"], kw["model"], kw["effort"], supplied))
                decisions = []
                for c in supplied:
                    d = decision(c["product"]["id"])
                    if d["product_id"] == "2" or kw["stage"] == "primary-2":
                        d.update(status="unknown", reason=None)
                    decisions.append(d)
                payload = {"decisions": decisions}
                kw["validator"](payload)
                return {"parsed_response": payload, "call_id": kw["stage"]}

            with mock.patch.object(review.codex_evidence, "run", side_effect=fake):
                result = review.batch_work(root, 0, "codex")
                self.assertEqual(result["arbitrated"], 1)
                self.assertEqual(len(calls), 4)
                self.assertEqual(calls[0][1:3], review.PRIMARY)
                self.assertEqual(calls[2][1:3], review.ARBITRATION)
                self.assertEqual(calls[2][3], [context("1")])
                self.assertEqual(calls[3][3], [context("1")])
                review.batch_work(root, 0, "codex")
                self.assertEqual(len(calls), 4, "resume reuses completed valid calls")
            self.assertEqual(review.progress(root)["reviewed"], 2)

    def test_included_plan_guard(self):
        for snapshot in (
            {},
            {
                "auth_type": "chatgpt",
                "ordinary_usage_allowed": True,
                "buckets": {"codex": {"primary": {"usedPercent": 100}}},
            },
        ):
            with self.assertRaises(review.codex_evidence.UsageLimitError):
                review.codex_evidence.require_included_allowance(snapshot)

    def test_tools_and_paid_auth_are_disabled(self):
        cmd = review.codex_evidence.invocation(
            "codex", Path("/tmp"), Path("/tmp/schema"), Path("/tmp/output"), *review.PRIMARY
        )
        for item in [
            "--ephemeral",
            "--ignore-user-config",
            'forced_login_method="chatgpt"',
            "features.shell_tool=false",
            'web_search="disabled"',
        ]:
            self.assertIn(item, cmd)

    def test_model_input_has_no_prior_answers(self):
        self.assertNotIn("expected", review.PROMPT)
        self.assertNotIn("previous_audit", json.dumps(context()))

    def test_invalid_quote_retries_only_affected_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def fake(prompt, schema, **kw):
                ids = kw["product_ids"]
                calls.append(ids)
                decisions = [decision(pid) for pid in ids]
                if len(calls) == 1:
                    decisions[1]["support"][0]["quote"] = "Invented ingredient"
                payload = {"decisions": decisions}
                record = {
                    "call_id": str(len(calls)),
                    "product_ids": ids,
                    "context_hash": kw["context_hash"],
                    "model": kw["model"],
                    "reasoning_effort": kw["effort"],
                    "exit_status": 0,
                    "usage": {"input_tokens": 1},
                    "parsed_response": payload,
                }
                with review.codex_evidence.USAGE_JOURNAL.get().open("a") as stream:
                    stream.write(json.dumps(record) + "\n")
                kw["validator"](payload)
                return record

            with mock.patch.object(review.codex_evidence, "run", side_effect=fake):
                result, ids = review.read_pass(
                    root, 0, "primary-1", [context("1"), context("2")], review.PRIMARY, "codex"
                )
            self.assertEqual(calls, [["1", "2"], ["2"]])
            self.assertEqual(set(result), {"1", "2"})
            self.assertEqual(ids, ["1", "2"])

    def test_freeze_selects_every_current_unknown_and_excludes_other_products(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "source.sqlite"
            root = Path(tmp) / "queue"
            with sqlite3.connect(db) as conn:
                conn.executescript(
                    "CREATE TABLE products(id TEXT, current_on_ocado INTEGER, vegan_status TEXT, vegan_reason TEXT); CREATE TABLE manufacturer_website_evidence(product_id TEXT);"
                )
                conn.executemany(
                    "INSERT INTO products VALUES(?,?,?,?)",
                    [
                        ("1", 1, "unknown", None),
                        ("2", 1, "unknown", None),
                        ("3", 0, "unknown", None),
                        ("4", 1, "vegan", "ingredients"),
                        ("5", 1, "nonvegan", None),
                    ],
                )
            with mock.patch.object(review.baseline, "validate"), mock.patch.object(
                review.baseline, "source_context", side_effect=lambda c, pid: context(pid)
            ), mock.patch("builtins.print"):
                review.freeze(db, root)
            with review.closing(review.connect(root)) as conn:
                self.assertEqual(
                    conn.execute("SELECT id,result_json FROM products ORDER BY id").fetchall(),
                    [("1", None), ("2", None)],
                )
            self.assertEqual(review.progress(root)["remaining"], 2)

    def test_literal_reference_normalization_cannot_invent_evidence(self):
        c = context()
        c["product"]["ingredients"] = None
        d = decision(status="unknown", reason=None)
        d["support"] = [
            {"field": "product.name", "quote": "Plain apples"},
            {"field": "ingredients", "quote": ""},
        ]
        normalized = review.validate_payload({"decisions": [d]}, [c])["1"]
        self.assertEqual(normalized["support"], [{"field": "name", "quote": "Plain apples"}])
        self.assertEqual(d["support"][0]["field"], "product.name", "raw response remains intact")
        d["support"][0]["quote"] = "Plain vegan apples"
        with self.assertRaises(ValueError):
            review.validate_payload({"decisions": [d]}, [c])

    def test_promotion_holds_require_current_exact_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            c = context()
            hashed = review.baseline.digest(c)
            rows = [("1", json.dumps(c), hashed, json.dumps({"decision": decision()}))]
            hold = {
                "product_id": "1",
                "context_hash": hashed,
                "rationale": "Example evidenced concern",
                "support": [{"field": "name", "quote": "Plain apples"}],
            }
            review.write_json(root / "promotion-exclusions.json", {"exclusions": [hold]})
            self.assertEqual(set(review.checked_exclusions(root, rows)), {"1"})
            hold["support"][0]["quote"] = "Waxed apples"
            review.write_json(root / "promotion-exclusions.json", {"exclusions": [hold]})
            with self.assertRaises(ValueError):
                review.checked_exclusions(root, rows)

    def test_exact_numeric_absence_of_tag_is_grounded_without_becoming_a_tag(self):
        c = context()
        d = decision(status="unknown", reason=None)
        d["support"].append({"field": "official_vegan", "quote": "0"})
        self.assertEqual(review.validate_payload({"decisions": [d]}, [c])["1"]["status"], "unknown")
        d["support"][-1]["quote"] = "1"
        with self.assertRaises(ValueError):
            review.validate_payload({"decisions": [d]}, [c])

    def test_nutrition_declaration_is_retained_without_changing_other_contexts(self):
        with sqlite3.connect(":memory:") as conn:
            conn.execute(
                "CREATE TABLE products("
                + ",".join(f"{field} TEXT" for field in review.baseline.SOURCE_FIELDS)
                + ")"
            )
            conn.executescript(
                "CREATE TABLE product_flags(product_id TEXT,flag TEXT); CREATE TABLE product_categories(product_id TEXT,category_path TEXT); CREATE TABLE manufacturer_vegan_evidence(product_id TEXT,field_title TEXT,content TEXT);"
            )
            conn.execute("INSERT INTO products(id,name) VALUES('1','Example vegan bangers')")
            before = review.baseline.source_context(conn, "1")
            self.assertNotIn("nutrition_declaration", before["product"])
            conn.execute(
                "INSERT INTO manufacturer_vegan_evidence VALUES('1','nutritionalData','Per 2 vegan bangers')"
            )
            after = review.baseline.source_context(conn, "1")
            self.assertEqual(after["product"].pop("nutrition_declaration"), "Per 2 vegan bangers")
            self.assertEqual(after, before)
