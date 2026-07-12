#!/usr/bin/env python3
"""DB-first Ocado product sync.

This script updates ``ocado_products.sqlite`` directly. It deliberately does
not read or write JSONL files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zlib
from collections import deque
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

import requests

from build_ocado_database import (
    KNOWN_TEXT_COLUMNS,
    PRODUCT_COLUMNS,
    attribute_flags,
    bool_int,
    field_column,
    html_to_text,
    json_dumps,
    life_parts,
    merge_promotions,
    money_to_gbp,
    parse_nutrition_rows,
    product_url,
    rating_parts,
    table_to_plain_text,
    unit_price_to_parts,
)
from classify_ocado_vegan import context_hash, load_product_context, product_context_for_llm


FIREFOX_HELPER_SCRIPTS = os.environ.get("BROWSE_WITH_FIREFOX_SCRIPTS", "")
if FIREFOX_HELPER_SCRIPTS and FIREFOX_HELPER_SCRIPTS not in sys.path:
    sys.path.insert(0, FIREFOX_HELPER_SCRIPTS)

REPO_ROOT = Path(__file__).resolve().parents[1]


def browser_session(*args: Any, **kwargs: Any) -> Any:
    try:
        from firefox_session import browser_session as real_browser_session
    except ImportError as exc:
        raise RuntimeError("Set BROWSE_WITH_FIREFOX_SCRIPTS to a directory containing firefox_session.py") from exc

    return real_browser_session(*args, **kwargs)


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"
CATEGORY_SITEMAP_URL = "https://www.ocado.com/sitemaps/sitemap-categories-part1.xml"
PRODUCT_SITEMAP_URL = "https://www.ocado.com/sitemaps/sitemap-products-part1.xml"
PRODUCT_URL_ID_RE = re.compile(r"/products/(?:[^/?#]*/)?(?P<id>\d+)(?:[/?#]|$)")

SYNC_PRODUCT_COLUMNS: dict[str, str] = {
    "current_in_latest_category_crawl": "INTEGER",
    "current_in_latest_product_sitemap": "INTEGER",
    "current_on_ocado": "INTEGER",
    "last_seen_sync_run_id": "INTEGER",
    "last_detail_fetch_run_id": "INTEGER",
    "last_detail_fetch_status": "INTEGER",
}

DETAIL_SCALAR_COLUMNS = [
    "ocado_product_uuid", "name", "brand", "url", "type", "pack_size", "alcohol", "is_new",
    "is_in_current_catalog", "medical_questionnaire_required", "time_restricted", "age_restriction_years",
    "price_gbp", "promo_price_gbp", "unit_price_gbp", "unit_price_unit", "promo_unit_price_gbp",
    "promo_unit_price_unit", "rating_count", "rating_overall", "guaranteed_product_life_quantity",
    "guaranteed_product_life_unit", "hfss_display_restriction_group", "quantity_restriction_group_json",
    "catchweight_json", "tax_codes_json", "retailer_financing_plan_ids_json", "promotions_json",
]


def now_epoch() -> int:
    return int(time.time())


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    if column_name not in table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def ensure_sync_schema(conn: sqlite3.Connection) -> None:
    product_columns = table_columns(conn, "products")
    missing_product_columns = sorted(set(PRODUCT_COLUMNS) - product_columns)
    if missing_product_columns:
        raise RuntimeError(f"products table is missing expected columns: {', '.join(missing_product_columns)}")

    for column_name, column_type in SYNC_PRODUCT_COLUMNS.items():
        ensure_column(conn, "products", column_name, column_type)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at_epoch INTEGER NOT NULL,
          completed_at_epoch INTEGER,
          status TEXT NOT NULL,
          error TEXT,
          category_count INTEGER DEFAULT 0,
          category_pages_attempted INTEGER DEFAULT 0,
          category_pages_succeeded INTEGER DEFAULT 0,
          category_pages_failed INTEGER DEFAULT 0,
          category_products_discovered INTEGER DEFAULT 0,
          sitemap_products_discovered INTEGER DEFAULT 0,
          detail_fetch_attempted INTEGER DEFAULT 0,
          detail_fetch_succeeded INTEGER DEFAULT 0,
          detail_fetch_failed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sync_category_page_fetches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER NOT NULL,
          slug_path TEXT NOT NULL,
          category_id TEXT NOT NULL,
          page_token TEXT,
          attempt INTEGER NOT NULL,
          status INTEGER,
          error TEXT,
          product_count INTEGER DEFAULT 0,
          next_page_token TEXT,
          fetched_at_epoch INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_product_discoveries (
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          source TEXT NOT NULL,
          url TEXT,
          name TEXT,
          brand TEXT,
          discovered_at_epoch INTEGER NOT NULL,
          PRIMARY KEY (run_id, product_id, source)
        );

        CREATE TABLE IF NOT EXISTS sync_product_categories (
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          category_path TEXT NOT NULL,
          PRIMARY KEY (run_id, product_id, category_path)
        );

        CREATE TABLE IF NOT EXISTS product_detail_fetches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          attempt INTEGER NOT NULL,
          status INTEGER,
          error TEXT,
          fetched_at_epoch INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_product_flags (
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          flag TEXT NOT NULL,
          source TEXT NOT NULL,
          PRIMARY KEY (run_id, product_id, flag, source)
        );

        CREATE TABLE IF NOT EXISTS product_detail_raw_latest (
          product_id TEXT PRIMARY KEY,
          run_id INTEGER NOT NULL,
          response_sha256 TEXT NOT NULL,
          response_json_zlib BLOB NOT NULL,
          fetched_at_epoch INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sync_product_context_changes (
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          change_kind TEXT NOT NULL,
          before_context_hash TEXT,
          after_context_hash TEXT,
          before_context_json TEXT,
          after_context_json TEXT,
          previous_vegan_status TEXT,
          previous_vegan_reason TEXT,
          PRIMARY KEY (run_id, product_id)
        );

        CREATE INDEX IF NOT EXISTS idx_sync_discoveries_run_source
          ON sync_product_discoveries(run_id, source);
        CREATE INDEX IF NOT EXISTS idx_sync_categories_run_product
          ON sync_product_categories(run_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_detail_fetches_run_product
          ON product_detail_fetches(run_id, product_id);
        CREATE INDEX IF NOT EXISTS idx_sync_context_changes_run_kind
          ON sync_product_context_changes(run_id, change_kind);
        CREATE INDEX IF NOT EXISTS idx_products_current_on_ocado
          ON products(current_on_ocado);
        CREATE INDEX IF NOT EXISTS idx_products_last_detail_status
          ON products(last_detail_fetch_status);
        """
    )
    for column_name in ("products_new", "products_changed", "products_removed", "classifications_invalidated"):
        ensure_column(conn, "sync_runs", column_name, "INTEGER DEFAULT 0")


