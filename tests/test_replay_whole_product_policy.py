from pathlib import Path
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import classify_ocado_vegan as classifier
import replay_whole_product_policy as repair
import vegan_policy as policy
import build_ocado_database as builder


class PolicyReplayTests(unittest.TestCase):
    def test_targeted_metadata_repair_preserves_other_decisions_calls_and_rollback(self):
        conn = sqlite3.connect(':memory:'); self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        builder.create_schema(conn); classifier.ensure_classification_schema(conn)
        conn.execute('ALTER TABLE products ADD COLUMN current_on_ocado INTEGER')
        conn.execute("INSERT INTO products(id,name,ingredients,official_vegan,current_on_ocado) VALUES('1','Avocado',NULL,1,0)")
        conn.execute("INSERT INTO products(id,name,ingredients,current_on_ocado) VALUES('2','Prepared Product',NULL,1)")
        conn.execute("INSERT INTO products(id,name,ingredients,current_on_ocado) VALUES('3','Milk','Milk',1)")
        conn.execute("INSERT INTO products(id,name,current_on_ocado) VALUES('4','Pending',1)")
        rid = classifier.start_run(conn, mode='fixture', model=None, reasoning_effort=None)
        for pid in ('1', '2'):
            call = {'call_id':'retained-'+pid, 'stage':'primary', 'model':'gpt-6-luna', 'reasoning_effort':'high'}
            classifier.write_classification(conn, rid, classifier.ClassificationResult(
                product_id=pid, vegan_status='unknown', vegan_reason=None, confidence='uncertain',
                summary='Retained unknown', evidence={}, source='codex', model='gpt-6-luna',
                reasoning_effort='high', model_calls=(call,)))
        classifier.write_classification(conn, rid, classifier.ClassificationResult(
            product_id='3', vegan_status='nonvegan', vegan_reason=None, confidence='certain',
            summary='Declared milk', evidence={}, source='rule'))
        classifier.finish_run(conn, rid, 'completed')
        conn.execute("UPDATE products SET vegan_positive_block=0 WHERE id='1'")  # Previously emitted metadata.
        conn.commit()
        before = {row['id']:dict(row) for row in conn.execute('SELECT * FROM products')}
        with self.assertRaisesRegex(ValueError, 'caller-owned'):
            repair.replay_unknown_metadata(conn, sync_run_id=None)
        conn.execute('BEGIN IMMEDIATE')
        report = repair.replay_unknown_metadata(conn, sync_run_id=None)
        self.assertEqual(report['decision_writes'], 1)
        self.assertEqual(report['new_model_calls'], 0)
        self.assertEqual(report['requeued_products'], [])
        after = {row['id']:dict(row) for row in conn.execute('SELECT * FROM products')}
        self.assertEqual(after['1']['vegan_status'], 'unknown')
        self.assertEqual(after['1']['vegan_positive_block'], 1)
        for pid in ('2', '3', '4'):
            self.assertEqual(before[pid], after[pid])
        self.assertEqual(conn.execute('SELECT call_id FROM vegan_audit_model_calls WHERE audit_id=?',
            (after['1']['vegan_audit_id'],)).fetchone()[0], 'retained-1')
        self.assertEqual(conn.execute('SELECT count(*) FROM vegan_model_calls').fetchone()[0], 2)
        conn.rollback()
        self.assertEqual({row['id']:dict(row) for row in conn.execute('SELECT * FROM products')}, before)
        conn.execute('BEGIN IMMEDIATE')
        report = repair.replay_candidate(conn, sync_run_id=None)
        after = {row['id']:dict(row) for row in conn.execute('SELECT * FROM products')}
        for pid in ('2', '3', '4'):
            self.assertEqual(before[pid], after[pid])
        self.assertEqual(after['1']['vegan_positive_block'], 1)
        self.assertEqual(report['decision_writes'], 1)
        self.assertEqual(report['unchanged_nonpositive_decisions_preserved'], 2)
        conn.rollback()

    def records(self, name, ingredients, status='unknown', **fields):
        context = {'product': {'id': '1', 'name': name, 'ingredients': ingredients,
            'vegan_status': status, 'vegan_reason': 'manufacturer' if status == 'vegan' else None,
            **fields}, 'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}
        context['product']['vegan_evidence_hash'] = classifier.context_hash(classifier.product_context_for_llm(context))
        audit = dict(id=10, product_id='1', validation_error=None, vegan_status=status,
            vegan_reason=context['product']['vegan_reason'], confidence='uncertain', summary='Retained result',
            evidence_json=json.dumps({'assessment': policy.base_assessment(context, complete=False)}),
            source='codex', model='gpt-6-luna', reasoning_effort='high', prompt_version='retained-prompt',
            context_hash='batch-hash', raw_response='retained response', parsed_response_json=None,
            exit_status=0, classifier_version='retained-classifier', knowledge_version='retained-registry')
        return context, audit

    def test_agreed_unknown_is_not_promoted(self):
        context, audit = self.records('Example Product', 'Water', dietary_information='Suitable for vegans.')
        self.assertEqual(repair.replay(context, audit).vegan_status, 'unknown')

    def test_uncited_native_maker_claim_requires_new_interpretation_after_policy_change(self):
        context, audit = self.records('Example Vegan Hair Oil', 'Water', status='vegan',
            vegan_reason='name', features='Guess what? This oil is also vegan!, Use it daily.')
        audit['confidence'] = 'certain'
        assessment = policy.base_assessment(context, complete=True)
        assessment['positive_support'] = [{'reason':'name','evidence_refs':[
            policy.reference('/product/name',context['product']['name'])]}]
        audit['evidence_json'] = json.dumps({'assessment':assessment})
        self.assertIsNone(repair.replay(context,audit))
        audit['knowledge_version'] = policy.KNOWLEDGE_VERSION
        self.assertEqual(repair.replay(context,audit).vegan_reason,'name')

    def test_new_native_maker_eligibility_never_requeues_or_promotes_agreed_unknown(self):
        context,audit = self.records('Example Hair Oil','Water',
            features='Guess what? This oil is also vegan!, Use it daily.')
        self.assertEqual(repair.replay(context,audit).vegan_status,'unknown')

    def test_original_agreed_negatives_are_validated_before_gate_replay(self):
        context, audit = self.records('Cheese Selection', None,
            detailed_description="A ready-to-serve selection made with pasteurised Cow's milk. Not suitable for vegetarians.")
        assessment = policy.base_assessment(context, complete=False)
        assessment['material_issues'] = [{'code':'animal_ingredient', 'target':'whole_product',
            'rule_id':'animal-ingredients-v1', 'evidence_refs':[policy.reference('/product/detailed_description', context['product']['detailed_description'])],
            'resolution_refs':[], 'blocks_claim':True}]
        reading = {'product_id':'1', 'vegan_status':'nonvegan', 'vegan_reason':None, 'confidence':'certain',
            'summary':'Declared dairy', 'evidence':[], 'ambiguity_notes':[], 'assessment':assessment}
        audit['evidence_json'] = json.dumps({'assessment':policy.base_assessment(context), 'independent_passes':[reading, reading]})
        result = repair.replay(context, audit)
        self.assertEqual(result.vegan_status, 'nonvegan')
        self.assertTrue(result.evidence['replayed_agreed_negative'])
        self.assertEqual(result.raw_response, 'retained response')
        assessment['material_issues'][0]['evidence_refs'][0]['quote'] = 'Invented milk declaration'
        audit['evidence_json'] = json.dumps({'independent_passes':[reading, reading]})
        with self.assertRaises(ValueError):repair.replay(context, audit)

    def test_conclusive_animal_proof_overrides_retained_unknown(self):
        context, audit = self.records('Pet Food', 'Cereals, Meat and Animal Derivatives')
        result = repair.replay(context, audit)
        self.assertEqual(result.vegan_status, 'nonvegan')
        self.assertEqual(result.evidence['policy_replay']['previous_audit_id'], 10)
        self.assertEqual(result.evidence['policy_replay']['new_model_calls'], 0)

    def test_changed_native_evidence_is_rejected(self):
        context, audit = self.records('Product', 'Water')
        context['product']['ingredients'] = 'Water, Milk'
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            repair.replay(context, audit)

    def test_failed_or_mismatched_audit_is_rejected(self):
        context, audit = self.records('Product', 'Water')
        for changed in ({'validation_error': 'failed'}, {'product_id': '2'}, {'vegan_status': 'nonvegan'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                repair.replay(context, {**audit, **changed})

    def test_withdrawn_rule_is_work_remaining_not_reused_generated_evidence(self):
        context, audit = self.records('Utensil', None, status='nonvegan')
        audit['source'] = 'rule'
        with patch.object(classifier, 'classify_by_rules', return_value=None):
            self.assertIsNone(repair.replay(context, audit))

    def test_requeue_preserves_old_audit_and_transaction_rollback(self):
        conn = sqlite3.connect(':memory:'); self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        builder.create_schema(conn); classifier.ensure_classification_schema(conn)
        conn.execute('ALTER TABLE products ADD COLUMN current_on_ocado INTEGER')
        conn.execute("INSERT INTO products(id,name,current_on_ocado) VALUES('1','Utensil',0)")
        rid = classifier.start_run(conn, mode='fixture', model=None, reasoning_effort=None)
        classifier.write_classification(conn, rid, classifier.ClassificationResult(product_id='1',
            vegan_status='nonvegan', vegan_reason=None, confidence='certain', summary='Old shortcut', source='rule', evidence={}))
        classifier.finish_run(conn, rid, 'completed'); conn.commit()
        old_id = conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0]
        conn.execute('BEGIN IMMEDIATE')
        with patch.object(classifier, 'classify_by_rules', return_value=None):
            report = repair.replay_candidate(conn, sync_run_id=None)
        self.assertEqual(report['requeued_products'][0]['previous_audit_id'], old_id)
        self.assertIsNone(conn.execute('SELECT vegan_status FROM products').fetchone()[0])
        self.assertEqual(conn.execute('SELECT count(*) FROM product_vegan_classification_audit').fetchone()[0], 1)
        conn.rollback()
        self.assertEqual(conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0], old_id)

    def test_transaction_preserves_call_linkage_and_rolls_back(self):
        conn = sqlite3.connect(':memory:'); self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        builder.create_schema(conn); classifier.ensure_classification_schema(conn)
        conn.execute('ALTER TABLE products ADD COLUMN current_on_ocado INTEGER')
        conn.execute("INSERT INTO products(id,name,ingredients,current_on_ocado) VALUES('1','Unresolved Product','Water',0)")
        rid = classifier.start_run(conn, mode='fixture', model='gpt-6-luna', reasoning_effort='high')
        call = {'call_id': 'retained-call', 'stage': 'primary', 'model': 'gpt-6-luna', 'reasoning_effort': 'high'}
        result = classifier.ClassificationResult(product_id='1', vegan_status='unknown', vegan_reason=None,
            confidence='uncertain', summary='Insufficient evidence', source='codex', evidence={},
            model='gpt-6-luna', reasoning_effort='high', model_calls=(call,))
        classifier.write_classification(conn, rid, result); classifier.finish_run(conn, rid, 'completed'); conn.commit()
        old_id = conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0]
        with self.assertRaisesRegex(ValueError, 'caller-owned'):
            repair.replay_candidate(conn, sync_run_id=None)
        conn.execute('BEGIN IMMEDIATE')
        report = repair.replay_candidate(conn, sync_run_id=None)
        self.assertEqual(report['replayed'], 1); self.assertEqual(report['new_model_calls'], 0)
        new_id = conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0]
        self.assertEqual(new_id, old_id)
        self.assertEqual(report['decision_writes'], 0)
        self.assertEqual(report['unchanged_nonpositive_decisions_preserved'], 1)
        self.assertEqual(conn.execute('SELECT call_id FROM vegan_audit_model_calls WHERE audit_id=?', (new_id,)).fetchone()[0], 'retained-call')
        self.assertEqual(conn.execute('SELECT count(*) FROM vegan_model_calls').fetchone()[0], 1)
        self.assertEqual(conn.execute('SELECT vegan_status FROM products').fetchone()[0], 'unknown')
        conn.rollback()
        self.assertEqual(conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0], old_id)
        self.assertEqual(conn.execute('SELECT count(*) FROM product_vegan_classification_audit').fetchone()[0], 1)

    def test_old_nonpositive_version_requires_current_attestation(self):
        for version in ('knowledge_version', 'schema_version'):
            with self.subTest(version=version):
                conn = sqlite3.connect(':memory:'); self.addCleanup(conn.close)
                conn.row_factory = sqlite3.Row
                builder.create_schema(conn); classifier.ensure_classification_schema(conn)
                conn.execute('ALTER TABLE products ADD COLUMN current_on_ocado INTEGER')
                conn.execute("INSERT INTO products(id,name,ingredients,current_on_ocado) VALUES('1','Unresolved Product','Water',0)")
                rid = classifier.start_run(conn, mode='fixture', model='gpt-6-luna', reasoning_effort='high')
                call = {'call_id':'original-reading', 'stage':'primary', 'model':'gpt-6-luna', 'reasoning_effort':'high'}
                classifier.write_classification(conn, rid, classifier.ClassificationResult(
                    product_id='1', vegan_status='unknown', vegan_reason=None,
                    confidence='uncertain', summary='Insufficient evidence', source='codex',
                    evidence={}, model='gpt-6-luna', reasoning_effort='high', model_calls=(call,)))
                classifier.finish_run(conn, rid, 'completed')
                old_id = conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0]
                conn.execute('UPDATE product_vegan_classification_audit SET '+version+'=? WHERE id=?', ('previous-version',old_id))
                conn.commit()
                original = dict(conn.execute('SELECT * FROM product_vegan_classification_audit WHERE id=?',(old_id,)).fetchone())
                conn.execute('BEGIN IMMEDIATE')
                report = repair.replay_candidate(conn, sync_run_id=None)
                new_id = conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0]
                self.assertNotEqual(new_id, old_id)
                self.assertEqual(report['decision_writes'], 1)
                self.assertEqual(report['new_model_calls'], 0)
                current = conn.execute('SELECT * FROM product_vegan_classification_audit WHERE id=?',(new_id,)).fetchone()
                self.assertEqual(current['knowledge_version'], policy.KNOWLEDGE_VERSION)
                self.assertEqual(current['schema_version'], policy.SCHEMA_VERSION)
                self.assertEqual(current['vegan_status'], 'unknown')
                self.assertEqual(dict(conn.execute('SELECT * FROM product_vegan_classification_audit WHERE id=?',(old_id,)).fetchone()), original)
                self.assertEqual(conn.execute('SELECT call_id FROM vegan_audit_model_calls WHERE audit_id=?',(new_id,)).fetchone()[0], 'original-reading')
                self.assertEqual(conn.execute('SELECT count(*) FROM vegan_model_calls').fetchone()[0], 1)
                conn.rollback()
                self.assertEqual(conn.execute('SELECT vegan_audit_id FROM products').fetchone()[0], old_id)
