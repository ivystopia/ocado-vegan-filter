"""Complete gift-set identities bind to their independent ingredient headings."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity
import classify_ocado_vegan as classifier


class BodyWashSetIdentityTests(unittest.TestCase):
    def context(self, last='Rose & Geranium', count=4, other=False):
        declared = ['Lemongrass & Ginger', 'Tonka Bean & Neroli', 'Oud, Cedar & Amber', 'Coconut, Vanilla & Shea']
        described = declared[:3] + [last]
        subject = 'Our other discovery set contains' if other else 'This curated discovery set contains'
        return {'product': {'id': '1', 'name': 'Example Discovery Gift Set', 'official_vegan': 1,
            'other_information': 'Box Contents: ' + ', '.join(declared) + ' Safety Warning: Keep safe.',
            'ingredients': ', '.join(label + ': Aqua (Water), Glycerin, Citric Acid' for label in declared),
            'detailed_description': subject + ', ' + ', '.join('100ml ' + label + ' Body Wash' for label in described[:count]) + '.'},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}

    def test_plant_variant_copy_difference_leaves_tag_for_semantic_assessment(self):
        value = self.context()
        self.assertTrue(identity.body_wash_set_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_matching_partial_and_other_sets_do_not_conflict(self):
        for value in [self.context(last='Coconut, Vanilla & Shea'), self.context(count=2), self.context(other=True)]:
            with self.subTest(description=value['product']['detailed_description']):
                self.assertEqual(identity.body_wash_set_conflict_refs(value), [])

    def test_uncorroborated_contents_are_not_a_definite_identity_conflict(self):
        value = self.context()
        value['product']['ingredients'] = 'Aqua, Parfum, Glycerin'
        self.assertEqual(identity.body_wash_set_conflict_refs(value), [])
