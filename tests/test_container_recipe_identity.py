"""Explicit primary-container and complete recipe-count review blockers."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity
import classify_ocado_vegan as classifier


def context(**fields):
    return {'product': {'id': '1', 'name': 'Example Food', **fields}, 'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class ContainerRecipeIdentityTests(unittest.TestCase):
    def test_soup_explicit_blend_is_composition_review(self):
        value=context(name='Butternut Chilli Soup', ingredients='Water, Butternut Squash (27%), Carrots (14%), Coconut Cream, Turmeric',
                      detailed_description='A silky blend of butternut, carrot, mango and turmeric, creamy coconut and a chilli kick.', official_vegan=1)
        self.assertTrue(identity.declared_soup_blend_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        value['product']['ingredients'] += ', Milk'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')

    def test_soup_named_blend_cannot_infer_absence_from_groups(self):
        description='A silky blend of butternut, carrot, mango and turmeric.'
        for ingredients in ['Butternut Squash, Carrots, Mango Purée, Turmeric',
                            'Butternut Squash, Carrots, Fruit Purée, Turmeric',
                            'Butternut Squash, Carrots, Natural Flavourings, Turmeric']:
            self.assertEqual(identity.declared_soup_blend_conflict_refs(context(name='Example Soup', ingredients=ingredients,detailed_description=description)),[])

    def test_other_soup_or_serving_recipe_does_not_assert_own_blend(self):
        for description in ['Our other soup uses a silky blend of butternut, carrot, mango and turmeric.',
                            'Try a blend of butternut, carrot, mango and turmeric.',
                            'A creamy soup with mango-like tasting notes.']:
            self.assertEqual(identity.declared_soup_blend_conflict_refs(context(name='Example Soup', ingredients='Butternut Squash, Carrots, Turmeric',detailed_description=description)),[])

    def test_nominal_can_feature_conflicts_with_bottle(self):
        value=context(name='Example Soft Drink', package_type='Bottle', features='No sugar, Can of coke, Suitable for Vegans')
        self.assertTrue(identity.component_material_conflict_refs(value))
        # This mixed field is not independently accepted by the claim parser.
        self.assertEqual(classifier.classify_by_rules(value).vegan_status, 'unknown')
        value['product']['official_vegan'] = 1
        self.assertIsNone(classifier.classify_by_rules(value))
        for text in ['Can be enjoyed cold', 'Can of another drink', 'Try pouring a can of coke', 'Bottle of coke']:
            self.assertEqual(identity.labelled_container_conflict_refs(context(package_type='Bottle', features=text)),[])

    def test_pot_carton_conflict_requires_own_locator_and_corroboration(self):
        text='For best before see top of carton. Tetra Pak® Tetra Brik® Aseptic'
        value=context(name='Coconut Cream', package_type='Pot', other_information=text, official_vegan=1)
        self.assertTrue(identity.component_material_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for package, other in [('Carton',text),('Pot','For best before see top of pot.'),('Pot','For best before see top of carton.'),('Pot',text+' Outer carton.')]:
            self.assertEqual(identity.labelled_container_conflict_refs(context(package_type=package,other_information=other)),[])

    def test_preservative_claim_conflict_is_composition_review_not_animal_proof(self):
        value=context(name='Fruit Muesli', ingredients='Dried Fruit (Papaya, Sugar, Preservative (Sulphur Dioxide)), Oats',
                      features='- No added preservatives, Suitable for vegans', official_vegan=1)
        self.assertTrue(identity.declared_preservative_claim_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        value['product']['ingredients']='Milk, Preservative (Potassium Sorbate)'
        value['product']['name']='Milk Drink'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')
        for ingredients, features in [('Grapes, Naturally Occurring Sulphites','No added preservatives'),
                                      ('Oats. May contain Preservative (Sulphur Dioxide)','No added preservatives'),
                                      ('Preservative (Potassium Sorbate)','Our other recipe uses no added preservatives')]:
            self.assertEqual(identity.declared_preservative_claim_conflict_refs(context(ingredients=ingredients,features=features)),[])

    def test_later_own_awarded_vintage_is_distinct_from_award_year(self):
        value=context(name='Chateau Cedar Vineyard Rose', brand='Chateau Cedar Vineyard', current_vintage='2021',
                      official_vegan=1, other_information='Full Product Name: Rosé French Wine. Cedar Vineyard Organic Rosé 2023 is proud to have won the IWC 2024 Gold Medal.')
        self.assertTrue(identity.later_awarded_vintage_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_earlier_vintage_and_competition_year_do_not_identify_sold_wine(self):
        for text in ['Cedar Vineyard Organic Rosé 2021 is proud to have won the IWC 2024 Gold Medal.',
                     'Cedar Vineyard Organic Rosé won the IWC 2024 Gold Medal.',
                     'Our previous Cedar Vineyard Organic Rosé 2023 is proud to have won the IWC 2024 Gold Medal.',
                     'Other Vineyard Organic Rosé 2023 is proud to have won the IWC 2024 Gold Medal.']:
            self.assertEqual(identity.later_awarded_vintage_conflict_refs(context(brand='Chateau Cedar Vineyard',
                current_vintage='2021', other_information='Full Product Name: Rosé French Wine. '+text)), [])

    def test_aerosol_versus_own_squeeze_bottle_instructions(self):
        instructions='Tip up the bottle. Gently squeeze the bottle and direct product under the toilet rim.'
        value=context(name='Example Toilet Cleaner', package_type='Aerosol', preparation_and_usage=instructions,
                      features='Suitable for vegans')
        self.assertTrue(identity.component_material_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for usage in ['Squeeze the trigger to spray.', 'Tip up the bottle of another cleaner; squeeze the bottle.',
                      'Use this refill to fill the bottle. Tip up the bottle and squeeze the bottle.']:
            self.assertEqual(identity.labelled_container_conflict_refs(context(package_type='Aerosol', preparation_and_usage=usage)), [])

    def test_muesli_diversity_counts_compare_own_recipe_accounts(self):
        description='Made with 12 of your 30 weekly diverse plant-Based foods.'
        marketing='Packed with 16 fabulous fruits, vegetables, wholegrains, legumes, nuts and seeds, our prebiotic muesli gives your biome diversity.'
        value=context(name='Example Fruit Muesli', detailed_description=description, brand_marketing=marketing, official_vegan=1)
        self.assertTrue(identity.explicit_recipe_count_conflict_refs(value))
        self.assertIsNone(classifier.classify_by_rules(value))
        for text in [marketing.replace('16', '12'), marketing.replace('our prebiotic muesli', 'our range'),
                     'Try 16 different plant foods across our muesli range.', 'Contains 16 servings. Our prebiotic muesli.']:
            self.assertEqual(identity.declared_plant_food_count_conflict_refs(context(name='Example Fruit Muesli', detailed_description=description, brand_marketing=text)), [])
        value['product']['name']='Example Milk Muesli'
        value['product']['ingredients']='Oats, Milk'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status, 'nonvegan')

    def test_recipe_count_review_does_not_erase_correctly_matched_animal_content(self):
        value=context(name='Example Milk Drink', ingredients='Milk (100%)', official_vegan=1,
                      detailed_description='This milk drink is made from just two ingredients.')
        self.assertTrue(identity.explicit_recipe_count_conflict_refs(value))
        result=classifier.classify_by_rules(value)
        self.assertIsNotNone(result)
        self.assertEqual(result.vegan_status,'nonvegan')

    def test_container_review_does_not_erase_correctly_matched_animal_content(self):
        value=context(name='Example Chocolate Milk Drink', ingredients='Milk (75%), Cocoa, Sugar',
                      official_vegan=1, package_type='Glass Bottle', other_information='Best before: See bottom of tin.')
        result=classifier.classify_by_rules(value)
        self.assertIsNotNone(result)
        self.assertEqual(result.vegan_status,'nonvegan')
        self.assertTrue(identity.component_material_conflict_refs(value))
        self.assertEqual(identity.conflict_refs(value), [])

    def test_own_date_locator_is_not_an_incidental_or_outer_tin(self):
        self.assertTrue(identity.labelled_container_conflict_refs(context(package_type='Glass Bottle', other_information='Storage: Keep cool. Lot & best before: See bottom of tin.')))
        for package, text, name in [('Can', 'Lot & best before: See bottom of tin.', 'Example Food'),
                                    ('Glass Bottle', 'Best before: See base of bottle.', 'Example Food'),
                                    ('Glass Bottle', 'This oil can be used in a baking tin.', 'Example Food'),
                                    ('Glass Bottle', 'Lot & best before: See bottom of tin. Outer presentation container.', 'Example Food'),
                                    ('Glass Bottle', 'Lot & best before: See bottom of tin.', 'Example Food Gift Set')]:
            self.assertEqual(identity.labelled_container_conflict_refs(context(name=name, package_type=package, other_information=text)), [])

    def test_complete_simple_recipe_count_does_not_count_groups_or_serving_recipes(self):
        ingredients='Apples (44%), Pears (42%), Mangos (14%)'
        text='Our Mango rolls are made from just four ingredients.'
        self.assertTrue(identity.explicit_recipe_count_conflict_refs(context(ingredients=ingredients, detailed_description=text)))
        for values, description in [(ingredients, 'Our Mango rolls are made from just three ingredients.'),
                                    ('Fruit (Apples, Pears) (86%), Mangos (14%)', text),
                                    ('Apples, Pears, Mangos', text),
                                    ('Apples (44%), Pears (42%), Mangos (13%)', text),
                                    (ingredients, 'Try this recipe made from just four ingredients.'),
                                    (ingredients, 'Our other rolls are made from just four ingredients.'),
                                    (ingredients, 'Made from just four ingredients including evaporated water.'),
                                    (ingredients, 'Our rolls use at least four ingredients.')]:
            self.assertEqual(identity.explicit_recipe_count_conflict_refs(context(ingredients=values, detailed_description=description)), [])