def create_rolling_backup(db_path: Path) -> Path:
    backup_path = db_path.with_suffix(db_path.suffix + ".bak")
    if backup_path.exists():
        backup_path.unlink()
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(backup_path)) as backup:
        source.backup(backup)
    return backup_path


def start_sync_run(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_runs (started_at_epoch, status) VALUES (?, 'running')",
        (now_epoch(),),
    )
    return int(cursor.lastrowid)


def finish_sync_run(conn: sqlite3.Connection, run_id: int, status: str, error: str | None = None) -> None:
    conn.execute(
        """
        UPDATE sync_runs
        SET completed_at_epoch = ?, status = ?, error = ?
        WHERE id = ?
        """,
        (now_epoch(), status, error, run_id),
    )


def record_run_count(conn: sqlite3.Connection, run_id: int, column_name: str, value: int) -> None:
    allowed = {
        "category_count",
        "category_pages_attempted",
        "category_pages_succeeded",
        "category_pages_failed",
        "category_products_discovered",
        "sitemap_products_discovered",
        "detail_fetch_attempted",
        "detail_fetch_succeeded",
        "detail_fetch_failed",
        "products_new",
        "products_changed",
        "products_removed",
        "classifications_invalidated",
    }
    if column_name not in allowed:
        raise ValueError(f"unsupported sync_runs column: {column_name}")
    conn.execute(f"UPDATE sync_runs SET {column_name} = ? WHERE id = ?", (value, run_id))


def http_get_text(url: str, timeout: int) -> str:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    response = session.get(url, timeout=max(timeout, 60))
    response.raise_for_status()
    return response.text


def load_leaf_categories(timeout: int, category_limit: int = 0) -> list[tuple[str, str]]:
    text = http_get_text(CATEGORY_SITEMAP_URL, timeout)
    urls = re.findall(r"<loc>(https://www\.ocado\.com/categories/[^<]+)</loc>", text)
    unique_paths: dict[str, str] = {}
    for url in urls:
        path = url.removeprefix("https://www.ocado.com/categories/")
        slug_path, category_id = path.rsplit("/", 1)
        unique_paths[slug_path] = category_id

    leafs = []
    for slug_path, category_id in unique_paths.items():
        prefix = slug_path + "/"
        if not any(other.startswith(prefix) for other in unique_paths if other != slug_path):
            leafs.append((slug_path, category_id))
    leafs = sorted(leafs)
    if category_limit:
        return leafs[:category_limit]
    return leafs


def product_id_from_url(url: str | None) -> str | None:
    match = PRODUCT_URL_ID_RE.search(url or "")
    return match.group("id") if match else None


