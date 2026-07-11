#!/usr/bin/env python3
"""Classify Ocado products as vegan/nonvegan/unknown from SQLite data only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = str(REPO_ROOT / "ocado_products.sqlite")
CLASSIFIER_VERSION = "db-vegan-codex-v5"
PROMPT_VERSION = "ocado-vegan-product-json-v3"
VALID_STATUSES = {"vegan", "nonvegan", "unknown"}
VALID_VEGAN_REASONS = {"tagged", "manufacturer", "ingredients", "name"}

LEGACY_PRODUCT_COLUMNS = [
    "vegan_according_to_manufacturer",
    "vegan_according_to_ingredients",
]

CLASSIFICATION_PRODUCT_COLUMNS: dict[str, str] = {
    "vegan_status": "TEXT",
    "vegan_reason": "TEXT",
    "vegan_classifier_version": "TEXT",
    "vegan_classified_at_epoch": "INTEGER",
}

TEXT_COLUMNS = [
    "dietary_information",
    "features",
    "detailed_description",
    "further_description",
    "manufacturer_marketing",
    "brand_marketing",
    "other_information",
    "preparation_and_usage",
    "specification",
    "ingredients",
]

POSITIVE_VEGAN_PATTERNS = [
    r"(?<!not )\bsuitable for (?:a )?vegans?\b",
    r"(?<!not )\bsuitable for (?:a )?vegan diet\b",
    r"(?<!not )\bsuitable for vegetarians? (?:and|&) vegans?\b",
    r"(?<!not )\bsuitable for vegans? (?:and|&) vegetarians?\b",
    r"\bcertified vegan\b",
    r"\bregistered with (?:the )?vegan society\b",
    r"\bvegan friendly\b",
]

NEGATIVE_VEGAN_PATTERNS = [
    r"\bnot suitable for vegans?\b",
    r"\bnot suitable for vegetarians? (?:or|and) vegans?\b",
    r"\bmay not be suitable for vegans?\b",
    r"\bnot vegan\b",
]

NONVEGAN_INGREDIENT_PATTERNS = [
    r"\bwhey\b",
    r"\bcasein(?:ate)?s?\b",
    r"\blactose\b",
    r"\bskimmed milk\b",
    r"\bwhole milk\b",
    r"\bmilk powder\b",
    r"\bmilk solids?\b",
    r"\bcream\b",
    r"\bbutter(?:milk)?\b",
    r"\bcheese\b",
    r"\byogh?urt\b",
    r"\beggs?\b",
    r"\balbumen\b",
    r"\bhoney\b",
    r"\bbeeswax\b",
    r"\bshellac\b",
    r"\bgelati[ne]\b",
    r"\bcollagen\b",
    r"\bisinglass\b",
    r"\bcarmine\b",
    r"\bcochineal\b",
    r"\be120\b",
    r"\blanolin\b",
    r"\blard\b",
    r"\bsuet\b",
    r"\btallow\b",
    r"\bbeef\b",
    r"\bpork\b",
    r"\bbacon\b",
    r"\bham\b",
    r"\bchicken\b",
    r"\bturkey\b",
    r"\bduck\b",
    r"\bgoose\b",
    r"\blamb\b",
    r"\bmutton\b",
    r"\bfish\b",
    r"\banchov(?:y|ies)\b",
    r"\btuna\b",
    r"\bsalmon\b",
    r"\bcod\b",
    r"\bshellfish\b",
    r"\bprawns?\b",
    r"\bshrimps?\b",
    r"\bcrab\b",
    r"\blobster\b",
    r"\boyster\b",
    r"\bmussels?\b",
    r"\bclams?\b",
]

AMBIGUOUS_INGREDIENT_PATTERNS = [
    r"\bvitamins?\b",
    r"\bvitamin d3?\b",
    r"\bcholecalciferol\b",
    r"\briboflavin\b",
    r"\bniacin\b",
    r"\bthiamin\b",
    r"\bb12\b",
    r"\benzymes?\b",
    r"\brennet\b",
    r"\bnatural flavourings?\b",
    r"\bflavourings?\b",
    r"\bmono[- ]? and diglycerides\b",
    r"\be47[12]\b",
    r"\bglycer(?:ine|ol)\b",
    r"\bstearic acid\b",
    r"\blactic acid\b",
    r"\blecithin\b",
    r"\bomega ?3\b",
    r"\bwax\b",
    r"\bglaze\b",
    r"\bcolou?rs?\b",
]

SAFE_INGREDIENT_TERMS = {
    "flour",
    "fortified wheat flour",
    "water",
    "salt",
    "sea salt",
    "sugar",
    "dextrose",
    "molasses",
    "glucose-fructose syrup",
    "barley malt extract",
    "malted barley flour",
    "wheat flour",
    "wholewheat flour",
    "whole grain wheat flour",
    "wholegrain wheat flour",
    "durum wheat semolina",
    "semolina",
    "wheat semolina",
    "wheat bran",
    "wheat protein",
    "wheat starch",
    "beans",
    "green beans",
    "fine beans",
    "red kidney beans",
    "kidney beans",
    "black beans",
    "organic black beans",
    "tomatoes",
    "potatoes",
    "sunflower oil",
    "olive oil",
    "extra virgin olive oil",
    "rapeseed oil",
    "vegetable oil",
    "vegetable oils",
    "palm oil",
    "palm",
    "rapeseed",
    "rice",
    "brown rice",
    "basmati rice",
    "oats",
    "oat flakes",
    "maize",
    "corn",
    "yeast",
    "dried yeast",
    "raising agent",
    "raising agents",
    "sodium bicarbonate",
    "ammonium bicarbonate",
    "acidity regulator",
    "sodium hydroxide",
    "firming agent",
    "calcium chloride",
}

SINGLE_INGREDIENT_NAME_TERMS = [
    "beans",
    "green beans",
    "fine beans",
    "peas",
    "petits pois",
    "sweetcorn",
    "corn",
    "mangetout",
    "mange tout",
    "tomatoes",
    "potatoes",
    "peppers",
    "carrots",
    "parsnips",
    "turnips",
    "swede",
    "cucumber",
    "onions",
    "leeks",
    "shallots",
    "garlic",
    "chillies",
    "chilli",
    "ginger",
    "asparagus",
    "aubergine",
    "courgettes",
    "broccoli",
    "cauliflower",
    "cabbage",
    "sprouts",
    "lettuce",
    "spinach",
    "kale",
    "cavolo nero",
    "spring greens",
    "choi sum",
    "rocket",
    "watercress",
    "mushrooms",
    "dates",
    "grapes",
    "avocados",
    "avocado",
    "apples",
    "bananas",
    "berries",
    "mixed berries",
    "blackberries",
    "blueberries",
    "strawberries",
    "raspberries",
    "pineapple",
    "melon",
    "mango",
    "mangoes",
    "lemons",
    "lemon",
    "limes",
    "lime",
    "oranges",
    "satsumas",
    "satsuma",
    "tangerines",
    "tangerine",
    "nectarines",
    "nectarine",
    "pears",
    "beetroot",
    "methi",
    "rice",
    "lentils",
    "chickpeas",
    "almonds",
    "walnuts",
    "parsley",
    "coriander",
    "basil",
    "mint",
    "rosemary",
    "thyme",
]

PROCESSED_NAME_BLOCKLIST = [
    "sauce",
    "soup",
    "juice",
    "oil",
    "powder",
    "vinegar",
    "drink",
    "smoothie",
    "card",
    "candle",
    "diffuser",
    "seeds",
    "seasoned",
    "flavoured",
    "flavored",
    "meal",
    "bites",
    "lollies",
    "dessert",
    "cake",
    "bread",
    "dressing",
    "marinated",
]

SINGLE_INGREDIENT_PRODUCE_CATEGORY_MARKERS = [
    "fresh-chilled-food/fruit/",
    "fresh-chilled-food/vegetables/",
    "fresh-chilled-food/salad-herbs/",
    "fresh-chilled-food/best-of-british/fruit-vegetables/",
    "dietary-lifestyle-world-foods/organic/fruit-vegetables/",
    "m-s/m-s-best-of-fresh/m-s-fruit-vegetables/",
    "ocado-own-range/fruit-vegetables-salad/",
    "frozen-food/frozen-fruit-vegetables-herbs/",
]

SAFE_FLOUR_FORTIFICATION_TERMS = {
    "added",
    "calcium",
    "calcium carbonate",
    "flour",
    "folic acid",
    "fortified",
    "iron",
    "niacin",
    "thiamin",
    "thiamine",
    "vitamin b1",
    "vitamin b3",
    "vitamin b9",
    "vitamins",
    "wheat flour",
    "with",
}

FLOUR_FORTIFICATION_HEAD_RE = re.compile(
    r"\b(?:fortified\s+)?(?:whole\s*grain\s+|wholegrain\s+|wholewheat\s+)?(?:wheat\s+)?flour\s*([\[(])",
    re.IGNORECASE,
)

CODEX_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "product_id",
                    "vegan_status",
                    "vegan_reason",
                    "confidence",
                    "summary",
                    "evidence",
                    "ambiguity_notes",
                ],
                "properties": {
                    "product_id": {"type": "string"},
                    "vegan_status": {"type": "string", "enum": sorted(VALID_STATUSES)},
                    "vegan_reason": {
                        "anyOf": [
                            {"type": "string", "enum": sorted(VALID_VEGAN_REASONS)},
                            {"type": "null"},
                        ]
                    },
                    "confidence": {"type": "string", "enum": ["certain", "uncertain"]},
                    "summary": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "ambiguity_notes": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


class CodexUsageLimitError(RuntimeError):
    """Codex CLI reported an account usage limit rather than a model response."""


@dataclass(frozen=True)
class ClassificationResult:
    product_id: str
    vegan_status: str
    vegan_reason: str | None
    confidence: str
    summary: str
    evidence: dict[str, Any]
    source: str
    model: str | None = None
    reasoning_effort: str | None = None
    prompt_version: str | None = None
    context_hash: str | None = None
    raw_response: str | None = None
    parsed_response_json: str | None = None
    validation_error: str | None = None
    exit_status: int | None = None


def now_epoch() -> int:
    return int(time.time())


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    if column_name not in table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def drop_column_if_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> None:
    if column_name in table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")


def create_backup(db_path: Path) -> Path:
    backup_path = db_path.with_suffix(db_path.suffix + ".bak")
    if backup_path.exists():
        backup_path.unlink()
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    return backup_path


def ensure_classification_schema(conn: sqlite3.Connection, *, drop_legacy: bool = True) -> None:
    if "products" not in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}:
        raise RuntimeError("missing required products table")

    for column_name, column_type in CLASSIFICATION_PRODUCT_COLUMNS.items():
        ensure_column(conn, "products", column_name, column_type)
    if drop_legacy:
        for column_name in LEGACY_PRODUCT_COLUMNS:
            drop_column_if_exists(conn, "products", column_name)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vegan_classification_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at_epoch INTEGER NOT NULL,
          completed_at_epoch INTEGER,
          status TEXT NOT NULL,
          mode TEXT NOT NULL,
          classifier_version TEXT NOT NULL,
          prompt_version TEXT,
          model TEXT,
          reasoning_effort TEXT,
          total_products INTEGER DEFAULT 0,
          rule_classified INTEGER DEFAULT 0,
          llm_submitted INTEGER DEFAULT 0,
          llm_classified INTEGER DEFAULT 0,
          errors INTEGER DEFAULT 0,
          error TEXT
        );

        CREATE TABLE IF NOT EXISTS product_vegan_classification_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER,
          product_id TEXT NOT NULL,
          source TEXT NOT NULL,
          vegan_status TEXT NOT NULL,
          vegan_reason TEXT,
          confidence TEXT NOT NULL,
          summary TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          model TEXT,
          reasoning_effort TEXT,
          prompt_version TEXT,
          context_hash TEXT,
          raw_response TEXT,
          parsed_response_json TEXT,
          validation_error TEXT,
          exit_status INTEGER,
          classifier_version TEXT NOT NULL,
          classified_at_epoch INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_vegan_audit_product
          ON product_vegan_classification_audit(product_id);
        CREATE INDEX IF NOT EXISTS idx_vegan_audit_run
          ON product_vegan_classification_audit(run_id);
        CREATE INDEX IF NOT EXISTS idx_products_vegan_status
          ON products(vegan_status);
        """
    )


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def start_run(
    conn: sqlite3.Connection,
    *,
    mode: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO vegan_classification_runs
          (started_at_epoch, status, mode, classifier_version, prompt_version, model, reasoning_effort)
        VALUES (?, 'running', ?, ?, ?, ?, ?)
        """,
        (now_epoch(), mode, CLASSIFIER_VERSION, PROMPT_VERSION, model, reasoning_effort),
    )
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, error: str | None = None) -> None:
    conn.execute(
        """
        UPDATE vegan_classification_runs
        SET completed_at_epoch = ?, status = ?, error = ?
        WHERE id = ?
        """,
        (now_epoch(), status, error, run_id),
    )


def increment_run(conn: sqlite3.Connection, run_id: int, column_name: str, amount: int) -> None:
    allowed = {"total_products", "rule_classified", "llm_submitted", "llm_classified", "errors"}
    if column_name not in allowed:
        raise ValueError(f"unsupported run counter: {column_name}")
    conn.execute(f"UPDATE vegan_classification_runs SET {column_name} = {column_name} + ? WHERE id = ?", (amount, run_id))


def validate_result(result: ClassificationResult) -> None:
    if result.vegan_status not in VALID_STATUSES:
        raise ValueError(f"{result.product_id}: invalid vegan_status {result.vegan_status!r}")
    if result.vegan_status == "vegan":
        if result.vegan_reason not in VALID_VEGAN_REASONS:
            raise ValueError(f"{result.product_id}: vegan product requires a valid vegan_reason")
    elif result.vegan_reason is not None:
        raise ValueError(f"{result.product_id}: non-vegan/unknown product must not have vegan_reason")


def write_classification(conn: sqlite3.Connection, run_id: int, result: ClassificationResult) -> None:
    validate_result(result)
    classified_at = now_epoch()
    conn.execute(
        """
        UPDATE products
        SET vegan_status = ?,
            vegan_reason = ?,
            vegan_classifier_version = ?,
            vegan_classified_at_epoch = ?
        WHERE id = ?
        """,
        (result.vegan_status, result.vegan_reason, CLASSIFIER_VERSION, classified_at, result.product_id),
    )
    conn.execute(
        """
        INSERT INTO product_vegan_classification_audit (
          run_id, product_id, source, vegan_status, vegan_reason, confidence, summary,
          evidence_json, model, reasoning_effort, prompt_version, context_hash,
          raw_response, parsed_response_json, validation_error, exit_status,
          classifier_version, classified_at_epoch
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            result.product_id,
            result.source,
            result.vegan_status,
            result.vegan_reason,
            result.confidence,
            result.summary,
            json_dumps(result.evidence),
            result.model,
            result.reasoning_effort,
            result.prompt_version,
            result.context_hash,
            result.raw_response,
            result.parsed_response_json,
            result.validation_error,
            result.exit_status,
            CLASSIFIER_VERSION,
            classified_at,
        ),
    )


