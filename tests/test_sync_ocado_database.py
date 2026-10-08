#!/usr/bin/env python3
"""Tests for the DB-first Ocado sync helpers."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_ocado_database
import sync_ocado_database as sync
import classify_ocado_vegan as classifier
import update_userscript_allowlists as allowlists


class SyncOcadoDatabaseTests(unittest.TestCase):
    def test_sync_upgrades_old_provenance_columns_without_deleting_decisions(self):
        with tempfile.TemporaryDirectory() as tmp, closing(self.create_db(Path(tmp)/'old.sqlite')) as conn:
            conn.execute("INSERT INTO products(id,name,ingredients,current_on_ocado,vegan_status,vegan_reason) VALUES ('1','Beans','Beans',1,'vegan','ingredients')")
            conn.execute('ALTER TABLE products DROP COLUMN vegan_policy_version')
            sync.ensure_sync_schema(conn)
            self.assertEqual(tuple(conn.execute('SELECT vegan_status,vegan_reason FROM products').fetchone()),('vegan','ingredients'))
            sync.snapshot_classifier_contexts(conn)
            run=sync.start_sync_run(conn)
            conn.execute("UPDATE products SET ingredients='Milk' WHERE id='1'")
            sync.finalize_context_changes(conn,run)
            self.assertIsNone(conn.execute('SELECT vegan_status FROM products').fetchone()[0])

    def acquired_failed_run(self,conn):
        conn.execute("INSERT INTO products(id,name,ingredients,current_on_ocado,vegan_status,vegan_reason) VALUES ('1','Beans','Beans',1,'vegan','ingredients')")
        run=sync.start_sync_run(conn);sync.snapshot_classifier_contexts(conn)
        index='<sitemapindex><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-categories-part1.xml</loc></sitemap><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-products-part1.xml</loc></sitemap></sitemapindex>'
        sync.retain_discovery_source(conn,run,sync.SITEMAP_INDEX_URL,index,'sitemap-index')
        for kind,url in [('categories','https://www.ocado.com/categories/food/1'),('products','https://www.ocado.com/products/1')]:
            sync.retain_discovery_source(conn,run,'https://www.ocado.com/sitemaps/sitemap-'+kind+'-part1.xml','<urlset><url><loc>'+url+'</loc></url></urlset>',kind)
        sync.record_category_fetch(conn,run,{'slugPath':'food','categoryId':'1','status':200},1)
        for source in ('category','sitemap'):sync.record_discovery(conn,run,'1',source)
        data={'product':{'retailerProductId':'1','name':'Beans','iconAttributes':[]},'bopData':{'fields':[{'title':'ingredients','content':'Beans, Milk'}]}}
        sync.apply_product_detail(conn,run,'1',data,200)
        sync.log_detail_fetch(conn,run,'1',1,{'status':200})
        for field,value in [('category_count',1),('category_pages_succeeded',1),('detail_fetch_succeeded',1)]:sync.record_run_count(conn,run,field,value)
        sync.finish_sync_run(conn,run,'failed','OperationalError: no such column: vegan_policy_version')
        conn.commit();return run

    def test_complete_acquisition_can_finish_offline_without_rewriting_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite'
            with closing(self.create_db(path)) as conn:source=self.acquired_failed_run(conn)
            args=sync.build_parser().parse_args(['--db',str(path),'--finish-run-id',str(source)])
            with mock.patch.object(sync,'http_get_text',side_effect=AssertionError('must use retained evidence')),mock.patch.object(sync,'browser_session',side_effect=AssertionError('must not browse')):
                self.assertEqual(sync.resume_finalization(args),0)
            with closing(sqlite3.connect(path)) as conn:
                rows=conn.execute('SELECT id,status,resumed_from_run_id,detail_fetch_attempted FROM sync_runs ORDER BY id').fetchall()
                self.assertEqual(rows,[(source,'failed',None,0),(source+1,'completed',source,0)])
                self.assertIsNone(conn.execute('SELECT vegan_status FROM products').fetchone()[0])
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_context_baseline'").fetchone())
                self.assertIn('Beans, Milk',conn.execute('SELECT after_context_json FROM sync_product_context_changes WHERE run_id=?',(source+1,)).fetchone()[0])

    def test_finalization_rejects_missing_capture_pagination_and_raw_detail(self):
        for damaged in ('capture','pagination','detail'):
            with self.subTest(damaged=damaged),tempfile.TemporaryDirectory() as tmp,closing(self.create_db(Path(tmp)/'db.sqlite')) as conn:
                source=self.acquired_failed_run(conn)
                if damaged=='capture':conn.execute("DELETE FROM sync_discovery_sources WHERE source_kind='products'")
                elif damaged=='pagination':conn.execute("UPDATE sync_category_page_fetches SET next_page_token='missing'")
                else:conn.execute('DELETE FROM product_detail_raw_latest')
                with self.assertRaises(ValueError):sync.validate_finalization_source(conn,source)

    def test_interrupted_finalization_preserves_source_and_rejects_running_writer(self):
        with tempfile.TemporaryDirectory() as tmp,closing(self.create_db(Path(tmp)/'db.sqlite')) as conn:
            source=self.acquired_failed_run(conn)
            retry=sync.start_sync_run(conn)
            conn.execute('UPDATE sync_runs SET resumed_from_run_id=? WHERE id=?',(source,retry))
            with self.assertRaisesRegex(ValueError,'latest acquisition'):sync.validate_finalization_source(conn,source)
            sync.finish_sync_run(conn,retry,'failed','Interrupted finalization')
            self.assertEqual(sync.validate_finalization_source(conn,source)['id'],source)
            sync.start_sync_run(conn)
            with self.assertRaisesRegex(ValueError,'latest acquisition'):sync.validate_finalization_source(conn,source)

    def test_sitemap_discovery_reads_all_parts_deduplicates_and_retains_sources(self):
        index = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-products-part2.xml</loc></sitemap><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-products-part1.xml</loc></sitemap><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-products-part2.xml</loc></sitemap><sitemap><loc>https://elsewhere.test/sitemap-products-part3.xml</loc></sitemap></sitemapindex>'
        def page(ids):
            return '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>https://www.ocado.com/products/item/'+p+'</loc></url>' for p in ids)+'</urlset>'
        captures = []
        with mock.patch.object(sync, 'http_get_text', side_effect=[index, page(['1','2']), page(['2','3'])]) as fetch:
            self.assertEqual(sync.product_ids_from_sitemap(30, source_recorder=lambda *args: captures.append(args)),
                             [('1','https://www.ocado.com/products/item/1'),('2','https://www.ocado.com/products/item/2'),('3','https://www.ocado.com/products/item/3')])
        self.assertEqual(fetch.call_count,3)
        self.assertEqual([row[2] for row in captures],['sitemap-index','products','products'])

    def test_failed_or_missing_sitemap_part_never_silently_returns_partial_catalogue(self):
        index='<sitemapindex><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-products-part1.xml</loc></sitemap><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-products-part2.xml</loc></sitemap></sitemapindex>'
        with mock.patch.object(sync,'http_get_text',side_effect=[index,'<urlset><url><loc>https://www.ocado.com/products/1</loc></url></urlset>',RuntimeError('unavailable')]):
            with self.assertRaisesRegex(RuntimeError,'unavailable'): sync.product_ids_from_sitemap(30)
        with mock.patch.object(sync,'http_get_text',return_value='<sitemapindex/>'):
            with self.assertRaisesRegex(ValueError,'No products'): sync.product_ids_from_sitemap(30)

    def test_category_leaf_selection_spans_multiple_parts(self):
        index='<sitemapindex><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-categories-part1.xml</loc></sitemap><sitemap><loc>https://www.ocado.com/sitemaps/sitemap-categories-part2.xml</loc></sitemap></sitemapindex>'
        def page(url): return '<urlset><url><loc>'+url+'</loc></url></urlset>'
        with mock.patch.object(sync,'http_get_text',side_effect=[index,page('https://www.ocado.com/categories/food/10'),page('https://www.ocado.com/categories/food/fruit/20')]):
            self.assertEqual(sync.load_leaf_categories(30),[('food/fruit','20')])

    def create_db(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        build_ocado_database.create_schema(conn)
        sync.ensure_sync_schema(conn)
        return conn

    def test_failed_sync_preserves_baseline_across_reopen_and_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ocado.sqlite"
            conn = self.create_db(path)
            classifier.ensure_classification_schema(conn, drop_legacy=False)
            with conn:
                conn.execute(
                    """INSERT INTO products
                       (id, name, ingredients, official_vegan, vegan_status, vegan_reason, current_on_ocado)
                       VALUES ('1', 'Sauce', 'Tomato', 0, 'vegan', 'ingredients', 1)"""
                )
                run_id = sync.start_sync_run(conn)
                sync.snapshot_classifier_contexts(conn)
            data = {
                "product": {"retailerProductId": "1", "name": "Sauce", "iconAttributes": []},
                "bopData": {"fields": [{"title": "ingredients", "content": "Milk, Tomato"}]},
            }
            with conn:
                sync.apply_product_detail(conn, run_id, "1", data, 200)
                sync.upsert_basic_product(conn, "2", name="New product", run_id=run_id)
                sync.finish_sync_run(conn, run_id, "failed", "Simulated next-batch failure")
            with self.assertRaisesRegex(ValueError, "incomplete catalogue sync"):
                allowlists.load_allowlists(conn)
            conn.close()

            with closing(sqlite3.connect(path)) as conn:
                conn.row_factory = sqlite3.Row
                with conn:
                    retry_id = sync.start_sync_run(conn)
                    sync.snapshot_classifier_contexts(conn)
                    sync.apply_product_detail(conn, retry_id, "1", data, 200)
                    sync.finalize_product_flags(conn, retry_id)
                    sync.finalize_context_changes(conn, retry_id)
                    sync.finish_sync_run(conn, retry_id, "completed")
                self.assertIsNone(conn.execute("SELECT vegan_status FROM products WHERE id = '1'").fetchone()[0])
                self.assertEqual(classifier.select_unclassified_product_ids(conn, sync_run_id=retry_id), ["1", "2"])
                before = conn.execute(
                    "SELECT before_context_json FROM sync_product_context_changes WHERE run_id = ? AND product_id = '1'",
                    (retry_id,),
                ).fetchone()[0]
                self.assertIn('"ingredients":"Tomato"', before)
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sync_context_baseline'").fetchone())
                with self.assertRaisesRegex(ValueError, "unclassified current products"):
                    allowlists.load_allowlists(conn)

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
            with closing(sqlite3.connect(backup)) as backup_conn:
                self.assertEqual(backup_conn.execute("select value from marker").fetchone()[0], "first")

            conn = sqlite3.connect(db_path)
            conn.execute("delete from marker")
            conn.execute("insert into marker(value) values ('second')")
            conn.commit()
            conn.close()

            backup = sync.create_rolling_backup(db_path)
            with closing(sqlite3.connect(backup)) as backup_conn:
                self.assertEqual(backup_conn.execute("select value from marker").fetchone()[0], "second")
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
                conn.execute("insert into products(id) values ('historical'),('sitemap-only')")
                conn.execute("insert into product_categories(product_id, category_path) values ('1', 'old/category')")
                conn.execute("insert into product_categories(product_id,category_path) values ('historical','last/known'),('sitemap-only','stale/current')")
                sync.record_discovery(conn,run_id,'1','category')
                sync.record_discovery(conn,run_id,'sitemap-only','sitemap')
                conn.execute(
                    "insert into sync_product_categories(run_id, product_id, category_path) values (?, '1', 'new/category')",
                    (run_id,),
                )

                sync.apply_category_replacement(conn, run_id)

                categories = [tuple(row) for row in conn.execute("select product_id,category_path from product_categories order by product_id")]
                self.assertEqual(categories, [('1','new/category'),('historical','last/known')])
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
                sync.finalize_product_flags(conn, run_id)
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
                self.assertEqual(
                    conn.execute("select count(*) from product_detail_raw_latest where product_id = '222'").fetchone()[0],
                    1,
                )
            finally:
                conn.close()

    def test_detail_refresh_clears_removed_fields_evidence_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.create_db(Path(tmp) / "ocado.sqlite")
            try:
                run_id = sync.start_sync_run(conn)
                conn.execute(
                    "insert into products(id, ingredients, official_vegan, current_on_ocado) values ('1', 'Old milk', 1, 1)"
                )
                conn.execute("insert into product_flags(product_id, flag) values ('1', 'vegan')")
                conn.execute(
                    "insert into manufacturer_vegan_evidence(product_id, field_title, content) values ('1', 'features', 'Vegan')"
                )
                sync.apply_product_detail(
                    conn,
                    run_id,
                    "1",
                    {"product": {"name": "Changed product", "iconAttributes": []}, "bopData": {"fields": []}},
                    200,
                )
                sync.finalize_product_flags(conn, run_id)
                product = conn.execute("select ingredients, official_vegan from products where id = '1'").fetchone()
                self.assertIsNone(product["ingredients"])
                self.assertEqual(product["official_vegan"], 0)
                self.assertEqual(conn.execute("select count(*) from product_flags where product_id = '1'").fetchone()[0], 0)
                self.assertEqual(
                    conn.execute("select count(*) from manufacturer_vegan_evidence where product_id = '1'").fetchone()[0], 0
                )
            finally:
                conn.close()

    def test_full_detail_selection_is_defaultable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self.create_db(Path(tmp) / "ocado.sqlite")
            try:
                conn.execute("insert into products(id, current_on_ocado, has_full_product_detail) values ('1', 1, 1)")
                conn.execute("insert into products(id, current_on_ocado, has_full_product_detail) values ('2', 1, 0)")
                self.assertEqual(sync.products_needing_detail(conn, missing_only=True), ["2"])
                self.assertEqual(sync.products_needing_detail(conn, missing_only=False), ["1", "2"])
            finally:
                conn.close()

    def test_change_payload_ignores_url_and_nonclassification_display_flags(self) -> None:
        before = {
            "product": {"id": "1", "name": "Beans", "ingredients": "Beans", "url": "https://old"},
            "flags": ["vegetarian"],
            "categories": ["old/category"],
            "manufacturer_vegan_evidence": [],
        }
        after = {
            "product": {"id": "1", "name": "Beans", "ingredients": "Beans", "url": "https://new"},
            "flags": ["organic"],
            "categories": ["new/category"],
            "manufacturer_vegan_evidence": [],
        }
        self.assertEqual(sync.classifier_change_payload(before), sync.classifier_change_payload(after))

        after["flags"].append("vegan")
        self.assertNotEqual(sync.classifier_change_payload(before), sync.classifier_change_payload(after))


if __name__ == "__main__":
    unittest.main()