def product_ids_from_sitemap(timeout: int, product_limit: int = 0) -> list[tuple[str, str]]:
    text = http_get_text(PRODUCT_SITEMAP_URL, timeout)
    products: dict[str, str] = {}
    for url in re.findall(r"<loc>(https://www\.ocado\.com/products/[^<]+)</loc>", text):
        product_id = product_id_from_url(url)
        if product_id:
            products[product_id] = url
    items = sorted(products.items(), key=lambda item: item[0])
    if product_limit:
        return items[:product_limit]
    return items


def browser_fetch_category_batch(driver: Any, items: list[dict[str, Any]], concurrency: int) -> list[dict[str, Any]]:
    script = r"""
    const items = arguments[0];
    const concurrency = arguments[1];
    const done = arguments[arguments.length - 1];
    const results = [];
    let index = 0;
    let active = 0;
    let completed = 0;

    function finishOne() {
      completed += 1;
      active -= 1;
      if (completed === items.length) done(results);
      else launch();
    }

    function launch() {
      while (active < concurrency && index < items.length) {
        const item = items[index++];
        active += 1;
        const params = new URLSearchParams({
          categoryId: item.categoryId,
          tag: "web,category-item",
          includeAdditionalPageInfo: "true",
          maxProductsToDecorate: "50",
          maxPageSize: "50",
        });
        if (item.pageToken) params.set("pageToken", item.pageToken);

        fetch(`/api/webproductpagews/v6/product-pages?${params.toString()}`, {
          credentials: "include",
          headers: {"Accept": "application/json, text/plain, */*"},
        })
          .then(async (response) => {
            const text = await response.text();
            let data = null;
            try {
              data = JSON.parse(text);
            } catch (error) {
              results.push({
                slugPath: item.slugPath,
                categoryId: item.categoryId,
                pageToken: item.pageToken || null,
                status: response.status,
                error: `JSON parse failure: ${String(error)}`,
                textStart: text.slice(0, 500),
              });
              return;
            }

            const products = [];
            for (const group of (data.productGroups || [])) {
              for (const product of (group.decoratedProducts || [])) {
                products.push({
                  retailerProductId: product.retailerProductId || null,
                  name: product.name || null,
                  brand: product.brand || null,
                  iconAttributes: product.iconAttributes || product.attributes || [],
                });
              }
            }
            results.push({
              slugPath: item.slugPath,
              categoryId: item.categoryId,
              pageToken: item.pageToken || null,
              status: response.status,
              nextPageToken: data.metadata ? data.metadata.nextPageToken || null : null,
              products,
            });
          })
          .catch((error) => {
            results.push({
              slugPath: item.slugPath,
              categoryId: item.categoryId,
              pageToken: item.pageToken || null,
              error: String(error),
            });
          })
          .finally(finishOne);
      }
    }
    launch();
    """
    return driver.execute_async_script(script, items, concurrency)


def browser_fetch_bop_batch(driver: Any, ids: list[str], concurrency: int) -> list[dict[str, Any]]:
    script = r"""
    const ids = arguments[0];
    const concurrency = arguments[1];
    const done = arguments[arguments.length - 1];
    const results = [];
    let index = 0;
    let active = 0;
    let completed = 0;

    function finishOne() {
      completed += 1;
      active -= 1;
      if (completed === ids.length) done(results);
      else launch();
    }

    function launch() {
      while (active < concurrency && index < ids.length) {
        const id = ids[index++];
        active += 1;
        fetch(`/api/webproductpagews/v5/products/bop?retailerProductId=${id}`, {
          credentials: "include",
          headers: {"Accept": "application/json, text/plain, */*"},
        })
          .then(async (response) => {
            const text = await response.text();
            let data = null;
            try {
              data = JSON.parse(text);
            } catch (error) {
              results.push({
                retailerProductId: id,
                status: response.status,
                error: `JSON parse failure: ${String(error)}`,
                textStart: text.slice(0, 500),
              });
              return;
            }
            results.push({retailerProductId: id, status: response.status, data});
          })
          .catch((error) => {
            results.push({retailerProductId: id, error: String(error)});
          })
          .finally(finishOne);
      }
    }
    launch();
    """
    return driver.execute_async_script(script, ids, concurrency)


