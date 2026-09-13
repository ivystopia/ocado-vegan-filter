"""Safety checks for retained manufacturer website evidence and import reviews."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_ocado_database
import classify_ocado_vegan as classifier
import manufacturer_evidence as evidence
import research_ocado_manufacturers as research
import sync_ocado_database as sync
import update_userscript_allowlists as exporter


class ManufacturerEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        build_ocado_database.create_schema(self.conn)
        classifier.ensure_classification_schema(self.conn)
        evidence.ensure_schema(self.conn)
        self.conn.execute(
            """INSERT INTO products (id, name, brand, ingredients, pack_size, vegan_status)
               VALUES ('1', 'Example Original Cola', 'Example', 'Water, flavouring', '330ml', 'unknown')"""
        )

    def add_claim(self, *, product_id: str = "1", quote: str = "Our Original Cola is vegan.") -> None:
        context = classifier.load_product_context(self.conn, product_id)
        binding = evidence.binding_hash(research.catalogue_payload(context), context["product"].get("pack_size"),
                                        **{key: context["product"].get(key) for key in evidence.PUBLISHER_FIELDS})
        self.conn.execute(
            """INSERT OR IGNORE INTO manufacturer_website_sources VALUES
               ('source', 'https://example.test/faq', 'FAQ', '2026-09-13T00:00:00Z',
                'Manufacturer site', 'UK', 'source-hash', ?)""", (quote,),
        )
        self.conn.execute(
            "INSERT INTO manufacturer_website_evidence VALUES (1, ?, 'source', ?, ?, 'Exact named UK product', '2026-09-13T00:00:00Z')",
            (product_id, binding, quote),
        )

    def test_verified_manufacturer_claim_resolves_ambiguous_flavouring_and_retains_source(self) -> None:
        self.add_claim()
        result = classifier.classify_by_rules(classifier.load_product_context(self.conn, "1"))
        self.assertEqual((result.vegan_status, result.vegan_reason), ("vegan", "manufacturer"))
        self.assertTrue(any(row.get("source_url") == "https://example.test/faq" for row in result.evidence["sources"]))

    def test_claim_does_not_spread_to_another_product_of_same_brand(self) -> None:
        self.add_claim()
        self.conn.execute(
            """INSERT INTO products (id, name, brand, ingredients, pack_size, vegan_status)
               VALUES ('2', 'Example Diet Cola', 'Example', 'Water, flavouring', '330ml', 'unknown')"""
        )
        context = classifier.load_product_context(self.conn, "2")
        self.assertFalse(context["manufacturer_vegan_evidence"])
        result = classifier.classify_by_rules(context)
        self.assertTrue(result is None or result.vegan_status == "unknown")

    def test_recipe_name_and_pack_changes_each_invalidate_claim(self) -> None:
        original = dict(self.conn.execute("SELECT * FROM products WHERE id = '1'").fetchone())
        self.add_claim()
        for field, value in (("ingredients", "Water, honey"), ("name", "Example Different Cola"),
                             ("pack_size", "draught keg"), ("manufacturer", "Another producer"),
                             ("producer", "New licensed producer"), ("return_to_address", "New company address")):
            with self.subTest(field=field):
                self.conn.execute(f"UPDATE products SET {field} = ? WHERE id = '1'", (value,))
                self.assertFalse(classifier.load_product_context(self.conn, "1")["manufacturer_vegan_evidence"])
                self.conn.execute(f"UPDATE products SET {field} = ? WHERE id = '1'", (original[field],))

    def test_price_change_preserves_claim(self) -> None:
        self.add_claim()
        self.conn.execute("UPDATE products SET price_gbp = 2.50 WHERE id = '1'")
        self.assertTrue(classifier.load_product_context(self.conn, "1")["manufacturer_vegan_evidence"])

    def test_verified_claim_does_not_override_explicit_animal_ingredients(self) -> None:
        self.conn.execute("UPDATE products SET ingredients = 'Water, honey' WHERE id = '1'")
        self.add_claim()
        result = classifier.classify_by_rules(classifier.load_product_context(self.conn, "1"))
        self.assertEqual(result.vegan_status, "nonvegan")

    def test_official_tag_preserves_authoritative_reason(self) -> None:
        self.conn.execute("UPDATE products SET official_vegan = 1 WHERE id = '1'")
        self.add_claim()
        result = classifier.classify_by_rules(classifier.load_product_context(self.conn, "1"))
        self.assertEqual((result.vegan_status, result.vegan_reason), ("vegan", "tagged"))

    def test_catalogue_manufacturer_statement_conflict_stays_unknown(self) -> None:
        self.conn.execute("UPDATE products SET dietary_information = 'Not suitable for vegans' WHERE id = '1'")
        self.add_claim()
        result = classifier.classify_by_rules(classifier.load_product_context(self.conn, "1"))
        self.assertEqual(result.vegan_status, "unknown")

    def test_detail_refresh_does_not_delete_external_sources(self) -> None:
        self.add_claim()
        sync.ensure_sync_schema(self.conn)
        run_id = sync.start_sync_run(self.conn)
        payload = {
            "product": {"retailerProductId": "1", "name": "Example Original Cola", "iconAttributes": []},
            "bopData": {"fields": [{"title": "ingredients", "content": "Water, flavouring"}]},
        }
        sync.apply_product_detail(self.conn, run_id, "1", payload, 200)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM manufacturer_website_sources").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM manufacturer_website_evidence").fetchone()[0], 1)


class ManufacturerReviewTests(unittest.TestCase):
    def inputs(self) -> list[dict]:
        return [{"product_id": "1", "sources": [{"url": "https://example.test/faq", "source_text": "Original is vegan. Diet is not suitable for vegans."}]}]

    def decision(self, **overrides: object) -> dict:
        return {"product_id": "1", "vegan_status": "vegan", "vegan_reason": "manufacturer", "source_url": "https://example.test/faq",
                "quote": "Original is vegan.", "scope_match": "Exact Original product", "notes": "", **overrides}

    def test_review_rejects_fabricated_quote_or_unprovided_url(self) -> None:
        for overrides in ({"quote": "All drinks are vegan."}, {"source_url": "https://unrelated.test/"}):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "absent from the cited source"):
                research.validate_review({"products": [self.decision(**overrides)]}, self.inputs())

    def test_quote_validation_removes_link_markers_but_keeps_qualifications(self) -> None:
        source = "L202: Our \ue200cite\ue20275†Original\ue201 cola is vegan.\nL203: Our Diet cola is not vegan."
        inputs = [{"product_id": "1", "sources": [{"url": "https://example.test/faq", "source_text": source}]}]
        research.validate_review({"products": [self.decision(quote="Our Original cola is vegan.")]}, inputs)
        with self.assertRaisesRegex(ValueError, "absent from the cited source"):
            research.validate_review({"products": [self.decision(quote="Our Diet cola is vegan.")]}, inputs)

    def test_incomplete_batch_retries_missing_product_and_still_requires_two_passes(self) -> None:
        inputs = [self.inputs()[0], {**self.inputs()[0], "product_id": "2"}]
        first = self.decision()
        second = self.decision(product_id="2")
        incomplete = research.ReviewValidationError("Missing product", {"products": [first]})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(research, "call_review", side_effect=[
            incomplete, {"products": {"2": second}}, {"products": {"1": first, "2": second}},
        ]) as call:
            result = research.review_batch(inputs, "unused", failure_dir=Path(tmp))
            self.assertEqual(set(result), {"1", "2"})
            self.assertEqual([value["product_id"] for value in call.call_args_list[1].args[0]], ["2"])
            self.assertEqual([value["product_id"] for value in call.call_args_list[2].args[0]], ["1", "2"])
            self.assertTrue(all(len(value["passes"]) == 2 for value in result.values()))
            self.assertTrue(all(value["validation_retries"] == 1 for value in result.values()))
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 1)

    def test_review_rejects_missing_duplicate_or_extra_product(self) -> None:
        for values in ([], [self.decision(), self.decision()], [self.decision(product_id="2")]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                research.validate_review({"products": values}, self.inputs())

    def test_incomplete_coverage_prevents_database_application(self) -> None:
        bundle = {"products": {"1": {"current_on_ocado": 1}}, "outcomes": {"1": {"outcome": "pending", "research_complete": False}}, "sources": {}, "brands": 1, "errors": []}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "Exhaustive research is incomplete"):
            research.apply_bundle(Path(tmp), Path(tmp) / "must-not-be-created.sqlite", bundle, dry_run=False)
        self.assertEqual(research.bundle_status(bundle)["complete_products"], 0)

    def test_no_confirmation_is_distinct_from_blocked_and_pending(self) -> None:
        bundle = {"products": {str(i): {"current_on_ocado": 1} for i in range(3)},
                  "outcomes": {str(i): {"outcome": outcome, "research_complete": True} for i, outcome in enumerate(("no_confirmation", "blocked", "pending"))},
                  "sources": {}, "brands": 1, "errors": []}
        self.assertEqual(research.bundle_status(bundle)["complete_products"], 1)

    def test_retained_source_benchmark_inputs_keep_their_hashes(self) -> None:
        fixture = json.loads((TOOLS.parent / "benchmarks/manufacturer-source-scope-v1.json").read_text())
        self.assertEqual(len(fixture["cases"]), 16)
        self.assertEqual(len({case["input"]["product_id"] for case in fixture["cases"]}), 16)
        for case in fixture["cases"]:
            with self.subTest(label=case["label"]):
                self.assertEqual(research.digest(case["input"]), case["input_sha256"])

    def test_brand_identity_benchmark_adds_cases_without_changing_frozen_cases(self) -> None:
        original = json.loads((TOOLS.parent / "benchmarks/manufacturer-source-scope-v1.json").read_text())
        expanded = json.loads((TOOLS.parent / "benchmarks/manufacturer-source-scope-v2.json").read_text())
        self.assertEqual(expanded["cases"][:16], original["cases"])
        self.assertEqual(len(expanded["cases"]), 19)
        for case in expanded["cases"]:
            self.assertEqual(research.digest(case["input"]), case["input_sha256"])


class ManufacturerImportTests(unittest.TestCase):
    def prepare(self, temporary: Path) -> tuple[Path, Path, dict]:
        db = temporary / "catalogue.sqlite"
        root = temporary / "research"
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        build_ocado_database.create_schema(conn)
        classifier.ensure_classification_schema(conn)
        sync.ensure_sync_schema(conn)
        with conn:
            conn.executemany(
                """INSERT INTO products (id, name, brand, ingredients, pack_size, vegan_status, current_on_ocado)
                   VALUES (?, ?, 'Example', ?, '330ml', ?, 1)""",
                [("1", "Example Original Cola", "Water, flavouring", "unknown"),
                 ("2", "Example Diet Cola", "Water, flavouring", "unknown"),
                 ("3", "Example Milk Drink", "Milk", "nonvegan")],
            )
        research.create_snapshot(root, db, 1)
        source_path = root / "part-1/sources/faq.txt"
        source_path.write_text("Example UK manufacturer FAQ. Our Original Cola is vegan. Diet Cola is not suitable for vegans.")
        url = "https://example.test/uk/faq"
        research.write_json(root / "part-1/results/example.json", {
            "brand": "Example", "checked_at": "2026-09-13T00:00:00Z", "research_complete": True,
            "queries": ["Example manufacturer vegan FAQ"],
            "sources": [{"url": url, "title": "Example UK FAQ", "retrieved_at": "2026-09-13T00:00:00Z",
                         "official_identity": "Example manufacturer's own website", "market_scope": "UK",
                         "source_text_path": str(source_path)}],
            "products": [
                {"product_id": "1", "outcome": "confirmed_vegan", "source_urls": [url], "quote": "Our Original Cola is vegan.", "scope_match": "Exact named product", "notes": ""},
                {"product_id": "2", "outcome": "no_confirmation", "source_urls": [url], "quote": "", "scope_match": "Explicitly excluded range", "notes": "Leave unknown in vegan-additions-only update"},
            ],
        })
        bundle = research.read_bundle(root)
        value = research.review_input(bundle, "1")
        decision = {"product_id": "1", "vegan_status": "vegan", "vegan_reason": "manufacturer", "source_url": url,
                    "quote": "Our Original Cola is vegan.", "scope_match": "Exact named UK Original Cola", "notes": ""}
        research.write_json(root / "reviews/1.json", {
            "product_id": "1", "input_hash": research.digest({"prompt_version": research.PROMPT_VERSION, "input": value}),
            "prompt_version": research.PROMPT_VERSION, "model": classifier.DEFAULT_CODEX_MODEL,
            "reasoning_effort": classifier.DEFAULT_REASONING_EFFORT, "outcome": "confirmed_vegan", "passes": [decision, dict(decision)],
        })
        return db, root, bundle

    def test_dry_run_is_read_only_and_import_retains_audited_additions_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db, root, bundle = self.prepare(Path(tmp))
            before_bytes = db.read_bytes()
            preview = research.apply_bundle(root, db, bundle, dry_run=True)
            self.assertEqual(db.read_bytes(), before_bytes)
            self.assertEqual(preview["accepted_ids"], ["1"])
            report = research.apply_bundle(root, db, bundle, dry_run=False)
            self.assertTrue(Path(report["backup_path"]).exists())
            conn = sqlite3.connect(db)
            self.addCleanup(conn.close)
            conn.row_factory = sqlite3.Row
            rows = [tuple(row) for row in conn.execute("SELECT id, vegan_status, vegan_reason FROM products ORDER BY id")]
            self.assertEqual(rows, [("1", "vegan", "manufacturer"), ("2", "unknown", None), ("3", "nonvegan", None)])
            self.assertEqual(exporter.load_allowlists(conn)["KNOWN_NON_VEGAN_PRODUCT_IDS"], ["3"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM manufacturer_research_products").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM manufacturer_website_sources").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT source FROM product_vegan_classification_audit").fetchone()[0], "manufacturer-website-review")
            self.assertEqual(classifier.classify_by_rules(classifier.load_product_context(conn, "1")).vegan_reason, "manufacturer")

    def test_concurrent_recipe_or_classification_change_blocks_import(self) -> None:
        for column, value in (("ingredients", "Water, honey"), ("vegan_status", "nonvegan"),
                              ("pack_size", "1 litre"), ("manufacturer", "Another producer")):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as tmp:
                db, root, bundle = self.prepare(Path(tmp))
                conn = sqlite3.connect(db)
                self.addCleanup(conn.close)
                with conn:
                    conn.execute(f"UPDATE products SET {column} = ? WHERE id = '1'", (value,))
                with self.assertRaisesRegex(ValueError, "changed during research"):
                    research.apply_bundle(root, db, bundle, dry_run=True)

    def test_disagreement_cannot_be_manually_promoted_in_saved_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, root, bundle = self.prepare(Path(tmp))
            path = root / "reviews/1.json"
            record = json.loads(path.read_text())
            record["passes"][1].update({"vegan_status": "unknown", "vegan_reason": None, "source_url": None, "quote": ""})
            research.write_json(path, record)
            with self.assertRaisesRegex(ValueError, "review merge"):
                research.read_reviews(root, bundle)
            record["outcome"] = "unknown"
            research.write_json(path, record)
            self.assertEqual(research.read_reviews(root, bundle)["1"]["outcome"], "unknown")

    def test_source_capture_cannot_read_outside_research_partition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, root, _ = self.prepare(Path(tmp))
            outside = Path(tmp) / "unrelated-private-file.txt"
            outside.write_text("This is not a manufacturer source.")
            path = root / "part-1/results/example.json"
            record = json.loads(path.read_text())
            record["sources"][0]["source_text_path"] = str(outside)
            research.write_json(path, record)
            bundle = research.read_bundle(root)
            self.assertTrue(any("inside its assigned research partition" in error for error in bundle["errors"]))
            self.assertFalse(bundle["sources"])


if __name__ == "__main__":
    unittest.main()
