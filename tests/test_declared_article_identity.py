"""Explicit ingredient, article and label identities must agree about the SKU."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity


def context(name, **fields):
    return {'product': {'id': '1', 'name': name, **fields}, 'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class DeclaredArticleIdentityTests(unittest.TestCase):
    def test_own_sunscreen_strength_is_not_range_advice(self):
        self.assertTrue(identity.sunscreen_strength_conflict_refs(context('Example SPF15 Dry Oil Sun Spray',
            features='Broad spectrum SPF15 sunscreen, SPF30 - Sun screen offers UVA and UVB protection')))
        for name, feature in [('Example SPF30 Sun Spray', 'SPF30 - Sun screen offers UVA and UVB protection'),
                              ('Example SPF50+ Sun Spray', 'SPF50 - Sunscreen protection'),
                              ('Example SPF15 Sun Spray', 'Our other SPF30 sunscreen offers protection'),
                              ('Example SPF15 Sun Spray', 'SPF30 - Sunscreen recommended instead for children'),
                              ('Example SPF15 and SPF30 Sun Spray Set', 'SPF30 - Sunscreen protection'),
                              ('Example SPF15 Sun Spray', 'Choose SPF30 sunscreen for higher protection')]:
            self.assertEqual(identity.sunscreen_strength_conflict_refs(context(name, features=feature)), [])

    def test_complete_toothpaste_flavour_identity_is_not_an_aroma_ingredient(self):
        value=context('Example Natural Toothpaste Bubblegum', detailed_description='Nine flavours in our range. • Organic Strawberry-flavoured toothpaste • Suitable for vegans')
        self.assertTrue(identity.toothpaste_flavour_attribute_conflict_refs(value))
        for name, text in [('Example Toothpaste Strawberry', 'Details • Organic Strawberry flavoring • Vegan'),
                           ('Example Toothpaste Bubblegum', 'Details • Organic Bubblegum flavouring • Vegan'),
                           ('Example Toothpaste Bubblegum', 'Details • Our other toothpaste has organic strawberry flavoring • Vegan'),
                           ('Example Toothpaste Bubblegum', 'Details • Organic strawberry and bubblegum flavoring • Vegan'),
                           ('Example Toothpaste Bubblegum and Strawberry Set', 'Details • Organic strawberry flavouring • Vegan'),
                           ('Example Toothpaste Bubblegum', 'Aroma ingredients include strawberry flavouring.'),
                           ('Example Toothpaste Bubblegum', 'Details • Organic Strawberry flavoring • Vegan')]:
            self.assertEqual(identity.toothpaste_flavour_attribute_conflict_refs(context(name, detailed_description=text)), [])

    def test_named_grain_with_only_cross_contact_is_not_recipe_identity(self):
        value = context('Example BBQ Oat Crunch Snack', ingredients='Fried corn, peas, sunflower oil', allergens='May Contain Milk, May Contain Oats')
        self.assertTrue(identity.named_grain_recipe_conflict_refs(value))
        for ingredients, allergens, name in [('Oats, Corn, Peas', 'May Contain Oats', 'Oat Crunch Snack'),
                                           ('Corn, Peas', 'Contains Oats', 'Oat Crunch Snack'),
                                           ('Corn, Peas', 'May Contain Oats', 'Oat-style Crunch Snack'),
                                           ('', 'May Contain Oats', 'Oat Crunch Snack')]:
            self.assertEqual(identity.named_grain_recipe_conflict_refs(context(name, ingredients=ingredients, allergens=allergens)), [])

    def test_carbohydrate_gel_has_a_different_own_drink_feature(self):
        fields = {'other_information': 'Full Product Name: Orange flavoured gel. Storage: Keep cool',
                  'specification': 'Suitable for vegans 23g of carbohydrate energy per gel'}
        self.assertTrue(identity.gel_drink_identity_conflict_refs(context('Example Energy Gel Orange',
            features='Light & refreshing electrolyte drink with fruit flavours Sugar free with zero calories', **fields)))
        for feature in ['Refreshing energy gel with orange flavour', 'Take with an electrolyte drink with zero calories',
                        'Our other electrolyte drink has zero calories', 'Light & refreshing electrolyte drink with 90 calories']:
            self.assertEqual(identity.gel_drink_identity_conflict_refs(context('Example Energy Gel Orange', features=feature, **fields)), [])

    def test_formal_wine_year_is_not_distributor_history_or_a_blend(self):
        self.assertTrue(identity.formal_vintage_conflict_refs(context('Example Chardonnay', current_vintage='2024',
            other_information='Full Product Name: 2021 South Australia Chardonnay Distributor: Example Drinks')))
        for formal in ['2024 South Australia Chardonnay', 'South Australia Chardonnay Distributor: Founded in 2021',
                       '2021/2024 Chardonnay blend', 'South Australia Chardonnay Additional Information: Our 2021 vintage won a prize']:
            self.assertEqual(identity.formal_vintage_conflict_refs(context('Example Chardonnay', current_vintage='2024', other_information='Full Product Name: '+formal)), [])
