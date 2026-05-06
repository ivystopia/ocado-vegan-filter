#!/usr/bin/env python3
import argparse
import csv
import html
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"
SITEMAP_INDEX_URL = "https://www.ocado.com/sitemaps/sitemap_index.xml"
PRODUCT_BOP_URL = "https://www.ocado.com/api/webproductpagews/v5/products/bop"
PRODUCT_URL_RE = re.compile(r"/products/(?P<slug>[^/]+)/(?P<retailer_product_id>\d+)$")
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
THREAD_LOCAL = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cookies-db",
        default=str(Path.home() / ".mozilla/firefox/<firefox-profile>/cookies.sqlite"),
        help="Path to Firefox cookies.sqlite",
    )
    parser.add_argument(
        "--output-prefix",
        default="ocado_vegan_according_to_manufacturer_sitemap",
        help="Prefix for the generated JSON and CSV files",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Number of concurrent requests for product JSON",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of products processed; 0 means all",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Per-request timeout in seconds",
    )
    return parser.parse_args()


def build_cookie_jar(cookies_db: str) -> requests.cookies.RequestsCookieJar:
    conn = sqlite3.connect(cookies_db)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT host, name, value, path
            FROM moz_cookies
            WHERE host LIKE '%ocado.com%'
            """
        )
        jar = requests.cookies.RequestsCookieJar()
        for host, name, value, path in cursor.fetchall():
            jar.set(name, value, domain=host, path=path)
        return jar
    finally:
        conn.close()


def get_session(cookie_jar: requests.cookies.RequestsCookieJar) -> requests.Session:
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.cookies.update(cookie_jar)
        THREAD_LOCAL.session = session
    return session


def fetch_product_sitemap_urls(cookie_jar: requests.cookies.RequestsCookieJar, timeout: int) -> list[str]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    session.cookies.update(cookie_jar)
    index_response = session.get(SITEMAP_INDEX_URL, timeout=timeout)
    index_response.raise_for_status()
    root = ET.fromstring(index_response.text)
    sitemap_urls = [element.text for element in root.findall(".//sm:loc", SITEMAP_NS)]
    product_sitemap_urls = [url for url in sitemap_urls if "sitemap-products-" in url]
    if not product_sitemap_urls:
        raise RuntimeError("Could not find a product sitemap in sitemap_index.xml")

    product_urls: list[str] = []
    for sitemap_url in product_sitemap_urls:
        response = session.get(sitemap_url, timeout=max(timeout, 60))
        response.raise_for_status()
        sitemap_root = ET.fromstring(response.text)
        product_urls.extend(element.text for element in sitemap_root.findall(".//sm:loc", SITEMAP_NS))
    return product_urls


def normalize_text(raw_text: str) -> str:
    text = html.unescape(raw_text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


SUITABLE_FOR_VEGAN_PATTERNS = [
    re.compile(r"\bsuitable\s+for\s+vegans?\b", re.IGNORECASE),
    re.compile(
        r"\bsuitable\s+for\s+vegetarians?\s*(?:and|&)\s*vegans?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsuitable\s+for\s+vegans?\s*(?:and|&)\s*vegetarians?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bsuitable\s+for\s+(?:a\s+)?vegan\s+diet\b", re.IGNORECASE),
    re.compile(r"\bsuitable\s+for\s+vegan\s+diets\b", re.IGNORECASE),
]
DIETARY_SHORT_VEGAN_RE = re.compile(
    r"^(?:suitable\s+for\s+)?(?:vegetarians?\s+and\s+)?vegans?$",
    re.IGNORECASE,
)
NEGATIVE_VEGAN_RE = re.compile(r"\bnot\s+suitable\s+for\b.{0,40}\bvegans?\b", re.IGNORECASE)


def field_explicitly_says_vegan(field_title: str, normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if NEGATIVE_VEGAN_RE.search(normalized_text):
        return False
    if any(pattern.search(normalized_text) for pattern in SUITABLE_FOR_VEGAN_PATTERNS):
        return True
    if field_title == "dietaryInformation" and DIETARY_SHORT_VEGAN_RE.fullmatch(normalized_text):
        return True
    return False


def extract_explicit_matches(bop_data: dict) -> list[dict]:
    matches: list[dict] = []
    for field in bop_data.get("fields", []):
        title = field.get("title", "")
        content = normalize_text(field.get("content", ""))
        if field_explicitly_says_vegan(title, content):
            matches.append({"title": title, "content": content})

    detailed_description = normalize_text(bop_data.get("detailedDescription", ""))
    if field_explicitly_says_vegan("detailedDescription", detailed_description):
        matches.append({"title": "detailedDescription", "content": detailed_description})

    return matches


def product_has_vegan_tag(product: dict) -> bool:
    for attribute in product.get("iconAttributes", []) or []:
        label = (attribute.get("label") or "").strip().lower()
        file_name = (attribute.get("file") or "").strip().lower()
        if label == "vegan" or file_name == "vegan":
            return True
    return False


@dataclass(frozen=True)
class ProductRef:
    retailer_product_id: str
    url: str


def parse_product_refs(product_urls: Iterable[str]) -> list[ProductRef]:
    refs: list[ProductRef] = []
    for url in product_urls:
        match = PRODUCT_URL_RE.search(url)
        if not match:
            continue
        refs.append(ProductRef(retailer_product_id=match.group("retailer_product_id"), url=url))
    return refs


def fetch_product_bop(
    product_ref: ProductRef,
    cookie_jar: requests.cookies.RequestsCookieJar,
    timeout: int,
    retries: int = 3,
) -> tuple[ProductRef, dict | None, str | None]:
    session = get_session(cookie_jar)
    params = {"retailerProductId": product_ref.retailer_product_id}
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                PRODUCT_BOP_URL,
                params=params,
                headers={"Accept": "application/json, text/plain, */*"},
                timeout=timeout,
            )
            if response.status_code == 404:
                return product_ref, None, None
            response.raise_for_status()
            return product_ref, response.json(), None
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(0.5 * attempt)
    return product_ref, None, last_error


def main() -> int:
    args = parse_args()
    start = time.time()
    cookie_jar = build_cookie_jar(args.cookies_db)
    product_urls = fetch_product_sitemap_urls(cookie_jar, args.timeout)
    refs = parse_product_refs(product_urls)
    if args.limit:
        refs = refs[: args.limit]

    print(f"Loaded {len(refs)} product refs from sitemap")
    results: list[dict] = []
    errors: list[dict] = []
    processed = 0
    matched = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(fetch_product_bop, ref, cookie_jar, args.timeout): ref for ref in refs
        }
        for future in as_completed(futures):
            processed += 1
            ref, payload, error = future.result()
            if error:
                errors.append(
                    {
                        "retailerProductId": ref.retailer_product_id,
                        "url": ref.url,
                        "error": error,
                    }
                )
            elif payload:
                product = payload.get("product", {})
                bop_data = payload.get("bopData", {})
                matches = extract_explicit_matches(bop_data)
                if matches and not product_has_vegan_tag(product):
                    matched += 1
                    results.append(
                        {
                            "retailerProductId": ref.retailer_product_id,
                            "url": ref.url,
                            "name": product.get("name"),
                            "brand": product.get("brand"),
                            "categoryPath": product.get("categoryPath") or [],
                            "iconAttributes": product.get("iconAttributes") or [],
                            "matchedFields": matches,
                        }
                    )

            if processed % 500 == 0 or processed == len(refs):
                elapsed = time.time() - start
                rate = processed / elapsed if elapsed else 0
                print(
                    f"Processed {processed}/{len(refs)} products | "
                    f"matches={matched} | errors={len(errors)} | "
                    f"rate={rate:.1f}/s"
                )

    results.sort(key=lambda item: (item["brand"] or "", item["name"] or "", item["retailerProductId"]))

    output_prefix = Path(args.output_prefix)
    json_path = output_prefix.with_suffix(".json")
    csv_path = output_prefix.with_suffix(".csv")
    meta_path = output_prefix.with_name(f"{output_prefix.name}_meta.json")

    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=True), encoding="utf-8")
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
            ],
        )
        writer.writeheader()
        for item in results:
            writer.writerow(
                {
                    "retailerProductId": item["retailerProductId"],
                    "name": item["name"],
                    "brand": item["brand"],
                    "url": item["url"],
                    "matchedFieldTitles": " | ".join(match["title"] for match in item["matchedFields"]),
                    "matchedSnippets": " | ".join(match["content"] for match in item["matchedFields"]),
                }
            )

    meta = {
        "generatedAtEpoch": int(time.time()),
        "productCount": len(refs),
        "matchCount": len(results),
        "errorCount": len(errors),
        "errors": errors[:500],
        "workers": args.workers,
        "timeoutSeconds": args.timeout,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