def load_product_context(conn: sqlite3.Connection, product_id: str) -> dict[str, Any]:
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        raise KeyError(f"unknown product id: {product_id}")
    flags = [row["flag"] for row in conn.execute("SELECT flag FROM product_flags WHERE product_id = ? ORDER BY flag", (product_id,))]
    categories = [
        row["category_path"]
        for row in conn.execute("SELECT category_path FROM product_categories WHERE product_id = ? ORDER BY category_path", (product_id,))
    ]
    manufacturer_evidence = [
        {"field_title": row["field_title"], "content": row["content"]}
        for row in conn.execute(
            "SELECT field_title, content FROM manufacturer_vegan_evidence WHERE product_id = ?",
            (product_id,),
        )
    ]
    return {
        "product": dict(product),
        "flags": flags,
        "categories": categories,
        "manufacturer_vegan_evidence": manufacturer_evidence,
    }


def load_product_contexts(conn: sqlite3.Connection, product_ids: Iterable[str]) -> list[dict[str, Any]]:
    return [load_product_context(conn, product_id) for product_id in product_ids]


def evidence_source(table: str, column: str, text: str) -> dict[str, str]:
    return {"table": table, "column": column, "text": text[:1000]}


def text_sources(context: dict[str, Any]) -> list[dict[str, str]]:
    product = context["product"]
    sources = []
    for column in TEXT_COLUMNS:
        text = normalize_space(product.get(column))
        if text:
            sources.append(evidence_source("products", column, text))
    for row in context["manufacturer_vegan_evidence"]:
        text = normalize_space(row.get("content"))
        if text:
            sources.append(evidence_source("manufacturer_vegan_evidence", row.get("field_title") or "content", text))
    return sources


