#!/usr/bin/env python3
"""Discover catalogue membership and fetch new products without reclassifying old IDs."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import time
import zlib
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

import build_ocado_database as builder
import classify_ocado_vegan as classifier
import published_baseline as policy
import sync_ocado_database as sync

INDEX = "https://www.ocado.com/sitemaps/sitemap_index.xml"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def locations(text):
    return [node.text.strip() for node in ET.fromstring(text).findall(".//s:loc", NS) if node.text]


def discovery_urls(root):
    """Capture index and every advertised product/category part; never assume part1."""
    captures = []

    def fetch(url):
        if urlparse(url).netloc != "www.ocado.com" or not urlparse(url).path.startswith("/sitemaps/"):
            raise ValueError("Unexpected sitemap source")
        text = sync.http_get_text(url, 60)
        file = root / ("sitemap-" + str(len(captures)) + ".xml")
        file.write_text(text)
        captures.append({"url": url, "file": file.name, "sha256": hashlib.sha256(text.encode()).hexdigest()})
        return text

    advertised = locations(fetch(INDEX))
    selected = [u for u in advertised if "/sitemap-categories-" in u or "/sitemap-products-" in u]
    if not any("/sitemap-categories-" in u for u in selected) or not any("/sitemap-products-" in u for u in selected):
        raise ValueError("Incomplete sitemap index")
    categories, products = {}, {}
    for url in selected:
        urls = locations(fetch(url))
        if not urls:
            raise ValueError("Empty advertised sitemap")
        for loc in urls:
            if loc.startswith("https://www.ocado.com/categories/"):
                slug, cid = loc.removeprefix("https://www.ocado.com/categories/").rsplit("/", 1)
                categories[slug] = cid
            elif loc.startswith("https://www.ocado.com/products/"):
                pid = sync.product_id_from_url(loc)
                if not pid:
                    raise ValueError("Unrecognized product URL")
                products[pid] = loc
    leaves = sorted(
        (s, c) for s, c in categories.items() if not any(t.startswith(s + "/") for t in categories if s != t)
    )
    (root / "sitemap-captures.json").write_text(json.dumps(captures, indent=2) + "\n")
    return leaves, sorted(products.items())


def membership(conn):
    return list(conn.execute("SELECT id,current_on_ocado FROM products ORDER BY id"))


def retained_product_hash(conn, known):
    membership_fields = {
        "current_on_ocado",
        "current_in_latest_category_crawl",
        "current_in_latest_product_sitemap",
        "last_seen_sync_run_id",
    }
    fields = [r[1] for r in conn.execute("PRAGMA main.table_info(products)") if r[1] not in membership_fields]
    result = hashlib.sha256()
    for row in conn.execute("SELECT " + ",".join(fields) + " FROM main.products ORDER BY id"):
        if row[fields.index("id")] in known:
            result.update(policy.encoded(row).encode() + b"\n")
    return result.hexdigest()


def gather(db, root):
    root.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        policy.validate(source)
        before = membership(source)
    state = {
        "stage": "gathering",
        "started_at_epoch": int(time.time()),
        "before": before,
        "before_hash": policy.digest(before),
        "detail_scope": "new IDs only; existing details retained",
    }
    (root / "manifest.json").write_text(json.dumps(state, indent=2) + "\n")
    with closing(sqlite3.connect(root / "discovery.sqlite")) as conn:
        # Destructive schema builder is used ONLY on this newly created empty stage.
        builder.create_schema(conn)
        classifier.ensure_classification_schema(conn)
        sync.ensure_sync_schema(conn)
        conn.executemany("INSERT INTO products(id,has_full_product_detail,current_on_ocado) VALUES(?,1,?)", before)
        run = sync.start_sync_run(conn)
        conn.commit()
        args = sync.build_parser().parse_args(["--db", str(root / "discovery.sqlite"), "--missing-details-only"])
        leaves, products = discovery_urls(root)
        print(f"Full discovery: {len(leaves)} category leaves; {len(products)} sitemap products", flush=True)
        with patch.object(sync, "load_leaf_categories", return_value=leaves), patch.object(
            sync, "product_ids_from_sitemap", return_value=products
        ):
            with sync.browser_session(direct_profile=False, headless=True, timeout=args.timeout) as session:
                session.driver.get("https://www.ocado.com/")
                session.wait_for_ready_state(timeout=args.timeout)
                sync.crawl_categories(conn, run, session.driver, args)
                sync.import_product_sitemap(conn, run, args)
                with conn:
                    sync.apply_current_flags(conn, run)
                    sync.apply_category_replacement(conn, run)
                sync.fetch_missing_product_details(conn, run, session.driver, args)
            with conn:
                sync.finalize_product_flags(conn, run)
                sync.finish_sync_run(conn, run, "completed")
        known = {r[0] for r in before}
        old_current = {r[0] for r in before if r[1] == 1}
        now = {r[0] for r in conn.execute("SELECT id FROM products WHERE current_on_ocado=1")}
        if not len(old_current) * 0.8 <= len(now) <= len(old_current) * 1.2:
            raise ValueError("Large membership change requires investigation before activation")
        state.update(
            stage="complete",
            completed_at_epoch=int(time.time()),
            run_id=run,
            new_ids=sorted(now - known, key=int),
            removed_ids=sorted(old_current - now, key=int),
            returned_ids=sorted((now - old_current) & known, key=int),
            current_count=len(now),
        )
        (root / "manifest.json").write_text(json.dumps(state, indent=2) + "\n")
        print(json.dumps({k: v for k, v in state.items() if k not in {"before"}}), flush=True)


def import_completed(db, root, apply=False):
    state = json.loads((root / "manifest.json").read_text())
    if state["stage"] != "complete":
        raise ValueError("Discovery is incomplete")
    with closing(sqlite3.connect(f'file:{db}?mode={"rw" if apply else "ro"}', uri=True)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        policy.validate(conn)
        if policy.digest(membership(conn)) != state["before_hash"]:
            raise ValueError("Catalogue membership changed concurrently")
        conn.execute("ATTACH DATABASE ? AS discovered", ((root / "discovery.sqlite").resolve().as_uri() + "?mode=ro",))
        run = state["run_id"]
        row = conn.execute(
            "SELECT status,category_pages_failed,detail_fetch_failed FROM discovered.sync_runs WHERE id=?", (run,)
        ).fetchone()
        if row != ("completed", 0, 0):
            raise ValueError("Discovery or detail errors remain")
        expected_new = set(state["new_ids"])
        actual_new = {
            r[0]
            for r in conn.execute("SELECT id FROM discovered.products WHERE id NOT IN (SELECT id FROM main.products)")
        }
        if expected_new != actual_new:
            raise ValueError("New-product accounting mismatch")
        known = {row[0] for row in state["before"]}
        old_current = {row[0] for row in state["before"] if row[1] == 1}
        discovered = list(conn.execute("SELECT id,current_on_ocado FROM discovered.products"))
        current = {pid for pid, present in discovered if present == 1}
        if {pid for pid, _ in discovered} != known | expected_new:
            raise ValueError("Known-product accounting mismatch")
        if (
            len(current) != state["current_count"]
            or old_current - current != set(state["removed_ids"])
            or (current - old_current) & known != set(state["returned_ids"])
        ):
            raise ValueError("Membership transition accounting mismatch")
        if conn.execute("SELECT count(*) FROM discovered.products WHERE has_full_product_detail!=1").fetchone()[0]:
            raise ValueError("Missing product details")
        for pid in expected_new:
            raw = conn.execute(
                "SELECT response_sha256,response_json_zlib FROM discovered.product_detail_raw_latest WHERE product_id=?",
                (pid,),
            ).fetchone()
            if not raw or hashlib.sha256(zlib.decompress(raw[1])).hexdigest() != raw[0]:
                raise ValueError(f"Missing or corrupt captured detail: {pid}")
        result = {
            "new": len(expected_new),
            "removed": len(state["removed_ids"]),
            "returned": len(state["returned_ids"]),
            "current": state["current_count"],
            "preview": not apply,
        }
        if not apply:
            return result
        retained_hash = retained_product_hash(conn, known)
        # Exact logical backup of the only existing fields this import changes.
        fields = [
            "id",
            "current_on_ocado",
            "current_in_latest_category_crawl",
            "current_in_latest_product_sitemap",
            "last_seen_sync_run_id",
        ]
        prior = {
            "fields": fields,
            "rows": list(conn.execute("SELECT " + ",".join(fields) + " FROM products ORDER BY id")),
            "new_ids": state["new_ids"],
        }
        backup = root / "before-import.membership.json.gz"
        if not backup.exists():
            with gzip.open(backup, "xt") as stream:
                stream.write(policy.encoded(prior))
        with gzip.open(backup, "rt") as stream:
            if policy.encoded(json.load(stream)) != policy.encoded(prior):
                raise ValueError("Membership backup mismatch")
        columns = [
            r[1] for r in conn.execute("PRAGMA discovered.table_info(products)") if not r[1].startswith("vegan_")
        ]
        # Preserve existing products and their classifications, including removed IDs.
        sync_run = sync.start_sync_run(conn)
        for pid in state["new_ids"]:
            values = list(
                conn.execute("SELECT " + ",".join(columns) + " FROM discovered.products WHERE id=?", (pid,)).fetchone()
            )
            for field in ("last_seen_sync_run_id", "last_detail_fetch_run_id"):
                values[columns.index(field)] = sync_run
            conn.execute(
                "INSERT INTO main.products(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
                values,
            )
            for table in (
                "product_flags",
                "product_categories",
                "product_breadcrumbs",
                "product_nutrition",
                "manufacturer_vegan_evidence",
            ):
                fields = [r[1] for r in conn.execute(f"PRAGMA discovered.table_info({table})") if r[1] != "id"]
                conn.execute(
                    f"INSERT INTO main.{table}("
                    + ",".join(fields)
                    + ") SELECT "
                    + ",".join(fields)
                    + f" FROM discovered.{table} WHERE product_id=?",
                    (pid,),
                )
            conn.execute(
                """INSERT INTO main.product_detail_raw_latest SELECT product_id,?,response_sha256,response_json_zlib,fetched_at_epoch
                            FROM discovered.product_detail_raw_latest WHERE product_id=?""",
                (sync_run, pid),
            )
        conn.execute(
            """UPDATE main.products SET
            current_on_ocado=(SELECT current_on_ocado FROM discovered.products d WHERE d.id=products.id),
            current_in_latest_category_crawl=(SELECT current_in_latest_category_crawl FROM discovered.products d WHERE d.id=products.id),
            current_in_latest_product_sitemap=(SELECT current_in_latest_product_sitemap FROM discovered.products d WHERE d.id=products.id)"""
        )
        conn.execute("UPDATE products SET last_seen_sync_run_id=? WHERE current_on_ocado=1", (sync_run,))
        classification_run = policy.new_run(conn, "new-catalogue-products-awaiting-review", len(expected_new))
        for pid in state["new_ids"]:
            policy.write_decision(
                conn,
                classification_run,
                pid,
                "unknown",
                None,
                {"discovery_root": str(root.resolve()), "sync_run_id": sync_run},
                "New product, awaiting individual review; no automatic promotion.",
            )
        policy.finish_run(conn, classification_run, len(expected_new))
        sync.rebuild_fts(conn)
        # Retain discovery/fetch attribution under the canonical sync run.
        for table in (
            "sync_category_page_fetches",
            "sync_product_discoveries",
            "sync_product_categories",
            "product_detail_fetches",
            "sync_product_flags",
        ):
            fields = [
                r[1] for r in conn.execute(f"PRAGMA discovered.table_info({table})") if r[1] not in {"id", "run_id"}
            ]
            conn.execute(
                f"INSERT INTO main.{table}(run_id,"
                + ",".join(fields)
                + ") SELECT ?,"
                + ",".join(fields)
                + f" FROM discovered.{table} WHERE run_id=?",
                (sync_run, run),
            )
        counters = [
            "category_count",
            "category_pages_attempted",
            "category_pages_succeeded",
            "category_pages_failed",
            "category_products_discovered",
            "sitemap_products_discovered",
            "detail_fetch_attempted",
            "detail_fetch_succeeded",
            "detail_fetch_failed",
        ]
        for name in counters:
            value = conn.execute(f"SELECT {name} FROM discovered.sync_runs WHERE id=?", (run,)).fetchone()[0]
            sync.record_run_count(conn, sync_run, name, value)
        sync.record_run_count(conn, sync_run, "products_new", len(expected_new))
        sync.record_run_count(conn, sync_run, "products_removed", len(state["removed_ids"]))
        sync.finish_sync_run(conn, sync_run, "completed")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS catalogue_membership_refreshes(sync_run_id INTEGER PRIMARY KEY, manifest_json TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO catalogue_membership_refreshes VALUES(?,?)", (sync_run, policy.encoded(state)))
        if retained_product_hash(conn, known) != retained_hash:
            raise ValueError("Existing product evidence/classifications changed during membership import")
        policy.validate(conn)
        result["sync_run_id"] = sync_run
        result["retained_product_fields_sha256"] = retained_hash
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=policy.ROOT / "ocado_products.sqlite")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--import-completed", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.import_completed:
        parser.error("--apply requires --import-completed")
    if args.import_completed:
        print(json.dumps(import_completed(args.db, args.root, args.apply)))
    else:
        gather(args.db, args.root)


if __name__ == "__main__":
    main()