def upsert_basic_product(
    conn: sqlite3.Connection,
    product_id: str,
    *,
    url: str | None = None,
    name: str | None = None,
    brand: str | None = None,
    run_id: int,
    current_in_category: int | None = None,
    current_in_sitemap: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO products (id, url, has_full_product_detail, current_on_ocado, last_seen_sync_run_id)
        VALUES (?, ?, 0, 1, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (product_id, url or product_url(product_id), run_id),
    )
    updates = ["current_on_ocado = 1", "last_seen_sync_run_id = ?"]
    values: list[Any] = [run_id]
    if url:
        updates.append("url = ?")
        values.append(url)
    if name:
        updates.append("name = ?")
        values.append(html_to_text(name))
    if brand:
        updates.append("brand = ?")
        values.append(html_to_text(brand))
    if current_in_category is not None:
        updates.append("current_in_latest_category_crawl = ?")
        values.append(current_in_category)
    if current_in_sitemap is not None:
        updates.append("current_in_latest_product_sitemap = ?")
        values.append(current_in_sitemap)
    values.append(product_id)
    conn.execute(f"UPDATE products SET {', '.join(updates)} WHERE id = ?", values)


def insert_product_flags(conn: sqlite3.Connection, product_id: str, flags: Iterable[str]) -> None:
    for flag in flags:
        if flag:
            conn.execute("INSERT OR IGNORE INTO product_flags (product_id, flag) VALUES (?, ?)", (product_id, flag))
    if "vegan" in set(flags):
        conn.execute("UPDATE products SET official_vegan = 1 WHERE id = ?", (product_id,))


def stage_product_flags(
    conn: sqlite3.Connection,
    run_id: int,
    product_id: str,
    flags: Iterable[str],
    source: str,
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO sync_product_flags (run_id, product_id, flag, source) VALUES (?, ?, ?, ?)",
        ((run_id, product_id, flag, source) for flag in set(flags) if flag),
    )


def record_discovery(
    conn: sqlite3.Connection,
    run_id: int,
    product_id: str,
    source: str,
    *,
    url: str | None = None,
    name: str | None = None,
    brand: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO sync_product_discoveries
          (run_id, product_id, source, url, name, brand, discovered_at_epoch)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, product_id, source, url, html_to_text(name), html_to_text(brand), now_epoch()),
    )


def stage_category_product(conn: sqlite3.Connection, run_id: int, slug_path: str, product: dict[str, Any]) -> None:
    product_id = str(product.get("retailerProductId") or "")
    if not product_id:
        return
    name = product.get("name")
    brand = product.get("brand")
    upsert_basic_product(
        conn,
        product_id,
        name=name,
        brand=brand,
        run_id=run_id,
        current_in_category=1,
    )
    record_discovery(conn, run_id, product_id, "category", name=name, brand=brand)
    conn.execute(
        """
        INSERT OR IGNORE INTO sync_product_categories (run_id, product_id, category_path)
        VALUES (?, ?, ?)
        """,
        (run_id, product_id, slug_path),
    )
    stage_product_flags(conn, run_id, product_id, attribute_flags(product.get("iconAttributes")), "category")


