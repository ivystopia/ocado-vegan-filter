#!/usr/bin/env python3
"""Build a local SQLite product database from Ocado scrape outputs."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


DEFAULT_OUTPUT = "ocado_products.sqlite"
DEFAULT_PRODUCT_UNIVERSE = "ocado_vegan_according_to_ingredients_product_universe.jsonl"
DEFAULT_BOP_RAW = "ocado_vegan_according_to_ingredients_bop_raw.jsonl"
DEFAULT_PRECHECK_AUDIT = "ocado_vegan_according_to_ingredients_precheck_audit.jsonl"
DEFAULT_MANUFACTURER_AUDIT = "ocado_vegan_according_to_manufacturer_audit.json"
DEFAULT_INGREDIENTS_AUDIT = "ocado_vegan_according_to_ingredients_audit.jsonl"
DEFAULT_CLASSIFIER_A = "ocado_vegan_according_to_ingredients_classifier_pass_a.jsonl"
DEFAULT_CLASSIFIER_B = "ocado_vegan_according_to_ingredients_classifier_pass_b.jsonl"
DEFAULT_MANUFACTURER_META = "ocado_vegan_according_to_manufacturer_meta.json"
DEFAULT_INGREDIENTS_META = "ocado_vegan_according_to_ingredients_meta.json"

PRODUCT_URL_ID_RE = re.compile(r"/products/(?:[^/?#]*[-/])?(?P<id>\d+)(?:/details)?(?:[/?#]|$)")
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
P_BREAK_RE = re.compile(r"</\s*(?:p|div|li|tr)\s*>", re.IGNORECASE)
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
NUTRITION_VALUE_RE = re.compile(
    r"(?P<prefix><)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>kcal|kj|mcg|ug|µg|mg|g|ml|%)",
    re.IGNORECASE,
)
NUTRIENT_UNIT_SUFFIX_RE = re.compile(r"\s*\(?\s*(kj|kcal|mcg|ug|µg|mg|g|ml)\s*\)?\s*$", re.IGNORECASE)

FIELD_COLUMN_OVERRIDES = {
    "alcoholbyvolume": "alcohol_by_volume",
    "beerdegree": "beer_degree",
    "brand": None,
    "brandmarketing": "brand_marketing",
    "cookingguidelines": "cooking_guidelines",
    "countryoforigin": "country_of_origin",
    "countryofpackaging": "country_of_packaging",
    "currentvintage": "current_vintage",
    "detaileddescription": "detailed_description",
    "dietaryinformation": "dietary_information",
    "furtherdescription": "further_description",
    "grapevariety": "grape_variety",
    "manufacturer": "manufacturer",
    "manufacturermarketing": "manufacturer_marketing",
    "nutritionaldata": "nutrition_plain_text",
    "otherinformation": "other_information",
    "packagetype": "package_type",
    "preparationandusage": "preparation_and_usage",
    "recyclinginformation": "recycling_information",
    "regionalinformation": "regional_information",
    "returntoaddress": "return_to_address",
    "servingsuggestions": "serving_suggestions",
    "tastecategory": "taste_category",
    "tastingnotes": "tasting_notes",
    "vinificationdetails": "vinification_details",
}

KNOWN_TEXT_COLUMNS = [
    "agent",
    "alcohol_by_volume",
    "allergens",
    "beer_degree",
    "brand_marketing",
    "cooking_guidelines",
    "country_of_origin",
    "country_of_packaging",
    "current_vintage",
    "detailed_description",
    "dietary_information",
    "features",
    "further_description",
    "grape_variety",
    "history",
    "ingredients",
    "manufacturer",
    "manufacturer_marketing",
    "nutrition_plain_text",
    "other_information",
    "package_type",
    "preparation_and_usage",
    "producer",
    "recipes",
    "recycling_information",
    "regional_information",
    "return_to_address",
    "serving_suggestions",
    "specification",
    "storage",
    "taste_category",
    "tasting_notes",
    "units",
    "vinification_details",
    "winemaker",
]

PRODUCT_COLUMNS = [
    "id",
    "ocado_product_uuid",
    "name",
    "brand",
    "url",
    "type",
    "pack_size",
    "alcohol",
    "is_new",
    "is_in_current_catalog",
    "medical_questionnaire_required",
    "time_restricted",
    "age_restriction_years",
    "price_gbp",
    "promo_price_gbp",
    "unit_price_gbp",
    "unit_price_unit",
    "promo_unit_price_gbp",
    "promo_unit_price_unit",
    "rating_count",
    "rating_overall",
    "guaranteed_product_life_quantity",
    "guaranteed_product_life_unit",
    "hfss_display_restriction_group",
    "quantity_restriction_group_json",
    "catchweight_json",
    "tax_codes_json",
    "retailer_financing_plan_ids_json",
    "promotions_json",
    "seen_in_category_pages",
    "has_full_product_detail",
    "official_vegan",
    "name_contains_vegan",
    "vegan_status",
    "vegan_reason",
    "vegan_classifier_version",
    "vegan_classified_at_epoch",
    "ingredients_precheck_status",
    "ingredients_precheck_reason",
    "latest_source_epoch",
    *KNOWN_TEXT_COLUMNS,
]


@dataclass(frozen=True)
class BuildConfig:
    output: Path
    product_universe: Path
    bop_raw: Path
    precheck_audit: Path
    manufacturer_audit: Path
    ingredients_audit: Path
    classifier_a: Path
    classifier_b: Path
    manufacturer_meta: Path
    ingredients_meta: Path


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None
        self.in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self.current_row = []
        elif tag.lower() in {"td", "th"} and self.current_row is not None:
            self.current_cell = []
            self.in_cell = True
        elif tag.lower() == "br" and self.in_cell and self.current_cell is not None:
            self.current_cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self.current_row is not None and self.current_cell is not None:
            self.current_row.append(normalize_space("".join(self.current_cell)))
            self.current_cell = None
            self.in_cell = False
        elif tag.lower() == "tr" and self.current_row is not None:
            if any(cell for cell in self.current_row):
                self.rows.append(self.current_row)
            self.current_row = None


def normalize_space(value: str | None) -> str:
    text = re.sub(r"\s+", " ", html.unescape(value or "")).strip()
    text = re.sub(r"\s+([,.;:%\]\)])", r"\1", text)
    text = re.sub(r"([\[\(])\s+", r"\1", text)
    return text


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = BR_RE.sub(" ", value)
    text = P_BREAK_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    return normalize_space(text)


def table_to_plain_text(value: str | None) -> str:
    rows = parse_html_table(value)
    if not rows:
        return html_to_text(value)
    return "; ".join(" | ".join(cell for cell in row if cell) for row in rows)


def snake_case(value: str) -> str:
    value = html_to_text(value).lower()
    value = value.replace("&", " and ")
    value = NON_ALNUM_RE.sub("_", value).strip("_")
    return value or "unknown"


def field_key(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", html_to_text(title).lower())


def field_column(title: str | None) -> str | None:
    key = field_key(title)
    if key in FIELD_COLUMN_OVERRIDES:
        return FIELD_COLUMN_OVERRIDES[key]
    return snake_case(title or "") if key else None


def product_id_from_url(url: str | None) -> str | None:
    match = PRODUCT_URL_ID_RE.search(url or "")
    return match.group("id") if match else None


def product_url(product_id: str) -> str:
    return f"https://www.ocado.com/products/{product_id}/details"


def json_dumps(value: Any) -> str | None:
    if value in (None, [], {}):
        return None
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


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


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_epoch(meta_path: Path) -> int:
    meta = read_json(meta_path, {})
    return int(meta.get("generatedAtEpoch") or path_mtime_epoch(meta_path) or 0)


def path_mtime_epoch(path: Path) -> int:
    return int(path.stat().st_mtime) if path.exists() else 0


def parse_html_table(value: str | None) -> list[list[str]]:
    if not value:
        return []
    parser = TableParser()
    parser.feed(value)
    parser.close()
    return parser.rows


def normalize_basis_label(header: str) -> str:
    value = html_to_text(header).lower()
    value = re.sub(r"\b%?\s*ri\*?.*$", "", value)
    value = re.sub(r"\bnrv\*?.*$", "", value)
    value = re.sub(r"\breference\s+intake.*$", "", value)
    value = value.replace(":", " ")
    value = re.sub(r"^/\s*", "per ", value)
    value = re.sub(r"[*^]+", "", value)
    value = normalize_space(value)
    if value.startswith("per "):
        return value
    if re.search(r"\b(?:100\s*g|100g|\d+\s*g|\d+g|serving|portion|slice|wrap|pack|bottle|can|ml)\b", value):
        return normalize_space(f"per {value}")
    return ""


def is_non_product_nutrition_header(header: str) -> bool:
    normalized = html_to_text(header).lower()
    if normalize_basis_label(header):
        return False
    return any(token in normalized for token in ["%ri", "% ri", "nrv", "rde", "reference intake", "adult"])


def normalize_nutrient_label(label: str) -> tuple[str, str | None]:
    value = html_to_text(label).lower()
    unit_match = NUTRIENT_UNIT_SUFFIX_RE.search(value)
    implied_unit = unit_match.group(1).lower().replace("µ", "u") if unit_match else None
    if unit_match:
        value = value[: unit_match.start()]
    value = re.sub(r"^of\s+which:?\s+", "", value)
    value = re.sub(r"^of\s+which\s+", "", value)
    value = value.replace("-", " ")
    value = re.sub(r"[*^]+", "", value)
    value = normalize_space(value)
    if not value:
        return "", implied_unit
    if any(token in value for token in ["reference intake", "serving size", "this pack contains", "contains portions"]):
        return "", implied_unit
    return snake_case(value), implied_unit


def parse_nutrition_values(value: str, implied_unit: str | None = None) -> list[dict[str, Any]]:
    text = html_to_text(value)
    if not text:
        return []
    results = []
    for match in NUTRITION_VALUE_RE.finditer(text):
        unit = match.group("unit").lower().replace("µ", "u")
        if unit == "%":
            continue
        results.append(
            {
                "amount_value": float(match.group("value")),
                "unit": unit,
                "original_value": text,
            }
        )
    if results:
        return results
    if implied_unit:
        number_match = re.search(r"(?P<value>\d+(?:\.\d+)?)", text)
        if number_match:
            return [
                {
                    "amount_value": float(number_match.group("value")),
                    "unit": implied_unit,
                    "original_value": text,
                }
            ]
    if re.search(r"[a-z0-9]", text, re.IGNORECASE):
        return [{"amount_value": None, "unit": None, "original_value": text}]
    return []


def parse_nutrition_rows(product_id: str, html_content: str | None) -> list[tuple[Any, ...]]:
    rows = parse_html_table(html_content)
    if len(rows) < 2:
        return []
    headers = rows[0]
    product_columns = []
    for index, header in enumerate(headers[1:], start=1):
        if is_non_product_nutrition_header(header):
            continue
        basis = normalize_basis_label(header)
        if basis:
            product_columns.append((index, basis))

    parsed = []
    current_nutrient = ""
    current_implied_unit: str | None = None
    for row in rows[1:]:
        if not row:
            continue
        padded = row + [""] * max(0, len(headers) - len(row))
        nutrient, implied_unit = normalize_nutrient_label(padded[0])
        if nutrient:
            current_nutrient = nutrient
            current_implied_unit = implied_unit
        elif not current_nutrient:
            continue

        for index, basis in product_columns:
            if index >= len(padded):
                continue
            values = parse_nutrition_values(padded[index], current_implied_unit)
            for value in values:
                parsed.append(
                    (
                        product_id,
                        current_nutrient,
                        basis,
                        value["amount_value"],
                        value["unit"],
                        value["original_value"],
                    )
                )
    return parsed


def money_to_gbp(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("amount")
    currency = value.get("currency")
    if amount in (None, "") or currency not in {"GBP", "GBX"}:
        return None
    try:
        numeric = float(amount)
    except (TypeError, ValueError):
        return None
    if currency == "GBX":
        numeric /= 100
    return numeric


def unit_price_to_parts(value: Any) -> tuple[float | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    return money_to_gbp(value.get("price")), normalize_space(value.get("unit")) or None


def rating_parts(value: Any) -> tuple[int | None, float | None]:
    if not isinstance(value, dict):
        return None, None
    count = value.get("count")
    rating = value.get("overallRating")
    try:
        count_value = int(count) if count not in (None, "") else None
    except (TypeError, ValueError):
        count_value = None
    try:
        rating_value = float(rating) if rating not in (None, "") else None
    except (TypeError, ValueError):
        rating_value = None
    return count_value, rating_value


def life_parts(value: Any) -> tuple[float | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    quantity = value.get("quantity")
    try:
        quantity_value = float(quantity) if quantity not in (None, "") else None
    except (TypeError, ValueError):
        quantity_value = None
    return quantity_value, normalize_space(value.get("unit")) or None


def bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def attribute_flags(attributes: Iterable[dict[str, Any]] | None) -> set[str]:
    flags: set[str] = set()
    for attribute in attributes or []:
        raw = normalize_space(attribute.get("label")) or normalize_space(attribute.get("file")) or normalize_space(attribute.get("icon"))
        if not raw:
            continue
        flags.add(snake_case(raw))
    return flags


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not normalize_space(value)
    return False


def merge_promotions(product: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    promotions = []
    seen = set()
    for source in [product.get("promotions") or [], data.get("bopPromotions") or []]:
        for promotion in source:
            if not isinstance(promotion, dict):
                continue
            key = promotion.get("retailerPromotionId") or promotion.get("promoId") or json_dumps(promotion)
            if key in seen:
                continue
            seen.add(key)
            promotions.append(promotion)
    return promotions


class ProductAccumulator:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.epochs: dict[str, dict[str, int]] = {}
        self.categories: set[tuple[str, str]] = set()
        self.breadcrumbs: list[tuple[str, int, str | None, str | None]] = []
        self.flags: set[tuple[str, str]] = set()
        self.nutrition_rows: list[tuple[Any, ...]] = []
        self.manufacturer_evidence: list[tuple[str, str, str]] = []
        self.precheck_rows: list[tuple[str, str | None, str | None, str | None, int | None]] = []
        self.classifier_rows: list[tuple[str, str, str | None, int | None, str | None, str | None]] = []
        self.import_warnings: list[tuple[str, str | None, str]] = []

    def ensure(self, product_id: str) -> dict[str, Any]:
        row = self.rows.setdefault(
            product_id,
            {
                "id": product_id,
                "url": product_url(product_id),
                "official_vegan": 0,
                "name_contains_vegan": 0,
                "has_full_product_detail": 0,
            },
        )
        self.epochs.setdefault(product_id, {})
        return row

    def set_value(self, product_id: str, column: str, value: Any, epoch: int, *, allow_blank: bool = False) -> None:
        if column not in PRODUCT_COLUMNS:
            self.import_warnings.append(("unknown_column", product_id, column))
            return
        if not allow_blank and is_blank(value):
            return
        self.ensure(product_id)
        current_epoch = self.epochs[product_id].get(column, -1)
        if epoch >= current_epoch:
            self.rows[product_id][column] = value
            self.epochs[product_id][column] = epoch
            if column != "latest_source_epoch":
                latest = self.rows[product_id].get("latest_source_epoch")
                if latest is None or epoch > latest:
                    self.rows[product_id]["latest_source_epoch"] = epoch

    def add_categories(self, product_id: str, categories: Iterable[str]) -> None:
        self.ensure(product_id)
        for category in categories:
            normalized = normalize_space(category)
            if normalized:
                self.categories.add((product_id, normalized))

    def add_flags(self, product_id: str, flags: Iterable[str], epoch: int) -> None:
        self.ensure(product_id)
        flag_set = set(flags)
        for flag in flag_set:
            if flag:
                self.flags.add((product_id, flag))
        self.set_value(product_id, "official_vegan", 1 if "vegan" in flag_set else 0, epoch, allow_blank=True)

    def update_name_contains_vegan(self, product_id: str, epoch: int) -> None:
        name = self.rows.get(product_id, {}).get("name")
        if isinstance(name, str):
            self.set_value(product_id, "name_contains_vegan", 1 if re.search(r"\bvegan\b", name, re.IGNORECASE) else 0, epoch, allow_blank=True)


def product_id_from_record(record: dict[str, Any]) -> str | None:
    value = record.get("retailerProductId") or record.get("id") or record.get("retailer_product_id")
    if value:
        return str(value)
    return product_id_from_url(str(record.get("url") or ""))


def import_manufacturer_audit(acc: ProductAccumulator, path: Path, epoch: int) -> None:
    for row in read_json(path, []):
        product_id = product_id_from_record(row)
        if not product_id:
            continue
        acc.set_value(product_id, "name", html_to_text(row.get("name")), epoch)
        acc.set_value(product_id, "brand", html_to_text(row.get("brand")), epoch)
        acc.set_value(product_id, "url", row.get("url") or product_url(product_id), epoch)
        acc.add_categories(product_id, row.get("categoryPaths") or [])
        acc.add_flags(product_id, attribute_flags(row.get("iconAttributes")), epoch)
        for match in row.get("matchedFields") or []:
            title = html_to_text(match.get("title"))
            content = html_to_text(match.get("content"))
            column = field_column(title)
            if column:
                acc.set_value(product_id, column, content, epoch)
            if title or content:
                acc.manufacturer_evidence.append((product_id, title, content))
        acc.update_name_contains_vegan(product_id, epoch)


def import_product_universe(acc: ProductAccumulator, path: Path, epoch: int) -> None:
    for row in jsonl_rows(path):
        product_id = product_id_from_record(row)
        if not product_id:
            continue
        acc.set_value(product_id, "name", html_to_text(row.get("name")), epoch)
        acc.set_value(product_id, "brand", html_to_text(row.get("brand")), epoch)
        acc.set_value(product_id, "url", row.get("url") or product_url(product_id), epoch)
        acc.set_value(product_id, "seen_in_category_pages", row.get("seenInCategoryPages"), epoch)
        acc.add_categories(product_id, row.get("categoryPaths") or [])
        acc.add_flags(product_id, attribute_flags(row.get("iconAttributes")), epoch)
        acc.update_name_contains_vegan(product_id, epoch)


def import_bop_raw(acc: ProductAccumulator, path: Path) -> None:
    for row in jsonl_rows(path):
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        product = data.get("product") if isinstance(data.get("product"), dict) else {}
        product_id = str(row.get("retailerProductId") or product.get("retailerProductId") or "")
        if not product_id or row.get("status") != 200 or not product:
            continue
        epoch = int(row.get("fetchedAtEpoch") or path_mtime_epoch(path))
        acc.set_value(product_id, "has_full_product_detail", 1, epoch, allow_blank=True)
        acc.set_value(product_id, "ocado_product_uuid", product.get("productId"), epoch)
        acc.set_value(product_id, "name", html_to_text(product.get("name")), epoch)
        acc.set_value(product_id, "brand", html_to_text(product.get("brand")), epoch)
        acc.set_value(product_id, "url", product_url(product_id), epoch)
        acc.set_value(product_id, "type", product.get("type"), epoch)
        acc.set_value(product_id, "pack_size", product.get("packSizeDescription"), epoch)
        acc.set_value(product_id, "alcohol", bool_int(product.get("alcohol")), epoch, allow_blank=True)
        acc.set_value(product_id, "is_new", bool_int(product.get("isNew")), epoch, allow_blank=True)
        acc.set_value(product_id, "is_in_current_catalog", bool_int(product.get("isInCurrentCatalog")), epoch, allow_blank=True)
        acc.set_value(product_id, "medical_questionnaire_required", bool_int(product.get("medicalQuestionnaireRequired")), epoch, allow_blank=True)
        acc.set_value(product_id, "time_restricted", bool_int(product.get("timeRestricted")), epoch, allow_blank=True)
        acc.set_value(product_id, "age_restriction_years", product.get("ageRestriction"), epoch)
        acc.set_value(product_id, "price_gbp", money_to_gbp(product.get("price")), epoch)
        acc.set_value(product_id, "promo_price_gbp", money_to_gbp(product.get("promoPrice")), epoch)
        unit_price_gbp, unit_price_unit = unit_price_to_parts(product.get("unitPrice"))
        acc.set_value(product_id, "unit_price_gbp", unit_price_gbp, epoch)
        acc.set_value(product_id, "unit_price_unit", unit_price_unit, epoch)
        promo_unit_price_gbp, promo_unit_price_unit = unit_price_to_parts(product.get("promoUnitPrice"))
        acc.set_value(product_id, "promo_unit_price_gbp", promo_unit_price_gbp, epoch)
        acc.set_value(product_id, "promo_unit_price_unit", promo_unit_price_unit, epoch)
        rating_count, rating_overall = rating_parts(product.get("ratingSummary"))
        acc.set_value(product_id, "rating_count", rating_count, epoch)
        acc.set_value(product_id, "rating_overall", rating_overall, epoch)
        life_quantity, life_unit = life_parts(product.get("guaranteedProductLife"))
        acc.set_value(product_id, "guaranteed_product_life_quantity", life_quantity, epoch)
        acc.set_value(product_id, "guaranteed_product_life_unit", life_unit, epoch)
        acc.set_value(product_id, "hfss_display_restriction_group", product.get("hfssDisplayRestrictionGroup"), epoch)
        acc.set_value(product_id, "quantity_restriction_group_json", json_dumps(product.get("quantityRestrictionGroup")), epoch)
        acc.set_value(product_id, "catchweight_json", json_dumps(product.get("catchweight")), epoch)
        acc.set_value(product_id, "tax_codes_json", json_dumps(product.get("taxCodesDisplayNames")), epoch)
        acc.set_value(product_id, "retailer_financing_plan_ids_json", json_dumps(product.get("retailerFinancingPlanIds")), epoch)
        acc.set_value(product_id, "promotions_json", json_dumps(merge_promotions(product, data)), epoch)
        acc.add_flags(product_id, attribute_flags(product.get("iconAttributes")), epoch)

        bop = data.get("bopData") if isinstance(data.get("bopData"), dict) else {}
        detailed_description = html_to_text(bop.get("detailedDescription"))
        acc.set_value(product_id, "detailed_description", detailed_description, epoch)
        for position, breadcrumb in enumerate(bop.get("breadcrumbs") or []):
            if isinstance(breadcrumb, dict):
                acc.breadcrumbs.append(
                    (
                        product_id,
                        position,
                        breadcrumb.get("categoryId"),
                        html_to_text(breadcrumb.get("categoryName")),
                    )
                )
        for field in bop.get("fields") or []:
            title = field.get("title")
            column = field_column(title)
            if not column:
                continue
            content = field.get("content")
            text = table_to_plain_text(content) if column == "nutrition_plain_text" else html_to_text(content)
            if column not in PRODUCT_COLUMNS:
                acc.import_warnings.append(("unknown_text_field", product_id, html_to_text(title)))
                continue
            acc.set_value(product_id, column, text, epoch)
            if column == "nutrition_plain_text":
                acc.nutrition_rows.extend(parse_nutrition_rows(product_id, content))
        acc.update_name_contains_vegan(product_id, epoch)


def import_precheck(acc: ProductAccumulator, path: Path) -> None:
    for row in jsonl_rows(path):
        product_id = product_id_from_record(row)
        if not product_id:
            continue
        epoch = row.get("precheckedAtEpoch")
        epoch_int = int(epoch) if epoch else None
        status = row.get("status")
        reason = row.get("reason")
        evidence_json = json_dumps(row.get("evidence"))
        acc.precheck_rows.append((product_id, status, reason, evidence_json, epoch_int))
        if epoch_int:
            acc.set_value(product_id, "ingredients_precheck_status", status, epoch_int)
            acc.set_value(product_id, "ingredients_precheck_reason", reason, epoch_int)


def import_ingredients_audit(acc: ProductAccumulator, path: Path) -> None:
    if not path.exists():
        return
    for row in jsonl_rows(path):
        product_id = product_id_from_record(row)
        if not product_id:
            continue
        acc.ensure(product_id)


def import_classifier_pass(acc: ProductAccumulator, path: Path, pass_id: str) -> None:
    if not path.exists():
        return
    for row in jsonl_rows(path):
        product_id = str(row.get("retailerProductId") or "")
        if not product_id:
            continue
        acc.classifier_rows.append(
            (
                product_id,
                pass_id,
                row.get("model"),
                row.get("classifiedAtEpoch"),
                json_dumps(row.get("decision")),
                row.get("error"),
            )
        )


def product_column_sql(column: str) -> str:
    if column == "id":
        return "id TEXT PRIMARY KEY"
    if column in {
        "alcohol",
        "is_new",
        "is_in_current_catalog",
        "medical_questionnaire_required",
        "time_restricted",
        "age_restriction_years",
        "rating_count",
        "seen_in_category_pages",
        "has_full_product_detail",
        "official_vegan",
        "name_contains_vegan",
        "vegan_classified_at_epoch",
        "latest_source_epoch",
    }:
        return f"{column} INTEGER"
    if column in {
        "price_gbp",
        "promo_price_gbp",
        "unit_price_gbp",
        "promo_unit_price_gbp",
        "rating_overall",
        "guaranteed_product_life_quantity",
    }:
        return f"{column} REAL"
    return f"{column} TEXT"


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS product_categories;
        DROP TABLE IF EXISTS product_breadcrumbs;
        DROP TABLE IF EXISTS product_flags;
        DROP TABLE IF EXISTS product_nutrition;
        DROP TABLE IF EXISTS manufacturer_vegan_evidence;
        DROP TABLE IF EXISTS precheck_results;
        DROP TABLE IF EXISTS classifier_decisions;
        DROP TABLE IF EXISTS import_metadata;
        DROP TABLE IF EXISTS import_sources;
        DROP TABLE IF EXISTS import_warnings;
        DROP TABLE IF EXISTS products_fts;

        CREATE TABLE products (
          {", ".join(product_column_sql(column) for column in PRODUCT_COLUMNS)}
        );

        CREATE TABLE product_categories (
          product_id TEXT NOT NULL,
          category_path TEXT NOT NULL,
          PRIMARY KEY (product_id, category_path)
        );

        CREATE TABLE product_breadcrumbs (
          product_id TEXT NOT NULL,
          position INTEGER NOT NULL,
          category_id TEXT,
          category_name TEXT,
          PRIMARY KEY (product_id, position)
        );

        CREATE TABLE product_flags (
          product_id TEXT NOT NULL,
          flag TEXT NOT NULL,
          PRIMARY KEY (product_id, flag)
        );

        CREATE TABLE product_nutrition (
          product_id TEXT NOT NULL,
          nutrient TEXT NOT NULL,
          basis_label TEXT NOT NULL,
          amount_value REAL,
          unit TEXT,
          original_value TEXT
        );

        CREATE TABLE manufacturer_vegan_evidence (
          product_id TEXT NOT NULL,
          field_title TEXT,
          content TEXT
        );

        CREATE TABLE precheck_results (
          product_id TEXT PRIMARY KEY,
          status TEXT,
          reason TEXT,
          evidence_json TEXT,
          prechecked_at_epoch INTEGER
        );

        CREATE TABLE classifier_decisions (
          product_id TEXT NOT NULL,
          pass_id TEXT NOT NULL,
          model TEXT,
          classified_at_epoch INTEGER,
          decision_json TEXT,
          error TEXT,
          PRIMARY KEY (product_id, pass_id)
        );

        CREATE TABLE import_metadata (
          key TEXT PRIMARY KEY,
          value TEXT
        );

        CREATE TABLE import_sources (
          path TEXT PRIMARY KEY,
          exists_on_import INTEGER NOT NULL,
          size_bytes INTEGER,
          mtime_epoch INTEGER,
          sha256 TEXT
        );

        CREATE TABLE import_warnings (
          warning_type TEXT NOT NULL,
          product_id TEXT,
          message TEXT
        );

        CREATE VIRTUAL TABLE products_fts USING fts5(
          id UNINDEXED,
          name,
          brand,
          ingredients,
          allergens,
          dietary_information,
          features,
          detailed_description,
          nutrition_plain_text
        );
        """
    )


