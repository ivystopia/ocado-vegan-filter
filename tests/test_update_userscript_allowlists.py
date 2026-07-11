#!/usr/bin/env python3
"""Tests for deterministic userscript allowlist generation."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import update_userscript_allowlists as generator


class UpdateUserscriptAllowlistsTests(unittest.TestCase):
    def test_regenerate_uses_only_canonical_vegan_rows(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("create table products(id text, vegan_status text, vegan_reason text)")
        conn.executemany(
            "insert into products values (?, ?, ?)",
            [
                ("30", "vegan", "tagged"),
                ("10", "vegan", "manufacturer"),
                ("20", "vegan", "name"),
                ("40", "vegan", "ingredients"),
                ("50", "unknown", None),
                ("60", "nonvegan", None),
            ],
        )
        allowlists = generator.load_allowlists(conn)
        source = (ROOT / "ocado-vegan-filter.user.js").read_text(encoding="utf-8")
        updated = generator.regenerate(source, allowlists, "9.9.9")
        self.assertEqual(generator.extract_set(updated, "OFFICIAL_VEGAN_PRODUCT_IDS"), {"30"})
        self.assertEqual(generator.extract_set(updated, "MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS"), {"10", "20"})
        self.assertEqual(generator.extract_set(updated, "INGREDIENTS_VEGAN_PRODUCT_IDS"), {"40"})
        self.assertIn("// @version     9.9.9", updated)
        self.assertIn("Recognised vegan product IDs: 4", updated)
        self.assertIn("Additional vegan product IDs added by this script: 3", updated)


if __name__ == "__main__":
    unittest.main()