def record_category_fetch(conn: sqlite3.Connection, run_id: int, result: dict[str, Any], attempt: int) -> None:
    conn.execute(
        """
        INSERT INTO sync_category_page_fetches
          (run_id, slug_path, category_id, page_token, attempt, status, error, product_count, next_page_token, fetched_at_epoch)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            result.get("slugPath"),
            result.get("categoryId"),
            result.get("pageToken"),
            attempt,
            result.get("status"),
            result.get("error"),
            len(result.get("products") or []),
            result.get("nextPageToken"),
            now_epoch(),
        ),
    )


def crawl_categories(conn: sqlite3.Connection, run_id: int, driver: Any, args: argparse.Namespace) -> None:
    leaf_categories = load_leaf_categories(args.timeout, args.category_limit)
    record_run_count(conn, run_id, "category_count", len(leaf_categories))
    print(f"Loaded {len(leaf_categories)} leaf categories", flush=True)

    pending = deque({"slugPath": slug_path, "categoryId": category_id, "pageToken": None} for slug_path, category_id in leaf_categories)
    retries: dict[tuple[str, str, str], int] = {}
    page_success = 0
    page_failed = 0
    page_attempted = 0
    completed_pages = 0

    while pending:
        batch = []
        while pending and len(batch) < args.category_batch_size:
            batch.append(pending.popleft())
        results = browser_fetch_category_batch(driver, batch, args.category_concurrency)
        page_attempted += len(results)
        with conn:
            for result in results:
                key = (result["slugPath"], result["categoryId"], result.get("pageToken") or "")
                attempt = retries.get(key, 0) + 1
                record_category_fetch(conn, run_id, result, attempt)
                if result.get("error") or result.get("status") != 200:
                    if attempt < args.retries:
                        retries[key] = attempt
                        pending.append(
                            {
                                "slugPath": result["slugPath"],
                                "categoryId": result["categoryId"],
                                "pageToken": result.get("pageToken"),
                            }
                        )
                    else:
                        page_failed += 1
                    continue

                page_success += 1
                completed_pages += 1
                for product in result.get("products") or []:
                    stage_category_product(conn, run_id, result["slugPath"], product)
                if result.get("nextPageToken"):
                    pending.append(
                        {
                            "slugPath": result["slugPath"],
                            "categoryId": result["categoryId"],
                            "pageToken": result["nextPageToken"],
                        }
                    )

            record_run_count(conn, run_id, "category_pages_attempted", page_attempted)
            record_run_count(conn, run_id, "category_pages_succeeded", page_success)
            record_run_count(conn, run_id, "category_pages_failed", page_failed)
            category_products = conn.execute(
                "SELECT count(DISTINCT product_id) FROM sync_product_discoveries WHERE run_id = ? AND source = 'category'",
                (run_id,),
            ).fetchone()[0]
            record_run_count(conn, run_id, "category_products_discovered", int(category_products))
        if completed_pages and (completed_pages % 200 == 0 or not pending):
            print(
                f"Category pages ok={page_success} failed={page_failed} pending={len(pending)} "
                f"products={category_products}",
                flush=True,
            )
        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)

    if page_failed:
        raise RuntimeError(f"{page_failed} category pages failed after retries")


def apply_category_replacement(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM product_categories")
    conn.execute(
        """
        INSERT OR IGNORE INTO product_categories (product_id, category_path)
        SELECT product_id, category_path
        FROM sync_product_categories
        WHERE run_id = ?
        """,
        (run_id,),
    )
    conn.execute(
        """
        UPDATE products
        SET seen_in_category_pages = (
          SELECT count(*)
          FROM sync_product_categories
          WHERE run_id = ? AND product_id = products.id
        )
        WHERE id IN (
          SELECT product_id FROM sync_product_discoveries WHERE run_id = ? AND source = 'category'
        )
        """,
        (run_id, run_id),
    )


def import_product_sitemap(conn: sqlite3.Connection, run_id: int, args: argparse.Namespace) -> None:
    products = product_ids_from_sitemap(args.timeout, args.product_limit)
    with conn:
        for product_id, url in products:
            upsert_basic_product(
                conn,
                product_id,
                url=url,
                run_id=run_id,
                current_in_sitemap=1,
            )
            record_discovery(conn, run_id, product_id, "sitemap", url=url)
        record_run_count(conn, run_id, "sitemap_products_discovered", len(products))
    print(f"Loaded {len(products)} products from product sitemap", flush=True)


def apply_current_flags(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute(
        """
        UPDATE products
        SET current_in_latest_category_crawl = 0,
            current_in_latest_product_sitemap = 0,
            current_on_ocado = 0
        """
    )
    conn.execute(
        """
        UPDATE products
        SET current_in_latest_category_crawl = 1,
            current_on_ocado = 1,
            last_seen_sync_run_id = ?
        WHERE id IN (
          SELECT product_id FROM sync_product_discoveries WHERE run_id = ? AND source = 'category'
        )
        """,
        (run_id, run_id),
    )
    conn.execute(
        """
        UPDATE products
        SET current_in_latest_product_sitemap = 1,
            current_on_ocado = 1,
            last_seen_sync_run_id = ?
        WHERE id IN (
          SELECT product_id FROM sync_product_discoveries WHERE run_id = ? AND source = 'sitemap'
        )
        """,
        (run_id, run_id),
    )


def set_product_value(conn: sqlite3.Connection, product_id: str, column: str, value: Any) -> None:
    if column not in PRODUCT_COLUMNS and column not in SYNC_PRODUCT_COLUMNS:
        raise ValueError(f"unsupported product column: {column}")
    if value is None or value == "":
        return
    conn.execute(f"UPDATE products SET {column} = ? WHERE id = ?", (value, product_id))


def apply_product_detail(conn: sqlite3.Connection, run_id: int, product_id: str, data: dict[str, Any], status: int) -> None:
    product = data.get("product") if isinstance(data.get("product"), dict) else {}
    if status != 200 or not product:
        conn.execute(
            """
            UPDATE products
            SET last_detail_fetch_run_id = ?, last_detail_fetch_status = ?
            WHERE id = ?
            """,
            (run_id, status, product_id),
        )
        return

    upsert_basic_product(
        conn,
        product_id,
        url=product_url(product_id),
        name=product.get("name"),
        brand=product.get("brand"),
        run_id=run_id,
    )
    updates: dict[str, Any] = {column: None for column in [*DETAIL_SCALAR_COLUMNS, *KNOWN_TEXT_COLUMNS]}
    updates.update({
        "ocado_product_uuid": product.get("productId"),
        "name": html_to_text(product.get("name")),
        "brand": html_to_text(product.get("brand")),
        "url": product_url(product_id),
        "type": product.get("type"),
        "pack_size": product.get("packSizeDescription"),
        "alcohol": bool_int(product.get("alcohol")),
        "is_new": bool_int(product.get("isNew")),
        "is_in_current_catalog": bool_int(product.get("isInCurrentCatalog")),
        "medical_questionnaire_required": bool_int(product.get("medicalQuestionnaireRequired")),
        "time_restricted": bool_int(product.get("timeRestricted")),
        "age_restriction_years": product.get("ageRestriction"),
        "price_gbp": money_to_gbp(product.get("price")),
        "promo_price_gbp": money_to_gbp(product.get("promoPrice")),
        "hfss_display_restriction_group": product.get("hfssDisplayRestrictionGroup"),
        "quantity_restriction_group_json": json_dumps(product.get("quantityRestrictionGroup")),
        "catchweight_json": json_dumps(product.get("catchweight")),
        "tax_codes_json": json_dumps(product.get("taxCodesDisplayNames")),
        "retailer_financing_plan_ids_json": json_dumps(product.get("retailerFinancingPlanIds")),
        "promotions_json": json_dumps(merge_promotions(product, data)),
        "has_full_product_detail": 1,
        "last_detail_fetch_run_id": run_id,
        "last_detail_fetch_status": status,
    })
    unit_price_gbp, unit_price_unit = unit_price_to_parts(product.get("unitPrice"))
    updates["unit_price_gbp"] = unit_price_gbp
    updates["unit_price_unit"] = unit_price_unit
    promo_unit_price_gbp, promo_unit_price_unit = unit_price_to_parts(product.get("promoUnitPrice"))
    updates["promo_unit_price_gbp"] = promo_unit_price_gbp
    updates["promo_unit_price_unit"] = promo_unit_price_unit
    rating_count, rating_overall = rating_parts(product.get("ratingSummary"))
    updates["rating_count"] = rating_count
    updates["rating_overall"] = rating_overall
    life_quantity, life_unit = life_parts(product.get("guaranteedProductLife"))
    updates["guaranteed_product_life_quantity"] = life_quantity
    updates["guaranteed_product_life_unit"] = life_unit

    assignments = ", ".join(f"{column} = ?" for column in updates)
    conn.execute(
        f"UPDATE products SET {assignments} WHERE id = ?",
        [*updates.values(), product_id],
    )
    flags = attribute_flags(product.get("iconAttributes"))
    stage_product_flags(conn, run_id, product_id, flags, "detail")

    conn.execute("DELETE FROM product_breadcrumbs WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM product_nutrition WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM manufacturer_vegan_evidence WHERE product_id = ?", (product_id,))
    bop = data.get("bopData") if isinstance(data.get("bopData"), dict) else {}
    detailed_description = html_to_text(bop.get("detailedDescription"))
    set_product_value(conn, product_id, "detailed_description", detailed_description)
    for position, breadcrumb in enumerate(bop.get("breadcrumbs") or []):
        if isinstance(breadcrumb, dict):
            conn.execute(
                """
                INSERT OR REPLACE INTO product_breadcrumbs (product_id, position, category_id, category_name)
                VALUES (?, ?, ?, ?)
                """,
                (product_id, position, breadcrumb.get("categoryId"), html_to_text(breadcrumb.get("categoryName"))),
            )
    for field in bop.get("fields") or []:
        title = field.get("title")
        column = field_column(title)
        if not column or column == "brand":
            continue
        content = field.get("content")
        text = table_to_plain_text(content) if column == "nutrition_plain_text" else html_to_text(content)
        if text and "vegan" in text.lower():
            conn.execute(
                "INSERT INTO manufacturer_vegan_evidence (product_id, field_title, content) VALUES (?, ?, ?)",
                (product_id, title or column, text),
            )
        if column in PRODUCT_COLUMNS:
            set_product_value(conn, product_id, column, text)
        if column == "nutrition_plain_text":
            conn.executemany(
                """
                INSERT INTO product_nutrition
                  (product_id, nutrient, basis_label, amount_value, unit, original_value)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                parse_nutrition_rows(product_id, content),
            )
    name = conn.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()[0]
    conn.execute(
        "UPDATE products SET name_contains_vegan = ? WHERE id = ?",
        (1 if name and re.search(r"\bvegan\b", name, re.IGNORECASE) else 0, product_id),
    )
    raw_json = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    conn.execute(
        """
        INSERT INTO product_detail_raw_latest
          (product_id, run_id, response_sha256, response_json_zlib, fetched_at_epoch)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
          run_id = excluded.run_id,
          response_sha256 = excluded.response_sha256,
          response_json_zlib = excluded.response_json_zlib,
          fetched_at_epoch = excluded.fetched_at_epoch
        """,
        (product_id, run_id, hashlib.sha256(raw_json).hexdigest(), zlib.compress(raw_json, 9), now_epoch()),
    )


