"""Nominal own-bottle material bullets retain scope and recycling modifiers."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity
import classify_ocado_vegan as classifier


def context(features, package='Glass Bottle', ingredients='Aqua, Glycerin'):
    return {'product': {'id': '1', 'name': 'Example Face Wash', 'features': features,
                        'package_type': package, 'ingredients': ingredients,
                        'dietary_information': 'Suitable for Vegans', 'official_vegan': 0},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class BottleMaterialFeatureTests(unittest.TestCase):
    def test_nominal_post_consumer_bullet_after_feature_punctuation_conflicts(self):
        for text in ['Bottle made from 100% post-consumer recycled plastic.',
                     'Hydrated skin., Bottle made from 100% post-consumer recycled plastic.',
                     'Our bottle made from PCR plastic.', 'Bottle is made from recycled plastic.']:
            with self.subTest(text=text):
                self.assertTrue(identity.component_material_conflict_refs(context(text)))

    def test_matching_material_other_bottle_and_cap_or_label_are_not_conflicts(self):
        for text, package in [('Bottle made from 100% post-consumer recycled plastic.', 'Plastic Bottle'),
                              ('Bottle made from recycled glass.', 'Glass Bottle'),
                              ('Bottle cap made from recycled plastic.', 'Glass Bottle'),
                              ('Bottle label made from recycled plastic.', 'Glass Bottle'),
                              ('Our other bottle made from recycled plastic.', 'Glass Bottle'),
                              ('Bottle may be made from recycled plastic.', 'Glass Bottle'),
                              ('Recycle this bottle with plastic caps removed.', 'Glass Bottle')]:
            with self.subTest(text=text):
                self.assertEqual(identity.component_material_conflict_refs(context(text, package)), [])

    def test_definite_animal_formula_survives_component_review(self):
        value = context('Bottle made from 100% post-consumer recycled plastic.', ingredients='Milk')
        self.assertEqual(classifier.classify_by_rules(value).vegan_status, 'nonvegan')