def insert_rows(conn: sqlite3.Connection, acc: ProductAccumulator, config: BuildConfig, started_at: int) -> None:
    product_rows = []
    for product_id in sorted(acc.rows):
        row = acc.rows[product_id]
        product_rows.append(tuple(row.get(column) for column in PRODUCT_COLUMNS))

    placeholders = ", ".join("?" for _ in PRODUCT_COLUMNS)
    conn.executemany(
        f"INSERT INTO products ({', '.join(PRODUCT_COLUMNS)}) VALUES ({placeholders})",
        product_rows,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO product_categories (product_id, category_path) VALUES (?, ?)",
        sorted(acc.categories),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO product_breadcrumbs (product_id, position, category_id, category_name) VALUES (?, ?, ?, ?)",
        acc.breadcrumbs,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO product_flags (product_id, flag) VALUES (?, ?)",
        sorted(acc.flags),
    )
    conn.executemany(
        "INSERT INTO product_nutrition (product_id, nutrient, basis_label, amount_value, unit, original_value) VALUES (?, ?, ?, ?, ?, ?)",
        acc.nutrition_rows,
    )
    conn.executemany(
        "INSERT INTO manufacturer_vegan_evidence (product_id, field_title, content) VALUES (?, ?, ?)",
        acc.manufacturer_evidence,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO precheck_results (product_id, status, reason, evidence_json, prechecked_at_epoch) VALUES (?, ?, ?, ?, ?)",
        acc.precheck_rows,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO classifier_decisions (product_id, pass_id, model, classified_at_epoch, decision_json, error) VALUES (?, ?, ?, ?, ?, ?)",
        acc.classifier_rows,
    )
    conn.executemany(
        "INSERT INTO import_warnings (warning_type, product_id, message) VALUES (?, ?, ?)",
        acc.import_warnings,
    )
    conn.execute(
        """
        INSERT INTO products_fts (
          id, name, brand, ingredients, allergens, dietary_information, features, detailed_description, nutrition_plain_text
        )
        SELECT id, name, brand, ingredients, allergens, dietary_information, features, detailed_description, nutrition_plain_text
        FROM products
        """
    )

    metadata = {
        "imported_at_epoch": started_at,
        "product_count": len(product_rows),
        "category_count": len(acc.categories),
        "flag_count": len(acc.flags),
        "nutrition_row_count": len(acc.nutrition_rows),
        "manufacturer_vegan_evidence_count": len(acc.manufacturer_evidence),
        "precheck_count": len(acc.precheck_rows),
        "classifier_decision_count": len(acc.classifier_rows),
        "warning_count": len(acc.import_warnings),
    }
    conn.executemany("INSERT INTO import_metadata (key, value) VALUES (?, ?)", ((key, str(value)) for key, value in metadata.items()))

    source_paths = [
        config.product_universe,
        config.bop_raw,
        config.precheck_audit,
        config.manufacturer_audit,
        config.ingredients_audit,
        config.classifier_a,
        config.classifier_b,
        config.manufacturer_meta,
        config.ingredients_meta,
    ]
    source_rows = []
    for path in source_paths:
        source_rows.append(
            (
                str(path),
                1 if path.exists() else 0,
                path.stat().st_size if path.exists() else None,
                path_mtime_epoch(path) if path.exists() else None,
                file_sha256(path),
            )
        )
    conn.executemany(
        "INSERT INTO import_sources (path, exists_on_import, size_bytes, mtime_epoch, sha256) VALUES (?, ?, ?, ?, ?)",
        source_rows,
    )

    conn.executescript(
        """
        CREATE INDEX idx_products_name ON products(name);
        CREATE INDEX idx_products_brand ON products(brand);
        CREATE INDEX idx_product_categories_path ON product_categories(category_path);
        CREATE INDEX idx_product_categories_product ON product_categories(product_id);
        CREATE INDEX idx_product_flags_flag ON product_flags(flag);
        CREATE INDEX idx_product_nutrition_product ON product_nutrition(product_id);
        CREATE INDEX idx_product_nutrition_nutrient ON product_nutrition(nutrient);
        CREATE INDEX idx_precheck_status ON precheck_results(status);
        """
    )


