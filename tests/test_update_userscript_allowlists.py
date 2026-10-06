#!/usr/bin/env python3
"""Tests for deterministic userscript allowlist generation."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import update_userscript_allowlists as generator
import build_ocado_database as builder
import classify_ocado_vegan as classifier


class UpdateUserscriptAllowlistsTests(unittest.TestCase):
    def test_export_requires_latest_sync_completed(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE sync_runs(id INTEGER, status TEXT)")
            conn.execute("INSERT INTO sync_runs VALUES (1, 'completed'), (2, 'failed')")
            with self.assertRaisesRegex(ValueError, "sync run 2"):
                generator.validate_export_readiness(conn)

    def test_replace_set_preserves_retained_order_and_appends_new_ids(self) -> None:
        source = "const IDS = new Set(\n    `\n  30 10 20\n  `\n      .trim()\n      .split(/\\s+/),\n  );"
        updated = generator.replace_set(source, "IDS", ["10", "30", "40"])
        self.assertIn("  30 10\n  40", updated)

    def populate(self, conn):
        conn.row_factory = sqlite3.Row
        builder.create_schema(conn)
        classifier.ensure_classification_schema(conn)
        run_id = classifier.start_run(conn, mode="test")
        for product_id, name, ingredients, tag, text in [
            ("30", "Beans", "Beans", 1, None),
            ("10", "Example beans", "Beans", 0, "Suitable for vegans"),
            ("20", "Vegan beans", "Beans", 0, None),
            ("40", "Plain beans", "Beans", 0, None),
            ("50", "White Wine", None, 0, None),
            ("60", "Gelatine capsules", "Gelatine", 1, None),
            ("70", "Ground cumin", "Pork fat, pork meat", 1, None),
        ]:
            conn.execute("INSERT INTO products(id,name,ingredients,official_vegan,dietary_information) VALUES (?,?,?,?,?)",
                         (product_id,name,ingredients,tag,text))
            context=classifier.load_product_context(conn,product_id)
            result = classifier.classify_by_rules(context)
            if product_id=='20':
                self.assertIsNone(result)  # Offline name interpretation is required.
                assessment=classifier.policy.base_assessment(context,complete=True)
                assessment['positive_support']=[{'reason':'name','evidence_refs':[classifier.policy.reference('/product/name',name)]}]
                result=classifier.ClassificationResult(product_id=product_id,vegan_status='vegan',vegan_reason='name',
                    confidence='certain',summary='Fixture with assessed whole-product name',source='codex',evidence={'assessment':assessment})
            self.assertIsNotNone(result, product_id)
            classifier.write_classification(conn,run_id,result)
        conn.commit()

    def test_regenerate_uses_only_canonical_vegan_rows(self):
        with sqlite3.connect(":memory:") as conn:
            self.populate(conn)
            allowlists = generator.load_allowlists(conn)
            updated = generator.regenerate((ROOT / "ocado-vegan-filter.user.js").read_text(), allowlists, "9.9.9")
        for name, expected in {
            "OFFICIAL_VEGAN_PRODUCT_IDS": {"30"},
            "MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS": {"10", "20"},
            "INGREDIENTS_VEGAN_PRODUCT_IDS": {"40"},
            "KNOWN_NON_VEGAN_PRODUCT_IDS": {"60"},
            "BLOCKED_VEGAN_PROMOTION_PRODUCT_IDS": {"70"},
        }.items():
            self.assertEqual(generator.extract_set(updated,name), expected)
        self.assertIn("// @version     9.9.9",updated)
        self.assertIn("Recognised vegan product IDs: 4",updated)
        self.assertIn("Blocked vegan promotion product IDs: 1",updated)

    def test_export_allows_tagged_animal_negative_and_identity_block(self):
        with sqlite3.connect(":memory:") as conn:
            self.populate(conn)
            result = generator.load_allowlists(conn)
            self.assertIn("60",result["KNOWN_NON_VEGAN_PRODUCT_IDS"])
            self.assertIn("70",result["BLOCKED_VEGAN_PROMOTION_PRODUCT_IDS"])

    def test_export_rejects_legacy_positive_and_wrong_audit(self):
        for update in ("vegan_policy_version=NULL", "vegan_audit_id=9999"):
            with self.subTest(update=update), sqlite3.connect(":memory:") as conn:
                self.populate(conn)
                conn.execute("UPDATE products SET " + update + " WHERE id='30'")
                with self.assertRaisesRegex(ValueError,"unsupported/stale"):
                    generator.load_allowlists(conn)

    def test_export_rejects_edited_product_evidence(self):
        with sqlite3.connect(":memory:") as conn:
            self.populate(conn)
            conn.execute("UPDATE products SET ingredients='Gelatine' WHERE id='30'")
            with self.assertRaisesRegex(ValueError,"stale product evidence"):
                generator.load_allowlists(conn)

    def test_dry_run_does_not_write_userscript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "products.sqlite"
            userscript_path = tmp_path / "filter.user.js"
            original = (ROOT / "ocado-vegan-filter.user.js").read_text(encoding="utf-8")
            userscript_path.write_text(original, encoding="utf-8")

            with sqlite3.connect(db_path) as conn:
                self.populate(conn)

            with redirect_stdout(StringIO()):
                with mock.patch.object(
                    sys,
                    "argv",
                    [
                        "update_userscript_allowlists.py",
                        "--db",
                        str(db_path),
                        "--userscript",
                        str(userscript_path),
                        "--dry-run",
                    ],
                ):
                    self.assertEqual(generator.main(), 0)

            self.assertEqual(userscript_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