def log_detail_fetch(conn: sqlite3.Connection, run_id: int, product_id: str, attempt: int, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO product_detail_fetches
          (run_id, product_id, attempt, status, error, fetched_at_epoch)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, product_id, attempt, row.get("status"), row.get("error"), now_epoch()),
    )


def products_needing_detail(
    conn: sqlite3.Connection,
    product_limit: int = 0,
    *,
    missing_only: bool = True,
) -> list[str]:
    query = """
        SELECT id
        FROM products
        WHERE current_on_ocado = 1
          AND (? = 0 OR COALESCE(has_full_product_detail, 0) != 1)
        ORDER BY id
    """
    parameters: tuple[Any, ...] = (1 if missing_only else 0,)
    if product_limit:
        query += " LIMIT ?"
        parameters += (product_limit,)
    return [row[0] for row in conn.execute(query, parameters)]


def fetch_missing_product_details(conn: sqlite3.Connection, run_id: int, driver: Any, args: argparse.Namespace) -> None:
    ids = products_needing_detail(conn, args.detail_limit, missing_only=args.missing_details_only)
    print(f"Products selected for detail refresh: {len(ids)}", flush=True)
    attempted = 0
    succeeded: set[str] = set()
    failed: dict[str, dict[str, Any]] = {}
    remaining = list(ids)

    for attempt in range(1, args.retries + 1):
        if not remaining:
            break
        next_remaining: list[str] = []
        total_batches = (len(remaining) + args.bop_batch_size - 1) // args.bop_batch_size
        for batch_index in range(total_batches):
            batch = remaining[batch_index * args.bop_batch_size : (batch_index + 1) * args.bop_batch_size]
            rows = browser_fetch_bop_batch(driver, batch, args.bop_concurrency)
            attempted += len(rows)
            with conn:
                for row in rows:
                    product_id = str(row.get("retailerProductId") or "")
                    if not product_id:
                        continue
                    log_detail_fetch(conn, run_id, product_id, attempt, row)
                    status = int(row.get("status") or 0)
                    data = row.get("data") if isinstance(row.get("data"), dict) else {}
                    if status == 200 and isinstance(data.get("product"), dict):
                        apply_product_detail(conn, run_id, product_id, data, status)
                        succeeded.add(product_id)
                        failed.pop(product_id, None)
                    else:
                        conn.execute(
                            """
                            UPDATE products
                            SET last_detail_fetch_run_id = ?, last_detail_fetch_status = ?
                            WHERE id = ?
                            """,
                            (run_id, status, product_id),
                        )
                        failed[product_id] = row
                        if attempt < args.retries:
                            next_remaining.append(product_id)
                record_run_count(conn, run_id, "detail_fetch_attempted", attempted)
                record_run_count(conn, run_id, "detail_fetch_succeeded", len(succeeded))
                record_run_count(conn, run_id, "detail_fetch_failed", len(failed))
            if (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                print(
                    f"Detail attempt {attempt}/{args.retries} batch {batch_index + 1}/{total_batches} "
                    f"success={len(succeeded)} failed={len(failed)} remaining={len(next_remaining)}",
                    flush=True,
                )
            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)
        remaining = sorted(set(next_remaining))

    with conn:
        rebuild_fts(conn)
    terminal_missing = products_needing_detail(conn, 0)
    if terminal_missing:
        raise RuntimeError(f"{len(terminal_missing)} current products still lack full product detail")


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM products_fts")
    conn.execute(
        """
        INSERT INTO products_fts (
          id, name, brand, ingredients, allergens, dietary_information, features, detailed_description, nutrition_plain_text
        )
        SELECT id, name, brand, ingredients, allergens, dietary_information, features, detailed_description, nutrition_plain_text
        FROM products
        """
    )


