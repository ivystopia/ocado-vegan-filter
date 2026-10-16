"""A completed positive audit must validate reasons as well as safe statuses."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import audit_whole_product_positives as audit
import classify_ocado_vegan as classifier
import vegan_policy as policy


class PositiveAuditReasonTests(unittest.TestCase):
    def fixture(self):
        cases = []
        readings = []
        predictions = {}
        for i in range(600):
            key = 'case.' + str(i)
            context = {'product': {'id': str(i), 'name': 'Carrots', 'official_vegan': 1}}
            refs = [{'path': '/product/name', 'quote': 'Carrots'}, {'path': '/product/official_vegan', 'quote': '1'}]
            annotation = {'case_id': key, 'expected_status': 'vegan', 'expected_reason': 'tagged',
                'required_issue_codes': [], 'input_refs': refs, 'rationale': 'An applicable official vegan tag supports carrots.',
                'external_status': 'unknown', 'source_id': None, 'external_quote': '', 'scope_match': 'No external sources.',
                'component_assessment': {'status': 'established', 'evidence_refs': refs[:1]}}
            case = {'case_id': key, 'classifier_input': context, 'input_sha256': classifier.context_hash(context)}
            cases.append(case)
            readings.append({**case, 'expected': {'vegan_status': 'vegan', 'vegan_reason': 'tagged'},
                'adjudication': {'state': 'accepted', 'policy_version': policy.POLICY_VERSION,
                    'readings': [{'call_id': 'first-blind-pass', 'annotation': annotation},
                                 {'call_id': 'second-blind-pass', 'annotation': copy.deepcopy(annotation)}]}})
            predictions[key] = {'vegan_status': 'vegan', 'vegan_reason': 'tagged'}
        packet = {'policy_version': policy.POLICY_VERSION, 'candidates': cases, 'sampling': {'size': 600}}
        return packet, {'packet_sha256': classifier.context_hash(packet), 'predictions': predictions}, {
            'policy_version': policy.POLICY_VERSION, 'candidates': readings}

    def test_wrong_reason_fails_even_when_all_six_hundred_are_vegan(self):
        packet, predictions, annotations = self.fixture()
        predictions['predictions']['case.599']['vegan_reason'] = 'manufacturer'
        score = audit.score(packet, predictions, annotations)
        self.assertEqual(score['state'], 'failed')
        self.assertEqual(score['reference_counts'], {'vegan': 600})
        self.assertEqual(score['issues'][0]['finding'], 'unsupported_reason')
        self.assertIsNone(score['zero_error_binomial_upper_95'])

    def test_matching_supported_reasons_pass(self):
        packet, predictions, annotations = self.fixture()
        score = audit.score(packet, predictions, annotations)
        self.assertEqual(score['state'], 'passed')
        self.assertEqual(score['issues'], [])
        self.assertIsNotNone(score['zero_error_binomial_upper_95'])
