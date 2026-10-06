#!/usr/bin/env python3
"""Regenerate Ocado Vegan Filter allowlists from the SQLite source of truth."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path


import vegan_policy as policy


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "ocado_products.sqlite"
DEFAULT_USERSCRIPT = REPO_ROOT / "ocado-vegan-filter.user.js"

SET_QUERIES = {
    "OFFICIAL_VEGAN_PRODUCT_IDS": "vegan_status = 'vegan' AND vegan_reason = 'tagged'",
    "MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS": "vegan_status = 'vegan' AND vegan_reason IN ('manufacturer', 'name')",
    "INGREDIENTS_VEGAN_PRODUCT_IDS": "vegan_status = 'vegan' AND vegan_reason = 'ingredients'",
    "KNOWN_NON_VEGAN_PRODUCT_IDS": "vegan_status = 'nonvegan'",
    "BLOCKED_VEGAN_PROMOTION_PRODUCT_IDS": "vegan_status = 'unknown' AND vegan_positive_block = 1",
}


def validate_export_readiness(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "sync_context_baseline" in tables:
        raise ValueError("Refusing to export an incomplete catalogue sync; rerun the sync successfully first")
    if "sync_runs" in tables:
        latest = conn.execute("SELECT id, status FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        if latest and latest[1] != "completed":
            raise ValueError(f"Refusing to export sync run {latest[0]} with status {latest[1]!r}")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
    if "current_on_ocado" in columns:
        unfinished = conn.execute(
            "SELECT COUNT(*) FROM products WHERE current_on_ocado = 1 AND vegan_status IS NULL"
        ).fetchone()[0]
        if unfinished:
            raise ValueError(f"Refusing to export {unfinished} unclassified current products")


def validate_policy_decisions(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
    required = {"vegan_policy_version", "vegan_evidence_hash", "vegan_positive_block", "vegan_audit_id"}
    if not required <= columns:
        raise ValueError("Refusing to export before whole-product shadow migration: missing policy provenance")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "product_vegan_classification_audit" not in tables:
        raise ValueError("Refusing to export without classification audit provenance")
    invalid = conn.execute("""
        SELECT p.id FROM products p LEFT JOIN product_vegan_classification_audit a ON a.id=p.vegan_audit_id
        WHERE p.vegan_status='vegan' AND (
          p.vegan_policy_version IS NOT ? OR length(p.vegan_evidence_hash) IS NOT 64 OR p.vegan_positive_block IS NOT 0
          OR a.product_id IS NOT p.id OR a.vegan_status IS NOT p.vegan_status OR a.vegan_reason IS NOT p.vegan_reason
          OR a.policy_version IS NOT ? OR a.context_hash IS NOT p.vegan_evidence_hash
          OR p.vegan_reason NOT IN ('tagged','manufacturer','name','ingredients')
          OR a.knowledge_version IS NOT ? OR a.schema_version IS NOT ?
        ) ORDER BY p.id
    """, (policy.POLICY_VERSION, policy.POLICY_VERSION, policy.KNOWLEDGE_VERSION, policy.SCHEMA_VERSION)).fetchall()
    if invalid:
        raise ValueError(f"Refusing to export {len(invalid)} unsupported/stale vegan decisions: {' '.join(row[0] for row in invalid[:20])}")
    invalid_blocks = conn.execute("SELECT COUNT(*) FROM products WHERE vegan_positive_block=1 AND (vegan_status IS NOT 'unknown' OR vegan_policy_version IS NOT ?)", (policy.POLICY_VERSION,)).fetchone()[0]
    if invalid_blocks:
        raise ValueError("Refusing to export inconsistent promotion blocks")
    # Audit-to-row agreement alone cannot detect an edited ingredient field.
    # Rebuild only positive contexts, read-only, before permitting export.
    import classify_ocado_vegan as classifier
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""SELECT p.id,p.vegan_evidence_hash,p.vegan_status,p.vegan_reason,a.source,a.confidence,a.summary,a.evidence_json
                               FROM products p JOIN product_vegan_classification_audit a ON a.id=p.vegan_audit_id WHERE p.vegan_status='vegan'""").fetchall()
        for start in range(0,len(rows),500):
            for row, loaded in zip(rows[start:start+500], classifier.load_product_contexts(conn,[r['id'] for r in rows[start:start+500]])):
                context = classifier.product_context_for_llm(loaded)
                if classifier.context_hash(context) != row["vegan_evidence_hash"]:
                    raise ValueError(f"Refusing to export stale product evidence: {row['id']}")
                candidate = classifier.ClassificationResult(product_id=row['id'],vegan_status=row['vegan_status'],vegan_reason=row['vegan_reason'],
                    confidence=row['confidence'],summary=row['summary'],evidence=json.loads(row['evidence_json']),source=row['source'])
                accepted = classifier.apply_policy_gate(loaded,candidate)
                if (accepted.vegan_status,accepted.vegan_reason)!=(row['vegan_status'],row['vegan_reason']):
                    raise ValueError(f"Refusing to export a positive rejected by the current policy: {row['id']}")
    finally:
        conn.row_factory = previous_factory


# Compatibility name for callers; unconditional tag precedence is superseded.
validate_official_tag_precedence = validate_policy_decisions


def load_allowlists(conn: sqlite3.Connection) -> dict[str, list[str]]:
    validate_export_readiness(conn)
    validate_policy_decisions(conn)
    result = {}
    for name, status_clause in SET_QUERIES.items():
        rows = conn.execute(
            f"SELECT id FROM products WHERE {status_clause} ORDER BY CAST(id AS INTEGER), id"
        )
        result[name] = [row[0] for row in rows]
    positives = set().union(*(set(result[name]) for name in ("OFFICIAL_VEGAN_PRODUCT_IDS", "MANUFACTURER_OR_NAME_VEGAN_PRODUCT_IDS", "INGREDIENTS_VEGAN_PRODUCT_IDS")))
    if positives & (set(result["KNOWN_NON_VEGAN_PRODUCT_IDS"]) | set(result["BLOCKED_VEGAN_PROMOTION_PRODUCT_IDS"])):
        raise ValueError("Exported positive and excluded sets overlap")
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
        "Additional vegan product IDs recognised by this script": total - official,
        "Manufacturer/name evidence product IDs": manufacturer,
        "Ingredients evidence product IDs": ingredients,
        "Known non-vegan product IDs": nonvegan,
        "Blocked vegan promotion product IDs": len(allowlists["BLOCKED_VEGAN_PROMOTION_PRODUCT_IDS"]),
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report allowlist changes without writing the userscript.",
    )
    args = parser.parse_args()

    source = args.userscript.read_text(encoding="utf-8")
    before = {name: extract_set(source, name) for name in SET_QUERIES}
    with closing(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)) as conn:
        allowlists = load_allowlists(conn)
    updated = regenerate(source, allowlists, args.version)
    if not args.dry_run:
        args.userscript.write_text(updated, encoding="utf-8")

    for name, ids in allowlists.items():
        after = set(ids)
        print(f"{name}: {len(after)} (added {len(after - before[name])}, removed {len(before[name] - after)})")
        if after - before[name]:
            print("  added: " + " ".join(sorted(after - before[name], key=int)))
        if before[name] - after:
            print("  removed: " + " ".join(sorted(before[name] - after, key=int)))
    if args.dry_run:
        print("Dry run: userscript was not changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
