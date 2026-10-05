"""Safety and audit coverage for the bounded Sol arbitration stage."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_ocado_database
import classify_ocado_vegan as classifier


class ArbitrationTests(unittest.TestCase):
    def context(self, product_id="1", **fields):
        return {"product": {"id": product_id, "name": "Sample", **fields},
                "flags": [], "categories": [], "manufacturer_vegan_evidence": []}

    def decision(self, product_id="1", status="unknown", reason=None):
        return {"product_id": product_id, "vegan_status": status, "vegan_reason": reason,
                "confidence": "certain", "summary": "Evidence assessment", "evidence": ["Retained evidence"],
                "ambiguity_notes": []}

    def response(self, *decisions):
        payload = json.dumps({"decisions": decisions})
        return {d["product_id"]: d for d in decisions}, payload, payload, 0

    def run_pipeline(self, contexts, responses):
        with mock.patch.object(classifier, "call_codex_batch", side_effect=responses) as calls:
            results = classifier.classify_with_arbitration(
                contexts, passes=2, retries=0, model=classifier.DEFAULT_CODEX_MODEL,
                reasoning_effort="high", codex_bin="unused")
        return results, calls

    def test_arbitrates_only_disagreement_with_two_blind_medium_passes(self):
        contexts = [self.context(), self.context("2")]
        yes = self.decision(status="vegan", reason="ingredients")
        unknown = self.decision()
        stable = self.decision("2", "nonvegan")
        no = self.decision(status="nonvegan")
        results, calls = self.run_pipeline(contexts, [
            self.response(yes, stable), self.response(unknown, stable), self.response(no), self.response(no)])
        self.assertEqual([r.vegan_status for r in results], ["nonvegan", "nonvegan"])
        self.assertEqual(calls.call_count, 4)
        for call in calls.call_args_list[2:]:
            self.assertEqual(call.args[0], [contexts[0]])
            self.assertEqual(call.kwargs["model"], "gpt-6.1-sol")
            self.assertEqual(call.kwargs["reasoning_effort"], "medium")
            self.assertTrue(call.kwargs["arbitration_policy"])
        self.assertEqual(results[0].source, "codex_arbitration")
        self.assertEqual(results[0].model, "gpt-6.1-sol")
        stages = results[0].evidence["arbitration"]
        self.assertEqual(stages["primary"]["model"], "gpt-5.6-luna")
        self.assertEqual(len(json.loads(stages["primary"]["parsed_response_json"])), 2)
        self.assertEqual(len(json.loads(stages["assessment"]["parsed_response_json"])), 2)
        self.assertEqual(stages["outcome"], "resolved")
        self.assertEqual(results[1].source, "codex")

    def test_agreed_unknown_does_not_trigger_arbitration(self):
        unknown = self.response(self.decision())
        results, calls = self.run_pipeline([self.context()], [unknown, unknown])
        self.assertEqual(calls.call_count, 2)
        self.assertNotIn("arbitration", results[0].evidence)

    def test_reason_only_disagreement_triggers_arbitration(self):
        a = self.response(self.decision(status="vegan", reason="ingredients"))
        b = self.response(self.decision(status="vegan", reason="name"))
        unknown = self.response(self.decision())
        results, calls = self.run_pipeline([self.context()], [a, b, unknown, unknown])
        self.assertEqual(calls.call_count, 4)
        self.assertEqual(results[0].vegan_status, "unknown")
        self.assertEqual(results[0].evidence["arbitration"]["outcome"], "unresolved")

    def test_arbitration_disagreement_stops_at_unknown_without_recursing(self):
        a = self.response(self.decision(status="nonvegan"))
        b = self.response(self.decision())
        results, calls = self.run_pipeline([self.context()], [a, b, a, b])
        self.assertEqual(calls.call_count, 4)
        self.assertEqual(results[0].vegan_status, "unknown")
        self.assertIn("independent_codex_disagreement", results[0].evidence["ambiguity_notes"])

    def test_ingredient_and_name_only_arbitration_agreement_cannot_promote(self):
        for reason in ["ingredients", "name"]:
            with self.subTest(reason=reason):
                yes = self.response(self.decision(status="vegan", reason=reason))
                unknown = self.response(self.decision())
                results, _ = self.run_pipeline([self.context(name="Vegan beans", ingredients="Beans")],
                                               [yes, unknown, yes, yes])
                result = results[0]
                self.assertEqual(result.vegan_status, "unknown")
                self.assertIn("arbitration_confirmation_required", result.evidence["ambiguity_notes"])
                self.assertEqual(result.evidence["arbitration"]["assessment"]["vegan_status"], "vegan")

    def test_manufacturer_agreement_requires_stored_confirmation(self):
        for text, expected in [("Suitable for vegans", "vegan"), ("Made with beans", "unknown")]:
            with self.subTest(text=text):
                yes = self.response(self.decision(status="vegan", reason="manufacturer"))
                unknown = self.response(self.decision())
                results, _ = self.run_pipeline([self.context(dietary_information=text)],
                                               [yes, unknown, yes, yes])
                self.assertEqual(results[0].vegan_status, expected)

    def test_animal_ingredients_block_manufacturer_promotion(self):
        yes = self.response(self.decision(status="vegan", reason="manufacturer"))
        unknown = self.response(self.decision())
        results, _ = self.run_pipeline([self.context(ingredients="Milk", dietary_information="Suitable for vegans")],
                                       [yes, unknown, yes, yes])
        self.assertEqual(results[0].vegan_status, "unknown")

    def test_arbitration_failure_retains_primary_disagreement_and_error(self):
        no = self.response(self.decision(status="nonvegan"))
        unknown = self.response(self.decision())
        results, calls = self.run_pipeline([self.context()], [no, unknown, RuntimeError("invalid output")])
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(results[0].source, "codex_error")
        self.assertEqual(results[0].validation_error, "invalid output")
        self.assertEqual(results[0].evidence["arbitration"]["outcome"], "error")
        self.assertIn("independent_codex_disagreement",
                      results[0].evidence["arbitration"]["primary"]["evidence"]["ambiguity_notes"])

    def test_primary_execution_failure_does_not_trigger_arbitration(self):
        results, calls = self.run_pipeline([self.context()], [RuntimeError("invalid output")])
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(results[0].source, "codex_error")
        self.assertNotIn("arbitration", results[0].evidence)

    def test_usage_limit_propagates_without_repeated_escalation(self):
        no = self.response(self.decision(status="nonvegan"))
        unknown = self.response(self.decision())
        with self.assertRaises(classifier.CodexUsageLimitError):
            self.run_pipeline([self.context()], [no, unknown, classifier.CodexUsageLimitError("quota")])

    def test_single_primary_pass_rejected_before_model_call(self):
        with mock.patch.object(classifier, "call_codex_batch") as call:
            with self.assertRaises(ValueError):
                classifier.classify_with_arbitration([], passes=1, retries=0, model="test",
                                                     reasoning_effort="high", codex_bin="unused")
        call.assert_not_called()

    def test_arbitration_policy_survives_split_retry(self):
        unknown1 = self.response(self.decision())
        unknown2 = self.response(self.decision("2"))
        with mock.patch.object(classifier, "call_codex_batch",
                               side_effect=[RuntimeError("split"), unknown1, unknown1, unknown2, unknown2]) as calls:
            results = classifier.classify_codex_contexts(
                [self.context(), self.context("2")], passes=2, retries=0, model="gpt-6.1-sol",
                reasoning_effort="medium", codex_bin="unused", arbitration_policy=True)
        self.assertTrue(all(c.kwargs["arbitration_policy"] for c in calls.call_args_list))
        self.assertTrue(all(r.prompt_version == classifier.ARBITRATION_PROMPT_VERSION for r in results))

    def test_production_writer_persists_both_stages_and_counts_arbitration_errors(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                conn = sqlite3.connect(":memory:")
                self.addCleanup(conn.close)
                conn.row_factory = sqlite3.Row
                build_ocado_database.create_schema(conn)
                classifier.ensure_classification_schema(conn)
                conn.execute("INSERT INTO products(id, name) VALUES ('1', 'Sample')")
                no = self.response(self.decision(status="nonvegan"))
                unknown = self.response(self.decision())
                responses = [no, unknown, RuntimeError("arbitration failed")] if failure else [no, unknown, no, no]
                with mock.patch.object(classifier, "call_codex_batch", side_effect=responses):
                    count = classifier.classify_codex(
                        conn, limit=0, batch_size=10, passes=2, retries=0,
                        model=classifier.DEFAULT_CODEX_MODEL, reasoning_effort="high", codex_bin="unused")
                self.assertEqual(count, 1)
                row = conn.execute("SELECT * FROM product_vegan_classification_audit").fetchone()
                self.assertEqual(row["model"], "gpt-6.1-sol")
                self.assertEqual(row["prompt_version"], classifier.ARBITRATION_PROMPT_VERSION)
                self.assertIn("primary", json.loads(row["evidence_json"])["arbitration"])
                self.assertEqual(conn.execute("SELECT errors FROM vegan_classification_runs").fetchone()[0], int(failure))
                self.assertEqual(conn.execute("SELECT vegan_status FROM products").fetchone()[0],
                                 "unknown" if failure else "nonvegan")

    def test_nonzero_arbitration_exit_cannot_promote_valid_json(self):
        no = self.response(self.decision(status="nonvegan"))
        unknown = self.response(self.decision())
        bad = (*no[:3], 1)
        results, _ = self.run_pipeline([self.context()], [no, unknown, bad])
        self.assertEqual(results[0].vegan_status, "unknown")
        self.assertEqual(results[0].source, "codex_error")

    def test_prompt_restricts_positive_evidence_and_excludes_prior_answers(self):
        prompt = classifier.build_arbitration_prompt([self.context()])
        self.assertIn("do not return vegan/ingredients or vegan/name", prompt)
        self.assertIn("exact product, variant", prompt)
        self.assertNotIn("Luna", prompt)
        self.assertNotIn("disagreement", prompt)


if __name__ == "__main__":
    unittest.main()
