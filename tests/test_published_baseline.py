"""Published restoration and narrow additions must not rewrite other decisions."""

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import published_baseline as policy
import reviewed_safety as safety
import update_userscript_allowlists as export
import classify_ocado_vegan as legacy
import sync_ocado_database as legacy_sync
import research_ocado_manufacturers as legacy_research


class PublishedBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "fixture.sqlite"
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        columns = ",".join(f"{c} TEXT" for c in policy.SOURCE_FIELDS if c != "id")
        self.conn.execute(f"CREATE TABLE products(id TEXT PRIMARY KEY,{columns})")
        self.conn.execute("CREATE TABLE product_flags(product_id TEXT,flag TEXT)")
        self.conn.execute("CREATE TABLE product_categories(product_id TEXT,category_path TEXT)")
        legacy.ensure_classification_schema(self.conn)
        self.conn.executemany(
            "INSERT INTO products(id,name,ingredients,package_type,vegan_status) VALUES(?,?,?,?,?)",
            [
                ("1", "Published vegan", "Sugar", "Bag", "unknown"),
                ("2", "Published nonvegan", "Milk", "Carton", "vegan"),
                ("3", "Kissabel British Apples", None, "Wrap", "unknown"),
                ("4", "Butter carrots", "Carrot, butter", "Tray", "nonvegan"),
            ],
        )
        self.conn.commit()
        self.patch = mock.patch.object(
            policy, "baseline_decisions", return_value={"1": ("vegan", "ingredients"), "2": ("nonvegan", None)}
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def restore(self):
        with self.conn:
            policy.restore(self.conn, Path(self.tmp.name) / "before.json.gz")

    def review(self):
        context = policy.source_context(self.conn, "3")
        return {
            "product_id": "3",
            "reason": "ingredients",
            "context": context,
            "context_hash": policy.digest(context),
            "rationale": "Plain apples; ordinary wrapping provides no evidence of an animal component.",
            "support": [{"field": "name", "quote": "Kissabel British Apples"}],
            "reviewed_by": "test reviewer",
            "reviewed_on": "2026-10-18",
            "scope": "complete product; evidenced material concerns only",
        }

    def test_restores_exact_sets_without_rewriting_source_fields(self):
        self.restore()
        self.assertEqual(
            self.conn.execute("SELECT id,vegan_status,vegan_reason FROM products ORDER BY id").fetchall(),
            [("1", "vegan", "ingredients"), ("2", "nonvegan", None), ("3", "unknown", None), ("4", "unknown", None)],
        )
        self.assertEqual(
            self.conn.execute("SELECT ingredients,package_type FROM products WHERE id='1'").fetchone(), ("Sugar", "Bag")
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM product_vegan_classification_audit").fetchone()[0], 4)
        self.assertTrue((Path(self.tmp.name) / "before.json.gz").exists())

    def test_adds_only_reviewed_unknown_and_exports(self):
        self.restore()
        with self.conn:
            policy.apply_reviews(self.conn, [self.review()])
        sets = export.load_allowlists(self.conn)
        self.assertEqual(sets["INGREDIENTS_VEGAN_PRODUCT_IDS"], ["1", "3"])
        self.assertEqual(sets["KNOWN_NON_VEGAN_PRODUCT_IDS"], ["2"])

    def safety_reviews(self):
        for name, kind in {"vegan_positive_block": "INTEGER DEFAULT 0", "vegan_audit_id": "INTEGER",
                           "vegan_evidence_hash": "TEXT", "vegan_policy_version": "TEXT"}.items():
            self.conn.execute(f"ALTER TABLE products ADD COLUMN {name} {kind}")
        self.conn.execute("UPDATE products SET ingredients='Apples',detailed_description='Includes milk chocolate' WHERE id='3'")
        self.restore()
        reviews = []
        for pid, status, finding, support in [
            ("3", "unknown", "unresolved_evidence_conflict", [{"field": "ingredients", "quote": "Apples"}, {"field": "detailed_description", "quote": "Includes milk chocolate"}]),
            ("4", "nonvegan", "confirmed_animal_ingredient", [{"field": "ingredients", "quote": "Carrot, butter"}]),
        ]:
            context = policy.source_context(self.conn, pid)
            reviews.append({"product_id": pid, "status": status, "finding": finding, "context": context,
                            "context_hash": policy.digest(context), "rationale": "Reviewed source evidence.",
                            "support": support, "reviewed_by": "test reviewer", "reviewed_on": "2026-10-18",
                            "scope": "complete product; evidenced material concerns only"})
        return reviews

    def test_safety_decisions_export_with_previous_audit_linkage(self):
        reviews = self.safety_reviews()
        previous = self.conn.execute("SELECT vegan_audit_id FROM products WHERE id='4'").fetchone()[0]
        with self.conn:
            safety.apply_reviews(self.conn, reviews)
        sets = export.load_allowlists(self.conn)
        self.assertEqual(sets["KNOWN_NON_VEGAN_PRODUCT_IDS"], ["2", "4"])
        self.assertEqual(sets["BLOCKED_UNKNOWN_PRODUCT_IDS"], ["3"])
        row = self.conn.execute("SELECT a.evidence_json FROM product_vegan_classification_audit a JOIN products p ON p.vegan_audit_id=a.id WHERE p.id='4'").fetchone()
        self.assertEqual(json.loads(row[0])["previous_decision"]["vegan_audit_id"], previous)
        with self.assertRaisesRegex(ValueError, "unblocked unknown"):
            safety.apply_reviews(self.conn, reviews)
        with self.assertRaisesRegex(ValueError, "Resolve the reviewed conflict"):
            policy.apply_reviews(self.conn, [self.review()])

    def test_safety_rejects_changed_source_and_invented_quotes(self):
        reviews = self.safety_reviews()
        reviews[0]["support"][0]["quote"] = "not in the source"
        with self.assertRaisesRegex(ValueError, "quotation"):
            safety.apply_reviews(self.conn, reviews)
        self.assertFalse(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (safety.TABLE,)).fetchone())
        reviews[0]["support"][0]["quote"] = "Apples"
        with self.conn:
            safety.apply_reviews(self.conn, reviews)
        self.conn.execute("UPDATE products SET ingredients='New recipe' WHERE id='4'")
        with self.assertRaisesRegex(ValueError, "Stale safety evidence"):
            export.load_allowlists(self.conn)

    def test_safety_rejects_cleared_block_and_missing_audit(self):
        reviews = self.safety_reviews()
        with self.conn:
            safety.apply_reviews(self.conn, reviews)
        self.conn.execute("UPDATE products SET vegan_positive_block=0 WHERE id='3'")
        with self.assertRaisesRegex(ValueError, "canonical decision"):
            export.load_allowlists(self.conn)
        self.conn.rollback()
        self.conn.execute("DELETE FROM product_vegan_classification_audit WHERE id=(SELECT audit_id FROM reviewed_safety_decisions WHERE product_id='4')")
        with self.assertRaisesRegex(ValueError, "audit linkage"):
            export.load_allowlists(self.conn)

    def test_unreviewed_block_cannot_change_published_positive(self):
        self.safety_reviews()
        self.conn.execute("UPDATE products SET vegan_positive_block=1 WHERE id='1'")
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            export.load_allowlists(self.conn)

    def test_safety_application_rolls_back_all_rows_on_failure(self):
        reviews = self.safety_reviews()
        before = self.conn.execute("SELECT id,vegan_status,vegan_audit_id FROM products ORDER BY id").fetchall()
        real_write = policy.write_decision
        def fail_second(*args, **kwargs):
            if args[2] == "4":
                raise ValueError("injected write failure")
            return real_write(*args, **kwargs)
        with self.assertRaisesRegex(ValueError, "injected write failure"), self.conn:
            with mock.patch.object(policy, "write_decision", side_effect=fail_second):
                safety.apply_reviews(self.conn, reviews)
        self.assertEqual(self.conn.execute("SELECT id,vegan_status,vegan_audit_id FROM products ORDER BY id").fetchall(), before)
        policy.validate(self.conn)

    def test_changed_animal_ingredients_reject_review(self):
        self.restore()
        review = self.review()
        self.conn.execute("UPDATE products SET ingredients='Apples, shellac' WHERE id='3'")
        with self.assertRaisesRegex(ValueError, "Stale reviewed evidence"):
            policy.apply_reviews(self.conn, [review])

    def test_model_backed_addition_preserves_actual_audit_source(self):
        self.restore()
        with self.conn:
            result = policy.apply_reviews(self.conn, [self.review()], source="codex")
        self.assertEqual(
            self.conn.execute(
                "SELECT source FROM product_vegan_classification_audit WHERE run_id=?", (result["run_id"],)
            ).fetchall(),
            [("codex",)],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT rule_classified,llm_classified FROM vegan_classification_runs WHERE id=?",
                (result["run_id"],),
            ).fetchone(),
            (0, 1),
        )

    def test_complete_unknown_review_activation_keeps_baseline_and_backups(self):
        import gzip
        import review_current_unknowns as runner

        self.restore()
        self.conn.execute("ALTER TABLE products ADD COLUMN current_on_ocado INTEGER DEFAULT 1")
        self.conn.execute("UPDATE products SET current_on_ocado=0 WHERE id='4'")
        self.conn.execute("CREATE TABLE manufacturer_website_evidence(product_id TEXT)")
        self.conn.commit()
        root = Path(self.tmp.name) / "review"
        output = Path(self.tmp.name) / "report"
        with mock.patch("builtins.print"):
            runner.freeze(self.path, root)
        context = policy.source_context(self.conn, "3")
        decision = {
            "product_id": "3",
            "status": "vegan",
            "reason": "ingredients",
            "rationale": "Plain apples with no evidenced material concern.",
            "support": [{"field": "name", "quote": "Kissabel British Apples"}],
            "concerns": [],
            "assessment": dict.fromkeys(runner.DIMENSIONS, "clear"),
        }
        result = {
            "decision": decision,
            "primary": [decision, decision],
            "arbitration": None,
            "call_ids": ["one", "two"],
        }
        with closing(runner.connect(root)) as queue, queue:
            queue.execute("UPDATE products SET result_json=? WHERE id='3'", (json.dumps(result),))
        folder = root / "passes/00000"
        folder.mkdir(parents=True)
        for call_id, stage in [("one", "primary-1"), ("two", "primary-2")]:
            binding = policy.digest(
                {
                    "version": runner.VERSION,
                    "prompt": runner.PROMPT + policy.encoded([context]),
                    "model": runner.PRIMARY[0],
                    "effort": runner.PRIMARY[1],
                }
            )
            record = {
                "call_id": call_id,
                "stage": stage,
                "product_ids": ["3"],
                "model": runner.PRIMARY[0],
                "reasoning_effort": runner.PRIMARY[1],
                "context_hash": binding,
                "invocation_started": True,
                "exit_status": 0,
                "usage": {"input_tokens": 1},
                "parsed_response": {"decisions": [decision]},
            }
            (folder / f"{stage}.attempts.jsonl").write_text(json.dumps(record) + "\n")
        with mock.patch("builtins.print"):
            runner.finalize(self.path, root, output, True)
        application = json.loads((output / "application.json").read_text())
        self.assertTrue(application["applied"])
        with gzip.open(application["before_activation_backup"]["path"], "rt") as stream:
            before = json.load(stream)["before"]
        self.assertEqual([(row["id"], row["vegan_status"]) for row in before], [("3", "unknown")])
        self.assertEqual(
            self.conn.execute("SELECT id,vegan_status FROM products ORDER BY id").fetchall(),
            [("1", "vegan"), ("2", "nonvegan"), ("3", "vegan"), ("4", "unknown")],
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM current_unknown_reviews").fetchone()[0], 1)
        with mock.patch("builtins.print"), self.assertRaisesRegex(ValueError, "cohort changed"):
            runner.finalize(self.path, root, output, True)

    def test_changed_identity_cannot_reuse_review(self):
        self.restore()
        review = self.review()
        self.conn.execute("UPDATE products SET name='Toffee Apples' WHERE id='3'")
        with self.assertRaisesRegex(ValueError, "Stale reviewed evidence"):
            policy.apply_reviews(self.conn, [review])

    def test_unknown_packaging_alone_does_not_block_explicit_plain_food_review(self):
        self.restore()
        review = self.review()
        policy.validate_review(self.conn, review)
        self.assertEqual(review["context"]["product"]["package_type"], "Wrap")

    def test_unquoted_or_fabricated_evidence_rejected(self):
        self.restore()
        review = self.review()
        review["support"][0]["quote"] = "Certified vegan"
        with self.assertRaisesRegex(ValueError, "quotation"):
            policy.apply_reviews(self.conn, [review])

    def test_tagged_reason_requires_an_actual_tag(self):
        self.restore()
        review = self.review()
        review["reason"] = "tagged"
        with self.assertRaisesRegex(ValueError, "official-tag precedence"):
            policy.apply_reviews(self.conn, [review])

    def test_actual_tag_has_canonical_reason_precedence(self):
        self.restore()
        self.conn.execute("INSERT INTO product_flags VALUES('3','vegan')")
        review = self.review()
        with self.assertRaisesRegex(ValueError, "official-tag precedence"):
            policy.validate_review(self.conn, review)
        review["reason"] = "tagged"
        policy.validate_review(self.conn, review)

    def test_duplicate_and_repeat_reviews_rejected(self):
        self.restore()
        review = self.review()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            policy.apply_reviews(self.conn, [review, review])
        with self.conn:
            policy.apply_reviews(self.conn, [review])
        with self.assertRaisesRegex(ValueError, "Only unknown"):
            policy.apply_reviews(self.conn, [review])

    def test_export_rejects_unreviewed_promotions_and_demotions(self):
        self.restore()
        for pid, status in [("4", "vegan"), ("1", "unknown")]:
            with self.subTest(pid=pid):
                self.conn.execute("SAVEPOINT mutation")
                self.conn.execute("UPDATE products SET vegan_status=? WHERE id=?", (status, pid))
                with self.assertRaisesRegex(ValueError, "Unreviewed classification"):
                    export.load_allowlists(self.conn)
                self.conn.execute("ROLLBACK TO mutation")
                self.conn.execute("RELEASE mutation")

    def test_export_rejects_stale_additions(self):
        self.restore()
        with self.conn:
            policy.apply_reviews(self.conn, [self.review()])
        self.conn.execute("UPDATE products SET ingredients='Apples, honey' WHERE id='3'")
        with self.assertRaisesRegex(ValueError, "Stale reviewed evidence"):
            export.load_allowlists(self.conn)

    def test_export_rejects_missing_audit(self):
        self.restore()
        with self.conn:
            policy.apply_reviews(self.conn, [self.review()])
        self.conn.execute(
            "DELETE FROM product_vegan_classification_audit WHERE id=(SELECT audit_id FROM reviewed_vegan_additions)"
        )
        with self.assertRaisesRegex(ValueError, "audit linkage"):
            export.load_allowlists(self.conn)

    def test_repeated_restore_refused(self):
        self.restore()
        with self.assertRaisesRegex(ValueError, "already restored"):
            policy.restore(self.conn, Path(self.tmp.name) / "second.json.gz")

    def test_restore_rolls_back_on_failure(self):
        self.conn.execute("BEGIN IMMEDIATE")
        with mock.patch.object(policy, "validate", side_effect=ValueError("forced failure")):
            with self.assertRaisesRegex(ValueError, "forced failure"):
                with self.conn:
                    policy.restore(self.conn, Path(self.tmp.name) / "failed.json.gz")
        self.assertFalse(policy.active(self.conn))
        self.assertEqual(self.conn.execute("SELECT vegan_status FROM products WHERE id='2'").fetchone()[0], "vegan")

    def test_legacy_cli_cannot_modify_protected_database(self):
        self.restore()
        with self.assertRaisesRegex(ValueError, "baseline is protected"):
            legacy.main(["--db", str(self.path), "classify-rules", "--force"])

    def test_legacy_sync_refuses_before_creating_large_backup(self):
        self.restore()
        with mock.patch.object(legacy_sync, "create_rolling_backup") as backup:
            with self.assertRaisesRegex(ValueError, "baseline is protected"):
                legacy_sync.run_sync(legacy_sync.build_parser().parse_args(["--db", str(self.path)]))
            backup.assert_not_called()

    def test_legacy_research_cannot_bypass_reviewed_additions(self):
        self.restore()
        with self.assertRaisesRegex(ValueError, "baseline is protected"):
            legacy_research.apply_bundle(Path(self.tmp.name), self.path, {}, dry_run=False)

    def test_read_lock_prevents_concurrent_writer(self):
        self.restore()
        self.conn.execute("BEGIN IMMEDIATE")
        with closing(sqlite3.connect(self.path, timeout=0)) as other:
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("UPDATE products SET name='changed' WHERE id='3'")
        self.conn.rollback()
