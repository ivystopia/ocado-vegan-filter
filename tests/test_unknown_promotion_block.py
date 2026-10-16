"""A retained unknown must not be promoted through the same live claim."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import classify_ocado_vegan as classifier


class UnknownPromotionBlockTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            'product': {'id': '1', 'name': 'Example Avocado', 'official_vegan': 1,
                        'package_type': 'Plastic punnet',
                        'detailed_description': 'Our packaging is plastic-free.'},
            'flags': ['vegan'], 'categories': [], 'manufacturer_vegan_evidence': []}
        self.tag = {'path': '/product/official_vegan', 'quote': '1'}
        self.refs = [
            {'path': '/product/package_type', 'quote': 'Plastic punnet'},
            {'path': '/product/detailed_description', 'quote': 'Our packaging is plastic-free.'}]
        assessment = classifier.policy.base_assessment(self.context, complete=True)
        assessment['positive_support'] = [{'reason': 'tagged', 'evidence_refs': [self.tag]}]
        for dimension in ('components', 'sourcing', 'processing'):
            assessment[dimension] = {'status': 'covered_by_claim', 'evidence_refs': [self.tag]}
        self.positive = {'product_id': '1', 'vegan_status': 'vegan', 'vegan_reason': 'tagged',
                         'confidence': 'certain', 'summary': 'An applicable positive claim.',
                         'evidence': [], 'ambiguity_notes': [], 'assessment': assessment}
        self.unresolved = deepcopy(self.positive)
        self.unresolved.update(vegan_status='unknown', vegan_reason=None, confidence='uncertain',
                               summary='The packaging descriptions conflict.')
        self.unresolved['assessment']['components'] = {'status': 'conflicted', 'evidence_refs': self.refs}
        self.issue = {'code': 'evidence_conflict', 'target': 'supplied packaging',
                      'rule_id': 'packaging-description-conflict-v1', 'evidence_refs': self.refs,
                      'resolution_refs': [], 'blocks_claim': True}
        self.unresolved['assessment']['material_issues'] = [self.issue]

    def result(self, readings):
        merged = classifier.merge_pass_decisions([{'1': row} for row in readings], '1')
        result = classifier.decision_to_result(
            merged, source='codex_arbitration', model='gpt-6.1-sol', reasoning_effort='medium',
            context_hash_value='test', raw_response='retained raw responses',
            parsed_response_json='[]', exit_status=0)
        return replace(result, evidence={**result.evidence, 'independent_passes': readings})

    def test_disagreement_keeps_both_raw_outcomes_and_blocking_issue_in_either_order(self):
        for readings in ([self.positive, self.unresolved], [self.unresolved, self.positive]):
            with self.subTest(first=readings[0]['vegan_status']):
                original = deepcopy(readings)
                result = self.result(readings)
                self.assertEqual(result.vegan_status, 'unknown')
                self.assertIn('independent_codex_disagreement', result.evidence['ambiguity_notes'])
                self.assertIn(self.issue, result.evidence['assessment']['material_issues'])
                self.assertEqual(readings, original)
                self.assertEqual(result.evidence['independent_passes'], original)

    def test_final_gate_keeps_unknown_and_block_is_idempotent(self):
        result = classifier.apply_policy_gate(self.context, self.result([self.positive, self.unresolved]))
        repeated = classifier.apply_policy_gate(self.context, result)
        for value in (result, repeated):
            self.assertEqual((value.vegan_status, value.vegan_reason), ('unknown', None))
            self.assertTrue(value.evidence['positive_block'])
            self.assertEqual(value.evidence['assessment']['components']['status'], 'conflicted')

    def test_retained_disagreement_rebuilds_assessment_instead_of_reusing_old_gate_projection(self):
        result = self.result([self.positive, self.unresolved])
        result = replace(result, evidence={**result.evidence,
            'assessment': deepcopy(self.positive['assessment']), 'positive_block': False})
        fixed = classifier.apply_policy_gate(self.context, result)
        self.assertEqual(fixed.vegan_status, 'unknown')
        self.assertTrue(fixed.evidence['positive_block'])
        self.assertIn(self.issue, fixed.evidence['assessment']['material_issues'])

    def test_agreed_unknown_with_withheld_existing_tag_cannot_repromote(self):
        unknown = {**self.positive, 'vegan_status': 'unknown', 'vegan_reason': None,
                   'confidence': 'uncertain'}
        result = classifier.apply_policy_gate(self.context, self.result([unknown, deepcopy(unknown)]))
        self.assertEqual(result.vegan_status, 'unknown')
        self.assertTrue(result.evidence['positive_block'])

    def test_plain_evidence_absent_unknown_does_not_gain_a_quarantine(self):
        context = {'product': {'id': '1', 'name': 'Example Prepared Product'},
                   'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}
        result = classifier.ClassificationResult(
            product_id='1', vegan_status='unknown', vegan_reason=None, confidence='uncertain',
            summary='Insufficient evidence.', evidence={}, source='codex')
        result = classifier.apply_policy_gate(context, result)
        self.assertEqual(result.vegan_status, 'unknown')
        self.assertFalse(result.evidence['positive_block'])

    def test_resolved_fresh_arbitration_is_not_blocked_by_historical_primary_disagreement(self):
        result = self.result([self.positive, deepcopy(self.positive)])
        result = replace(result, evidence={**result.evidence,
            'arbitration': {'primary': {'independent_passes': [self.positive, self.unresolved]}}})
        result = classifier.apply_policy_gate(self.context, result)
        self.assertEqual((result.vegan_status, result.vegan_reason), ('vegan', 'tagged'))
        self.assertFalse(result.evidence['positive_block'])


if __name__ == '__main__':
    unittest.main()
