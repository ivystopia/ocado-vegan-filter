"""Explicit formulation/variant contradictions; compatible accounts survive."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity
import classify_ocado_vegan as classifier


def context(name, **fields):
    return {'product': {'id': '1', 'name': name, 'official_vegan': 1, **fields},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class DeclaredCompositionConsistencyTests(unittest.TestCase):
    def test_explicit_flavour_attribute_conflicts_with_single_named_sku(self):
        value=context('Example Orange + Vitamin C Tablets', brand='Example', features='Gluten free, Lemon flavour, Suitable for vegans')
        self.assertTrue(identity.declared_flavour_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for name, feature in [('Example Lemon Tablets', 'Lemon flavour'),
                              ('Example Orange & Lemon Tablets', 'Lemon flavour'),
                              ('Example Orange Tablets', 'Serve with lemon flavour'),
                              ('Orange Example Lemon Tablets', 'Lemon flavour'),
                              ('Example Orange Tablets', 'No lemon flavour'),
                              ('Example Orange Tablets', 'Lemon flavour notes')]:
            self.assertEqual(identity.declared_flavour_conflict_refs(context(name, brand='Example', features=feature)), [])

    def test_explicit_brine_vinegar_type_must_match_a_named_declared_type(self):
        value=context('Roasted Peppers', ingredients='Peppers, Spirit Vinegar, Salt',
                      detailed_description='These peppers are preserved in a delicate red wine vinegar brine.')
        self.assertTrue(identity.vinegar_composition_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for ingredients in ('Peppers, Red Wine Vinegar, Salt', 'Peppers, Spirit Vinegar, Red Wine Vinegar', 'Peppers, Vinegar, Salt'):
            value['product']['ingredients']=ingredients
            self.assertEqual(identity.vinegar_composition_conflict_refs(value), [])
        value['product']['ingredients']='Peppers, Spirit Vinegar'
        for text in ('Try this recipe preserved in red wine vinegar brine.',
                     'Our other peppers are preserved in red wine vinegar brine.'):
            value['product']['detailed_description']=text
            self.assertEqual(identity.vinegar_composition_conflict_refs(value), [])

    def test_oil_blend_uses_botanical_aliases_and_preserves_marker_footnotes(self):
        value=context('Example Hand Wash', ingredients='Aqua, glycerin, citrus aurantium dulcis (orange) peel oil. *essential oils **potential allergens',
                      detailed_description='Essential oils of Grapefruit, Bitter Orange and Lavender.')
        self.assertTrue(identity.essential_oil_composition_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for ingredients in ('Aqua, Citrus Paradisi Peel Oil, Lavandula Angustifolia Oil',
                            'Aqua, grapefruit oil, lavender oil',
                            'Aqua, parfum', 'Aqua, essential oil blend'):
            value['product']['ingredients']=ingredients
            self.assertEqual(identity.essential_oil_composition_conflict_refs(value), [])
        value['product']['ingredients']='Aqua, glycerin'
        for text in ('A fragrance with notes of Grapefruit and Lavender.',
                     'Our other wash contains essential oils of Grapefruit and Lavender.',
                     'Essential oils of Lavender and Orange.'):
            value['product']['detailed_description']=text
            self.assertEqual(identity.essential_oil_composition_conflict_refs(value), [])

    def test_main_and_minor_fermentation_accounts_require_applicable_scope(self):
        oak='The free run component of the juice filled to French oak for spontaneous/wild fermentation. A small juice portion completed fermentation and maturation in stainless steel.'
        steel='Temperature controlled fermentation in stainless steel define the vinification process.'
        value=context('Example White Wine', other_information=oak, vinification_details=steel)
        self.assertTrue(identity.fermentation_account_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for other, details in [('Fermented in stainless steel then matured in French oak.', steel),
                               (oak, 'Fermentation takes place in French oak and stainless steel.'),
                               ('A small portion matured in steel. Oak barrels are used for ageing.', steel),
                               ('Our previous wine: '+oak, steel)]:
            value['product'].update(other_information=other, vinification_details=details)
            self.assertEqual(identity.fermentation_account_conflict_refs(value), [])
