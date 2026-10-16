"""Complete dietary lists retain manufacturer precedence and scope guards."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import classify_ocado_vegan as classifier
import vegan_policy as policy


class SlashDietaryAttributesTests(unittest.TestCase):
    def test_independent_vegan_attribute_outranks_name_but_requires_paired_readings(self):
        text = 'Vegan / Plant Based / High in Protein / No artificial sweetners'
        context = {'product': {'id': '1', 'name': 'Example Vegan Protein', 'brand': 'Example',
            'ingredients': 'Pea Protein, Natural Flavourings', 'dietary_information': text},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}
        self.assertTrue(policy.dietary_attribute_claim(text))
        self.assertTrue(policy.manufacturer_reference_claim(context,
            policy.reference('/product/dietary_information', text)))
        result = classifier.classify_rule_candidate(context)
        self.assertEqual((result.vegan_status, result.vegan_reason), ('vegan', 'manufacturer'))
        self.assertIsNone(classifier.classify_by_rules(context))

    def test_formula_scope_negation_exceptions_and_other_products_are_not_lists(self):
        for text in ('Vegan formula / Plant Based', 'Not vegan / High in Protein',
                     'Vegan / except the filter', 'Vegan / other variants only',
                     'Vegan / some products', 'Packaging / Vegan / Plant Based',
                     'Vegan / probably animal-free', 'Vegan / Contains Milk'):
            with self.subTest(text=text):
                self.assertFalse(policy.dietary_attribute_claim(text))

    def test_nutrition_claims_alone_cannot_establish_vegan_suitability(self):
        for text in ('Plant Based / High in Protein / No artificial sweeteners',
                     'High in Protein / No artificial sweetners'):
            self.assertFalse(policy.dietary_attribute_claim(text))