def classifier_change_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only evidence whose change can alter a vegan decision."""
    product = dict(payload.get("product") or {})
    product.pop("url", None)
    result: dict[str, Any] = {
        "product": product,
        "official_vegan": "vegan" in set(payload.get("flags") or []),
    }
    if not product.get("ingredients"):
        result["categories"] = sorted(set(payload.get("categories") or []))
    evidence = {
        re.sub(r"\s+", " ", row.get("content") or "").strip()
        for row in payload.get("manufacturer_vegan_evidence") or []
        if row.get("content")
    }
    if evidence:
        result["manufacturer_vegan_evidence"] = sorted(evidence)
    return result


def snapshot_classifier_contexts(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS temp.sync_context_baseline")
    conn.execute(
        """
        CREATE TEMP TABLE sync_context_baseline (
          product_id TEXT PRIMARY KEY,
          was_current INTEGER NOT NULL,
          context_hash TEXT NOT NULL,
          context_json TEXT NOT NULL,
          vegan_status TEXT,
          vegan_reason TEXT
        )
        """
    )
    rows = list(conn.execute("SELECT id, COALESCE(current_on_ocado, 0), vegan_status, vegan_reason FROM products ORDER BY id"))
    for product_id, was_current, vegan_status, vegan_reason in rows:
        payload = product_context_for_llm(load_product_context(conn, product_id))
        payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "INSERT INTO sync_context_baseline VALUES (?, ?, ?, ?, ?, ?)",
            (product_id, was_current, context_hash(classifier_change_payload(payload)), payload_json, vegan_status, vegan_reason),
        )
    print(f"Snapshotted classifier context for {len(rows)} products", flush=True)


def finalize_product_flags(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM product_flags WHERE product_id IN (SELECT id FROM products WHERE current_on_ocado = 1)")
    conn.execute(
        "INSERT OR IGNORE INTO product_flags (product_id, flag) SELECT product_id, flag FROM sync_product_flags WHERE run_id = ?",
        (run_id,),
    )
    conn.execute(
        """
        UPDATE products
        SET official_vegan = CASE WHEN EXISTS (
          SELECT 1 FROM product_flags WHERE product_id = products.id AND flag = 'vegan'
        ) THEN 1 ELSE 0 END
        WHERE current_on_ocado = 1
        """
    )


def finalize_context_changes(conn: sqlite3.Connection, run_id: int) -> None:
    new_count = changed_count = invalidated = 0
    current_ids = [row[0] for row in conn.execute("SELECT id FROM products WHERE current_on_ocado = 1 ORDER BY id")]
    for product_id in current_ids:
        payload = product_context_for_llm(load_product_context(conn, product_id))
        after_json = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        after_hash = context_hash(classifier_change_payload(payload))
        before = conn.execute(
            "SELECT context_hash, context_json, vegan_status, vegan_reason FROM temp.sync_context_baseline WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        if before is not None and before[0] == after_hash:
            continue
        kind = "new" if before is None else "changed"
        new_count += int(kind == "new")
        changed_count += int(kind == "changed")
        conn.execute(
            """
            INSERT INTO sync_product_context_changes
              (run_id, product_id, change_kind, before_context_hash, after_context_hash,
               before_context_json, after_context_json, previous_vegan_status, previous_vegan_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, product_id, kind, before[0] if before else None, after_hash,
                before[1] if before else None, after_json, before[2] if before else None,
                before[3] if before else None,
            ),
        )
        conn.execute(
            """
            UPDATE products SET vegan_status = NULL, vegan_reason = NULL,
              vegan_classifier_version = NULL, vegan_classified_at_epoch = NULL
            WHERE id = ?
            """,
            (product_id,),
        )
        invalidated += 1

    removed_rows = conn.execute(
        """
        SELECT b.product_id, b.context_hash, b.context_json, b.vegan_status, b.vegan_reason
        FROM temp.sync_context_baseline b JOIN products p ON p.id = b.product_id
        WHERE b.was_current = 1 AND p.current_on_ocado = 0
        """
    ).fetchall()
    conn.executemany(
        """
        INSERT INTO sync_product_context_changes
          (run_id, product_id, change_kind, before_context_hash, before_context_json,
           previous_vegan_status, previous_vegan_reason)
        VALUES (?, ?, 'removed', ?, ?, ?, ?)
        """,
        ((run_id, row[0], row[1], row[2], row[3], row[4]) for row in removed_rows),
    )
    record_run_count(conn, run_id, "products_new", new_count)
    record_run_count(conn, run_id, "products_changed", changed_count)
    record_run_count(conn, run_id, "products_removed", len(removed_rows))
    record_run_count(conn, run_id, "classifications_invalidated", invalidated)
    print(
        f"Context changes new={new_count} changed={changed_count} removed={len(removed_rows)} invalidated={invalidated}",
        flush=True,
    )


