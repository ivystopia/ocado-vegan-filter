#!/usr/bin/env python3
"""Tests for DB-only vegan classification."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import build_ocado_database
import classify_ocado_vegan as classifier


class ClassifyOcadoVeganTests(unittest.TestCase):
    def create_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        build_ocado_database.create_schema(conn)
        classifier.ensure_classification_schema(conn)
        return conn

    def insert_product(self, conn: sqlite3.Connection, product_id: str = "1", **values: object) -> None:
        columns = ["id", *values.keys()]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"insert into products ({', '.join(columns)}) values ({placeholders})",
            [product_id, *values.values()],
        )

    def insert_category(self, conn: sqlite3.Connection, product_id: str = "1", category_path: str = "fresh-chilled-food/vegetables/beans") -> None:
        conn.execute("insert into product_categories (product_id, category_path) values (?, ?)", (product_id, category_path))

    def classify(self, conn: sqlite3.Connection, product_id: str = "1") -> classifier.ClassificationResult | None:
        return classifier.classify_by_rules(classifier.load_product_context(conn, product_id))

    def test_migration_adds_canonical_columns_and_drops_legacy_phase_columns(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            create table products (
              id text primary key,
              vegan_according_to_manufacturer integer,
              vegan_according_to_ingredients integer
            );
            """
        )

        classifier.ensure_classification_schema(conn)

        columns = {row["name"] for row in conn.execute("pragma table_info(products)")}
        self.assertIn("vegan_status", columns)
        self.assertIn("vegan_reason", columns)
        self.assertNotIn("vegan_according_to_manufacturer", columns)
        self.assertNotIn("vegan_according_to_ingredients", columns)
        self.assertIsNotNone(
            conn.execute(
                "select name from sqlite_master where type = 'table' and name = 'product_vegan_classification_audit'"
            ).fetchone()
        )

    def test_official_vegan_tag_classifies_as_tagged(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, official_vegan=1, name="Tagged Product")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "tagged")

    def test_explicit_manufacturer_vegan_text_classifies_as_manufacturer(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Squeaky Bean Pieces", dietary_information="Suitable for Vegans")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "manufacturer")

    def test_explicit_nonvegan_text_classifies_as_nonvegan(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Sample Dessert", dietary_information="Not suitable for vegans")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "nonvegan")
        self.assertIsNone(result.vegan_reason)

    def test_conflicting_manufacturer_text_classifies_unknown(self) -> None:
        conn = self.create_db()
        self.insert_product(
            conn,
            name="Conflicting Product",
            dietary_information="Suitable for Vegans. Not suitable for vegans.",
        )

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "unknown")
        self.assertIsNone(result.vegan_reason)

    def test_product_name_contains_vegan_classifies_as_name(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Quorn Vegan Chicken Free Slices")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "name")

    def test_obvious_nonvegan_ingredients_classify_as_nonvegan(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Chocolate", ingredients="Sugar, Cocoa Butter, Skimmed Milk Powder")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "nonvegan")

    def test_may_contain_milk_warning_does_not_classify_nonvegan(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Plain Rice", ingredients="Rice. May contain milk.")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "ingredients")

    def test_ambiguous_ingredients_classify_unknown(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Supplement", ingredients="Vitamin D3, Glycerine")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "unknown")

    def test_fortified_wheat_flour_classifies_vegan_when_other_terms_are_safe(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Udon Noodles", ingredients="Wheat Flour (with Calcium, Iron, Niacin (Vitamin B3), Thiamin (Vitamin B1)), Water")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "ingredients")

    def test_allowlisted_ingredients_classify_vegan(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Rummo Spaghetti Pasta No.3", ingredients="Durum wheat semolina")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "ingredients")

    def test_single_ingredient_product_identity_classifies_vegan(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="M&S Extra Fine Beans")
        self.insert_category(conn)

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "ingredients")

    def test_single_ingredient_product_identity_requires_produce_category(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Absolut Lime Flavoured Swedish Vodka")
        self.insert_category(conn, category_path="beer-wine-spirits/spirits/vodka/flavoured")

        result = self.classify(conn)

        self.assertIsNone(result)

    def test_single_ingredient_examples_classify_vegan(self) -> None:
        examples = {
            "281646011": ("Wholegood Organic Mixed Peppers", "fresh-chilled-food/vegetables/peppers"),
            "310608011": ("Ocado Washed Baby Spinach", "fresh-chilled-food/vegetables/cabbage-spinach-greens/spinach"),
            "518478011": ("M&S British White Mushrooms", "fresh-chilled-food/vegetables/mushrooms/white"),
            "518483011": ("M&S British Baby Parsnips", "fresh-chilled-food/vegetables/carrots-root-vegetables/parsnips"),
        }
        for product_id, (name, category_path) in examples.items():
            with self.subTest(product_id=product_id):
                conn = self.create_db()
                self.insert_product(conn, product_id=product_id, name=name)
                self.insert_category(conn, product_id=product_id, category_path=category_path)

                result = self.classify(conn, product_id=product_id)

                self.assertEqual(result.vegan_status, "vegan")
                self.assertEqual(result.vegan_reason, "ingredients")

    def test_codex_payload_validation_and_disagreement_merge(self) -> None:
        pass_a = {
            "1": {
                "product_id": "1",
                "vegan_status": "vegan",
                "vegan_reason": "ingredients",
                "confidence": "certain",
                "summary": "Only beans.",
                "evidence": ["beans"],
                "ambiguity_notes": [],
            }
        }
        pass_b = {
            "1": {
                "product_id": "1",
                "vegan_status": "unknown",
                "vegan_reason": None,
                "confidence": "uncertain",
                "summary": "Ambiguous.",
                "evidence": [],
                "ambiguity_notes": ["ambiguous"],
            }
        }

        validated = classifier.validate_codex_payload({"decisions": list(pass_a.values())}, {"1"})
        merged = classifier.merge_pass_decisions([pass_a, pass_b], "1")

        self.assertEqual(validated["1"]["vegan_status"], "vegan")
        self.assertEqual(merged["vegan_status"], "unknown")
        self.assertIsNone(merged["vegan_reason"])

    def test_write_classification_updates_product_and_audit(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Sample Beans", ingredients="Beans, Water, Salt")
        run_id = classifier.start_run(conn, mode="rules")
        result = self.classify(conn)

        classifier.write_classification(conn, run_id, result)

        product = conn.execute("select vegan_status, vegan_reason from products where id = '1'").fetchone()
        self.assertEqual(product["vegan_status"], "vegan")
        self.assertEqual(product["vegan_reason"], "ingredients")
        self.assertEqual(
            conn.execute("select count(*) from product_vegan_classification_audit where product_id = '1'").fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
