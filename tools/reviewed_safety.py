#!/usr/bin/env python3
"""Record source-reviewed safety findings for unknown products; preview by default.

This is not a classifier or a revival of historical conflict records. The reviewer
establishes the finding; this module validates source provenance and audit linkage.
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

import published_baseline as policy

VERSION = "reviewed-safety-v1"
TABLE = "reviewed_safety_decisions"


def validate_review(conn, review):
    required = {"product_id", "status", "finding", "context", "context_hash", "rationale",
                "support", "reviewed_by", "reviewed_on", "scope"}
    if set(review) != required:
        raise ValueError("Invalid safety review schema")
    if (review["status"], review["finding"]) not in {
        ("nonvegan", "confirmed_animal_ingredient"), ("unknown", "unresolved_evidence_conflict")
    }:
        raise ValueError("Invalid safety finding/status")
    if review["scope"] != "complete product; evidenced material concerns only":
        raise ValueError("Invalid safety review scope")
    for key in ("product_id", "rationale", "reviewed_by", "reviewed_on"):
        if not isinstance(review[key], str) or not review[key].strip():
            raise ValueError("Missing safety review explanation")
    context = policy.source_context(conn, review["product_id"])
    if context != review["context"] or policy.digest(context) != review["context_hash"]:
        raise ValueError("Stale safety evidence: " + review["product_id"])
    if not isinstance(review["support"], list) or not review["support"]:
        raise ValueError("Safety finding needs source quotations")
    fields = set()
    for item in review["support"]:
        if not isinstance(item, dict) or set(item) != {"field", "quote"}:
            raise ValueError("Invalid safety quotation")
        text = context["product"].get(item["field"])
        if not isinstance(item["quote"], str) or not item["quote"].strip() or not isinstance(text, str) or item["quote"] not in text:
            raise ValueError("Safety quotation not in stored evidence")
        fields.add(item["field"])
    if review["status"] == "nonvegan" and "ingredients" not in fields:
        raise ValueError("Confirmed animal ingredient requires ingredient evidence")
    if review["status"] == "unknown" and len(fields) < 2:
        raise ValueError("Unresolved conflict requires both source fields")


def validated_decisions(conn):
    """Read-only export contract for active safety decisions and promotion blocks."""
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    decisions, blocked = {}, set()
    if exists:
        for pid, record, aid, previous in conn.execute(f"SELECT product_id,review_json,audit_id,previous_json FROM {TABLE}"):
            review = json.loads(record)
            validate_review(conn, review)
            if review["product_id"] != pid:
                raise ValueError("Safety review ID mismatch")
            audit = conn.execute(
                "SELECT product_id,vegan_status,vegan_reason,context_hash,evidence_json FROM product_vegan_classification_audit WHERE id=?",
                (aid,),
            ).fetchone()
            evidence = dict(review, previous_decision=json.loads(previous))
            if audit != (pid, review["status"], None, review["context_hash"], policy.encoded(evidence)):
                raise ValueError("Safety audit linkage mismatch: " + pid)
            current = conn.execute(
                "SELECT vegan_status,vegan_reason,vegan_positive_block,vegan_audit_id,vegan_evidence_hash FROM products WHERE id=?", (pid,)
            ).fetchone()
            block = int(review["status"] == "unknown")
            if current != (review["status"], None, block, aid, review["context_hash"]):
                raise ValueError("Safety canonical decision mismatch: " + pid)
            decisions[pid] = (review["status"], None)
            if block:
                blocked.add(pid)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    if "vegan_positive_block" in columns:
        actual = {r[0] for r in conn.execute("SELECT id FROM products WHERE vegan_positive_block != 0")}
        if actual != blocked:
            raise ValueError("Unreviewed or missing promotion blocks")
    return decisions


def apply_reviews(conn, reviews):
    """Caller owns the transaction; all inputs are checked before any write."""
    policy.validate(conn)
    ids = [r["product_id"] for r in reviews]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Missing or duplicate safety review IDs")
    before = {}
    for review in reviews:
        validate_review(conn, review)
        cursor = conn.execute("SELECT id,vegan_status,vegan_reason,vegan_positive_block,vegan_audit_id,vegan_evidence_hash,vegan_classifier_version,vegan_policy_version,vegan_classified_at_epoch FROM products WHERE id=?", (review["product_id"],))
        row = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))
        if (row["vegan_status"], row["vegan_reason"], row["vegan_positive_block"]) != ("unknown", None, 0):
            raise ValueError("Safety review requires an unblocked unknown product")
        before[review["product_id"]] = row
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE}(product_id TEXT PRIMARY KEY REFERENCES products(id),review_json TEXT NOT NULL,audit_id INTEGER NOT NULL,previous_json TEXT NOT NULL)")
    run = policy.new_run(conn, "reviewed-safety-findings", len(reviews), classifier_version=VERSION)
    for review in reviews:
        pid = review["product_id"]
        previous = before[pid]
        evidence = dict(review, previous_decision=previous)
        aid = policy.write_decision(conn, run, pid, review["status"], None, evidence, review["rationale"], classifier_version=VERSION)
        conn.execute("UPDATE products SET vegan_positive_block=? WHERE id=?", (int(review["status"] == "unknown"), pid))
        conn.execute(f"INSERT INTO {TABLE} VALUES(?,?,?,?)", (pid, policy.encoded(review), aid, policy.encoded(previous)))
    policy.finish_run(conn, run, len(reviews))
    policy.validate(conn)
    return {"run_id": run, "decisions": {r["product_id"]: r["status"] for r in reviews}, "previous_decisions": before}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=policy.ROOT / "ocado_products.sqlite")
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    reviews = json.loads(args.reviews.read_text())["reviews"]
    if args.apply and not args.backup:
        parser.error("--apply requires a new --backup path")
    with closing(sqlite3.connect(f"file:{args.db}?mode={'rw' if args.apply else 'ro'}", uri=True)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        try:
            from update_userscript_allowlists import validate_export_readiness
            validate_export_readiness(conn)
            policy.validate(conn)
            for review in reviews:
                validate_review(conn, review)
            if args.apply:
                # A WAL-consistent logical backup of every row this transaction may change.
                saved = []
                for review in reviews:
                    cursor = conn.execute("SELECT * FROM products WHERE id=?", (review["product_id"],))
                    row = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))
                    saved.append({k: v for k, v in row.items() if k == "id" or k.startswith("vegan_")})
                backup = {"created_at_epoch": int(time.time()), "reviews_sha256": hashlib.sha256(args.reviews.read_bytes()).hexdigest(), "previous_decisions": saved}
                with args.backup.open("xb") as raw:
                    with gzip.GzipFile(fileobj=raw, mode="wb") as stream:
                        stream.write(policy.encoded(backup).encode())
                with gzip.open(args.backup, "rt") as stream:
                    if json.load(stream) != backup:
                        raise ValueError("Safety backup verification failed")
                result = apply_reviews(conn, reviews)
                conn.commit()
                result["backup"] = {"path": str(args.backup), "sha256": hashlib.sha256(args.backup.read_bytes()).hexdigest()}
                print(json.dumps(result, indent=2))
            else:
                print(json.dumps({"preview": True, "decisions": {r["product_id"]: r["status"] for r in reviews}}, indent=2))
        except BaseException:
            conn.rollback()
            raise


if __name__ == "__main__":
    main()