def run_sync(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    backup_path = create_rolling_backup(db_path)
    print(f"Backed up database to {backup_path}", flush=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        with conn:
            ensure_sync_schema(conn)
            run_id = start_sync_run(conn)
            snapshot_classifier_contexts(conn)
        print(f"Started sync run {run_id}", flush=True)

        try:
            with browser_session(headless=True, timeout=args.timeout) as session:
                session.driver.get("https://www.ocado.com/")
                session.wait_for_ready_state(timeout=args.timeout)
                crawl_categories(conn, run_id, session.driver, args)
                with conn:
                    apply_category_replacement(conn, run_id)
                import_product_sitemap(conn, run_id, args)
                with conn:
                    apply_current_flags(conn, run_id)
                fetch_missing_product_details(conn, run_id, session.driver, args)
            with conn:
                finalize_product_flags(conn, run_id)
                finalize_context_changes(conn, run_id)
                finish_sync_run(conn, run_id, "completed")
            print(f"Completed sync run {run_id}", flush=True)
            return 0
        except Exception as exc:
            with conn:
                finish_sync_run(conn, run_id, "failed", f"{type(exc).__name__}: {exc}")
            raise
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "ocado_products.sqlite"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--category-limit", type=int, default=0)
    parser.add_argument("--product-limit", type=int, default=0, help="Limit product sitemap imports, mainly for tests/smoke runs.")
    parser.add_argument("--detail-limit", type=int, default=0, help="Limit detail fetches, mainly for tests/smoke runs.")
    parser.add_argument(
        "--missing-details-only",
        action="store_true",
        help="Fetch only current products without full details instead of refreshing every current product.",
    )
    parser.add_argument("--category-batch-size", type=int, default=10)
    parser.add_argument("--category-concurrency", type=int, default=4)
    parser.add_argument("--bop-batch-size", type=int, default=50)
    parser.add_argument("--bop-concurrency", type=int, default=4)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_sync(args)


if __name__ == "__main__":
    raise SystemExit(main())
