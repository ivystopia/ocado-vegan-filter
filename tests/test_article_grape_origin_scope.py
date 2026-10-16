"""Article, named-grape and wine-origin assertions bind to the sold SKU."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity
import classify_ocado_vegan as classifier


def context(name, **fields):
    return {'product': {'id':'1','name':name,'official_vegan':1,**fields},
            'flags':[],'categories':[],'manufacturer_vegan_evidence':[]}


class ArticleGrapeOriginScopeTests(unittest.TestCase):
    def test_food_assurance_cannot_label_an_independent_garment(self):
        value=context('Green Cotton Cardigan', ingredients='Wheat flour, milk, water',
                      other_information='Full Product Name: Pizza with Chickpea Sauce Storage: Keep frozen.',
                      specification='100% cotton, exclusive of trimmings', dietary_information='Suitable for vegans')
        self.assertTrue(identity.formal_article_kind_conflict_refs(value))
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertFalse(result.evidence['tag_animal_conflict'])
        for name, full in [('Cotton Cardigan','Cotton Cardigan'),
                           ('Pizza Print T-Shirt','Pizza Print T-Shirt'),
                           ('Chocolate and Shirt Gift Bundle','Chocolate'),
                           ('Pizza','Pizza with vegetables')]:
            self.assertEqual(identity.formal_article_kind_conflict_refs(context(name,
                other_information='Full Product Name: '+full)),[])
        self.assertEqual(identity.formal_article_kind_conflict_refs(context(
            'Boots Vitamin C Tablets',brand='Boots',other_information='Full Product Name: Food supplement with Vitamin C')),[])
        teddy=context('Example Milk Chocolate Teddy Red & Blue Jumper',brand='Example',
                      ingredients='Sugar, Milk, Cocoa Butter',other_information='Full Product Name: Milk chocolate')
        self.assertEqual(identity.formal_article_kind_conflict_refs(teddy),[])
        self.assertEqual(classifier.classify_by_rules(teddy).vegan_status,'nonvegan')

    def test_named_greek_varietal_must_match_the_made_from_account(self):
        value=context('Example Xinomavro Greek Rose',
            detailed_description='An aromatic rosé made from Moschofilero and Assyrtiko grapes grown in Greece.')
        self.assertTrue(identity.named_grape_composition_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for name, text in [('Example Xinomavro Rose','Made from Xinomavro and Assyrtiko grapes.'),
                           ('Example Pinot Gris','Made from Pinot Grigio grapes.'),
                           ('Example Xinomavro Rose','Our other wine is made from Assyrtiko grapes.'),
                           ('Example Xinomavro Rose','Serve with a dessert made from Assyrtiko grapes.'),
                           ('Example Xinomavro Style Rose','Made from Assyrtiko grapes.')]:
            self.assertEqual(identity.named_grape_composition_conflict_refs(context(name,detailed_description=text)),[])

    def test_direct_wine_of_origin_is_not_oak_address_or_bottling_scope(self):
        value=context('Example Sauvignon Blanc', country_of_origin='Produce of the EU',
                      features='Wine of Nancagua, Chile, Suitable for vegans')
        self.assertTrue(identity.conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for origin, text in [('Produce of the EU','Wine of France, Suitable for vegans'),
                             ('Chile','Wine of Nancagua, Chile'),
                             ('Produce of the EU','Aged in Chilean oak barrels'),
                             ('Produce of the EU','Bottled in Chile'),
                             ('Produce of the EU','Wine of Chile bottled in France'),
                             ('Produce of the EU','Serve with wine of Chile')]:
            self.assertEqual(identity.conflict_refs(context('Example Sauvignon Blanc',
                country_of_origin=origin,features=text)),[])
