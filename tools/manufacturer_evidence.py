"""Retained, product-bound manufacturer website evidence (no network access)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import re
import time
from typing import Any


PUBLISHER_FIELDS = ("manufacturer", "producer", "return_to_address")


def record_assertion(conn: sqlite3.Connection, *, run_id: int, product_id: str, source_id: str,
                     catalogue_binding_hash: str, statement: str, scope_match: str,
                     accepted_at: str, status: str, review_policy_version: str) -> None:
    """Store a reviewed assertion without extrapolation; caller owns transaction.

    This is storage validation, not semantic review. Scope/identity must already
    have been assessed independently against the complete retained source.
    """
    if status not in {"vegan", "nonvegan"} or not scope_match.strip() or len(catalogue_binding_hash) != 64 or not review_policy_version:
        raise ValueError("Invalid assertion status, scope or product binding")
    source = conn.execute("SELECT source_text FROM manufacturer_website_sources WHERE id=?", (source_id,)).fetchone()
    normalize = lambda value: re.sub(r"\s+", " ", value).strip()
    if not source or not statement.strip() or normalize(statement) not in normalize(source[0]):
        raise ValueError("Assertion quote is absent from the retained source")
    conn.execute("""INSERT INTO manufacturer_website_evidence
        (run_id,product_id,source_id,catalogue_binding_hash,statement,scope_match,accepted_at,assertion_status,review_policy_version)
        VALUES (?,?,?,?,?,?,?,?,?)""", (run_id,product_id,source_id,catalogue_binding_hash,statement,scope_match,accepted_at,status,review_policy_version))
    assertion_event(conn,run_id=run_id,product_id=product_id,source_id=source_id,from_state=None,to_state='active',
                    reason='Fresh source/scope review: '+review_policy_version)


def assertion_event(conn, *, run_id, product_id, source_id, from_state, to_state, reason, superseded_by=None):
    conn.execute('''INSERT INTO manufacturer_assertion_events
        (run_id,product_id,source_id,from_state,to_state,reason,superseded_by,at_epoch)
        VALUES (?,?,?,?,?,?,?,?)''',(run_id,product_id,source_id,from_state,to_state,reason,superseded_by,int(time.time())))


def transition_assertion(conn: sqlite3.Connection, *, run_id: int, product_id: str, source_id: str,
                         state: str, reason: str, superseded_by: str | None = None) -> None:
    """Preserve withdrawn evidence and invalidate its canonical dependent row."""
    if not reason.strip() or state not in {"revoked", "superseded"} or (state == "superseded" and not superseded_by):
        raise ValueError("A withdrawal needs revoked or an identified superseding assertion")
    changed = conn.execute("""UPDATE manufacturer_website_evidence SET assertion_state=?,superseded_by=?
        WHERE run_id=? AND product_id=? AND source_id=? AND assertion_state='active'""",
        (state,superseded_by,run_id,product_id,source_id)).rowcount
    if changed != 1:
        raise ValueError("No unique active assertion matches the withdrawal")
    assertion_event(conn,run_id=run_id,product_id=product_id,source_id=source_id,from_state='active',to_state=state,
                    reason=reason,superseded_by=superseded_by)
    conn.execute("""UPDATE products SET vegan_status=NULL,vegan_reason=NULL,vegan_policy_version=NULL,
        vegan_evidence_hash=NULL,vegan_audit_id=NULL,vegan_positive_block=1 WHERE id=?""", (product_id,))


def binding_hash(
    catalogue_payload: dict[str, Any], pack_size: str | None, *,
    manufacturer: str | None = None, producer: str | None = None,
    return_to_address: str | None = None,
) -> str:
    """Bind a claim to the reviewed catalogue identity, recipe and pack format."""
    value = {"catalogue": catalogue_payload, "pack_size": pack_size,
             "manufacturer": manufacturer or None, "producer": producer or None,
             "return_to_address": return_to_address or None}
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS manufacturer_research_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          status TEXT NOT NULL,
          scope_json TEXT NOT NULL,
          bundle_hash TEXT NOT NULL UNIQUE,
          model TEXT NOT NULL,
          reasoning_effort TEXT NOT NULL,
          prompt_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manufacturer_research_products (
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          current_at_start INTEGER NOT NULL,
          catalogue_context_hash TEXT NOT NULL,
          research_outcome TEXT NOT NULL,
          review_outcome TEXT NOT NULL,
          research_json TEXT NOT NULL,
          review_json TEXT,
          PRIMARY KEY (run_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS manufacturer_website_sources (
          id TEXT PRIMARY KEY,
          url TEXT NOT NULL,
          title TEXT NOT NULL,
          retrieved_at TEXT NOT NULL,
          official_identity TEXT NOT NULL,
          market_scope TEXT NOT NULL,
          content_sha256 TEXT NOT NULL,
          source_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manufacturer_website_evidence (
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          source_id TEXT NOT NULL,
          catalogue_binding_hash TEXT NOT NULL,
          statement TEXT NOT NULL,
          scope_match TEXT NOT NULL,
          accepted_at TEXT NOT NULL,
          assertion_status TEXT NOT NULL DEFAULT 'vegan',
          assertion_state TEXT NOT NULL DEFAULT 'active',
          superseded_by TEXT,
          review_policy_version TEXT,
          PRIMARY KEY (run_id, product_id, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_manufacturer_website_evidence_product
          ON manufacturer_website_evidence(product_id);
        CREATE TABLE IF NOT EXISTS manufacturer_assertion_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          source_id TEXT NOT NULL,
          from_state TEXT,
          to_state TEXT NOT NULL,
          reason TEXT NOT NULL,
          superseded_by TEXT,
          at_epoch INTEGER NOT NULL
        );
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(manufacturer_website_evidence)")}
    for name, kind in (("assertion_status", "TEXT NOT NULL DEFAULT 'vegan'"),
                       ("assertion_state", "TEXT NOT NULL DEFAULT 'active'"), ("superseded_by", "TEXT"),
                       ("review_policy_version", "TEXT")):
        if name not in columns:
            conn.execute(f"ALTER TABLE manufacturer_website_evidence ADD COLUMN {name} {kind}")


def load_verified_evidence(
    conn: sqlite3.Connection, product_id: str, catalogue_binding_hash: str | list[str]
) -> list[dict[str, str]]:
    # Older/frozen databases remain readable without a schema migration.
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'manufacturer_website_evidence'"
    ).fetchone():
        return []
    bindings = [catalogue_binding_hash] if isinstance(catalogue_binding_hash, str) else catalogue_binding_hash
    columns = {row[1] for row in conn.execute("PRAGMA table_info(manufacturer_website_evidence)")}
    status = "e.assertion_status" if "assertion_status" in columns else "'vegan'"
    review_version = "e.review_policy_version" if "review_policy_version" in columns else "NULL"
    active = "AND e.assertion_state = 'active'" if "assertion_state" in columns else ""
    placeholders = ','.join('?' for _ in bindings)
    return [
        {
            "field_title": "Verified manufacturer website",
            "content": row[0],
            "source_kind": "manufacturer_website",
            "verified_status": row[6],
            "source_url": row[1],
            "source_sha256": row[2],
            "retrieved_at": row[3],
            "scope_match": row[4],
            "research_run_id": str(row[5]),
            "source_text": row[7],
            "official_identity": row[8],
            "market_scope": row[9],
            "review_policy_version": row[10],
        }
        for row in conn.execute(
            f"""
            SELECT e.statement, s.url, s.content_sha256, s.retrieved_at,
                   e.scope_match, e.run_id, {status}, s.source_text, s.official_identity, s.market_scope, {review_version}
            FROM manufacturer_website_evidence e
            JOIN manufacturer_website_sources s ON s.id = e.source_id
            WHERE e.product_id = ? AND e.catalogue_binding_hash IN ({placeholders}) {active}
            ORDER BY e.run_id, e.source_id
            """,
            (product_id, *bindings),
        )
    ]


def load_stale_negative_evidence(conn: sqlite3.Connection, product_id: str, bindings: list[str]) -> list[dict[str, str]]:
    """An active contrary assertion does not disappear on a recipe/identity edit."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(manufacturer_website_evidence)")}
    if "assertion_status" not in columns:
        return []
    placeholders = ','.join('?' for _ in bindings)
    rows = conn.execute(f"""SELECT e.statement,s.url,e.scope_match,e.catalogue_binding_hash FROM manufacturer_website_evidence e
        JOIN manufacturer_website_sources s ON s.id=e.source_id WHERE e.product_id=? AND e.assertion_status='nonvegan'
        AND e.assertion_state='active' AND e.catalogue_binding_hash NOT IN ({placeholders})""", (product_id,*bindings))
    return [{"content":row[0],"source_url":row[1],"scope_match":row[2],"previous_binding_hash":row[3],"applicability":"stale_binding"} for row in rows]


