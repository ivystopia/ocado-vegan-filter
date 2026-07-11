#!/usr/bin/env python3
"""Regenerate Ocado Vegan Filter allowlists from the SQLite source of truth."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "ocado_products.sqlite"
DEFAULT_USERSCRIPT = REPO_ROOT / "ocado-vegan-filter.user.js"

SET_QUERIES = {
    "OFFICIAL_VEGAN_PRODUCT_IDS": "vegan_status = 'vegan' AND vegan_reason = 'tagged'",
    "MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS": "vegan_status = 'vegan' AND vegan_reason IN ('manufacturer', 'name')",
    "INGREDIENTS_VEGAN_PRODUCT_IDS": "vegan_status = 'vegan' AND vegan_reason = 'ingredients'",
    "KNOWN_NON_VEGAN_PRODUCT_IDS": "vegan_status = 'nonvegan'",
}


def validate_official_tag_precedence(conn: sqlite3.Connection) -> None:
    conflicts = [
        row[0]
        for row in conn.execute(
            """
            SELECT p.id
            FROM products AS p
            WHERE (
                p.official_vegan = 1
                OR EXISTS (
                    SELECT 1 FROM product_flags AS f
                    WHERE f.product_id = p.id AND f.flag = 'vegan'
                )
              )
              AND (
                p.vegan_status IS NOT 'vegan'
                OR p.vegan_reason IS NOT 'tagged'
              )
            ORDER BY CAST(p.id AS INTEGER), p.id
            """
        )
    ]
    if conflicts:
        sample = " ".join(conflicts[:20])
        raise ValueError(
            f"Refusing to export {len(conflicts)} products whose official Ocado vegan tag "
            f"conflicts with the canonical classification: {sample}"
        )


def load_allowlists(conn: sqlite3.Connection) -> dict[str, list[str]]:
    validate_official_tag_precedence(conn)
    result = {}
    for name, status_clause in SET_QUERIES.items():
        rows = conn.execute(
            f"SELECT id FROM products WHERE {status_clause} ORDER BY CAST(id AS INTEGER), id"
        )
        result[name] = [row[0] for row in rows]
    return result


def extract_set(source: str, name: str) -> set[str]:
    marker = f"const {name} = new Set("
    start = source.index("`", source.index(marker)) + 1
    end = source.index("`", start)
    return set(re.findall(r"\b\d+\b", source[start:end]))


def format_ids(ids: list[str]) -> str:
    return "\n".join("  " + " ".join(ids[index : index + 8]) for index in range(0, len(ids), 8))


def replace_set(source: str, name: str, ids: list[str]) -> str:
    marker = f"const {name} = new Set("
    start = source.index("`", source.index(marker)) + 1
    end = source.index("`", start)
    desired = set(ids)
    retained: set[str] = set()
    lines = []
    for line in source[start:end].splitlines():
        existing = re.findall(r"\b\d+\b", line)
        kept = [product_id for product_id in existing if product_id in desired]
        retained.update(kept)
        if kept:
            lines.append("  " + " ".join(kept))
    new_ids = sorted(desired - retained, key=int)
    if new_ids:
        lines.extend(format_ids(new_ids).splitlines())
    return source[:start] + "\n" + "\n".join(lines) + "\n  " + source[end:]


def update_counts(source: str, allowlists: dict[str, list[str]]) -> str:
    official = len(allowlists["OFFICIAL_VEGAN_PRODUCT_IDS"])
    manufacturer = len(allowlists["MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS"])
    ingredients = len(allowlists["INGREDIENTS_VEGAN_PRODUCT_IDS"])
    nonvegan = len(allowlists["KNOWN_NON_VEGAN_PRODUCT_IDS"])
    total = official + manufacturer + ingredients
    replacements = {
        "Recognised vegan product IDs": total,
        "Official Ocado vegan product IDs": official,
        "Additional vegan product IDs added by this script": total - official,
        "Manufacturer/name evidence product IDs": manufacturer,
        "Ingredients evidence product IDs": ingredients,
        "Known non-vegan product IDs": nonvegan,
    }
    for label, value in replacements.items():
        source, count = re.subn(rf"(\* {re.escape(label)}: )[\d,]+", rf"\g<1>{value:,}", source, count=1)
        if count != 1:
            raise ValueError(f"Could not update count line: {label}")
    return source


def regenerate(source: str, allowlists: dict[str, list[str]], version: str | None = None) -> str:
    for name, ids in allowlists.items():
        source = replace_set(source, name, ids)
    source = update_counts(source, allowlists)
    if version:
        source, count = re.subn(r"(?m)^(// @version\s+)\S+$", rf"\g<1>{version}", source, count=1)
        if count != 1:
            raise ValueError("Could not update userscript version")
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--userscript", type=Path, default=DEFAULT_USERSCRIPT)
    parser.add_argument("--version")
    args = parser.parse_args()

    source = args.userscript.read_text(encoding="utf-8")
    before = {name: extract_set(source, name) for name in SET_QUERIES}
    with sqlite3.connect(f"file:{args.db}?mode=ro", uri=True) as conn:
        allowlists = load_allowlists(conn)
    updated = regenerate(source, allowlists, args.version)
    args.userscript.write_text(updated, encoding="utf-8")

    for name, ids in allowlists.items():
        after = set(ids)
        print(f"{name}: {len(after)} (added {len(after - before[name])}, removed {len(before[name] - after)})")
        if after - before[name]:
            print("  added: " + " ".join(sorted(after - before[name], key=int)))
        if before[name] - after:
            print("  removed: " + " ".join(sorted(before[name] - after, key=int)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
