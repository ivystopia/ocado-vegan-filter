#!/usr/bin/env python3
"""Tests for the local Ocado SQLite importer."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_ocado_database as importer


class BuildOcadoDatabaseTests(unittest.TestCase):
    def test_html_to_text_removes_formatting_and_collapses_whitespace(self) -> None:
        text = importer.html_to_text(
            "Wheat Flour [with added <b>Calcium Carbonate</b>, Iron]<br />Water"
        )

        self.assertEqual(text, "Wheat Flour [with added Calcium Carbonate, Iron] Water")

    def test_money_to_gbp_normalizes_gbp_and_gbx(self) -> None:
        self.assertEqual(importer.money_to_gbp({"amount": "2.70", "currency": "GBP"}), 2.7)
        self.assertEqual(importer.money_to_gbp({"amount": "85", "currency": "GBX"}), 0.85)
        self.assertIsNone(importer.money_to_gbp({"amount": "2.70", "currency": "EUR"}))

    def test_parse_nutrition_rows_extracts_product_specific_values(self) -> None:
        rows = importer.parse_nutrition_rows(
            "abc",
            """
            <table class="nutrition"><tbody>
              <tr><th>Typical Values</th><th>Per 100g</th><th>RDE (Adults)</th><th>Per slice 45g</th></tr>
              <tr><td>Energy</td><td>985kJ</td><td>8400kJ</td><td>443kJ</td></tr>
              <tr><td></td><td>233kcal</td><td>2000kcal</td><td>105kcal</td></tr>
              <tr><td>Fat</td><td>2.7g</td><td>70g</td><td>1.2g</td></tr>
              <tr><td>Salt (g)</td><td>0.81</td><td>6g</td><td>0.37</td></tr>
            </tbody></table>
            """,
        )

        self.assertIn(("abc", "energy", "per 100g", 985.0, "kj", "985kJ"), rows)
        self.assertIn(("abc", "energy", "per 100g", 233.0, "kcal", "233kcal"), rows)
        self.assertIn(("abc", "fat", "per slice 45g", 1.2, "g", "1.2g"), rows)
        self.assertIn(("abc", "salt", "per 100g", 0.81, "g", "0.81"), rows)
        self.assertFalse(any(row[2] == "rde adults" for row in rows))

    def test_build_database_imports_union_latest_values_text_nutrition_and_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manufacturer_audit = root / "manufacturer.json"
            product_universe = root / "universe.jsonl"
            bop_raw = root / "bop.jsonl"
            precheck = root / "precheck.jsonl"
            manufacturer_meta = root / "manufacturer_meta.json"
            ingredients_meta = root / "ingredients_meta.json"
            output = root / "ocado.sqlite"

            manufacturer_audit.write_text(
                json.dumps(
                    [
                        {
                            "retailerProductId": "111",
                            "name": "Old Manufacturer Vegan Name",
                            "brand": "Old Brand",
                            "url": "https://www.ocado.com/products/111/details",
                            "iconAttributes": [{"file": "vegetarian", "label": "Vegetarian"}],
                            "categoryPaths": ["old/category"],
                            "matchedFields": [
                                {"title": "dietaryInformation", "content": "Suitable for <b>Vegans</b>"}
                            ],
                        },
                        {
                            "retailerProductId": "999",
                            "name": "Historical Only Vegan",
                            "brand": "History Brand",
                            "url": "https://www.ocado.com/products/999/details",
                            "categoryPaths": ["old/only"],
                            "matchedFields": [
                                {"title": "features", "content": "Suitable for Vegans"}
                            ],
                        },
                    ]
                ),
                encoding="utf-8",
            )
            manufacturer_meta.write_text(json.dumps({"generatedAtEpoch": 100}), encoding="utf-8")
            ingredients_meta.write_text(json.dumps({"generatedAtEpoch": 200}), encoding="utf-8")
            product_universe.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "retailerProductId": "111",
                            "name": "Latest Manufacturer Vegan Name",
                            "brand": "Latest Brand",
                            "url": "https://www.ocado.com/products/111/details",
                            "categoryPaths": ["current/category"],
                            "iconAttributes": [{"file": "vegetarian", "label": "Vegetarian"}],
                            "seenInCategoryPages": 1,
                        },
                        {
                            "retailerProductId": "222",
                            "name": "Sample Beans",
                            "brand": "Bean Brand",
                            "url": "https://www.ocado.com/products/222/details",
                            "categoryPaths": ["food/beans"],
                            "iconAttributes": [{"file": "vegan", "label": "Vegan"}],
                            "seenInCategoryPages": 2,
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            bop_raw.write_text(
                json.dumps(
                    {
                        "retailerProductId": "222",
                        "status": 200,
                        "fetchedAtEpoch": 300,
                        "data": {
                            "product": {
                                "retailerProductId": "222",
                                "productId": "uuid-222",
                                "name": "Sample Beans",
                                "brand": "Bean Brand",
                                "type": "REGULAR",
                                "packSizeDescription": "400g",
                                "alcohol": False,
                                "available": False,
                                "isNew": True,
                                "isInCurrentCatalog": True,
                                "price": {"amount": "1.25", "currency": "GBP"},
                                "unitPrice": {
                                    "price": {"amount": "31.25", "currency": "GBX"},
                                    "unit": "fop.price.per.100g",
                                },
                                "ratingSummary": {"count": 12, "overallRating": "4.5"},
                                "iconAttributes": [{"file": "vegan", "label": "Vegan"}],
                                "promotions": [{"description": "Now £1.25", "promoId": "promo-1"}],
                            },
                            "bopData": {
                                "breadcrumbs": [{"categoryId": "cat-1", "categoryName": "Beans"}],
                                "detailedDescription": "Good <b>beans</b>.",
                                "fields": [
                                    {
                                        "title": "ingredients",
                                        "content": "Beans (99%), <b>Water</b>, Salt",
                                    },
                                    {
                                        "title": "nutritionalData",
                                        "content": (
                                            "<table><tr><th>Typical Values</th><th>Per 100g</th>"
                                            "<th>RDE (Adults)</th></tr>"
                                            "<tr><td>Energy</td><td>100kJ</td><td>8400kJ</td></tr>"
                                            "<tr><td></td><td>24kcal</td><td>2000kcal</td></tr>"
                                            "<tr><td>Fat</td><td>0.4g</td><td>70g</td></tr></table>"
                                        ),
                                    },
                                ],
                            },
                            "bopPromotions": [{"description": "Now £1.25", "promoId": "promo-1"}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            precheck.write_text(
                json.dumps(
                    {
                        "retailerProductId": "222",
                        "status": "candidate",
                        "reason": "",
                        "evidence": [{"source": "ingredients", "text": "Beans, Water, Salt"}],
                        "precheckedAtEpoch": 400,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            counts = importer.build_database(
                importer.BuildConfig(
                    output=output,
                    product_universe=product_universe,
                    bop_raw=bop_raw,
                    precheck_audit=precheck,
                    manufacturer_audit=manufacturer_audit,
                    ingredients_audit=root / "missing_audit.jsonl",
                    classifier_a=root / "missing_a.jsonl",
                    classifier_b=root / "missing_b.jsonl",
                    manufacturer_meta=manufacturer_meta,
                    ingredients_meta=ingredients_meta,
                )
            )

            self.assertEqual(counts["products"], 3)
            conn = sqlite3.connect(output)
            conn.row_factory = sqlite3.Row
            try:
                latest = conn.execute("select * from products where id = '111'").fetchone()
                self.assertEqual(latest["name"], "Latest Manufacturer Vegan Name")
                self.assertIsNone(self.column_named(conn, "products", "vegan_according_to_manufacturer"))
                self.assertIsNone(self.column_named(conn, "products", "vegan_according_to_ingredients"))
                self.assertEqual(
                    conn.execute(
                        "select count(*) from manufacturer_vegan_evidence where product_id = '111'"
                    ).fetchone()[0],
                    1,
                )

                beans = conn.execute("select * from products where id = '222'").fetchone()
                self.assertEqual(beans["ingredients"], "Beans (99%), Water, Salt")
                self.assertEqual(beans["price_gbp"], 1.25)
                self.assertEqual(beans["unit_price_gbp"], 0.3125)
                self.assertEqual(beans["official_vegan"], 1)
                self.assertIsNone(beans["vegan_status"])
                self.assertIsNone(beans["vegan_reason"])
                self.assertIsNone(self.column_named(conn, "products", "available"))

                categories = {
                    row["category_path"]
                    for row in conn.execute("select category_path from product_categories where product_id = '111'")
                }
                self.assertEqual(categories, {"old/category", "current/category"})

                self.assertEqual(
                    conn.execute("select count(*) from product_nutrition where product_id = '222'").fetchone()[0],
                    3,
                )
                self.assertEqual(
                    conn.execute("select count(*) from products_fts where products_fts match 'beans'").fetchone()[0],
                    1,
                )
            finally:
                conn.close()

    @staticmethod
    def column_named(conn: sqlite3.Connection, table: str, column: str) -> str | None:
        for row in conn.execute(f"pragma table_info({table})"):
            if row["name"] == column:
                return row["name"]
        return None


if __name__ == "__main__":
    unittest.main()
