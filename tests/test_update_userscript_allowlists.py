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


class UpdateUserscriptAllowlistsTests(unittest.TestCase):
    def test_replace_set_preserves_retained_order_and_appends_new_ids(self) -> None:
        source = "const IDS = new Set(\n    `\n  30 10 20\n  `\n      .trim()\n      .split(/\\s+/),\n  );"
        updated = generator.replace_set(source, "IDS", ["10", "30", "40"])
        self.assertIn("  30 10\n  40", updated)

    def test_regenerate_uses_only_canonical_vegan_rows(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(
            "create table products(id text, official_vegan integer, vegan_status text, vegan_reason text)"
        )
        conn.execute("create table product_flags(product_id text, flag text)")
        conn.executemany(
            "insert into products values (?, ?, ?, ?)",
            [
                ("30", 1, "vegan", "tagged"),
                ("10", 0, "vegan", "manufacturer"),
                ("20", 0, "vegan", "name"),
                ("40", 0, "vegan", "ingredients"),
                ("50", 0, "unknown", None),
                ("60", 0, "nonvegan", None),
            ],
        )
        allowlists = generator.load_allowlists(conn)
        source = (ROOT / "ocado-vegan-filter.user.js").read_text(encoding="utf-8")
        updated = generator.regenerate(source, allowlists, "9.9.9")
        self.assertEqual(generator.extract_set(updated, "OFFICIAL_VEGAN_PRODUCT_IDS"), {"30"})
        self.assertEqual(generator.extract_set(updated, "MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS"), {"10", "20"})
        self.assertEqual(generator.extract_set(updated, "INGREDIENTS_VEGAN_PRODUCT_IDS"), {"40"})
        self.assertEqual(generator.extract_set(updated, "KNOWN_NON_VEGAN_PRODUCT_IDS"), {"60"})
        self.assertIn("// @version     9.9.9", updated)
        self.assertIn("Recognised vegan product IDs: 4", updated)
        self.assertIn("Additional vegan product IDs recognised by this script: 3", updated)
        self.assertIn("Known non-vegan product IDs: 1", updated)

    def test_load_allowlists_rejects_official_tag_classification_conflicts(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(
            "create table products(id text, official_vegan integer, vegan_status text, vegan_reason text)"
        )
        conn.execute("create table product_flags(product_id text, flag text)")
        conn.executemany(
            "insert into products values (?, ?, ?, ?)",
            [
                ("10", 1, "nonvegan", None),
                ("20", 0, "unknown", None),
            ],
        )
        conn.execute("insert into product_flags values ('20', 'vegan')")

        with self.assertRaisesRegex(ValueError, "Refusing to export 2 products"):
            generator.load_allowlists(conn)

    def test_dry_run_does_not_write_userscript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "products.sqlite"
            userscript_path = tmp_path / "filter.user.js"
            original = (ROOT / "ocado-vegan-filter.user.js").read_text(encoding="utf-8")
            userscript_path.write_text(original, encoding="utf-8")

            conn = sqlite3.connect(db_path)
            conn.execute(
                "create table products(id text, official_vegan integer, vegan_status text, vegan_reason text)"
            )
            conn.execute("create table product_flags(product_id text, flag text)")
            conn.execute("insert into products values ('10', 0, 'vegan', 'ingredients')")
            conn.commit()
            conn.close()

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
