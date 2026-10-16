from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import native_product_identity as identity
import vegan_policy as policy
import classify_ocado_vegan as classifier


class DeclaredRecipeComponentsTests(unittest.TestCase):
    def context(self, ingredients, description, features='', **fields):
        return {'product': {'id':'1', 'name':'Example food', 'ingredients':ingredients,
            'detailed_description':description, 'features':features, **fields},
            'flags':[], 'categories':[], 'manufacturer_vegan_evidence':[]}

    def berries(self, **fields):
        values = dict(ingredients='Oats, Freeze Dried Berries (2%) (Blackcurrant, Cranberry, Strawberry), Natural Flavourings',
            description='Delicious diversity 15 Plant-Based Foods. Blackcurrant, Cherry and Strawberry & Oats & Seeds',
            features='Wholegrain oats, blackcurrants, cherries, strawberries & more, Suitable for vegans')
        values.update(fields)
        return self.context(**values)

    def test_corroborated_substituted_berry_requires_review(self):
        context = self.berries()
        refs = identity.declared_recipe_components_conflict_refs(context)
        self.assertEqual(len(refs), 3)
        for ref in refs:
            policy.resolve_reference(context, ref)

    def test_matching_berry_profile_is_not_a_conflict(self):
        self.assertEqual(identity.declared_recipe_components_conflict_refs(
            self.berries(ingredients='Oats, Berries (Blackcurrant, Cherry, Strawberry)')), [])

    def test_unexpanded_group_cannot_establish_berry_exclusion(self):
        self.assertEqual(identity.declared_recipe_components_conflict_refs(
            self.berries(ingredients='Oats, Berries, Natural Flavourings')), [])

    def test_partial_title_and_uncorroborated_description_do_not_suffice(self):
        self.assertEqual(identity.declared_recipe_components_conflict_refs(
            self.berries(features='Wholegrain oat granola')), [])

    def test_berry_serving_ideas_are_not_own_recipe(self):
        self.assertEqual(identity.declared_recipe_components_conflict_refs(self.berries(
            description='15 Plant-Based Foods. Serve with blackcurrant, cherry and strawberry.',
            features='Serving suggestion: blackcurrants, cherries and strawberries.')), [])

    def test_explicit_own_vegetable_pair_differs_from_closed_recipe(self):
        context = self.context('Chickpeas*, Vegetables* (35%) (Onions*, Hokkaido Pumpkin*), Spices*',
            'Combined with tomatoes and pumpkin and lightly spiced, these falafels pair with salads.')
        refs = identity.declared_recipe_components_conflict_refs(context)
        self.assertEqual(len(refs), 2)
        for ref in refs:
            policy.resolve_reference(context, ref)

    def test_matching_vegetable_pair_is_not_a_conflict(self):
        self.assertEqual(identity.declared_recipe_components_conflict_refs(self.context(
            'Chickpeas, Vegetables (Tomatoes, Pumpkin)', 'Combined with tomatoes and pumpkin, these are tasty.')), [])

    def test_vegetable_accompaniments_are_not_recipe(self):
        for description in ('Serve combined with tomatoes and pumpkin.',
                            'Try these falafels with tomatoes and pumpkin.',
                            'Another recipe is made with tomatoes and pumpkin.'):
            with self.subTest(description=description):
                self.assertEqual(identity.declared_recipe_components_conflict_refs(self.context(
                    'Chickpeas, Vegetables (Onions, Pumpkin)', description)), [])

    def test_unexpanded_vegetable_group_does_not_prove_omission(self):
        self.assertEqual(identity.declared_recipe_components_conflict_refs(self.context(
            'Chickpeas, Vegetables, Spices', 'Combined with tomatoes and pumpkin, these are tasty.')), [])

    def test_wheat_varieties_do_not_prove_recipe_conflict(self):
        self.assertEqual(identity.declared_recipe_components_conflict_refs(self.context(
            'Wholemeal Wheat Flour, Rye, Barley, Wholemeal Spelt Flour (Wheat)',
            'Made from a blend of wheat, rye, barley, spelt and emmer grains.')), [])

    def test_definite_animal_content_keeps_nonvegan_precedence(self):
        context = self.berries(ingredients='Milk, Berries (Blackcurrant, Cranberry, Strawberry)', official_vegan=1)
        result = classifier.classify_by_rules(context)
        self.assertEqual(result.vegan_status, 'nonvegan')

    def test_plant_recipe_copy_does_not_defeat_independent_tag(self):
        context = self.berries(official_vegan=1)
        result = policy.evaluate(classifier.product_context_for_llm(context), **classifier.policy_facts(context))
        self.assertEqual(result['vegan_status'], 'vegan')
        self.assertFalse(result['positive_block'])
        self.assertTrue(result['copy_discrepancy_refs'])
        self.assertIsNone(classifier.classify_by_rules(context))
