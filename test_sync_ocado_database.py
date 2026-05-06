#!/usr/bin/env python3
"""Tests for the DB-first Ocado sync helpers."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import build_ocado_database
import sync_ocado_database as sync


class SyncOcadoDatabaseTests(unittest.TestCase):
    def create_db(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        build_ocado_database.create_schema(conn)
        sync.ensure_sync_schema(conn)
        return conn

    def test_product_id_from_url_handles_slugged_product_urls(self) -> None:
        self.assertEqual(
            sync.product_id_from_url("https://www.ocado.com/products/m-s-extra-fine-beans/517986011"),
            "517986011",
        )

    def test_rolling_backup_replaces_existing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ocado.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("create table marker(value text)")
            conn.execute("insert into marker(value) values ('first')")
            conn.commit()
            conn.close()

            backup = sync.create_rolling_backup(db_path)
            self.assertTrue(backup.exists())
            self.assertEqual(sqlite3.connect(backup).execute("select value from marker").fetchone()[0], "first")

            conn = sqlite3.connect(db_path)
            conn.execute("delete from marker")
            conn.execute("insert into marker(value) values ('second')")
            conn.commit()
            conn.close()

            backup = sync.create_rolling_backup(db_path)
            self.assertEqual(sqlite3.connect(backup).execute("select value from marker").fetchone()[0], "second")
            self.assertEqual(len(list(db_path.parent.glob("ocado.sqlite.bak*"))), 1)

    def test_apply_current_flags_marks_current_and_stale_products(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.create_db(Path(tmp) / "ocado.sqlite")
            try:
                run_id = sync.start_sync_run(conn)
                sync.upsert_basic_product(conn, "1", name="Category Product", run_id=run_id, current_in_category=1)
                sync.upsert_basic_product(conn, "2", name="Old Product", run_id=run_id)
                sync.upsert_basic_product(conn, "3", name="Sitemap Product", run_id=run_id, current_in_sitemap=1)
                sync.record_discovery(conn, run_id, "1", "category")
                sync.record_discovery(conn, run_id, "3", "sitemap", url="https://www.ocado.com/products/x/3")

                sync.apply_current_flags(conn, run_id)

                rows = {
                    row["id"]: dict(row)
                    for row in conn.execute(
                        """
                        select id, current_on_ocado, current_in_latest_category_crawl,
                               current_in_latest_product_sitemap
                        from products
                        """
                    )
                }
                self.assertEqual(rows["1"]["current_on_ocado"], 1)
                self.assertEqual(rows["1"]["current_in_latest_category_crawl"], 1)
                self.assertEqual(rows["3"]["current_on_ocado"], 1)
                self.assertEqual(rows["3"]["current_in_latest_product_sitemap"], 1)
                self.assertEqual(rows["2"]["current_on_ocado"], 0)
            finally:
                conn.close()

    def test_apply_category_replacement_is_atomic_for_latest_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.create_db(Path(tmp) / "ocado.sqlite")
            try:
                run_id = sync.start_sync_run(conn)
                conn.execute("insert into products(id) values ('1')")
                conn.execute("insert into product_categories(product_id, category_path) values ('1', 'old/category')")
                conn.execute(
                    "insert into sync_product_categories(run_id, product_id, category_path) values (?, '1', 'new/category')",
                    (run_id,),
                )

                sync.apply_category_replacement(conn, run_id)

                categories = [row[0] for row in conn.execute("select category_path from product_categories")]
                self.assertEqual(categories, ["new/category"])
            finally:
                conn.close()

    def test_apply_product_detail_updates_product_tables_and_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.create_db(Path(tmp) / "ocado.sqlite")
            try:
                run_id = sync.start_sync_run(conn)
                data = {
                    "product": {
                        "retailerProductId": "222",
                        "productId": "uuid-222",
                        "name": "Sample Vegan Beans",
                        "brand": "Bean Brand",
                        "type": "REGULAR",
                        "packSizeDescription": "400g",
                        "alcohol": False,
                        "available": False,
                        "isNew": True,
                        "isInCurrentCatalog": True,
                        "price": {"amount": "1.25", "currency": "GBP"},
                        "unitPrice": {"price": {"amount": "31.25", "currency": "GBX"}, "unit": "fop.price.per.100g"},
                        "ratingSummary": {"count": 12, "overallRating": "4.5"},
                        "iconAttributes": [{"file": "vegan", "label": "Vegan"}],
                        "promotions": [{"description": "Now £1.25", "promoId": "promo-1"}],
                    },
                    "bopData": {
                        "breadcrumbs": [{"categoryId": "cat-1", "categoryName": "Beans"}],
                        "detailedDescription": "Good <b>beans</b>.",
                        "fields": [
                            {"title": "ingredients", "content": "Beans (99%), <b>Water</b>, Salt"},
                            {
                                "title": "nutritionalData",
                                "content": (
                                    "<table><tr><th>Typical Values</th><th>Per 100g</th></tr>"
                                    "<tr><td>Energy (kJ)</td><td>100</td></tr>"
                                    "<tr><td>Energy (kcal)</td><td>24</td></tr>"
                                    "<tr><td>Fat (g)</td><td>0.4</td></tr></table>"
                                ),
                            },
                        ],
                    },
                    "bopPromotions": [{"description": "Now £1.25", "promoId": "promo-1"}],
                }

                sync.apply_product_detail(conn, run_id, "222", data, 200)
                sync.rebuild_fts(conn)

                product = conn.execute("select * from products where id = '222'").fetchone()
                self.assertEqual(product["name"], "Sample Vegan Beans")
                self.assertEqual(product["ingredients"], "Beans (99%), Water, Salt")
                self.assertEqual(product["price_gbp"], 1.25)
                self.assertEqual(product["unit_price_gbp"], 0.3125)
                self.assertEqual(product["official_vegan"], 1)
                self.assertEqual(product["has_full_product_detail"], 1)
                self.assertNotIn("available", [row["name"] for row in conn.execute("pragma table_info(products)")])
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


if __name__ == "__main__":
    unittest.main()