def build_database(config: BuildConfig) -> dict[str, int]:
    started_at = int(time.time())
    manufacturer_epoch = source_epoch(config.manufacturer_meta)
    ingredients_epoch = source_epoch(config.ingredients_meta)
    acc = ProductAccumulator()

    import_manufacturer_audit(acc, config.manufacturer_audit, manufacturer_epoch)
    import_product_universe(acc, config.product_universe, ingredients_epoch)
    import_bop_raw(acc, config.bop_raw)
    import_precheck(acc, config.precheck_audit)
    import_ingredients_audit(acc, config.ingredients_audit)
    import_classifier_pass(acc, config.classifier_a, "a")
    import_classifier_pass(acc, config.classifier_b, "b")

    for path in [config.output, Path(f"{config.output}-wal"), Path(f"{config.output}-shm")]:
        if path.exists():
            path.unlink()
    config.output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.output)
    try:
        with conn:
            create_schema(conn)
            insert_rows(conn, acc, config, started_at)
    finally:
        conn.close()

    return {
        "products": len(acc.rows),
        "categories": len(acc.categories),
        "flags": len(acc.flags),
        "nutritionRows": len(acc.nutrition_rows),
        "manufacturerEvidenceRows": len(acc.manufacturer_evidence),
        "precheckRows": len(acc.precheck_rows),
        "classifierRows": len(acc.classifier_rows),
        "warnings": len(acc.import_warnings),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--product-universe", default=DEFAULT_PRODUCT_UNIVERSE)
    parser.add_argument("--bop-raw", default=DEFAULT_BOP_RAW)
    parser.add_argument("--precheck-audit", default=DEFAULT_PRECHECK_AUDIT)
    parser.add_argument("--manufacturer-audit", default=DEFAULT_MANUFACTURER_AUDIT)
    parser.add_argument("--ingredients-audit", default=DEFAULT_INGREDIENTS_AUDIT)
    parser.add_argument("--classifier-a", default=DEFAULT_CLASSIFIER_A)
    parser.add_argument("--classifier-b", default=DEFAULT_CLASSIFIER_B)
    parser.add_argument("--manufacturer-meta", default=DEFAULT_MANUFACTURER_META)
    parser.add_argument("--ingredients-meta", default=DEFAULT_INGREDIENTS_META)
    return parser


def config_from_args(args: argparse.Namespace) -> BuildConfig:
    return BuildConfig(
        output=Path(args.output),
        product_universe=Path(args.product_universe),
        bop_raw=Path(args.bop_raw),
        precheck_audit=Path(args.precheck_audit),
        manufacturer_audit=Path(args.manufacturer_audit),
        ingredients_audit=Path(args.ingredients_audit),
        classifier_a=Path(args.classifier_a),
        classifier_b=Path(args.classifier_b),
        manufacturer_meta=Path(args.manufacturer_meta),
        ingredients_meta=Path(args.ingredients_meta),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    counts = build_database(config)
    print(f"Wrote {config.output}")
    for key, value in counts.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
