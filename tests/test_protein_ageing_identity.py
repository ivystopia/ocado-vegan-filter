"""Explicit complete recipe and pre-release production accounts must agree."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity


def context(**fields):
    return {'product': {'id': '1', 'name': 'Example', **fields}, 'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class ProteinAgeingIdentityTests(unittest.TestCase):
    def test_named_protein_blend_source_is_not_a_flour_protein_assumption(self):
        text='This wrap is crafted from a blend of chickpea flour, rice protein, and pea protein.'
        self.assertTrue(identity.protein_blend_composition_conflict_refs(context(ingredients='Water, Rice Protein, Chickpea Flour', detailed_description=text)))
        for ingredients, description in [('Water, Rice Protein, Chickpea Flour, Pea Protein', text),
                                          ('Rice Protein, Chickpea Flour, Pea Flour', text),
                                          ('Rice Protein, Vegetable Protein, Chickpea Flour', text),
                                          ('', text),
                                          ('Rice Protein, Chickpea Flour', 'Try another recipe crafted from a blend of rice protein and pea protein.'),
                                          ('Rice Protein, Chickpea Flour', 'Our other wrap is crafted from a blend of rice protein and pea protein.'),
                                          ('Soya Flour, Rice Protein', 'This wrap is made from a blend of soy protein and rice protein.')]:
            self.assertEqual(identity.protein_blend_composition_conflict_refs(context(ingredients=ingredients, detailed_description=description)), [])

    def test_two_complete_ageing_accounts_are_not_partial_or_minimum_periods(self):
        first='The wine was aged in mixed barrels - American oak staves and French oak ends for 9 months, completing its ageing with 2 months in bottle before being released to the market.'
        second='This wine spends 28 months in French oak barrels, completing its ageing with a further 36 months in bottle prior to release.'
        self.assertTrue(identity.wine_ageing_account_conflict_refs(context(other_information=first, vinification_details=second)))
        self.assertTrue(identity.wine_ageing_account_conflict_refs(context(other_information=first,
            vinification_details='Fermentation was at between 26 and 28C. ' + second)))
        for text in [first, second.replace('28 months', 'at least 28 months'),
                     second.replace('prior to release', 'during an additional stage'),
                     'Our previous wine spends 28 months in French oak barrels and 36 months in bottle prior to release.',
                     'This wine spends 9-28 months in French oak barrels and 36 months in bottle prior to release.',
                     'This wine spends between 9 and 28 months in French oak barrels and 36 months in bottle prior to release.',
                     'This wine spends 28 months in French oak barrels prior to release.']:
            self.assertEqual(identity.wine_ageing_account_conflict_refs(context(other_information=first, vinification_details=text)), [])
