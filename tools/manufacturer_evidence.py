"""Retained, product-bound manufacturer website evidence (no network access)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


PUBLISHER_FIELDS = ("manufacturer", "producer", "return_to_address")


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
          PRIMARY KEY (run_id, product_id, source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_manufacturer_website_evidence_product
          ON manufacturer_website_evidence(product_id);
        """
    )


def load_verified_evidence(
    conn: sqlite3.Connection, product_id: str, catalogue_binding_hash: str
) -> list[dict[str, str]]:
    # Older/frozen databases remain readable without a schema migration.
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'manufacturer_website_evidence'"
    ).fetchone():
        return []
    return [
        {
            "field_title": "Verified manufacturer website",
            "content": row[0],
            "source_kind": "manufacturer_website",
            "verified_status": "vegan",
            "source_url": row[1],
            "source_sha256": row[2],
            "retrieved_at": row[3],
            "scope_match": row[4],
            "research_run_id": str(row[5]),
        }
        for row in conn.execute(
            """
            SELECT e.statement, s.url, s.content_sha256, s.retrieved_at,
                   e.scope_match, e.run_id
            FROM manufacturer_website_evidence e
            JOIN manufacturer_website_sources s ON s.id = e.source_id
            WHERE e.product_id = ? AND e.catalogue_binding_hash = ?
            ORDER BY e.run_id, e.source_id
            """,
            (product_id, catalogue_binding_hash),
        )
    ]