def find_matching_sources(sources: list[dict[str, str]], patterns: list[str]) -> list[dict[str, str]]:
    matches = []
    for source in sources:
        text = source["text"].lower()
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            matches.append(source)
    return matches


def features_text_has_standalone_vegan_claim(text: str) -> bool:
    """Return true when "vegan" is a discrete product feature, not incidental prose."""
    for part in re.split(r"[,;|\n\r\u2022]+", text):
        token = normalize_space(part).strip(" .:-").lower()
        if token in {
            "vegan",
            "suitable for vegan",
            "suitable for vegans",
            "suitable for a vegan diet",
            "vegan friendly",
            "certified vegan",
        }:
            return True
        if re.fullmatch(r"(?:suitable for )?(?:vegetarians? (?:and|&) )?vegans?", token):
            return True
        if re.fullmatch(r"vegans? (?:and|&) vegetarians?", token):
            return True
    return False


def classify_by_manufacturer_text(context: dict[str, Any]) -> ClassificationResult | None:
    product_id = context["product"]["id"]
    sources = text_sources(context)
    positive = find_matching_sources(sources, POSITIVE_VEGAN_PATTERNS)
    negative = find_matching_sources(sources, NEGATIVE_VEGAN_PATTERNS)
    for source in sources:
        if source["text"].strip().lower() in {"vegan", "suitable for vegans"}:
            positive.append(source)
        if source["table"] == "products" and source["column"] == "features" and features_text_has_standalone_vegan_claim(source["text"]):
            positive.append(source)
    if positive and negative:
        return ClassificationResult(
            product_id=product_id,
            vegan_status="unknown",
            vegan_reason=None,
            confidence="uncertain",
            summary="Conflicting explicit vegan and non-vegan manufacturer text was found.",
            evidence={"rule": "manufacturer_conflict", "positive": positive, "negative": negative},
            source="rule",
        )
    if negative:
        return ClassificationResult(
            product_id=product_id,
            vegan_status="nonvegan",
            vegan_reason=None,
            confidence="certain",
            summary="Manufacturer text explicitly says the product is not suitable for vegans.",
            evidence={"rule": "manufacturer_explicit_nonvegan", "sources": negative},
            source="rule",
        )
    if positive:
        return ClassificationResult(
            product_id=product_id,
            vegan_status="vegan",
            vegan_reason="manufacturer",
            confidence="certain",
            summary="Manufacturer text explicitly says the product is suitable for vegans.",
            evidence={"rule": "manufacturer_explicit_vegan", "sources": positive},
            source="rule",
        )
    return None


