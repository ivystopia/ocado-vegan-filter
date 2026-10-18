#!/usr/bin/env python3
"""Restore published decisions, then apply explicit source-bound unknown reviews.

No scraping, automatic classification, or model calls. Preview is the default.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "audit/published-baseline-restart/published-baseline.json"
BASELINE_SHA256 = "f57942f295b0346db033dcf7263aca96de1eac099482e52a64b54db8023fa63e"
VERSION = "published-baseline-1.7.2-reviewed-v1"
SOURCE_FIELDS = """id name brand url pack_size official_vegan alcohol ingredients allergens
 detailed_description dietary_information features further_description brand_marketing
 manufacturer_marketing manufacturer producer preparation_and_usage cooking_guidelines
 other_information package_type recycling_information specification storage recipes
 serving_suggestions country_of_origin country_of_packaging current_vintage
 vinification_details""".split()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def baseline_decisions():
    data = BASELINE_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != BASELINE_SHA256:
        raise ValueError("Published baseline manifest has changed")
    manifest = json.loads(data)
    names = set(manifest["name_reason_ids"])
    result = {}
    for name, ids in manifest["sets"].items():
        for pid in ids:
            if pid in result:
                raise ValueError("Overlapping published sets")
            reason = (
                "tagged"
                if name == "OFFICIAL_VEGAN_PRODUCT_IDS"
                else (
                    "ingredients"
                    if name == "INGREDIENTS_VEGAN_PRODUCT_IDS"
                    else ("name" if pid in names else "manufacturer")
                )
            )
            result[pid] = ("nonvegan", None) if name == "KNOWN_NON_VEGAN_PRODUCT_IDS" else ("vegan", reason)
    return result


def source_context(conn, pid):
    row = conn.execute("SELECT " + ",".join(SOURCE_FIELDS) + " FROM products WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise ValueError(f"Missing product {pid}")
    product = dict(zip(SOURCE_FIELDS, row))
    # Some suppliers place explicit vegan declarations in nutrition-table text.
    # Preserve that source only when present; existing source-bound reviews with
    # no such text retain their exact context/hash.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='manufacturer_vegan_evidence'").fetchone():
        nutrition = [r[0] for r in conn.execute(
            "SELECT DISTINCT content FROM manufacturer_vegan_evidence WHERE product_id=? AND field_title='nutritionalData' ORDER BY content", (pid,)
        )]
        if nutrition:
            product["nutrition_declaration"] = "\n".join(nutrition)
    return {
        "product": product,
        "flags": [
            r[0] for r in conn.execute("SELECT flag FROM product_flags WHERE product_id=? ORDER BY flag", (pid,))
        ],
        "categories": [
            r[0]
            for r in conn.execute(
                "SELECT category_path FROM product_categories WHERE product_id=? ORDER BY category_path", (pid,)
            )
        ],
    }


def active(conn):
    return bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='published_baseline_state' AND type='table'").fetchone()
    )


def validate(conn):
    """Export contract: exact published classifications plus reviewed additions."""
    state = conn.execute("SELECT manifest_sha256 FROM published_baseline_state").fetchall()
    if state != [(BASELINE_SHA256,)]:
        raise ValueError("Invalid published baseline binding")
    expected = baseline_decisions().copy()
    for pid, record_json, audit_id in conn.execute(
        "SELECT product_id, review_json, audit_id FROM reviewed_vegan_additions"
    ):
        if pid in expected:
            raise ValueError(f"Review overwrites published classification: {pid}")
        review = json.loads(record_json)
        validate_review(conn, review)
        if review["product_id"] != pid:
            raise ValueError("Review ID mismatch")
        audit = conn.execute(
            "SELECT product_id, vegan_status, vegan_reason, context_hash, evidence_json FROM product_vegan_classification_audit WHERE id=?",
            (audit_id,),
        ).fetchone()
        if audit != (pid, "vegan", review["reason"], review["context_hash"], record_json):
            raise ValueError(f"Review audit linkage mismatch: {pid}")
        expected[pid] = ("vegan", review["reason"])
    from reviewed_safety import validated_decisions
    safety = validated_decisions(conn)
    if set(safety) & set(expected):
        raise ValueError("Safety review cannot overwrite protected published/reviewed classifications")
    expected.update(safety)
    actual = {
        pid: (status, reason)
        for pid, status, reason in conn.execute("SELECT id, vegan_status, vegan_reason FROM products")
    }
    if set(expected) - set(actual):
        raise ValueError("Published/reviewed products missing from database")
    mismatch = [pid for pid, value in actual.items() if value != expected.get(pid, ("unknown", None))]
    if mismatch:
        raise ValueError(f'Unreviewed classification changes ({len(mismatch)}): {", ".join(mismatch[:10])}')


def validate_review(conn, review):
    required = {
        "product_id",
        "reason",
        "context",
        "context_hash",
        "rationale",
        "support",
        "reviewed_by",
        "reviewed_on",
        "scope",
    }
    if set(review) != required or review["reason"] not in {"tagged", "manufacturer", "name", "ingredients"}:
        raise ValueError("Invalid review schema")
    if review["scope"] != "complete product; evidenced material concerns only":
        raise ValueError("Invalid review scope")
    if not all(
        isinstance(review[k], str) and review[k].strip()
        for k in ("product_id", "rationale", "reviewed_by", "reviewed_on")
    ):
        raise ValueError("Missing review explanation")
    context = source_context(conn, review["product_id"])
    if context != review["context"] or digest(context) != review["context_hash"]:
        raise ValueError(f'Stale reviewed evidence: {review["product_id"]}')
    tagged = context["product"]["official_vegan"] == 1 or "vegan" in context["flags"]
    if (review["reason"] == "tagged") != tagged:
        raise ValueError("Reviewed reason must preserve applicable official-tag precedence")
    if not isinstance(review["support"], list) or not review["support"]:
        raise ValueError("Review needs source quotations")
    for quote in review["support"]:
        if set(quote) != {"field", "quote"} or not isinstance(quote["quote"], str) or not quote["quote"].strip():
            raise ValueError("Invalid supporting quotation")
        text = context["product"].get(quote["field"])
        if not isinstance(text, str) or quote["quote"] not in text:
            raise ValueError("Supporting quotation is not in stored evidence")
    # This validates provenance, not semantic truth. Each record is explicitly reviewed.


def new_run(conn, mode, count, *, classifier_version=VERSION):
    return conn.execute(
        "INSERT INTO vegan_classification_runs(started_at_epoch,status,mode,classifier_version,total_products) VALUES (?,'running',?,?,?)",
        (int(time.time()), mode, classifier_version, count),
    ).lastrowid


def write_decision(conn, run, pid, status, reason, evidence, summary, *, source="rule", classifier_version=VERSION):
    if source not in {"rule", "codex"}:
        raise ValueError("Invalid reviewed decision source")
    stamp = int(time.time())
    context_hash = evidence.get("context_hash")
    aid = conn.execute(
        """INSERT INTO product_vegan_classification_audit
        (run_id,product_id,source,vegan_status,vegan_reason,confidence,summary,evidence_json,
         context_hash,classifier_version,classified_at_epoch)
        VALUES (?,?,?,?,?,'high',?,?,?,?,?)""",
        (run, pid, source, status, reason, summary, encoded(evidence), context_hash, classifier_version, stamp),
    ).lastrowid
    values = {
        "vegan_status": status,
        "vegan_reason": reason,
        "vegan_classifier_version": classifier_version,
        "vegan_classified_at_epoch": stamp,
        "vegan_policy_version": classifier_version,
        "vegan_evidence_hash": context_hash,
        "vegan_positive_block": 0,
        "vegan_audit_id": aid,
    }
    columns = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    values = {k: v for k, v in values.items() if k in columns}
    conn.execute(
        "UPDATE products SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", (*values.values(), pid)
    )
    return aid


def finish_run(conn, run, count, *, source="rule"):
    count_column = {"rule": "rule_classified", "codex": "llm_classified"}[source]
    conn.execute(
        f"UPDATE vegan_classification_runs SET status='completed',completed_at_epoch=?,{count_column}=? WHERE id=?",
        (int(time.time()), count, run),
    )


def restore(conn, backup_path):
    """Caller owns transaction. Save all replaced fields before writing any decision."""
    if active(conn):
        raise ValueError("Baseline already restored; refusing to discard subsequent reviews")
    expected = baseline_decisions()
    columns = ["id"] + [r[1] for r in conn.execute("PRAGMA table_info(products)") if r[1].startswith("vegan_")]
    before = [
        dict(zip(columns, row)) for row in conn.execute("SELECT " + ",".join(columns) + " FROM products ORDER BY id")
    ]
    if set(expected) - {r["id"] for r in before}:
        raise ValueError("Published products missing from database")
    backup = {"baseline_manifest_sha256": BASELINE_SHA256, "before": before}
    with backup_path.open("xb") as file:
        with gzip.GzipFile(fileobj=file, mode="wb") as stream:
            stream.write(encoded(backup).encode())
    with gzip.open(backup_path, "rt") as stream:
        if json.load(stream) != backup:
            raise ValueError("Classification backup verification failed")
    conn.execute(
        "CREATE TABLE published_baseline_state(manifest_sha256 TEXT NOT NULL, backup_path TEXT NOT NULL, backup_sha256 TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO published_baseline_state VALUES(?,?,?)",
        (BASELINE_SHA256, str(backup_path.resolve()), hashlib.sha256(backup_path.read_bytes()).hexdigest()),
    )
    conn.execute(
        "CREATE TABLE reviewed_vegan_additions(product_id TEXT PRIMARY KEY REFERENCES products(id), review_json TEXT NOT NULL, audit_id INTEGER NOT NULL)"
    )
    run = new_run(conn, "published-baseline-restore", len(before))
    for row in before:
        status, reason = expected.get(row["id"], ("unknown", None))
        write_decision(
            conn,
            run,
            row["id"],
            status,
            reason,
            {
                "published_release": "1.7.2",
                "baseline_sha256": BASELINE_SHA256,
                "previous_audit_id": row.get("vegan_audit_id"),
                "restoration_not_fresh_assessment": True,
            },
            "User-requested restoration of published classification; absent IDs remain unknown.",
        )
    finish_run(conn, run, len(before))
    validate(conn)
    return {"run_id": run, "restored_products": len(before), "published_classified": len(expected)}


def apply_reviews(conn, reviews, *, source="rule"):
    """Caller owns transaction. Validate every record before the first write."""
    validate(conn)
    ids = [r["product_id"] for r in reviews]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate review IDs")
    for review in reviews:
        validate_review(conn, review)
        row = conn.execute(
            "SELECT vegan_status,vegan_reason FROM products WHERE id=?", (review["product_id"],)
        ).fetchone()
        if row != ("unknown", None):
            raise ValueError("Only unknown products may be promoted")
        if "vegan_positive_block" in {r[1] for r in conn.execute("PRAGMA table_info(products)")} and conn.execute("SELECT 1 FROM products WHERE id=? AND vegan_positive_block=1", (review["product_id"],)).fetchone():
            raise ValueError("Resolve the reviewed conflict before promoting this product")
    run = new_run(conn, "reviewed-unknown-additions", len(reviews))
    for review in reviews:
        aid = write_decision(
            conn,
            run,
            review["product_id"],
            "vegan",
            review["reason"],
            review,
            review["rationale"],
            source=source,
        )
        conn.execute(
            "INSERT INTO reviewed_vegan_additions VALUES(?,?,?)", (review["product_id"], encoded(review), aid)
        )
    finish_run(conn, run, len(reviews), source=source)
    validate(conn)
    return {"run_id": run, "added_vegan": len(reviews)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "ocado_products.sqlite")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    if args.restore == bool(args.reviews) or (args.restore and args.apply and not args.backup):
        parser.error("Choose --restore (with --backup for application) or --reviews")
    with closing(sqlite3.connect(f'file:{args.db}?mode={"rw" if args.apply else "ro"}', uri=True)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        from update_userscript_allowlists import validate_export_readiness

        validate_export_readiness(conn)
        if args.restore:
            if args.apply:
                result = restore(conn, args.backup)
            else:
                expected = baseline_decisions()
                rows = list(conn.execute("SELECT id,vegan_status,vegan_reason FROM products"))
                result = {
                    "preview": True,
                    "products": len(rows),
                    "changed_status_or_reason": sum(
                        (s, r) != expected.get(pid, ("unknown", None)) for pid, s, r in rows
                    ),
                }
        else:
            reviews = json.loads(args.reviews.read_text())["reviews"]
            if args.apply:
                result = apply_reviews(conn, reviews)
            else:
                validate(conn)
                for review in reviews:
                    validate_review(conn, review)
                result = {"preview": True, "proposed_vegan": len(reviews)}
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
