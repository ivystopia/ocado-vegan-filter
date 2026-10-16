"""Flavour headings are ambiguous; nested/adjacent animal content stays strict."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import classify_ocado_vegan as c


class FlavourSeasoningScopeTests(unittest.TestCase):
    def context(self, ingredients, **fields):
        return {'product':{'id':'1','name':'Example plant-style flakes','ingredients':ingredients,**fields},
                'flags':[],'categories':[],'manufacturer_vegan_evidence':[]}

    def test_flavour_heading_does_not_establish_animal_content_or_vegan_source(self):
        for animal in ('salmon','chicken','beef','prawn'):
            context=self.context(f'Wheat protein, {animal} flavour seasoning (sugar, natural flavouring)')
            candidate=c.classify_rule_candidate(context)
            self.assertTrue(candidate is None or candidate.vegan_status=='unknown',candidate)
            self.assertIsNone(c.classify_by_rules({**context,'product':{**context['product'],'dietary_information':'Suitable for vegans'}}))

    def test_real_animal_ingredients_inside_and_outside_flavour_components_survive(self):
        for ingredients in ('Salmon flavour seasoning (salmon powder, salt)',
                            'Salmon flavour seasoning (sugar, natural flavouring), milk powder',
                            'Chicken flavour seasoning (chicken fat, salt)',
                            'Fish flavour powder (cod, salt)',
                            'Salmon, sugar, natural flavouring'):
            result=c.classify_by_rules(self.context(ingredients,official_vegan=1))
            self.assertEqual(result.vegan_status,'nonvegan',ingredients)
            self.assertTrue(result.evidence['tag_animal_conflict'])

    def test_source_qualified_species_styles_are_not_content_declarations(self):
        for text in ('Plant Based Salmon Style Flakes Made with Wheat Protein', 'Plant-based Tuna Style Flakes', 'Salmon-style slices'):
            cleaned=c.strip_explicit_absence_and_plant_names(text)
            self.assertEqual(c.ingredient_matches(cleaned,c.NONVEGAN_INGREDIENT_PATTERNS),[],text)
        value=c.strip_explicit_absence_and_plant_names('Salmon-style flakes (salmon, wheat protein)')
        self.assertTrue(c.ingredient_matches(value,c.NONVEGAN_INGREDIENT_PATTERNS))

    def test_qualification_after_eighth_word_survives_declaration_extraction(self):
        value=self.context('Wheat Protein, Salmon Flavour Seasoning (Sugar, Natural Flavouring)',
            dietary_information='Suitable for vegans',
            other_information='Full Product Name: Ready to Eat Plant Based Salmon Style Flakes Made with Wheat Protein Marinated in a Sweet Chilli Salmon Flavour Seasoning. Storage: Keep refrigerated.')
        candidate=c.classify_rule_candidate(value)
        self.assertEqual((candidate.vegan_status,candidate.vegan_reason),('vegan','manufacturer'))
        self.assertIsNone(c.classify_by_rules(value))

    def test_unqualified_fish_ingredient_is_never_erased(self):
        self.assertEqual(c.strip_explicit_absence_and_plant_names('Salmon (Fish), salt'),'Salmon (Fish), salt')