def strip_allergen_warnings(text: str) -> str:
    chunks = re.split(r"(?<=[.;])\s+", text)
    kept = []
    warning_start = re.compile(
        r"^\s*(?:may (?:also )?contain|made in a factory|produced in a factory|packed in a factory|not suitable for allergy sufferers)",
        re.IGNORECASE,
    )
    for chunk in chunks:
        if warning_start.search(chunk):
            continue
        kept.append(chunk)
    return " ".join(kept)


def ingredient_matches(text: str, patterns: list[str]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def split_ingredient_terms(text: str) -> list[str]:
    normalized = text.lower()
    normalized = re.sub(r"\b\d+(?:\.\d+)?\s*%", " ", normalized)
    normalized = re.sub(r"[*]", " ", normalized)
    normalized = re.sub(r"[\[\](){}]", ",", normalized)
    raw_terms = re.split(r"[,;/]", normalized)
    terms = []
    for term in raw_terms:
        term = re.sub(r"\b(?:ingredient|ingredients|and)\b", " ", term)
        term = re.sub(r"\s+", " ", term).strip(" .:-")
        if term:
            terms.append(term)
    return terms


def normalize_fortification_term(term: str) -> str:
    term = re.sub(r"\b(?:with|added)\b", " ", term)
    term = re.sub(r"\s+", " ", term).strip(" .:-")
    return term


def is_safe_flour_fortification(segment: str) -> bool:
    terms = split_ingredient_terms(segment)
    if not terms:
        return False
    return all(term in SAFE_FLOUR_FORTIFICATION_TERMS or normalize_fortification_term(term) in SAFE_FLOUR_FORTIFICATION_TERMS for term in terms)


def matching_delimiter(text: str, open_index: int) -> int:
    opener = text[open_index]
    closer = ")" if opener == "(" else "]"
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def strip_safe_flour_fortification(text: str) -> str:
    # UK flour fortification minerals/vitamins are treated as vegan for this
    # project. Strip only the parenthesised/bracketed flour fortification
    # detail, leaving unrelated vitamins elsewhere to remain ambiguous.
    result = []
    cursor = 0
    while True:
        match = FLOUR_FORTIFICATION_HEAD_RE.search(text, cursor)
        if not match:
            result.append(text[cursor:])
            return "".join(result)

        open_index = match.end(1) - 1
        close_index = matching_delimiter(text, open_index)
        if close_index == -1:
            result.append(text[cursor:])
            return "".join(result)

        segment = text[open_index + 1 : close_index]
        if is_safe_flour_fortification(segment):
            result.append(text[cursor : match.start()])
            result.append("Wheat Flour")
            cursor = close_index + 1
        else:
            result.append(text[cursor : close_index + 1])
            cursor = close_index + 1


def all_terms_are_safe(terms: list[str]) -> bool:
    if not terms:
        return False
    return all(term in SAFE_INGREDIENT_TERMS for term in terms)


def looks_like_single_ingredient_vegan_product(context: dict[str, Any]) -> bool:
    product = context["product"]
    name = (product.get("name") or "").lower()
    if not name or any(blocked in name for blocked in PROCESSED_NAME_BLOCKLIST):
        return False
    categories = context["categories"]
    if not categories or not any(marker in category for category in categories for marker in SINGLE_INGREDIENT_PRODUCE_CATEGORY_MARKERS):
        return False

    full_product_name = ""
    full_product_name_match = re.search(r"\bfull product name:\s*([^.;]+)", normalize_space(product.get("other_information")), re.IGNORECASE)
    if full_product_name_match:
        full_product_name = full_product_name_match.group(1).lower()

    identity_text = " ".join(
        [
            name,
            full_product_name,
            " ".join(categories).replace("-", " ").replace("/", " ").lower(),
        ]
    )
    return any(re.search(rf"\b{re.escape(term)}\b", identity_text) for term in SINGLE_INGREDIENT_NAME_TERMS)


def classify_by_ingredients(context: dict[str, Any]) -> ClassificationResult | None:
    product = context["product"]
    product_id = product["id"]
    ingredients = normalize_space(product.get("ingredients"))
    if not ingredients:
        if looks_like_single_ingredient_vegan_product(context):
            return ClassificationResult(
                product_id=product_id,
                vegan_status="vegan",
                vegan_reason="ingredients",
                confidence="certain",
                summary="Product identity is an unambiguous single vegan ingredient and no ingredients field is present.",
                evidence={
                    "rule": "single_ingredient_product_identity",
                    "name": product.get("name"),
                    "categories": context["categories"],
                },
                source="rule",
            )
        return None

    ingredient_text = strip_safe_flour_fortification(strip_allergen_warnings(ingredients))
    nonvegan_matches = ingredient_matches(ingredient_text, NONVEGAN_INGREDIENT_PATTERNS)
    if nonvegan_matches:
        return ClassificationResult(
            product_id=product_id,
            vegan_status="nonvegan",
            vegan_reason=None,
            confidence="certain",
            summary="Ingredients contain obvious animal-derived terms.",
            evidence={
                "rule": "ingredients_obvious_nonvegan",
                "sources": [evidence_source("products", "ingredients", ingredients)],
                "matched_patterns": nonvegan_matches,
            },
            source="rule",
        )

    ambiguous_matches = ingredient_matches(ingredient_text, AMBIGUOUS_INGREDIENT_PATTERNS)
    if ambiguous_matches:
        return ClassificationResult(
            product_id=product_id,
            vegan_status="unknown",
            vegan_reason=None,
            confidence="uncertain",
            summary="Ingredients contain ambiguous terms that may be animal-derived.",
            evidence={
                "rule": "ingredients_ambiguous",
                "sources": [evidence_source("products", "ingredients", ingredients)],
                "matched_patterns": ambiguous_matches,
            },
            source="rule",
        )

    terms = split_ingredient_terms(ingredient_text)
    if all_terms_are_safe(terms):
        return ClassificationResult(
            product_id=product_id,
            vegan_status="vegan",
            vegan_reason="ingredients",
            confidence="certain",
            summary="All parsed ingredients are in the conservative vegan allowlist.",
            evidence={
                "rule": "ingredients_all_allowlisted_vegan",
                "sources": [evidence_source("products", "ingredients", ingredients)],
                "terms": terms,
            },
            source="rule",
        )

    return None


def classify_by_rules(context: dict[str, Any]) -> ClassificationResult | None:
    product = context["product"]
    product_id = product["id"]
    flags = set(context["flags"])
    if product.get("official_vegan") == 1 or "vegan" in flags:
        return ClassificationResult(
            product_id=product_id,
            vegan_status="vegan",
            vegan_reason="tagged",
            confidence="certain",
            summary="Ocado metadata contains the official vegan tag.",
            evidence={"rule": "official_vegan_tag", "official_vegan": product.get("official_vegan"), "flags": sorted(flags)},
            source="rule",
        )

    manufacturer = classify_by_manufacturer_text(context)
    if manufacturer:
        return manufacturer

    name = product.get("name") or ""
    if re.search(r"\bvegan\b", name, re.IGNORECASE):
        return ClassificationResult(
            product_id=product_id,
            vegan_status="vegan",
            vegan_reason="name",
            confidence="certain",
            summary="Product name contains the standalone word vegan.",
            evidence={"rule": "name_contains_vegan", "name": name},
            source="rule",
        )

    ingredients = classify_by_ingredients(context)
    if ingredients:
        return ingredients
    return None


def product_context_for_llm(context: dict[str, Any]) -> dict[str, Any]:
    product = context["product"]
    keys = [
        "id",
        "name",
        "brand",
        "url",
        "ingredients",
        "allergens",
        "dietary_information",
        "features",
        "detailed_description",
        "further_description",
        "brand_marketing",
        "manufacturer_marketing",
        "other_information",
        "preparation_and_usage",
        "specification",
        "country_of_origin",
    ]
    return {
        "product": {key: product.get(key) for key in keys if product.get(key) not in (None, "")},
        "flags": context["flags"],
        "categories": context["categories"][:20],
        "manufacturer_vegan_evidence": context["manufacturer_vegan_evidence"],
    }


def context_hash(payload: Any) -> str:
    return hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()


def build_codex_prompt(products: list[dict[str, Any]]) -> str:
    payload = {"products": products}
    product_ids = [str(product.get("product", {}).get("id")) for product in products]
    return (
        "Classify Ocado grocery products as vegan, nonvegan, or unknown using only the supplied JSON.\n"
        "Do not browse, search, read files, or infer from brand reputation. The full product page is not available.\n"
        "Return JSON matching the provided output schema.\n\n"
        f"Return exactly {len(product_ids)} decisions, one for each product_id in this exact set: {', '.join(product_ids)}.\n"
        "Do not omit products. Do not add products.\n\n"
        "Rules:\n"
        "- vegan_status must be one of vegan, nonvegan, unknown.\n"
        "- vegan_reason must be tagged, manufacturer, ingredients, or name only when vegan_status is vegan; otherwise it must be null.\n"
        "- Use manufacturer when the supplied product text explicitly says vegan or suitable for vegans.\n"
        "- Use ingredients only when ingredients or single-ingredient identity make vegan status certain.\n"
        "- If the product has no ingredients field, treat it as a single-ingredient product and classify from the supplied product identity/category text when that identity is unambiguous.\n"
        "- Ingredients such as milk, egg, honey, gelatine, meat, fish, shellfish, beeswax, shellac, carmine, or lanolin are nonvegan.\n"
        "- May-contain allergen warnings do not make a product nonvegan.\n"
        "- Treat fortified wheat/flour as vegan when the fortification is limited to standard flour additions such as calcium, iron, niacin, thiamin, or folic acid.\n"
        "- Ambiguous ingredients such as natural flavourings, enzymes, vitamins outside standard flour fortification, vitamin D3, glycerine, E471/E472, wax, glaze, or colours mean unknown unless other explicit vegan evidence exists.\n"
        "- Prefer unknown over guessing.\n\n"
        f"Product JSON:\n{json_dumps(payload)}\n"
    )


def extract_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Codex output root is not an object")
    return parsed


def validate_codex_payload(payload: dict[str, Any], expected_ids: set[str]) -> dict[str, dict[str, Any]]:
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("Codex output is missing a decisions array")
    by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("Codex decision is not an object")
        product_id = str(decision.get("product_id") or "")
        if product_id not in expected_ids:
            raise ValueError(f"Codex returned unexpected product_id {product_id!r}")
        status = decision.get("vegan_status")
        reason = decision.get("vegan_reason")
        if status not in VALID_STATUSES:
            raise ValueError(f"{product_id}: invalid vegan_status {status!r}")
        if status == "vegan" and reason not in VALID_VEGAN_REASONS:
            raise ValueError(f"{product_id}: vegan decision requires valid vegan_reason")
        if status != "vegan" and reason is not None:
            raise ValueError(f"{product_id}: non-vegan/unknown decision must have null vegan_reason")
        by_id[product_id] = decision
    missing = expected_ids - set(by_id)
    if missing:
        raise ValueError(f"Codex output omitted product IDs: {', '.join(sorted(missing))}")
    return by_id


def is_empty_codex_response_error(exc: Exception) -> bool:
    return (isinstance(exc, json.JSONDecodeError) and "line 1 column 1" in str(exc)) or "Codex returned empty output" in str(exc)


def decision_to_result(
    decision: dict[str, Any],
    *,
    source: str,
    model: str,
    reasoning_effort: str,
    context_hash_value: str,
    raw_response: str,
    parsed_response_json: str,
    exit_status: int,
) -> ClassificationResult:
    product_id = str(decision["product_id"])
    evidence = {
        "rule": "codex_llm",
        "evidence": decision.get("evidence") or [],
        "ambiguity_notes": decision.get("ambiguity_notes") or [],
    }
    return ClassificationResult(
        product_id=product_id,
        vegan_status=decision["vegan_status"],
        vegan_reason=decision.get("vegan_reason"),
        confidence=decision.get("confidence") or "uncertain",
        summary=decision.get("summary") or "",
        evidence=evidence,
        source=source,
        model=model,
        reasoning_effort=reasoning_effort,
        prompt_version=PROMPT_VERSION,
        context_hash=context_hash_value,
        raw_response=raw_response,
        parsed_response_json=parsed_response_json,
        exit_status=exit_status,
    )


def call_codex_batch(
    contexts: list[dict[str, Any]],
    *,
    model: str,
    reasoning_effort: str,
    codex_bin: str,
) -> tuple[dict[str, dict[str, Any]], str, str, int]:
    products = [product_context_for_llm(context) for context in contexts]
    prompt = build_codex_prompt(products)
    expected_ids = {context["product"]["id"] for context in contexts}
    with tempfile.TemporaryDirectory(prefix="ocado-vegan-codex-") as tmp:
        tmp_path = Path(tmp)
        schema_path = tmp_path / "schema.json"
        output_path = tmp_path / "last-message.json"
        schema_path.write_text(json_dumps(CODEX_OUTPUT_SCHEMA), encoding="utf-8")
        command = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-C",
            str(Path.cwd()),
            "-s",
            "read-only",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "-c",
            'web_search="disabled"',
            "-",
        ]
        completed = subprocess.run(command, input=prompt, text=True, capture_output=True, check=False)
        raw = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
        if completed.returncode != 0 and "hit your usage limit" in completed.stderr.lower():
            raise CodexUsageLimitError(completed.stderr.strip())
        if not raw.strip():
            stderr_tail = completed.stderr.strip()[-2000:]
            raise RuntimeError(f"Codex returned empty output with exit status {completed.returncode}: {stderr_tail}")
        parsed = extract_json_object(raw)
        parsed_json = json_dumps(parsed)
        return validate_codex_payload(parsed, expected_ids), raw, parsed_json, completed.returncode