def retained_source_candidates(conn, product_ids):
    """Unverified associated captures, without inherited outcomes/review notes."""
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'manufacturer_research_products','manufacturer_website_sources'}<=tables:return {}
    associations={};source_ids=set()
    for start in range(0,len(product_ids),500):
        batch=product_ids[start:start+500];marks=','.join('?' for _ in batch)
        for row in conn.execute(f'SELECT product_id,catalogue_context_hash,research_json FROM manufacturer_research_products WHERE product_id IN ({marks})',batch):
            ids=json.loads(row[2]).get('source_ids',[])
            if not isinstance(ids,list) or any(not isinstance(value,str) for value in ids):raise ValueError('Invalid retained source association')
            for source_id in ids:
                associations.setdefault(row[0],{}).setdefault(source_id,set()).add(row[1]);source_ids.add(source_id)
    sources={}
    ids=sorted(source_ids)
    for start in range(0,len(ids),500):
        batch=ids[start:start+500];marks=','.join('?' for _ in batch)
        sources.update((row['id'],dict(row)) for row in conn.execute(f'SELECT * FROM manufacturer_website_sources WHERE id IN ({marks})',batch))
    if source_ids-set(sources):raise ValueError('Retained manufacturer association references a missing source capture')
    return {product_id:[{'source_id':source_id,'source_url':sources[source_id]['url'],'source_text':sources[source_id]['source_text'],
        'source_sha256':sources[source_id]['content_sha256'],'retrieved_at':sources[source_id]['retrieved_at'],
        'official_identity':sources[source_id]['official_identity'],'market_scope':sources[source_id]['market_scope'],
        'catalogue_hashes':sorted(hashes)} for source_id,hashes in sorted(values.items()) if source_id in sources]
        for product_id,values in associations.items()}
