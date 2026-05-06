#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sqlite3
import time
from collections import deque
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"
CATEGORY_SITEMAP_URL = "https://www.ocado.com/sitemaps/sitemap-categories-part1.xml"
PRODUCT_BOP_URL = "/api/webproductpagews/v5/products/bop?retailerProductId="


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cookies-db",
        default=str(Path.home() / ".mozilla/firefox/<firefox-profile>/cookies.sqlite"),
    )
    parser.add_argument("--output-prefix", default="ocado_vegan_according_to_manufacturer")
    parser.add_argument("--category-limit", type=int, default=0)
    parser.add_argument("--category-batch-size", type=int, default=20)
    parser.add_argument("--category-batch-concurrency", type=int, default=10)
    parser.add_argument("--bop-batch-size", type=int, default=200)
    parser.add_argument("--bop-batch-concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=30)
    return parser.parse_args()


def build_cookie_rows(cookies_db: str) -> list[tuple]:
    conn = sqlite3.connect(cookies_db)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT host, name, value, path, isSecure, expiry
            FROM moz_cookies
            WHERE host LIKE '%ocado.com%'
            """
        )
        return cursor.fetchall()
    finally:
        conn.close()


def build_cookie_jar(cookie_rows: list[tuple]) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    for host, name, value, path, _secure, _expiry in cookie_rows:
        jar.set(name, value, domain=host, path=path)
    return jar

def load_leaf_categories(cookie_jar: requests.cookies.RequestsCookieJar, timeout: int) -> list[tuple[str, str]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    session.cookies.update(cookie_jar)
    response = session.get(CATEGORY_SITEMAP_URL, timeout=max(timeout, 60))
    response.raise_for_status()

    import re

    urls = re.findall(r"<loc>(https://www\.ocado\.com/categories/[^<]+)</loc>", response.text)
    unique_paths: dict[str, str] = {}
    for url in urls:
        path = url.removeprefix("https://www.ocado.com/categories/")
        slug_path, category_id = path.rsplit("/", 1)
        unique_paths[slug_path] = category_id

    leafs: list[tuple[str, str]] = []
    for slug_path, category_id in unique_paths.items():
        prefix = slug_path + "/"
        if not any(other.startswith(prefix) for other in unique_paths if other != slug_path):
            leafs.append((slug_path, category_id))

    leafs.sort()
    return leafs


def start_browser(cookie_rows: list[tuple]) -> webdriver.Firefox:
    options = Options()
    options.add_argument("-headless")
    service = Service(log_output=os.devnull)
    driver = webdriver.Firefox(options=options, service=service)
    driver.set_page_load_timeout(60)
    driver.get("https://www.ocado.com/")

    now = int(time.time())
    for host, name, value, path, secure, expiry in cookie_rows:
        if "ocado.com" not in host:
            continue
        cookie = {
            "name": name,
            "value": value,
            "path": path or "/",
            "secure": bool(secure),
            "domain": host,
        }
        if expiry and expiry > now:
            cookie["expiry"] = int(expiry)
        try:
            driver.add_cookie(cookie)
        except Exception:
            continue

    driver.get("https://www.ocado.com/")
    return driver


def browser_fetch_category_batch(
    driver: webdriver.Firefox,
    items: list[dict],
    concurrency: int,
) -> list[dict]:
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
      if (completed === items.length) {
        done(results);
      } else {
        launch();
      }
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
        if (item.pageToken) {
          params.set("pageToken", item.pageToken);
        }

        fetch(`/api/webproductpagews/v6/product-pages?${params.toString()}`, {
          credentials: "include",
          headers: {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
          },
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
                error: `JSON parse failure: ${String(error)}`,
                status: response.status,
                textStart: text.slice(0, 200),
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
                  iconAttributes: product.iconAttributes || [],
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
          .finally(() => {
            finishOne();
          });
      }
    }

    launch();
    """
    return driver.execute_async_script(script, items, concurrency)


def browser_fetch_bop_batch(
    driver: webdriver.Firefox,
    ids: list[str],
    concurrency: int,
) -> dict:
    script = r"""
    const ids = arguments[0];
    const concurrency = arguments[1];
    const done = arguments[arguments.length - 1];

    function normalize(text) {
      return (text || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    }

    function explicitlySaysVegan(title, text) {
      const normalized = normalize(text);
      if (!normalized) return false;
      if (/\bnot\s+suitable\s+for\b.{0,40}\bvegans?\b/i.test(normalized)) return false;
      if (/\bsuitable\s+for\s+vegans?\b/i.test(normalized)) return true;
      if (/\bsuitable\s+for\s+vegetarians?\s*(?:and|&)\s*vegans?\b/i.test(normalized)) return true;
      if (/\bsuitable\s+for\s+vegans?\s*(?:and|&)\s*vegetarians?\b/i.test(normalized)) return true;
      if (/\bsuitable\s+for\s+(?:a\s+)?vegan\s+diet\b/i.test(normalized)) return true;
      if (/\bsuitable\s+for\s+vegan\s+diets\b/i.test(normalized)) return true;
      if (title === "dietaryInformation" && /^(?:suitable\s+for\s+)?(?:vegetarians?\s+and\s+)?vegans?$/i.test(normalized)) {
        return true;
      }
      return false;
    }

    function hasVeganIcon(product) {
      const attrs = (product && product.iconAttributes) || [];
      return attrs.some((attr) => {
        const label = (attr.label || "").toLowerCase();
        const file = (attr.file || "").toLowerCase();
        return label === "vegan" || file === "vegan";
      });
    }

    const results = [];
    const errors = [];
    let index = 0;
    let active = 0;
    let completed = 0;

    function finishOne() {
      completed += 1;
      active -= 1;
      if (completed === ids.length) {
        done({ processed: ids.length, manufacturerMatches: results, errors });
      } else {
        launch();
      }
    }

    function launch() {
      while (active < concurrency && index < ids.length) {
        const id = ids[index++];
        active += 1;

        fetch(`/api/webproductpagews/v5/products/bop?retailerProductId=${id}`, { credentials: "include" })
          .then(async (response) => {
            const text = await response.text();
            let data = null;
            try {
              data = JSON.parse(text);
            } catch (error) {
              errors.push({ id, error: `JSON parse failure: ${String(error)}` });
              return;
            }

            if (response.status !== 200 || !data.product) {
              errors.push({ id, status: response.status, error: "Unexpected product response" });
              return;
            }

            if (hasVeganIcon(data.product)) {
              return;
            }

            const matches = [];
            const fields = (data.bopData && data.bopData.fields) || [];
            for (const field of fields) {
              if (explicitlySaysVegan(field.title, field.content)) {
                matches.push({ title: field.title, content: normalize(field.content) });
              }
            }

            const detailedDescription = data.bopData ? data.bopData.detailedDescription : "";
            if (explicitlySaysVegan("detailedDescription", detailedDescription)) {
              matches.push({
                title: "detailedDescription",
                content: normalize(detailedDescription),
              });
            }

            if (matches.length > 0) {
              results.push({
                retailerProductId: id,
                name: data.product.name || null,
                brand: data.product.brand || null,
                iconAttributes: data.product.iconAttributes || [],
                matchedFields: matches,
              });
            }
          })
          .catch((error) => {
            errors.push({ id, error: String(error) });
          })
          .finally(() => {
            finishOne();
          });
      }
    }

    launch();
    """
    return driver.execute_async_script(script, ids, concurrency)


def main() -> int:
    args = parse_args()
    start = time.time()

    cookie_rows = build_cookie_rows(args.cookies_db)
    cookie_jar = build_cookie_jar(cookie_rows)

    leaf_categories = load_leaf_categories(cookie_jar, args.timeout)
    if args.category_limit:
        leaf_categories = leaf_categories[: args.category_limit]
    print(f"Loaded {len(leaf_categories)} leaf categories", flush=True)

    driver = start_browser(cookie_rows)
    manufacturer_matches: list[dict] = []
    bop_errors: list[dict] = []
    failed_categories: list[dict] = []
    products_by_id: dict[str, dict] = {}
    category_page_count = 0
    completed_leaf_categories = 0
    open_page_count_by_category = {category_id: 1 for _slug_path, category_id in leaf_categories}

    try:
        pending_category_pages = deque(
            {"slugPath": slug_path, "categoryId": category_id, "pageToken": None}
            for slug_path, category_id in leaf_categories
        )
        category_retry_counts: dict[tuple[str, str, str | None], int] = {}

        while pending_category_pages:
            batch: list[dict] = []
            while pending_category_pages and len(batch) < args.category_batch_size:
                batch.append(pending_category_pages.popleft())

            page_results = browser_fetch_category_batch(
                driver,
                batch,
                args.category_batch_concurrency,
            )

            for result in page_results:
                key = (
                    result["slugPath"],
                    result["categoryId"],
                    result.get("pageToken"),
                )
                if result.get("error") or result.get("status") != 200:
                    retry_count = category_retry_counts.get(key, 0)
                    if retry_count < 3:
                        category_retry_counts[key] = retry_count + 1
                        pending_category_pages.append(
                            {
                                "slugPath": result["slugPath"],
                                "categoryId": result["categoryId"],
                                "pageToken": result.get("pageToken"),
                            }
                        )
                    else:
                        failed_categories.append(
                            {
                                "slugPath": result["slugPath"],
                                "categoryId": result["categoryId"],
                                "pageToken": result.get("pageToken"),
                                "error": result.get("error"),
                                "status": result.get("status"),
                                "textStart": result.get("textStart"),
                            }
                        )
                        open_page_count_by_category[result["categoryId"]] -= 1
                    continue

                category_page_count += 1
                for product in result.get("products", []):
                    retailer_product_id = product.get("retailerProductId")
                    if not retailer_product_id:
                        continue
                    entry = products_by_id.setdefault(
                        retailer_product_id,
                        {
                            "retailerProductId": retailer_product_id,
                            "name": product.get("name"),
                            "brand": product.get("brand"),
                            "iconAttributes": product.get("iconAttributes") or [],
                            "categoryPaths": set(),
                        },
                    )
                    entry["categoryPaths"].add(result["slugPath"])
                    if not entry.get("name") and product.get("name"):
                        entry["name"] = product.get("name")
                    if not entry.get("brand") and product.get("brand"):
                        entry["brand"] = product.get("brand")
                    if not entry.get("iconAttributes") and product.get("iconAttributes"):
                        entry["iconAttributes"] = product.get("iconAttributes") or []

                if result.get("nextPageToken"):
                    pending_category_pages.append(
                        {
                            "slugPath": result["slugPath"],
                            "categoryId": result["categoryId"],
                            "pageToken": result["nextPageToken"],
                        }
                    )
                    open_page_count_by_category[result["categoryId"]] += 1

                open_page_count_by_category[result["categoryId"]] -= 1
                if open_page_count_by_category[result["categoryId"]] == 0:
                    completed_leaf_categories += 1

                if (
                    completed_leaf_categories % 100 == 0
                    or completed_leaf_categories == len(leaf_categories)
                ) and completed_leaf_categories:
                    print(
                        f"Enumerated {completed_leaf_categories}/{len(leaf_categories)} leaf categories | "
                        f"pages={category_page_count} | unique_products={len(products_by_id)} | "
                        f"pending_pages={len(pending_category_pages)} | category_errors={len(failed_categories)}",
                        flush=True,
                    )

        candidate_ids = []
        for retailer_product_id, product in products_by_id.items():
            attrs = product.get("iconAttributes") or []
            has_vegan_icon = any(
                (attr.get("label") or "").strip().lower() == "vegan"
                or (attr.get("file") or "").strip().lower() == "vegan"
                for attr in attrs
            )
            if not has_vegan_icon:
                candidate_ids.append(retailer_product_id)

        candidate_ids.sort()
        print(
            f"Unique products from categories: {len(products_by_id)} | "
            f"candidate products without vegan icon: {len(candidate_ids)}",
            flush=True,
        )

        total_batches = (len(candidate_ids) + args.bop_batch_size - 1) // args.bop_batch_size
        for batch_index in range(total_batches):
            batch_ids = candidate_ids[
                batch_index * args.bop_batch_size : (batch_index + 1) * args.bop_batch_size
            ]
            batch_result = browser_fetch_bop_batch(
                driver,
                batch_ids,
                args.bop_batch_concurrency,
            )

            manufacturer_matches.extend(batch_result["manufacturerMatches"])
            bop_errors.extend(batch_result["errors"])

            if (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                print(
                    f"BOP batches {batch_index + 1}/{total_batches} | "
                    f"processed={min((batch_index + 1) * args.bop_batch_size, len(candidate_ids))}/{len(candidate_ids)} | "
                    f"vegan_according_to_manufacturer={len(manufacturer_matches)} | errors={len(bop_errors)}",
                    flush=True,
                )
    finally:
        driver.quit()

    final_results: list[dict] = []
    for item in manufacturer_matches:
        product_meta = products_by_id.get(item["retailerProductId"], {})
        final_results.append(
            {
                "retailerProductId": item["retailerProductId"],
                "name": item.get("name") or product_meta.get("name"),
                "brand": item.get("brand") or product_meta.get("brand"),
                "url": f"https://www.ocado.com/products/{item['retailerProductId']}/details",
                "iconAttributes": item.get("iconAttributes") or product_meta.get("iconAttributes") or [],
                "categoryPaths": sorted(product_meta.get("categoryPaths") or []),
                "matchedFields": item["matchedFields"],
            }
        )

    final_results.sort(key=lambda item: (item["brand"] or "", item["name"] or "", item["retailerProductId"]))

    output_prefix = Path(args.output_prefix)
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")
    meta_path = output_prefix.with_name(f"{output_prefix.name}_meta.json")

    json_path.write_text(json.dumps(final_results, indent=2, ensure_ascii=True), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "retailerProductId",
                "name",
                "brand",
                "url",
                "matchedFieldTitles",
                "matchedSnippets",
                "categoryPaths",
            ],
        )
        writer.writeheader()
        for item in final_results:
            writer.writerow(
                {
                    "retailerProductId": item["retailerProductId"],
                    "name": item["name"],
                    "brand": item["brand"],
                    "url": item["url"],
                    "matchedFieldTitles": " | ".join(match["title"] for match in item["matchedFields"]),
                    "matchedSnippets": " | ".join(match["content"] for match in item["matchedFields"]),
                    "categoryPaths": " | ".join(item["categoryPaths"]),
                }
            )

    meta = {
        "generatedAtEpoch": int(time.time()),
        "leafCategoryCount": len(leaf_categories),
        "categoryPageCount": category_page_count,
        "uniqueProducts": len(products_by_id),
        "failedCategoryCount": len(failed_categories),
        "failedCategories": failed_categories[:500],
        "candidateProductsWithoutVeganIcon": len(candidate_ids),
        "matchCount": len(final_results),
        "bopErrorCount": len(bop_errors),
        "bopErrors": bop_errors[:500],
        "elapsedSeconds": round(time.time() - start, 2),
        "categoryBatchSize": args.category_batch_size,
        "categoryBatchConcurrency": args.category_batch_concurrency,
        "bopBatchSize": args.bop_batch_size,
        "bopBatchConcurrency": args.bop_batch_concurrency,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"Wrote {json_path}", flush=True)
    print(f"Wrote {csv_path}", flush=True)
    print(f"Wrote {meta_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