def merge_pass_decisions(pass_decisions: list[dict[str, dict[str, Any]]], product_id: str) -> dict[str, Any]:
    decisions = [pass_payload[product_id] for pass_payload in pass_decisions]
    first = decisions[0]
    if all(decision["vegan_status"] == first["vegan_status"] and decision.get("vegan_reason") == first.get("vegan_reason") for decision in decisions):
        return first
    return {
        "product_id": product_id,
        "vegan_status": "unknown",
        "vegan_reason": None,
        "confidence": "uncertain",
        "summary": "Independent Codex passes disagreed, so the product is classified unknown.",
        "evidence": [json_dumps(decision) for decision in decisions],
        "ambiguity_notes": ["independent_codex_disagreement"],
    }


def classify_codex_contexts(
    contexts: list[dict[str, Any]],
    *,
    passes: int,
    retries: int,
    model: str,
    reasoning_effort: str,
    codex_bin: str,
    depth: int = 0,
) -> list[ClassificationResult]:
    payload = [product_context_for_llm(context) for context in contexts]
    hash_value = context_hash({"prompt_version": PROMPT_VERSION, "products": payload})
    last_error: Exception | None = None
    indent = "  " + ("  " * depth)

    for attempt in range(1, retries + 2):
        try:
            pass_payloads = []
            raw_responses = []
            parsed_jsons = []
            exit_statuses = []
            for pass_number in range(1, passes + 1):
                print(f"{indent}pass {pass_number}/{passes}, attempt {attempt}", flush=True)
                decisions, raw, parsed_json, exit_status = call_codex_batch(
                    contexts,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    codex_bin=codex_bin,
                )
                pass_payloads.append(decisions)
                raw_responses.append(raw)
                parsed_jsons.append(parsed_json)
                exit_statuses.append(exit_status)
            results = []
            for context in contexts:
                product_id = context["product"]["id"]
                merged = merge_pass_decisions(pass_payloads, product_id)
                results.append(
                    decision_to_result(
                        merged,
                        source="codex",
                        model=model,
                        reasoning_effort=reasoning_effort,
                        context_hash_value=hash_value,
                        raw_response="\n--- pass ---\n".join(raw_responses),
                        parsed_response_json=json_dumps(parsed_jsons),
                        exit_status=max(exit_statuses),
                    )
                )
            return results
        except Exception as exc:
            if isinstance(exc, CodexUsageLimitError):
                raise
            last_error = exc
            if attempt <= retries:
                print(f"{indent}retrying batch after error: {exc}", flush=True)
                if is_empty_codex_response_error(exc):
                    time.sleep(min(30, 5 * attempt))

    if len(contexts) > 1:
        midpoint = max(1, len(contexts) // 2)
        print(
            f"{indent}splitting failed batch of {len(contexts)} into {midpoint} and {len(contexts) - midpoint}: {last_error}",
            flush=True,
        )
        if last_error and is_empty_codex_response_error(last_error):
            time.sleep(15)
        return [
            *classify_codex_contexts(
                contexts[:midpoint],
                passes=passes,
                retries=retries,
                model=model,
                reasoning_effort=reasoning_effort,
                codex_bin=codex_bin,
                depth=depth + 1,
            ),
            *classify_codex_contexts(
                contexts[midpoint:],
                passes=passes,
                retries=retries,
                model=model,
                reasoning_effort=reasoning_effort,
                codex_bin=codex_bin,
                depth=depth + 1,
            ),
        ]

    product_id = contexts[0]["product"]["id"]
    return [
        ClassificationResult(
            product_id=product_id,
            vegan_status="unknown",
            vegan_reason=None,
            confidence="uncertain",
            summary="Codex could not return a schema-valid DB-only classification after retries.",
            evidence={
                "rule": "codex_failed_after_retries",
                "error": str(last_error),
                "product_context": product_context_for_llm(contexts[0]),
            },
            source="codex_error",
            model=model,
            reasoning_effort=reasoning_effort,
            prompt_version=PROMPT_VERSION,
            context_hash=hash_value,
            validation_error=str(last_error),
            exit_status=1,
        )
    ]


def select_unclassified_product_ids(
    conn: sqlite3.Connection,
    *,
    limit: int = 0,
    sync_run_id: int | None = None,
) -> list[str]:
    query = "SELECT id FROM products WHERE vegan_status IS NULL ORDER BY id"
    parameters: tuple[Any, ...] = ()
    if sync_run_id is not None:
        query = """
            SELECT id FROM products
            WHERE vegan_status IS NULL
              AND id IN (
                SELECT product_id FROM sync_product_context_changes
                WHERE run_id = ? AND change_kind IN ('new', 'changed')
              )
            ORDER BY id
        """
        parameters = (sync_run_id,)
    if limit:
        query += " LIMIT ?"
        parameters += (limit,)
    return [row["id"] for row in conn.execute(query, parameters)]


def classify_rules(
    conn: sqlite3.Connection,
    *,
    limit: int = 0,
    force: bool = False,
    sync_run_id: int | None = None,
) -> int:
    if force and sync_run_id is None:
        query = "SELECT id FROM products ORDER BY id"
        parameters: tuple[Any, ...] = ()
        if limit:
            query += " LIMIT ?"
            parameters = (limit,)
        ids = [row["id"] for row in conn.execute(query, parameters)]
    else:
        ids = select_unclassified_product_ids(conn, limit=limit, sync_run_id=sync_run_id)
    run_id = start_run(conn, mode="rules")
    classified = 0
    try:
        increment_run(conn, run_id, "total_products", len(ids))
        for product_id in ids:
            context = load_product_context(conn, product_id)
            result = classify_by_rules(context)
            if result:
                write_classification(conn, run_id, result)
                classified += 1
        increment_run(conn, run_id, "rule_classified", classified)
        finish_run(conn, run_id, "completed")
    except Exception as exc:
        finish_run(conn, run_id, "failed", str(exc))
        raise
    return classified


def classify_codex(
    conn: sqlite3.Connection,
    *,
    limit: int,
    batch_size: int,
    passes: int,
    retries: int,
    model: str,
    reasoning_effort: str,
    codex_bin: str,
    workers: int = 1,
    sync_run_id: int | None = None,
) -> int:
    ids = select_unclassified_product_ids(conn, limit=limit, sync_run_id=sync_run_id)
    run_id = start_run(conn, mode="codex", model=model, reasoning_effort=reasoning_effort)
    classified = 0
    batch_total = (len(ids) + batch_size - 1) // batch_size

    def classify_batch(
        batch_start: int,
        batch_ids: list[str],
        contexts: list[dict[str, Any]],
    ) -> tuple[int, list[str], list[ClassificationResult]]:
        batch_number = (batch_start // batch_size) + 1
        print(
            f"Codex batch {batch_number}/{batch_total}: products {batch_start + 1}-{batch_start + len(batch_ids)} of {len(ids)}",
            flush=True,
        )
        results = classify_codex_contexts(
            contexts,
            passes=passes,
            retries=retries,
            model=model,
            reasoning_effort=reasoning_effort,
            codex_bin=codex_bin,
        )
        return batch_number, batch_ids, results

    def write_batch_results(batch_ids: list[str], results: list[ClassificationResult]) -> None:
        nonlocal classified
        with conn:
            for result in results:
                write_classification(conn, run_id, result)
                classified += 1
            increment_run(conn, run_id, "llm_submitted", len(batch_ids))
            increment_run(conn, run_id, "llm_classified", len(batch_ids))
            error_count = sum(1 for result in results if result.validation_error)
            if error_count:
                increment_run(conn, run_id, "errors", error_count)

    try:
        increment_run(conn, run_id, "total_products", len(ids))
        if workers <= 1:
            for batch_start in range(0, len(ids), batch_size):
                batch_ids = ids[batch_start : batch_start + batch_size]
                contexts = load_product_contexts(conn, batch_ids)
                _, batch_ids, results = classify_batch(batch_start, batch_ids, contexts)
                write_batch_results(batch_ids, results)
        else:
            batches = [(batch_start, ids[batch_start : batch_start + batch_size]) for batch_start in range(0, len(ids), batch_size)]
            with ThreadPoolExecutor(max_workers=workers) as executor:
                batch_iter = iter(batches)
                futures: dict[Future[tuple[int, list[str], list[ClassificationResult]]], None] = {}

                def submit_next_batch() -> bool:
                    try:
                        batch_start, batch_ids = next(batch_iter)
                    except StopIteration:
                        return False
                    contexts = load_product_contexts(conn, batch_ids)
                    futures[executor.submit(classify_batch, batch_start, batch_ids, contexts)] = None
                    return True

                for _ in range(workers):
                    if not submit_next_batch():
                        break

                while futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        futures.pop(future)
                        batch_number, batch_ids, results = future.result()
                        write_batch_results(batch_ids, results)
                        print(f"Codex batch {batch_number}/{batch_total}: wrote {len(results)} products", flush=True)
                        submit_next_batch()
        finish_run(conn, run_id, "completed")
    except Exception as exc:
        increment_run(conn, run_id, "errors", 1)
        finish_run(conn, run_id, "failed", str(exc))
        raise
    return classified


def status(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT COALESCE(vegan_status, 'unclassified') AS vegan_status,
                   COALESCE(vegan_reason, '') AS vegan_reason,
                   COUNT(*) AS count
            FROM products
            GROUP BY vegan_status, vegan_reason
            ORDER BY vegan_status, vegan_reason
            """
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate-schema")

    rules = subparsers.add_parser("classify-rules")
    rules.add_argument("--limit", type=int, default=0)
    rules.add_argument("--force", action="store_true")
    rules.add_argument("--sync-run-id", type=int)

    codex = subparsers.add_parser("classify-codex")
    codex.add_argument("--limit", type=int, default=0)
    codex.add_argument("--batch-size", type=int, default=10)
    codex.add_argument("--passes", type=int, default=2)
    codex.add_argument("--retries", type=int, default=2)
    codex.add_argument("--model", default="gpt-5.6-sol")
    codex.add_argument("--reasoning-effort", default="medium")
    codex.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    codex.add_argument("--workers", type=int, default=1)
    codex.add_argument("--sync-run-id", type=int)

    all_parser = subparsers.add_parser("classify-all")
    all_parser.add_argument("--codex", action="store_true", help="Also run Codex on unresolved products.")
    all_parser.add_argument("--codex-limit", type=int, default=0)
    all_parser.add_argument("--batch-size", type=int, default=10)
    all_parser.add_argument("--passes", type=int, default=2)
    all_parser.add_argument("--retries", type=int, default=2)
    all_parser.add_argument("--model", default="gpt-5.6-sol")
    all_parser.add_argument("--reasoning-effort", default="medium")
    all_parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    all_parser.add_argument("--workers", type=int, default=1)
    all_parser.add_argument("--sync-run-id", type=int)

    subparsers.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)

    if args.command == "migrate-schema":
        backup = create_backup(db_path)
        with connect(db_path) as conn:
            with conn:
                ensure_classification_schema(conn)
        print(f"Migrated {db_path}; backup at {backup}")
        return 0

    with connect(db_path) as conn:
        with conn:
            ensure_classification_schema(conn)

        if args.command == "classify-rules":
            with conn:
                count = classify_rules(conn, limit=args.limit, force=args.force, sync_run_id=args.sync_run_id)
            print(f"Rule-classified {count} products")
            return 0

        if args.command == "classify-codex":
            count = classify_codex(
                conn,
                limit=args.limit,
                batch_size=args.batch_size,
                passes=args.passes,
                retries=args.retries,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                codex_bin=args.codex_bin,
                workers=args.workers,
                sync_run_id=args.sync_run_id,
            )
            print(f"Codex-classified {count} products")
            return 0

        if args.command == "classify-all":
            with conn:
                rule_count = classify_rules(conn, sync_run_id=args.sync_run_id)
            print(f"Rule-classified {rule_count} products")
            if args.codex:
                count = classify_codex(
                    conn,
                    limit=args.codex_limit,
                    batch_size=args.batch_size,
                    passes=args.passes,
                    retries=args.retries,
                    model=args.model,
                    reasoning_effort=args.reasoning_effort,
                    codex_bin=args.codex_bin,
                    workers=args.workers,
                    sync_run_id=args.sync_run_id,
                )
                print(f"Codex-classified {count} products")
            return 0

        if args.command == "status":
            for row in status(conn):
                reason = f" {row['vegan_reason']}" if row["vegan_reason"] else ""
                print(f"{row['vegan_status']}{reason}: {row['count']}")
            return 0

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
