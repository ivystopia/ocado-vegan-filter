"""Materiality never erases animal proof or a semantic claim restriction."""
from pathlib import Path
import copy
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import classify_ocado_vegan as classifier
import replay_whole_product_policy as replay
import vegan_policy as policy
import test_replay_whole_product_policy as replay_fixtures


class MaterialityTests(unittest.TestCase):
    def context(self, **fields):
        return {'product': {'id': '1', 'name': 'Example Shampoo',
            'official_vegan': 1, 'package_type': 'Glass Bottle',
            'features': 'Recycled plastic bottle', **fields},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}

    def test_copy_observation_retained_without_bypassing_paired_readings(self):
        context = self.context()
        result = classifier.classify_rule_candidate(context)
        self.assertEqual((result.vegan_status, result.vegan_reason), ('vegan', 'tagged'))
        self.assertFalse(result.evidence['positive_block'])
        self.assertTrue(result.evidence['copy_discrepancy_refs'])
        self.assertIsNone(classifier.classify_by_rules(context))

    def test_animal_proof_still_overrides_tag_despite_unrelated_copy(self):
        result = classifier.classify_by_rules(self.context(ingredients='Water, Gelatine'))
        self.assertEqual(result.vegan_status, 'nonvegan')
        self.assertTrue(result.evidence['tag_animal_conflict'])

    def test_supplied_semantic_restriction_is_not_erased_by_matching_rule_id(self):
        context = self.context(features='This vegan assurance applies only to our other variant.')
        assessment = policy.base_assessment(context, complete=True)
        assessment['identity']['status'] = 'conflicted'
        assessment['material_issues'] = [{'code': 'identity_conflict',
            'target': 'The only assurance explicitly excludes this variant',
            'rule_id': 'full-product-identity-v1', 'blocks_claim': True,
            'evidence_refs': [policy.reference('/product/features', context['product']['features'])],
            'resolution_refs': []}]
        result = policy.evaluate(classifier.product_context_for_llm(context),
            **classifier.policy_facts(context), assessment=assessment,
            requested_status='vegan', requested_reason='tagged')
        self.assertEqual(result['vegan_status'], 'unknown')
        self.assertTrue(result['positive_block'])
        self.assertTrue(result['assessment']['material_issues'])

    def saved_pair(self):
        context, audit = replay_fixtures.PolicyReplayTests().records('Example Shampoo', 'Water',
            official_vegan=1, package_type='Glass Bottle', features='Recycled plastic bottle')
        assessment = policy.base_assessment(context, complete=True)
        tag = policy.reference('/product/official_vegan', '1')
        for dimension in policy.DIMENSIONS:
            if dimension != 'identity':
                assessment[dimension] = {'status': 'covered_by_claim', 'evidence_refs': [tag]}
        assessment['positive_support'] = [{'reason': 'tagged', 'evidence_refs': [tag]}]
        reading = {'product_id': '1', 'vegan_status': 'vegan', 'vegan_reason': 'tagged',
            'confidence': 'certain', 'summary': 'Applicable whole-product assurance',
            'evidence': [], 'ambiguity_notes': [], 'assessment': assessment}
        gated = copy.deepcopy(assessment)
        gated['material_issues'] = [{'code': 'evidence_conflict', 'target': 'bottle copy',
            'rule_id': 'component-material-consistency-v1', 'blocks_claim': True,
            'evidence_refs': [policy.reference('/product/features', 'Recycled plastic bottle')],
            'resolution_refs': []}]
        audit['knowledge_version'] = policy.KNOWLEDGE_VERSION
        audit['evidence_json'] = json.dumps({'assessment': gated,
            'independent_passes': [copy.deepcopy(reading), copy.deepcopy(reading)]})
        return context, audit

    def test_exact_saved_agreed_positive_can_clear_obsolete_mechanical_downgrade(self):
        context, audit = self.saved_pair()
        original = copy.deepcopy(audit)
        result = replay.replay(context, audit)
        self.assertEqual((result.vegan_status, result.vegan_reason), ('vegan', 'tagged'))
        self.assertTrue(result.evidence['replayed_agreed_positive_materiality'])
        self.assertEqual(result.raw_response, 'retained response')
        self.assertEqual(result.evidence['policy_replay']['new_model_calls'], 0)
        self.assertEqual(audit, original)

    def test_saved_unknown_or_raw_disagreement_never_promoted(self):
        for statuses in [('unknown', 'unknown'), ('vegan', 'unknown')]:
            context, audit = self.saved_pair()
            evidence = json.loads(audit['evidence_json'])
            for row, status in zip(evidence['independent_passes'], statuses):
                row['vegan_status'] = status
                row['vegan_reason'] = 'tagged' if status == 'vegan' else None
            audit['evidence_json'] = json.dumps(evidence)
            with self.subTest(statuses=statuses):
                self.assertEqual(replay.replay(context, audit).vegan_status, 'unknown')

    def test_recovery_does_not_erase_original_material_issue_or_accept_changed_input(self):
        context, audit = self.saved_pair()
        evidence = json.loads(audit['evidence_json'])
        for row in evidence['independent_passes']:
            row['assessment']['material_issues'] = [{'code': 'evidence_conflict',
                'target': 'actual supplied component restriction', 'rule_id': 'semantic-component-scope',
                'blocks_claim': True, 'evidence_refs': [policy.reference('/product/features', context['product']['features'])],
                'resolution_refs': []}]
        audit['evidence_json'] = json.dumps(evidence)
        self.assertEqual(replay.replay(context, audit).vegan_status, 'unknown')
        context['product']['ingredients'] = 'Water, Milk'
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            replay.replay(context, audit)
