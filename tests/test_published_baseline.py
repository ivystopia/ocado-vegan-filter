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

    def test_changed_animal_ingredients_reject_review(self):
        self.restore()
        review = self.review()
        self.conn.execute("UPDATE products SET ingredients='Apples, shellac' WHERE id='3'")
        with self.assertRaisesRegex(ValueError, "Stale reviewed evidence"):
            policy.apply_reviews(self.conn, [review])

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
