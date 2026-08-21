#!/usr/bin/env python3
"""Tests for DB-only vegan classification."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_ocado_database
import classify_ocado_vegan as classifier


class ClassifyOcadoVeganTests(unittest.TestCase):
    def create_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
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
        self.addCleanup(conn.close)
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
        run_columns = {row["name"] for row in conn.execute("pragma table_info(vegan_classification_runs)")}
        self.assertIn("sync_run_id", run_columns)
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

    def test_official_vegan_tag_overrides_conflicting_ingredient_text(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, official_vegan=1, name="Conflicting capsules", ingredients="Starch, gelatine")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "tagged")
        self.assertIn("apparent_animal_ingredient_conflict", result.evidence)
        self.assertIn(
            r"\bgelati(?:n|ne)\b",
            result.evidence["apparent_animal_ingredient_conflict"]["matched_patterns"],
        )

    def test_explicit_animal_ingredient_overrides_manufacturer_claim(self) -> None:
        conn = self.create_db()
        self.insert_product(
            conn,
            name="Conflicting balm",
            ingredients="Sunflower oil, honey, beeswax",
            dietary_information="Suitable for vegans",
        )

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "nonvegan")
        self.assertIsNone(result.vegan_reason)

    def test_plant_butter_does_not_override_official_vegan_tag(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, official_vegan=1, name="Dark chocolate", ingredients="Cocoa mass, cocoa butter")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "tagged")

    def test_plant_context_animal_words_do_not_classify_nonvegan(self) -> None:
        examples = {
            "Cocoa butter": "Cocoa mass, cocoa butter, sugar",
            "Botanical shea butter": "Aqua, Butyrospermum Parkii Butter, Glycerin",
            "Butter beans": "Butter Beans, Water, Salt",
            "Coconut cream": "Coconut cream, water",
            "Coconut milk": "Coconut milk, water",
            "Coconut cultured milk": "Coconut cultured milk, water",
            "Coconut milk powder": "Coconut milk powder, tapioca starch",
            "Hazelnut milk": "Hazelnut milk, water",
            "Milk thistle": "Milk thistle extract, cellulose",
            "Oat milk chocolate": "Oat milk chocolate, cocoa butter, sugar",
            "Pea milk": "Pea milk concentrate, sugar",
            "Plant milk": "Plant milk, water",
            "Potato milk": "Potato milk, water",
            "Rica milk": "Rica milk, sugar",
            "Milk chocolate flavour": "Milk chocolate flavour, cocoa butter, sugar",
            "Cream of tartar": "Cream of tartar, bicarbonate of soda",
            "Oyster mushrooms": "King oyster mushrooms",
            "Plant-based cheese": "Plant-based cheese, tomato, basil",
            "Chicken-style pieces": "Chicken-style pieces, pea protein, salt",
            "Artificial chicken flavour": "Rice, artificial chicken flavour, salt",
            "Vegan bacon": "Vegan bacon, tomato, lettuce",
            "Vegan keratin": "Vegan keratin, glycerin",
        }
        for name, ingredients in examples.items():
            with self.subTest(name=name):
                conn = self.create_db()
                self.insert_product(conn, name=name, ingredients=ingredients)

                result = self.classify(conn)

                self.assertTrue(result is None or result.vegan_status != "nonvegan", result)

    def test_explicit_animal_variants_classify_nonvegan(self) -> None:
        examples = {
            "Butterfat": "Cocoa mass, butterfat, sugar",
            "Milk fat": "Cocoa mass, milk fat, sugar",
            "Nonfat milk": "Cocoa mass, nonfat milk, sugar",
            "Milk": "Cocoa mass, milk, sugar",
            "Milk protein": "Cocoa mass, milk protein, sugar",
            "Milk chocolate": "Milk chocolate, hazelnuts",
            "Spaced bees wax": "Sunflower oil, bees wax",
            "INCI beeswax": "Aqua, Cera Alba, sunflower oil",
            "Chicken powder": "Rice, chicken powder, salt",
            "Scampi": "Scampi, wheat flour, salt",
            "Hydrolyzed keratin": "Aqua, hydrolyzed keratin, parfum",
        }
        for name, ingredients in examples.items():
            with self.subTest(name=name):
                conn = self.create_db()
                self.insert_product(conn, name=name, ingredients=ingredients)

                result = self.classify(conn)

                self.assertEqual(result.vegan_status, "nonvegan")

    def test_inline_may_contain_does_not_override_official_vegan_tag(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, official_vegan=1, name="Plant protein", ingredients="Soya protein. May contain egg and gluten")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")

    def test_allergy_sufferer_warning_does_not_override_official_vegan_tag(self) -> None:
        conn = self.create_db()
        self.insert_product(
            conn,
            official_vegan=1,
            name="Plant protein",
            ingredients=(
                "Soya protein, sunflower oil. May contain egg and gluten. "
                "Prepared to a vegan recipe but not suitable for egg allergy sufferers"
            ),
        )

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "tagged")

    def test_free_from_statement_does_not_override_official_vegan_tag(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, official_vegan=1, name="Plant oil", ingredients="Evening primrose oil. Free from: lactose and milk products.")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")

    def test_explicit_manufacturer_vegan_text_classifies_as_manufacturer(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Squeaky Bean Pieces", dietary_information="Suitable for Vegans")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "manufacturer")

    def test_standalone_vegan_feature_classifies_as_manufacturer(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Gherkins", features="Vegetarian, Vegan, Gluten free")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "manufacturer")

    def test_incidental_vegan_feature_text_does_not_classify_as_manufacturer(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Recipe Card", features="Great with vegan mayo")

        result = self.classify(conn)

        self.assertIsNone(result)

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

    def test_may_contain_milk_protein_warning_does_not_classify_nonvegan(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Plain Rice", ingredients="Rice. May contain milk protein.")

        result = self.classify(conn)

        self.assertEqual(result.vegan_status, "vegan")
        self.assertEqual(result.vegan_reason, "ingredients")

    def test_other_milk_allergen_warnings_do_not_classify_nonvegan(self) -> None:
        examples = [
            "Rice. Contains traces of milk and soya.",
            "Rice. Allergen present on manufacturing line: milk and soya.",
            "Rice. Not suitable for those with a milk allergy.",
            "Rice. Made in the same environment as our milk chocolate.",
        ]
        for ingredients in examples:
            with self.subTest(ingredients=ingredients):
                conn = self.create_db()
                self.insert_product(conn, name="Plain Rice", ingredients=ingredients)

                result = self.classify(conn)

                self.assertEqual(result.vegan_status, "vegan")
                self.assertEqual(result.vegan_reason, "ingredients")

    def test_free_from_milk_protein_statement_does_not_classify_nonvegan(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, name="Plain Rice", ingredients="Rice. Free from: milk protein.")

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
            "349510011": ("Albert Bartlett Butter Gold Bakers", "fresh-chilled-food/vegetables/potatoes/baking-potatoes"),
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

    def test_sync_run_selector_limits_unclassified_products(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, "1", name="Changed")
        self.insert_product(conn, "2", name="Not targeted")
        conn.execute(
            "create table sync_product_context_changes(run_id integer, product_id text, change_kind text)"
        )
        conn.execute("insert into sync_product_context_changes values (7, '1', 'changed')")
        self.assertEqual(classifier.select_unclassified_product_ids(conn, sync_run_id=7), ["1"])

    def test_codex_refuses_unclassified_officially_tagged_products(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, "1", name="Tagged product")
        conn.execute("insert into product_flags(product_id, flag) values ('1', 'vegan')")

        with self.assertRaisesRegex(RuntimeError, "officially tagged vegan products"):
            classifier.classify_codex(
                conn,
                limit=0,
                batch_size=10,
                passes=2,
                retries=0,
                model=classifier.DEFAULT_CODEX_MODEL,
                reasoning_effort="high",
                codex_bin="unused",
            )

    def test_codex_commits_completed_run_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "products.sqlite"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            build_ocado_database.create_schema(conn)
            classifier.ensure_classification_schema(conn)
            conn.commit()

            count = classifier.classify_codex(
                conn,
                limit=0,
                batch_size=10,
                passes=2,
                retries=0,
                model=classifier.DEFAULT_CODEX_MODEL,
                reasoning_effort="high",
                codex_bin="unused",
            )
            conn.close()

            self.assertEqual(count, 0)
            reopened = sqlite3.connect(db_path)
            run = reopened.execute(
                "select status, total_products, completed_at_epoch from vegan_classification_runs"
            ).fetchone()
            reopened.close()
            self.assertEqual(run[0], "completed")
            self.assertEqual(run[1], 0)
            self.assertIsNotNone(run[2])

    def test_run_records_originating_sync_run(self) -> None:
        conn = self.create_db()

        run_id = classifier.start_run(conn, mode="rules", sync_run_id=42)
        recorded_sync_run_id = conn.execute(
            "select sync_run_id from vegan_classification_runs where id = ?",
            (run_id,),
        ).fetchone()[0]

        self.assertEqual(recorded_sync_run_id, 42)

    def test_codex_commits_interrupted_run_status(self) -> None:
        conn = self.create_db()
        self.insert_product(conn, "1", name="Unresolved product")

        with mock.patch.object(classifier, "classify_codex_contexts", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                classifier.classify_codex(
                    conn,
                    limit=0,
                    batch_size=10,
                    passes=2,
                    retries=0,
                    model=classifier.DEFAULT_CODEX_MODEL,
                    reasoning_effort="high",
                    codex_bin="unused",
                )

        run = conn.execute(
            "select status, error, completed_at_epoch from vegan_classification_runs"
        ).fetchone()
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["error"], "Interrupted by operator.")
        self.assertIsNotNone(run["completed_at_epoch"])

    def test_codex_defaults_use_benchmarked_luna_configuration(self) -> None:
        args = classifier.build_parser().parse_args(["classify-codex"])
        self.assertEqual(args.model, "gpt-5.6-luna")
        self.assertEqual(args.reasoning_effort, "high")
        prompt = classifier.build_codex_prompt([])
        self.assertIn("manufactured non-food goods", prompt)
        self.assertIn("fields materially conflict", prompt)
        self.assertIn("official Ocado vegan tag is authoritative", prompt)


if __name__ == "__main__":
    unittest.main()
