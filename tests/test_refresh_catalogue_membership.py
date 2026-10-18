"""Membership refresh is isolated, complete, and preserves existing decisions/evidence."""

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zlib
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import refresh_catalogue_membership as refresh
import published_baseline as policy
import build_ocado_database as builder
import classify_ocado_vegan as classifier
import sync_ocado_database as sync


class MembershipRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "canonical.sqlite"
        self.patch = mock.patch.object(
            policy, "baseline_decisions", return_value={"1": ("vegan", "ingredients"), "2": ("nonvegan", None)}
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for name in ("canonical.sqlite", "discovery.sqlite"):
            with closing(sqlite3.connect(self.root / name)) as c, c:
                builder.create_schema(c)
                classifier.ensure_classification_schema(c)
                sync.ensure_sync_schema(c)
                c.executemany(
                    "INSERT INTO products(id,name,ingredients,current_on_ocado,has_full_product_detail) VALUES(?,?,?,1,1)",
                    [("1", "Published rice", "Rice"), ("2", "Published milk", "Milk")],
                )
        with closing(sqlite3.connect(self.db)) as c, c:
            policy.restore(c, self.root / "backup.json.gz")
            before = refresh.membership(c)
        with closing(sqlite3.connect(self.root / "discovery.sqlite")) as c, c:
            run = sync.start_sync_run(c)
            c.execute("UPDATE products SET current_on_ocado=0,name='Changed source copy' WHERE id='1'")
            c.execute(
                "INSERT INTO products(id,name,ingredients,current_on_ocado,has_full_product_detail,last_detail_fetch_status) VALUES('3','New apples','Apples',1,1,200)"
            )
            raw = b'{"product":{"retailerProductId":"3"}}'
            c.execute(
                "INSERT INTO product_detail_raw_latest VALUES(?,?,?,?,?)",
                ("3", run, hashlib.sha256(raw).hexdigest(), zlib.compress(raw), 123),
            )
            c.execute("INSERT INTO product_categories VALUES('3','fresh-chilled-food/fruit/apples')")
            sync.finish_sync_run(c, run, "completed")
        self.state = {
            "stage": "complete",
            "before": before,
            "before_hash": policy.digest(before),
            "run_id": run,
            "new_ids": ["3"],
            "removed_ids": ["1"],
            "returned_ids": [],
            "current_count": 2,
        }
        self.save()

    def save(self):
        (self.root / "manifest.json").write_text(json.dumps(self.state))

    def test_preview_does_not_change_canonical(self):
        result = refresh.import_completed(self.db, self.root)
        self.assertEqual(result["new"], 1)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM products").fetchone()[0], 2)

    def test_applies_membership_and_new_details_without_reclassification(self):
        refresh.import_completed(self.db, self.root, apply=True)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(
                c.execute("SELECT id,name,vegan_status,current_on_ocado FROM products ORDER BY id").fetchall(),
                [
                    ("1", "Published rice", "vegan", 0),
                    ("2", "Published milk", "nonvegan", 1),
                    ("3", "New apples", "unknown", 1),
                ],
            )
            self.assertEqual(c.execute("SELECT product_id FROM product_detail_raw_latest").fetchall(), [("3",)])
            policy.validate(c)

    def test_failed_discovery_cannot_activate(self):
        with closing(sqlite3.connect(self.root / "discovery.sqlite")) as c, c:
            c.execute("UPDATE sync_runs SET status='failed'")
        with self.assertRaisesRegex(ValueError, "errors remain"):
            refresh.import_completed(self.db, self.root, apply=True)

    def test_concurrent_membership_change_rejected(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE products SET current_on_ocado=0 WHERE id='2'")
        with self.assertRaisesRegex(ValueError, "concurrently"):
            refresh.import_completed(self.db, self.root, apply=True)

    def test_incomplete_new_product_accounting_rejected(self):
        self.state["new_ids"] = []
        self.save()
        with self.assertRaisesRegex(ValueError, "accounting mismatch"):
            refresh.import_completed(self.db, self.root, apply=True)

    def test_activation_failure_rolls_back_membership_and_inserts(self):
        original = policy.validate
        calls = []

        def fail_final(c):
            calls.append(1)
            if len(calls) > 1:
                raise ValueError("forced final failure")
            return original(c)

        with mock.patch.object(policy, "validate", side_effect=fail_final):
            with self.assertRaisesRegex(ValueError, "forced final"):
                refresh.import_completed(self.db, self.root, apply=True)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(refresh.membership(c), [("1", 1), ("2", 1)])

    def test_all_advertised_parts_are_captured(self):
        prefix = "https://www.ocado.com/sitemaps/"
        parts = [
            prefix + "sitemap-categories-part1.xml",
            prefix + "sitemap-products-part1.xml",
            prefix + "sitemap-products-part2.xml",
        ]

        def xml(urls):
            return (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                + "".join("<url><loc>" + u + "</loc></url>" for u in urls)
                + "</urlset>"
            )

        responses = {
            refresh.INDEX: xml(parts),
            parts[0]: xml(["https://www.ocado.com/categories/fruit/1"]),
            parts[1]: xml(["https://www.ocado.com/products/apples/10"]),
            parts[2]: xml(["https://www.ocado.com/products/pears/20"]),
        }
        with mock.patch.object(sync, "http_get_text", side_effect=lambda url, timeout: responses[url]):
            leaves, products = refresh.discovery_urls(self.root)
        self.assertEqual(leaves, [("fruit", "1")])
        self.assertEqual([p[0] for p in products], ["10", "20"])
        self.assertEqual(len(json.loads((self.root / "sitemap-captures.json").read_text())), 4)
