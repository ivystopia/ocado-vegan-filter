#!/usr/bin/env python3
"""Find Ocado products that are vegan according to title/ingredients evidence.

This scraper deliberately keeps raw/pre-check data:

- product universe JSONL: every product discovered through Ocado categories
- raw category page JSONL: raw category API payloads
- raw BOP JSONL: raw product API payloads for every non-vegan-according-to-manufacturer product fetched
- precheck audit JSONL: deterministic skip/candidate state before LLM classification

The final URL list includes only products that are not in the vegan-according-to-manufacturer
vegan-according-to-manufacturer list, are not officially tagged as vegan by
Ocado, and pass two independent conservative classifier passes.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import requests
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"
CATEGORY_SITEMAP_URL = "https://www.ocado.com/sitemaps/sitemap-categories-part1.xml"
DEFAULT_OUTPUT_PREFIX = "ocado_vegan_according_to_ingredients"
DEFAULT_MANUFACTURER_URLS = "ocado_vegan_according_to_manufacturer_urls.txt"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")

SUITABLE_FOR_VEGAN_PATTERNS = [
    re.compile(r"\bsuitable\s+for\s+vegans?\b", re.IGNORECASE),
    re.compile(r"\bsuitable\s+for\s+vegetarians?\s*(?:and|&)\s*vegans?\b", re.IGNORECASE),
    re.compile(r"\bsuitable\s+for\s+vegans?\s*(?:and|&)\s*vegetarians?\b", re.IGNORECASE),
    re.compile(r"\bsuitable\s+for\s+(?:a\s+)?vegan\s+diet\b", re.IGNORECASE),
    re.compile(r"\bsuitable\s+for\s+vegan\s+diets\b", re.IGNORECASE),
]
DIETARY_SHORT_VEGAN_RE = re.compile(r"^(?:suitable\s+for\s+)?(?:vegetarians?\s+and\s+)?vegans?$", re.IGNORECASE)
NEGATIVE_VEGAN_RE = re.compile(r"\bnot\s+suitable\s+for\b.{0,80}\bvegans?\b", re.IGNORECASE)
PRODUCT_URL_ID_RE = re.compile(r"/products/(?:[^/?#]*[-/])?(?P<id>\d+)(?:/details)?(?:[/?#]|$)")

OBVIOUS_NON_VEGAN_RE = re.compile(
    r"\b("
    r"milk|whey|casein|caseinate|lactose|cream|butter|cheese|yoghurt|yogurt|egg|albumen|honey|beeswax|shellac|"
    r"gelatine|gelatin|collagen|isinglass|carmine|cochineal|lanolin|lard|suet|tallow|beef|pork|bacon|ham|"
    r"chicken|turkey|duck|goose|lamb|mutton|fish|anchovy|tuna|salmon|cod|shellfish|prawn|shrimp|crab|lobster|"
    r"oyster|mussel|clam|e120"
    r")\b",
    re.IGNORECASE,
)
AMBIGUOUS_INGREDIENT_RE = re.compile(
    r"\b("
    r"fortified|vitamins?|vitamin\s+d3?|b3|b1|niacin|thiamin|riboflavin|cyanocobalamin|cholecalciferol|"
    r"enzymes?|rennet|flavou?rings?|natural\s+flavou?rings?|mono-?\s*and\s*diglycerides?|diglycerides?|"
    r"glycerine|glycerol|stearic\s+acid|lactic\s+acid|lecithin|omega\s*3|wax|glaze|colour|colors?"
    r")\b",
    re.IGNORECASE,
)
SINGLE_INGREDIENT_CATEGORY_RE = re.compile(
    r"\b(fruit|vegetables?|beans|peas|sweetcorn|salad|herbs?|spices?|rice|pasta|pulses?|nuts?|seeds?)\b",
    re.IGNORECASE,
)
SIMPLE_PRODUCT_WORD_RE = re.compile(
    r"\b("
    r"beans?|peas?|broccoli|carrots?|potatoes?|tomatoes?|cucumber|lettuce|spinach|rocket|cabbage|onions?|"
    r"garlic|ginger|mushrooms?|peppers?|courgettes?|aubergine|apples?|bananas?|oranges?|lemons?|limes?|"
    r"grapes?|berries|strawberries|blueberries|raspberries|mango|pineapple|melon|rice|lentils?|chickpeas?|"
    r"semolina|spaghetti|penne|fusilli"
    r")\b",
    re.IGNORECASE,
)
COMPLEX_PRODUCT_WORD_RE = re.compile(
    r"\b("
    r"sauce|seasoned|marinated|flavou?red|mix|mixed|meal|ready|kit|stir\s*fry|soup|stock|dressing|"
    r"cake|biscuit|cookie|bar|snack|crisps?|chocolate|dessert|yogurt|yoghurt|cheese|cream"
    r")\b",
    re.IGNORECASE,
)


def normalize_text(raw_text: str | None) -> str:
    text = html.unescape(raw_text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def explicitly_says_vegan(field_title: str, text: str | None = None) -> bool:
    if text is None:
        field_title, text = "", field_title

    normalized = normalize_text(text)
    normalized_title = normalize_text(field_title).replace(" ", "").lower()
    if not normalized or NEGATIVE_VEGAN_RE.search(normalized):
        return False
    if any(pattern.search(normalized) for pattern in SUITABLE_FOR_VEGAN_PATTERNS):
        return True
    return normalized_title == "dietaryinformation" and DIETARY_SHORT_VEGAN_RE.fullmatch(normalized) is not None


def product_has_vegan_tag(product: dict[str, Any] | None) -> bool:
    if not product:
        return False
    for attribute in product.get("iconAttributes", []) or product.get("attributes", []) or []:
        label = normalize_text(attribute.get("label")).lower()
        file_name = normalize_text(attribute.get("file") or attribute.get("icon")).lower()
        if label == "vegan" or file_name == "vegan":
            return True
    return False


def product_id_from_url(url: str) -> str | None:
    match = PRODUCT_URL_ID_RE.search(url or "")
    return match.group("id") if match else None


def product_url(retailer_product_id: str) -> str:
    return f"https://www.ocado.com/products/{retailer_product_id}/details"


def product_payload(record: dict[str, Any]) -> dict[str, Any]:
    if "product" in record and isinstance(record["product"], dict):
        return record["product"]
    if "data" in record and isinstance(record["data"], dict) and isinstance(record["data"].get("product"), dict):
        return record["data"]["product"]
    return record


def bop_payload(record: dict[str, Any]) -> dict[str, Any]:
    if "bopData" in record and isinstance(record["bopData"], dict):
        return record["bopData"]
    if "data" in record and isinstance(record["data"], dict) and isinstance(record["data"].get("bopData"), dict):
        return record["data"]["bopData"]
    return record


def product_id(record: dict[str, Any]) -> str | None:
    product = product_payload(record)
    value = record.get("retailerProductId") or product.get("retailerProductId") or product.get("retailer_product_id")
    return str(value) if value else product_id_from_url(str(record.get("url") or ""))


def product_name(record: dict[str, Any]) -> str:
    return normalize_text(record.get("name") or product_payload(record).get("name"))


def product_brand(record: dict[str, Any]) -> str:
    return normalize_text(record.get("brand") or product_payload(record).get("brand"))


def product_category_path(record: dict[str, Any]) -> list[str]:
    product = product_payload(record)
    paths = record.get("categoryPaths") or record.get("categoryPath") or product.get("categoryPath") or []
    if isinstance(paths, str):
        return [paths]
    return [normalize_text(str(path)) for path in paths if normalize_text(str(path))]


def product_fields(record: dict[str, Any]) -> list[dict[str, str]]:
    fields = record.get("fields")
    if fields is None:
        fields = bop_payload(record).get("fields", [])
    normalized_fields = []
    for field in fields or []:
        title = normalize_text(field.get("title"))
        content = normalize_text(field.get("content"))
        if title or content:
            normalized_fields.append({"title": title, "content": content})
    detailed = normalize_text(record.get("detailedDescription") or bop_payload(record).get("detailedDescription"))
    if detailed:
        normalized_fields.append({"title": "detailedDescription", "content": detailed})
    return normalized_fields


def field_content(record: dict[str, Any], title_names: Iterable[str]) -> str:
    wanted = {normalize_text(name).replace(" ", "").lower() for name in title_names}
    values = []
    for field in product_fields(record):
        title = field["title"].replace(" ", "").lower()
        if title in wanted:
            values.append(field["content"])
    return " | ".join(values)


def name_says_vegan(record: dict[str, Any]) -> bool:
    return re.search(r"\bvegan\b", product_name(record), re.IGNORECASE) is not None


def ingredients_are_deterministically_unsafe(ingredients: str) -> str | None:
    if not ingredients:
        return None
    if OBVIOUS_NON_VEGAN_RE.search(ingredients):
        return "obvious_non_vegan_ingredient"
    if AMBIGUOUS_INGREDIENT_RE.search(ingredients):
        return "ambiguous_ingredient_or_processing_route"
    return None


def looks_like_single_ingredient_product(record: dict[str, Any]) -> bool:
    name = product_name(record)
    categories = " ".join(product_category_path(record))
    descriptions = " ".join(field["content"] for field in product_fields(record) if field["title"].lower() in {"description", "detaileddescription", "features"})
    haystack = f"{name} {categories} {descriptions}"

    if not SIMPLE_PRODUCT_WORD_RE.search(haystack):
        return False
    if COMPLEX_PRODUCT_WORD_RE.search(name):
        return False
    return bool(SINGLE_INGREDIENT_CATEGORY_RE.search(categories) or SIMPLE_PRODUCT_WORD_RE.search(name))


def extract_product_evidence(record: dict[str, Any]) -> list[dict[str, str]]:
    """Return only evidence safe enough to submit to the strict classifier."""
    retailer_product_id = product_id(record) or ""
    name = product_name(record)
    evidence: list[dict[str, str]] = []

    if name_says_vegan(record):
        evidence.append(
            {
                "source": "product_name",
                "retailerProductId": retailer_product_id,
                "text": name,
                "reason": "Product name explicitly contains the word vegan.",
            }
        )

    for field in product_fields(record):
        if explicitly_says_vegan(field["title"], field["content"]):
            evidence.append(
                {
                    "source": field["title"],
                    "retailerProductId": retailer_product_id,
                    "text": field["content"],
                    "reason": "Product page text explicitly says the product is suitable for vegans.",
                }
            )

    ingredients = field_content(record, ["Ingredients", "Ingredient", "Composition", "Materials"])
    unsafe_reason = ingredients_are_deterministically_unsafe(ingredients)
    if ingredients and not unsafe_reason:
        evidence.append(
            {
                "source": "ingredients",
                "retailerProductId": retailer_product_id,
                "text": ingredients,
                "reason": "Ingredients/composition field contains no deterministic non-vegan or ambiguous terms.",
            }
        )
    elif not ingredients and looks_like_single_ingredient_product(record):
        evidence.append(
            {
                "source": "single_ingredient_no_ingredients",
                "retailerProductId": retailer_product_id,
                "text": f"{name} appears to be a single-ingredient product and no ingredients field is present.",
                "reason": "Single-ingredient product identity can be judged by the classifier without an ingredients list.",
            }
        )

    if unsafe_reason and not evidence:
        return []
    return evidence


def build_classifier_prompt(product: dict[str, Any], evidence: list[dict[str, str]]) -> str:
    context = {
        "retailerProductId": product_id(product),
        "name": product_name(product),
        "brand": product_brand(product),
        "url": product.get("url") or product_url(product_id(product) or ""),
        "categoryPath": product_category_path(product),
        "iconAttributes": product_payload(product).get("iconAttributes") or product.get("iconAttributes") or [],
        "fields": product_fields(product),
        "evidence": evidence,
    }
    return (
        "You are independently deciding whether an Ocado product can be treated as vegan with 100% certainty.\n"
        "Use only the supplied product context and evidence. Do not infer from brand reputation.\n"
        "Return include only when the product is definitely vegan because of explicit vegan title/text or because every ingredient/composition item is definitely vegan.\n"
        "Return ambiguous for ingredients that are usually vegan but can have animal-derived routes, including fortified wheat/flour, vitamins, enzymes, unspecified flavourings, glycerine/glycerol, waxes/glazes, colours, lecithin, mono/diglycerides, or insufficient evidence.\n"
        "Return exclude for obvious animal ingredients or any non-vegan statement.\n"
        "May-contain allergen traces do not by themselves make a product non-vegan, but actual ingredients do.\n\n"
        f"Product context JSON:\n{json.dumps(context, indent=2, ensure_ascii=True)}\n"
    )


def decision_is_include(decision: dict[str, Any] | None) -> bool:
    if not decision:
        return False
    normalized = normalize_text(str(decision.get("decision") or "")).lower()
    if normalized:
        return normalized == "include"
    return decision.get("isVegan") is True


def merge_classifier_decisions(
    candidates: list[dict[str, Any]],
    decisions_a: dict[str, dict[str, Any]],
    decisions_b: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    included = []
    for candidate in candidates:
        retailer_product_id = str(candidate.get("retailerProductId") or product_id(candidate) or "")
        decision_a = decisions_a.get(retailer_product_id)
        decision_b = decisions_b.get(retailer_product_id) if decisions_b is not None else None
        if not decision_is_include(decision_a):
            continue
        if decisions_b is not None and not decision_is_include(decision_b):
            continue
        merged = dict(candidate)
        merged["classifierPassA"] = decision_a
        if decisions_b is not None:
            merged["classifierPassB"] = decision_b
            merged["consensus"] = "include" if decision_is_include(decision_b) else "disagreement"
        else:
            merged["classifierDecision"] = decision_a
        included.append(merged)
    return included


def should_skip_vegan_according_to_manufacturer(product: dict[str, Any], manufacturer_vegan_ids: set[str]) -> bool:
    retailer_product_id = product_id(product)
    return bool(retailer_product_id and retailer_product_id in manufacturer_vegan_ids)


def jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []

    def iterator() -> Iterable[dict[str, Any]]:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSONL row: {exc}") from exc

    return iterator()


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
            count += 1
    return count


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def load_manufacturer_vegan_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ids.add(product_id_from_url(line) or line.rsplit("/", 1)[-1])
    return ids


def load_env_file(path: Path = Path.home() / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_cookie_rows(cookies_db: str) -> list[tuple]:
    source = Path(cookies_db).expanduser()
    with tempfile.TemporaryDirectory() as temporary_directory:
        copy_path = Path(temporary_directory) / "cookies.sqlite"
        shutil.copy2(source, copy_path)
        conn = sqlite3.connect(copy_path)
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

    urls = re.findall(r"<loc>(https://www\.ocado\.com/categories/[^<]+)</loc>", response.text)
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

    return sorted(leafs)


def start_browser(cookie_rows: list[tuple], timeout: int) -> webdriver.Firefox:
    options = Options()
    options.add_argument("-headless")
    service = Service(log_output=os.devnull)
    driver = webdriver.Firefox(options=options, service=service)
    driver.set_page_load_timeout(max(timeout, 60))
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


def browser_fetch_category_batch(driver: webdriver.Firefox, items: list[dict[str, Any]], concurrency: int) -> list[dict[str, Any]]:
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
              raw: data,
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


def browser_fetch_bop_batch(driver: webdriver.Firefox, ids: list[str], concurrency: int) -> list[dict[str, Any]]:
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
              results.push({retailerProductId: id, status: response.status, error: `JSON parse failure: ${String(error)}`, textStart: text.slice(0, 500)});
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


def output_paths(prefix: str) -> dict[str, Path]:
    return {
        "product_universe": Path(f"{prefix}_product_universe.jsonl"),
        "category_pages_raw": Path(f"{prefix}_category_pages_raw.jsonl"),
        "bop_raw": Path(f"{prefix}_bop_raw.jsonl"),
        "precheck_audit": Path(f"{prefix}_precheck_audit.jsonl"),
        "classifier_a": Path(f"{prefix}_classifier_pass_a.jsonl"),
        "classifier_b": Path(f"{prefix}_classifier_pass_b.jsonl"),
        "audit_jsonl": Path(f"{prefix}_audit.jsonl"),
        "audit_csv": Path(f"{prefix}_audit.csv"),
        "urls": Path(f"{prefix}_urls.txt"),
        "meta": Path(f"{prefix}_meta.json"),
    }


def enumerate_categories(args: argparse.Namespace) -> dict[str, Any]:
    paths = output_paths(args.output_prefix)
    start = time.time()
    cookie_rows = build_cookie_rows(args.cookies_db)
    cookie_jar = build_cookie_jar(cookie_rows)
    leaf_categories = load_leaf_categories(cookie_jar, args.timeout)
    if args.category_limit:
        leaf_categories = leaf_categories[: args.category_limit]
    print(f"Loaded {len(leaf_categories)} leaf categories", flush=True)

    products_by_id: dict[str, dict[str, Any]] = {}
    failed_categories: list[dict[str, Any]] = []
    category_page_count = 0
    completed_leaf_categories = 0
    open_page_count_by_category = {category_id: 1 for _slug_path, category_id in leaf_categories}

    driver = start_browser(cookie_rows, args.timeout)
    try:
        pending = deque(
            {"slugPath": slug_path, "categoryId": category_id, "pageToken": None}
            for slug_path, category_id in leaf_categories
        )
        retries: dict[tuple[str, str, str | None], int] = {}

        while pending:
            batch = []
            while pending and len(batch) < args.category_batch_size:
                batch.append(pending.popleft())
            page_results = browser_fetch_category_batch(driver, batch, args.category_batch_concurrency)

            raw_rows = []
            for result in page_results:
                key = (result["slugPath"], result["categoryId"], result.get("pageToken"))
                if result.get("error") or result.get("status") != 200:
                    retry_count = retries.get(key, 0)
                    if retry_count < 3:
                        retries[key] = retry_count + 1
                        pending.append(
                            {
                                "slugPath": result["slugPath"],
                                "categoryId": result["categoryId"],
                                "pageToken": result.get("pageToken"),
                            }
                        )
                    else:
                        failed_categories.append({k: result.get(k) for k in ["slugPath", "categoryId", "pageToken", "status", "error", "textStart"]})
                        open_page_count_by_category[result["categoryId"]] -= 1
                    continue

                category_page_count += 1
                raw_rows.append(
                    {
                        "fetchedAtEpoch": int(time.time()),
                        "slugPath": result["slugPath"],
                        "categoryId": result["categoryId"],
                        "pageToken": result.get("pageToken"),
                        "status": result.get("status"),
                        "raw": result.get("raw"),
                    }
                )

                for product in result.get("products", []):
                    retailer_product_id = product.get("retailerProductId")
                    if not retailer_product_id:
                        continue
                    entry = products_by_id.setdefault(
                        str(retailer_product_id),
                        {
                            "retailerProductId": str(retailer_product_id),
                            "name": product.get("name"),
                            "brand": product.get("brand"),
                            "iconAttributes": product.get("iconAttributes") or [],
                            "categoryPaths": set(),
                            "seenInCategoryPages": 0,
                        },
                    )
                    entry["categoryPaths"].add(result["slugPath"])
                    entry["seenInCategoryPages"] += 1
                    if not entry.get("name") and product.get("name"):
                        entry["name"] = product.get("name")
                    if not entry.get("brand") and product.get("brand"):
                        entry["brand"] = product.get("brand")
                    if not entry.get("iconAttributes") and product.get("iconAttributes"):
                        entry["iconAttributes"] = product.get("iconAttributes") or []

                if result.get("nextPageToken"):
                    pending.append(
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

            append_jsonl(paths["category_pages_raw"], raw_rows)
            if completed_leaf_categories and (
                completed_leaf_categories % 100 == 0 or completed_leaf_categories == len(leaf_categories)
            ):
                print(
                    f"Enumerated {completed_leaf_categories}/{len(leaf_categories)} leaf categories | "
                    f"pages={category_page_count} | unique_products={len(products_by_id)} | "
                    f"pending_pages={len(pending)} | category_errors={len(failed_categories)}",
                    flush=True,
                )
    finally:
        driver.quit()

    universe_rows = []
    for item in sorted(products_by_id.values(), key=lambda row: row["retailerProductId"]):
        row = dict(item)
        row["categoryPaths"] = sorted(row["categoryPaths"])
        row["url"] = product_url(row["retailerProductId"])
        universe_rows.append(row)
    paths["product_universe"].write_text("", encoding="utf-8")
    append_jsonl(paths["product_universe"], universe_rows)

    return {
        "leafCategoryCount": len(leaf_categories),
        "categoryPageCount": category_page_count,
        "uniqueProducts": len(products_by_id),
        "failedCategoryCount": len(failed_categories),
        "failedCategories": failed_categories[:500],
        "enumerateElapsedSeconds": round(time.time() - start, 2),
    }


def fetch_bop(args: argparse.Namespace) -> dict[str, Any]:
    paths = output_paths(args.output_prefix)
    manufacturer_vegan_ids = load_manufacturer_vegan_ids(Path(args.manufacturer_urls))
    universe = list(jsonl_rows(paths["product_universe"]))
    existing = {
        str(row.get("retailerProductId"))
        for row in jsonl_rows(paths["bop_raw"])
        if row.get("status") == 200
        and isinstance(row.get("data"), dict)
        and isinstance(row["data"].get("product"), dict)
    }
    ids = [
        str(row["retailerProductId"])
        for row in universe
        if str(row["retailerProductId"]) not in existing
        and (args.include_manufacturer_vegan or str(row["retailerProductId"]) not in manufacturer_vegan_ids)
    ]
    ids.sort()
    if args.product_limit:
        ids = ids[: args.product_limit]

    print(f"BOP rows already present={len(existing)} | to_fetch={len(ids)}", flush=True)
    start = time.time()
    errors = []
    fetched = 0
    cookie_rows = build_cookie_rows(args.cookies_db)
    driver = start_browser(cookie_rows, args.timeout)
    try:
        total_batches = (len(ids) + args.bop_batch_size - 1) // args.bop_batch_size
        for batch_index in range(total_batches):
            batch_ids = ids[batch_index * args.bop_batch_size : (batch_index + 1) * args.bop_batch_size]
            rows = browser_fetch_bop_batch(driver, batch_ids, args.bop_batch_concurrency)
            for row in rows:
                row["fetchedAtEpoch"] = int(time.time())
                if row.get("error") or row.get("status") != 200:
                    errors.append(row)
            fetched += append_jsonl(paths["bop_raw"], rows)
            if (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                print(f"BOP batch {batch_index + 1}/{total_batches} | fetched={fetched}/{len(ids)} | errors={len(errors)}", flush=True)
    finally:
        driver.quit()

    return {
        "bopFetched": fetched,
        "bopExistingBeforeRun": len(existing),
        "bopErrorCount": len(errors),
        "bopErrors": errors[:500],
        "bopElapsedSeconds": round(time.time() - start, 2),
    }


def build_product_record(universe_by_id: dict[str, dict[str, Any]], bop_row: dict[str, Any] | None, retailer_product_id: str) -> dict[str, Any]:
    universe_row = universe_by_id.get(retailer_product_id, {})
    if bop_row and isinstance(bop_row.get("data"), dict):
        data = bop_row["data"]
        product = dict(data.get("product") or {})
        bop = data.get("bopData") or {}
        product.setdefault("retailerProductId", retailer_product_id)
        product.setdefault("name", universe_row.get("name"))
        product.setdefault("brand", universe_row.get("brand"))
        product.setdefault("iconAttributes", universe_row.get("iconAttributes") or [])
        product["categoryPaths"] = universe_row.get("categoryPaths") or product.get("categoryPath") or []
        product["url"] = product_url(retailer_product_id)
        product["fields"] = bop.get("fields") or []
        product["detailedDescription"] = bop.get("detailedDescription") or ""
        return product
    row = dict(universe_row)
    row.setdefault("retailerProductId", retailer_product_id)
    row.setdefault("url", product_url(retailer_product_id))
    row.setdefault("fields", [])
    row.setdefault("detailedDescription", "")
    return row


def extract_precheck(args: argparse.Namespace) -> dict[str, Any]:
    paths = output_paths(args.output_prefix)
    manufacturer_vegan_ids = load_manufacturer_vegan_ids(Path(args.manufacturer_urls))
    universe = list(jsonl_rows(paths["product_universe"]))
    universe_by_id = {str(row["retailerProductId"]): row for row in universe}
    bop_by_id = {}
    bop_error_by_id = {}
    for row in jsonl_rows(paths["bop_raw"]):
        retailer_product_id = str(row.get("retailerProductId") or "")
        if not retailer_product_id:
            continue
        if row.get("status") == 200 and isinstance(row.get("data"), dict) and isinstance(row["data"].get("product"), dict):
            bop_by_id[retailer_product_id] = row
        else:
            bop_error_by_id[retailer_product_id] = row
    counts: dict[str, int] = {}
    rows = []

    for universe_row in universe:
        retailer_product_id = str(universe_row["retailerProductId"])
        status = "candidate"
        reason = ""
        product = build_product_record(universe_by_id, bop_by_id.get(retailer_product_id), retailer_product_id)
        evidence: list[dict[str, str]] = []

        if retailer_product_id in manufacturer_vegan_ids:
            status = "skip_old_vegan_according_to_manufacturer"
            reason = "Product was already included in the vegan-according-to-manufacturer vegan-according-to-manufacturer list."
        elif product_has_vegan_tag(universe_row) or product_has_vegan_tag(product):
            status = "skip_official_vegan_tag"
            reason = "Product is already officially tagged vegan in Ocado metadata."
        elif retailer_product_id not in bop_by_id:
            if retailer_product_id in bop_error_by_id:
                status = "skip_bop_error"
                reason = "BOP raw payload fetch failed; see raw BOP audit."
            else:
                status = "skip_missing_bop_raw"
                reason = "No BOP raw payload is available for this product."
        else:
            evidence = extract_product_evidence(product)
            if not evidence:
                status = "skip_no_safe_vegan_evidence"
                reason = "No explicit vegan title/text and no deterministic ingredient/composition evidence safe enough for classification."

        row = {
            "retailerProductId": retailer_product_id,
            "url": product_url(retailer_product_id),
            "name": product_name(product),
            "brand": product_brand(product),
            "categoryPaths": product_category_path(product),
            "status": status,
            "reason": reason,
            "evidence": evidence,
            "precheckedAtEpoch": int(time.time()),
        }
        counts[status] = counts.get(status, 0) + 1
        rows.append(row)

    paths["precheck_audit"].write_text("", encoding="utf-8")
    append_jsonl(paths["precheck_audit"], rows)
    return {"precheckCounts": counts, "precheckRows": len(rows)}


def decision_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "retailerProductId": {"type": "string"},
            "decision": {"type": "string", "enum": ["include", "exclude", "ambiguous"]},
            "isVegan": {"type": "boolean"},
            "evidenceType": {"type": "string"},
            "reason": {"type": "string"},
            "riskNotes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["retailerProductId", "decision", "isVegan", "evidenceType", "reason", "riskNotes"],
    }


def response_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "items": decision_json_schema(),
            }
        },
        "required": ["decisions"],
    }


def extract_response_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for output in payload.get("output", []) or []:
        for content in output.get("content", []) or []:
            if isinstance(content.get("text"), str):
                return content["text"]
            if "json" in content:
                return json.dumps(content["json"])
    raise ValueError("Could not find response text in OpenAI response")


def call_openai_classifier(prompt: str, model: str) -> dict[str, Any]:
    load_env_file()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict vegan ingredient reviewer. "
                        "Be conservative: if there is any uncertainty, choose ambiguous or exclude."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "vegan_according_to_ingredients_decision",
                    "schema": response_json_schema(),
                    "strict": True,
                }
            },
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI API error {response.status_code}: {response.text[:500]}")
    text = extract_response_text(response.json())
    return json.loads(text)


def build_batch_classifier_prompt(candidates: list[dict[str, Any]], pass_id: str) -> str:
    payload = []
    for candidate in candidates:
        payload.append(
            {
                "retailerProductId": candidate["retailerProductId"],
                "name": candidate["name"],
                "brand": candidate["brand"],
                "url": candidate["url"],
                "categoryPaths": candidate.get("categoryPaths") or [],
                "evidence": candidate.get("evidence") or [],
            }
        )
    return (
        "You are independently deciding whether Ocado products can be treated as vegan with 100% certainty.\n"
        "Review every product separately and return exactly one decision per retailerProductId.\n"
        "Use only the supplied product context and evidence. Do not infer from brand reputation.\n"
        "Return include only when the product is definitely vegan because of explicit vegan title/text or because every ingredient/composition item is definitely vegan.\n"
        "Return ambiguous for ingredients that are usually vegan but can have animal-derived routes, including fortified wheat/flour, vitamins, enzymes, unspecified flavourings, glycerine/glycerol, waxes/glazes, colours, lecithin, mono/diglycerides, or insufficient evidence.\n"
        "Return exclude for obvious animal ingredients or any non-vegan statement.\n"
        "May-contain allergen traces do not by themselves make a product non-vegan, but actual ingredients do.\n"
        f"This is independent classifier pass {pass_id.upper()}. Do not assume another pass will agree.\n\n"
        f"Products JSON:\n{json.dumps(payload, indent=2, ensure_ascii=True)}\n"
    )


def classify_candidates(args: argparse.Namespace, pass_id: str) -> dict[str, Any]:
    paths = output_paths(args.output_prefix)
    output_path = paths[f"classifier_{pass_id}"]
    existing = {str(row.get("retailerProductId")) for row in jsonl_rows(output_path)}
    candidates = [row for row in jsonl_rows(paths["precheck_audit"]) if row.get("status") == "candidate"]
    candidates = [row for row in candidates if str(row["retailerProductId"]) not in existing]
    if args.classification_limit:
        candidates = candidates[: args.classification_limit]
    print(f"classifier pass {pass_id}: existing={len(existing)} | to_classify={len(candidates)}", flush=True)

    rows = []
    errors = []
    total_batches = (len(candidates) + args.classification_batch_size - 1) // args.classification_batch_size
    processed = 0
    for batch_index in range(total_batches):
        batch = candidates[
            batch_index * args.classification_batch_size : (batch_index + 1) * args.classification_batch_size
        ]
        prompt = build_batch_classifier_prompt(batch, pass_id)
        try:
            payload = call_openai_classifier(prompt, args.model)
            decisions = payload.get("decisions")
            if not isinstance(decisions, list):
                raise ValueError("Classifier response did not contain a decisions array")
            decisions_by_id = {str(decision.get("retailerProductId")): decision for decision in decisions if isinstance(decision, dict)}
            for candidate in batch:
                retailer_product_id = str(candidate["retailerProductId"])
                decision = decisions_by_id.get(retailer_product_id)
                if not decision:
                    raise ValueError(f"Classifier response omitted product {retailer_product_id}")
                rows.append(
                    {
                        "retailerProductId": retailer_product_id,
                        "passId": pass_id,
                        "model": args.model,
                        "classifiedAtEpoch": int(time.time()),
                        "decision": decision,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            for candidate in batch:
                row = {
                    "retailerProductId": str(candidate["retailerProductId"]),
                    "passId": pass_id,
                    "model": args.model,
                    "classifiedAtEpoch": int(time.time()),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                rows.append(row)
                errors.append(row)
        processed += len(batch)
        if len(rows) >= args.classification_flush_interval:
            append_jsonl(output_path, rows)
            rows = []
        if (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
            print(f"classifier pass {pass_id}: {processed}/{len(candidates)} | errors={len(errors)}", flush=True)
    append_jsonl(output_path, rows)
    return {f"classifierPass{pass_id.upper()}Rows": len(candidates), f"classifierPass{pass_id.upper()}Errors": len(errors)}


def load_classifier_decisions(path: Path) -> dict[str, dict[str, Any]]:
    decisions = {}
    for row in jsonl_rows(path):
        if row.get("error"):
            continue
        retailer_product_id = str(row.get("retailerProductId") or "")
        decision = row.get("decision")
        if retailer_product_id and isinstance(decision, dict):
            decisions[retailer_product_id] = decision
    return decisions


def finalize_outputs(args: argparse.Namespace) -> dict[str, Any]:
    paths = output_paths(args.output_prefix)
    candidates = [row for row in jsonl_rows(paths["precheck_audit"]) if row.get("status") == "candidate"]
    decisions_a = load_classifier_decisions(paths["classifier_a"])
    decisions_b = load_classifier_decisions(paths["classifier_b"])
    included = merge_classifier_decisions(candidates, decisions_a, decisions_b)
    included_by_id = {str(row["retailerProductId"]): row for row in included}

    audit_rows = []
    counts: dict[str, int] = {}
    for row in jsonl_rows(paths["precheck_audit"]):
        retailer_product_id = str(row["retailerProductId"])
        pass_a = decisions_a.get(retailer_product_id)
        pass_b = decisions_b.get(retailer_product_id)
        final_status = row["status"]
        if row["status"] == "candidate":
            if retailer_product_id in included_by_id:
                final_status = "include"
            elif pass_a is None or pass_b is None:
                final_status = "pending_classifier"
            elif decision_is_include(pass_a) != decision_is_include(pass_b):
                final_status = "classifier_disagreement"
            else:
                final_status = "exclude_or_ambiguous_by_consensus"
        counts[final_status] = counts.get(final_status, 0) + 1
        audit_rows.append({**row, "finalStatus": final_status, "classifierPassA": pass_a, "classifierPassB": pass_b})

    paths["audit_jsonl"].write_text("", encoding="utf-8")
    append_jsonl(paths["audit_jsonl"], audit_rows)
    paths["urls"].write_text("\n".join(row["url"] for row in included) + ("\n" if included else ""), encoding="utf-8")

    with paths["audit_csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "retailerProductId",
                "url",
                "name",
                "brand",
                "finalStatus",
                "evidence",
                "passA",
                "passB",
                "categoryPaths",
            ],
        )
        writer.writeheader()
        for row in audit_rows:
            writer.writerow(
                {
                    "retailerProductId": row["retailerProductId"],
                    "url": row["url"],
                    "name": row["name"],
                    "brand": row["brand"],
                    "finalStatus": row["finalStatus"],
                    "evidence": json.dumps(row.get("evidence") or [], ensure_ascii=True),
                    "passA": json.dumps(row.get("classifierPassA"), ensure_ascii=True),
                    "passB": json.dumps(row.get("classifierPassB"), ensure_ascii=True),
                    "categoryPaths": " | ".join(row.get("categoryPaths") or []),
                }
            )
    return {"finalCounts": counts, "includedCount": len(included)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["enumerate", "fetch-bop", "precheck", "classify-a", "classify-b", "finalize", "run"],
        help="Pipeline stage to run.",
    )
    parser.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--manufacturer-urls", default=DEFAULT_MANUFACTURER_URLS)
    parser.add_argument("--cookies-db", default=str(Path.home() / ".mozilla/firefox/<firefox-profile>/cookies.sqlite"))
    parser.add_argument("--category-limit", type=int, default=0)
    parser.add_argument("--product-limit", type=int, default=0)
    parser.add_argument("--category-batch-size", type=int, default=20)
    parser.add_argument("--category-batch-concurrency", type=int, default=10)
    parser.add_argument("--bop-batch-size", type=int, default=200)
    parser.add_argument("--bop-batch-concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--include-manufacturer-vegan",
        action="store_true",
        help="Fetch BOP raw data even for vegan-according-to-manufacturer vegan-according-to-manufacturer products.",
    )
    parser.add_argument("--include-old-known", action="store_true", dest="include_manufacturer_vegan", help=argparse.SUPPRESS)
    parser.add_argument("--classify", action="store_true", help="For command=run, also call OpenAI classifier passes and finalize.")
    parser.add_argument("--classification-limit", type=int, default=0)
    parser.add_argument("--classification-batch-size", type=int, default=10)
    parser.add_argument("--classification-flush-interval", type=int, default=10)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    meta: dict[str, Any] = {
        "generatedAtEpoch": int(time.time()),
        "outputPrefix": args.output_prefix,
        "manufacturerUrls": args.manufacturer_urls,
    }

    if args.command in {"enumerate", "run"}:
        meta.update(enumerate_categories(args))
    if args.command in {"fetch-bop", "run"}:
        meta.update(fetch_bop(args))
    if args.command in {"precheck", "run"}:
        meta.update(extract_precheck(args))
    if args.command == "classify-a" or (args.command == "run" and args.classify):
        meta.update(classify_candidates(args, "a"))
    if args.command == "classify-b" or (args.command == "run" and args.classify):
        meta.update(classify_candidates(args, "b"))
    if args.command == "finalize" or (args.command == "run" and args.classify):
        meta.update(finalize_outputs(args))

    if args.command in {"run", "finalize"}:
        write_json(output_paths(args.output_prefix)["meta"], meta)
        print(f"Wrote {output_paths(args.output_prefix)['meta']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
