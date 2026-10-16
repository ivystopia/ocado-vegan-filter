"""Regression contracts for actual animal content and its lexical lookalikes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import classify_ocado_vegan as classifier
import vegan_policy as policy


def context(name, ingredients=None, **fields):
    return {'product': {'id': '1', 'name': name, 'ingredients': ingredients, **fields},
            'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}


class DefiniteAnimalScopeTests(unittest.TestCase):
    def test_current_production_addition_survives_separate_tasting_and_usage_copy(self):
        value=context('Example French Liqueur', official_vegan=1,
            dietary_information='Suitable for vegans',
            features='An old recipe is said to date to 1510, our crafting still takes place today, The careful and complex production process uses copper stills and oak barrels, followed by blending the batches with honey and saffron, and ageing again, Example is ideal for cocktails and gifts.',
            detailed_description='A mild finish of honey. Serve in cocktails.')
        result=self.assert_status(value,'nonvegan')
        self.assertTrue(result.evidence['tag_animal_conflict'])
        for text in ('A mild finish of honey.',
                     'Try blending the batches with honey for a cocktail recipe.',
                     'Our other liqueur: The production process uses oak, followed by blending the batches with honey.',
                     'The production process uses oak, followed by blending the batches with honey flavouring.',
                     'The production process may involve blending the batches with honey.',
                     'The production process uses oak, followed by blending the batches with honeybush tea.'):
            with self.subTest(text=text):
                self.assert_not_nonvegan(context('Example French Liqueur',official_vegan=1,features=text))

    def test_standard_animal_digestive_enzymes_override_tag(self):
        for ingredient in ('Pepsin Enzyme', 'Pancreatin Enzyme',
                           'Microbial Pepsin, Pancreatin Enzyme'):
            result = self.assert_status(context('Example Digestive Enzyme Tablets',
                ingredient, official_vegan=1), 'nonvegan')
            self.assertTrue(result.evidence['tag_animal_conflict'])
        for ingredient in ('Amylase, Lipase, Protease, Cellulase', 'Microbial Pepsin',
                           'Recombinant Pepsin', 'Fermentation-derived Pancreatin',
                           'No Pepsin, Peppermint Extract', 'Pepsin-free enzyme formula'):
            self.assert_not_nonvegan(context('Example Enzyme Tablets', ingredient, official_vegan=1))
        self.assert_not_nonvegan(context('Example Peppermint Supplement',
            detailed_description='Supports natural pepsin production. Suitable for vegans.'))

    def test_formal_supplement_content_overrides_positive_claims(self):
        value=context('Example Creatine Sachets', 'Creatine, Vitamin C', official_vegan=1,
            detailed_description='Made to be Vegan.',
            other_information='Full Product Name: Strawberry Mango Flavour Powdered Food Supplement with Creatine, Collagen Peptides, Vitamin C. Storage: Keep cool.')
        result=self.assert_status(value,'nonvegan')
        self.assertTrue(result.evidence['tag_animal_conflict'])
        for declared in ('Food supplement with vegan collagen and vitamin C',
                         'Food supplement with collagen formation support',
                         'Food supplement with honey flavouring',
                         'Recipe book with collagen peptides',
                         'Food supplement with creatine. Serve with milk'):
            self.assert_not_nonvegan(context('Example Product', official_vegan=1,
                other_information='Full Product Name: '+declared))

    def assert_status(self, value, status):
        result = classifier.classify_rule_candidate(value)
        self.assertIsNotNone(result)
        self.assertEqual(result.vegan_status, status)
        return result

    def assert_not_nonvegan(self, value):
        result = classifier.classify_rule_candidate(value)
        self.assertTrue(result is None or result.vegan_status != 'nonvegan')

    def test_corroborated_ordinary_eggs_survive_unrelated_storage_warning(self):
        self.assert_status(context('M&S 6 Mixed Size Free Range Eggs',
            other_information='Full Product Name: 6 Mixed size free range eggs Storage: Eggs may be stored in a refrigerator.',
            allergens='Eggs'), 'nonvegan')
        for name, full in [('Decorative Easter Eggs', 'Decorative Easter Eggs'),
                           ('Plant Based Egg Alternative', 'Plant Based Egg Alternative')]:
            self.assert_not_nonvegan(context(name, other_information='Full Product Name: ' + full))

    def test_explicit_pet_food_animal_derivatives(self):
        for ingredients in ('Cereals, Meat and Animal Derivatives, Minerals',
                            'Seeds, Dried Mealworms, Sunflower Oil'):
            with self.subTest(ingredients=ingredients):
                result = self.assert_status(context('Pet Food', ingredients, official_vegan=1), 'nonvegan')
                self.assertTrue(result.evidence['tag_animal_conflict'])
        self.assert_not_nonvegan(context('Bird Food', 'Seeds, No Mealworms'))

    def test_corroborated_merino_wool_material(self):
        self.assert_status(context('M&S Merino Wool Socks',
            detailed_description='These merino wool socks are made from 54% merino wool and 46% polyamide.',
            specification='Composition: 54% Merino Wool, 46% Polyamide'), 'nonvegan')
        self.assert_status(context('M&S Mens Autograph 3pk Merino Wool Blend Socks, 6-8, Black',
            detailed_description="Upgrade your sock drawer with these luxurious Merino wool-blend socks from M&S' Autograph collection. They are the same quality as M&S' previous Merino Luxury line. Made with reinforced heels and toes for durability."), 'nonvegan')

    def test_animal_component_in_complete_gift_overrides_confirmation(self):
        self.assert_status(context('M&S Womens Do Not Disturb Essential Collection',
            detailed_description='This collection brings together everything needed for a peaceful night. A mulberry silk eye mask completes the ritual, offering a touch of luxury and a truly restorative sleep.',
            dietary_information='Suitable for vegans.'), 'nonvegan')

    def test_material_metaphors_and_external_targets_are_not_content(self):
        for name, description in [
            ('Knitwear Detergent', 'For wool, cashmere and silk. Suitable for vegans.'),
            ('Body Mist', 'A warm cashmere musk fragrance. Vegan.'),
            ('IPA Beer', 'Kettle Hops: Cashmere. Suitable for vegans.'),
            ('Cotton Liners', 'Feather-like comfort. Made from cotton.'),
            ('Vegan Leather Gift', 'Made from imitation leather.'),
            ('Polyester Eye Mask', 'Silk-like softness. Made from polyester.'),
        ]:
            with self.subTest(name=name):
                self.assert_not_nonvegan(context(name, detailed_description=description))

    def test_actual_declaration_not_vetoed_by_separate_warning_or_footer(self):
        self.assert_status(context('Chocolate Drink', ingredients=None,
            allergens='Contains Milk. May contain nuts. No added colours or preservatives.'), 'nonvegan')
        self.assert_not_nonvegan(context('Mocha Drinking Chocolate', 'Cocoa, Soya Lecithin',
            allergens='Not suitable for those with a Milk allergy because allergens are present in the environment.'))
        self.assert_not_nonvegan(context('Plant Drink',
            detailed_description='Our other variant contains milk. This product uses oat drink.'))

    def test_synthetic_beeswax_is_not_proof_of_animal_beeswax(self):
        self.assert_status(context('Mascara', 'Water, Synthetic Beeswax'), 'unknown')
        self.assert_status(context('Mascara', 'Water, Synthetic Beeswax',
            dietary_information='Suitable for vegans.'), 'vegan')
        for animal in ('Beeswax', 'Cera Alba', 'Honey'):
            with self.subTest(animal=animal):
                self.assert_status(context('Mascara', 'Water, Synthetic Beeswax, ' + animal,
                    dietary_information='Suitable for vegans.'), 'nonvegan')

    def test_synthetic_wax_cannot_clear_its_own_source_risk(self):
        value = context('Mascara', 'Water, Synthetic Beeswax')
        assessment = policy.base_assessment(value, complete=True)
        assessment['positive_support'] = [{'reason': 'ingredients',
            'evidence_refs': [policy.reference('/product/ingredients', 'Water, Synthetic Beeswax')]}]
        raw = classifier.ClassificationResult(product_id='1', vegan_status='vegan',
            vegan_reason='ingredients', confidence='certain', summary='Unresolved source',
            source='codex', evidence={'assessment': assessment})
        self.assertEqual(classifier.apply_policy_gate(value, raw).vegan_status, 'unknown')

    def test_egg_absence_and_external_food_target_are_not_egg_identity(self):
        for name, full in [
            ('Biona Organic Egg Free Mayo', 'Organic Egg Free Mayonnaise'),
            ('Hunter & Gather Egg Free Olive Oil Mayo', 'Classic Egg Free Mayo'),
            ('Just Spices Egg Topping', 'Seasoning for egg'),
            ('Eggless Mayonnaise', 'Eggless Mayonnaise'),
        ]:
            with self.subTest(name=name):
                self.assert_not_nonvegan(context(name, other_information='Full Product Name: '+full))

    def test_prose_material_colour_and_cosmetic_cream_are_not_dairy(self):
        for name, description in [
            ('Mason Cash Innovative Kitchen Pie Dish', 'The dish bakes perfect pies every time. Made from grey and cream glazed stoneware, the pie dish heats up gradually to give an even heat distribution when baking.'),
            ('Dove Deodorant', 'Made with a quarter moisturising cream, the gentle formula helps delicate underarm skin recover from shaving irritation.'),
            ('Wild Honey & Cactus Flower Deodorant Refill', 'Includes Honey & Cactus Flower natural deodorant refill - a little sweetener for your pits. Full of warming honey and floral notes.'),
            ('Zacapa Gran Solera', 'Our rum is made from sugar cane honey under the watchful eye of the master distiller.'),
        ]:
            with self.subTest(name=name):
                self.assert_not_nonvegan(context(name, detailed_description=description))

    def test_utensils_and_recipe_cards_do_not_contain_the_described_food(self):
        for name, description in [
            ('Tala Stainless Steel Butter Knife', 'Ideal for smoothing spreading butter and conserves. Includes single Butter Knife. Dishwasher Safe.'),
            ('Tala Stainless Steel Cheese Knife', 'Includes classic shape Cheese Knife with serrated edge and pronged top for picking up cheese.'),
            ('Disney Gnocchi and Cauliflower Cheese Bake Recipe Card', 'Go on an icy adventure with this magical family meal. Made with cauliflower and two types of cheese, this dinner is definitely worth melting for.'),
            ('Plant Based Ice Cream', 'Contains 30% less fat than similar ice cream products.'),
        ]:
            with self.subTest(name=name):
                self.assert_not_nonvegan(context(name, detailed_description=description))

    def test_joined_premises_warning_is_not_intentional_animal_content(self):
        self.assert_not_nonvegan(context('Farmhouse Pickle', 'Vegetables, Vinegar',
            allergens='Contains Barley, Contains Mustard Prepared in premises where Egg, Milk, Soya and Sulphites are also used.'))

    def test_beeswax_candle_declaration_survives_separate_paraffin_absence(self):
        self.assert_status(context('Beeswax Candle Medium', detailed_description=
            'Our Hive pillar candles are made from honeycomb textured beeswax with a clean and natural burn. '
            'Made using pure ingredients, entirely free from paraffin and GM derived waxes.'), 'nonvegan')
        for text in ('Our candles are made from synthetic beeswax.',
                     'Our other candles are made from beeswax.',
                     'Our candles are made from beeswax fragrance.'):
            self.assert_not_nonvegan(context('Beeswax Candle', detailed_description=text))

    def test_interpreted_animal_content_is_not_blocked_by_description_length(self):
        for name, text in [
            ('Beeswax Candle', 'This candle is hand-made by artisan candle-makers. Environmentally-friendly, and made using 100% pure beeswax. Not tested on animals.'),
            ('British Cheese Selection', 'A ready-to-serve selection. A cave aged Cheddar made with pasteurised Cow\'s milk. Not suitable for vegetarians.'),
        ]:
            with self.subTest(name=name):
                value = context(name, detailed_description=text)
                assessment = policy.base_assessment(value, complete=False)
                assessment['material_issues'] = [{'code':'animal_ingredient', 'target':'whole_product',
                    'rule_id':'animal-ingredients-v1', 'evidence_refs':[policy.reference('/product/detailed_description', text)],
                    'resolution_refs':[], 'blocks_claim':True}]
                result = classifier.ClassificationResult(product_id='1', vegan_status='nonvegan', vegan_reason=None,
                    confidence='certain', summary='Declared animal content', source='codex', evidence={'assessment':assessment})
                self.assertEqual(classifier.apply_policy_gate(value, result).vegan_status, 'nonvegan')
                assessment['identity']['status'] = 'conflicted'
                self.assertEqual(classifier.apply_policy_gate(value, result).vegan_status, 'unknown')


    def test_leather_belt_declaration_overrides_vegan_claim(self):
        value = context('Leather Belt', detailed_description='Made from genuine leather for durability.',
            dietary_information='Suitable for vegans.', official_vegan=1)
        result = self.assert_status(value, 'nonvegan')
        self.assertTrue(result.evidence['tag_animal_conflict'])
        for text in ('Made from synthetic leather.', 'Made from leather-look plastic.',
                     'Our other belt is made from genuine leather.', 'Not made from genuine leather.'):
            with self.subTest(text=text):
                self.assert_not_nonvegan(context('Leather Belt', detailed_description=text))
        self.assert_not_nonvegan(context('Leather Cleaner', detailed_description='For cleaning 100% leather belts.'))
        self.assert_not_nonvegan(context('Imperial Leather Soap', detailed_description='Made from moisturising ingredients.'))

    def test_actual_leather_upper_survives_faux_fur_trim(self):
        for material in ('Upper: Leather\nLining: Textile', 'Upper: Leather & textile\nLining: Other materials'):
            with self.subTest(material=material):
                value = context('Suede Faux Fur Trim Mule Slippers', specification=material)
                result = self.assert_status(value, 'nonvegan')
                refs = result.evidence['assessment']['material_issues'][0]['evidence_refs']
                self.assertTrue(any(ref['path'] == '/product/specification' for ref in refs))
                for ref in refs:
                    policy.resolve_reference(classifier.product_context_for_llm(value), ref)
        self.assert_not_nonvegan(context('Slippers', specification='Upper: Faux leather\nLining: Textile'))
        self.assert_not_nonvegan(context('Shoe Cleaner', specification='For upper: Leather'))

    def test_declared_natural_silk_is_material_not_softness(self):
        for name, specification in [
            ('Pure Mulberry Silk Pillowcase', 'Machine washable even at 30 100% Mulberry Silk Pillowcase: 76 x 48 cm'),
            ('Mulberry Silk Scrunchies', 'Machine washable 100% mulberry silk (exclusive of trimmings)'),
            ('Pure Silk Hair Bonnet', '100% Silk')]:
            with self.subTest(name=name):
                self.assert_status(context(name, specification=specification), 'nonvegan')
        for name, specification in [
            ('Silk Look Pillowcase', '100% silk-like polyester'),
            ('Cotton Pillowcase', 'Silk-like feel. 100% cotton.'),
            ('Pure Silk Pillowcase', 'Our other pillowcase is 100% silk.'),
            ('Silk Care Detergent', 'Suitable for 100% silk garments.')]:
            with self.subTest(name=name):
                self.assert_not_nonvegan(context(name, specification=specification))

    def test_indigo_carmine_does_not_remove_actual_carmine(self):
        self.assert_not_nonvegan(context('Sprinkles', 'Sugar, Indigo Carmine'))
        for ingredients in ('Sugar, Carmine', 'Sugar, Indigotine, Carmine',
                            'Sugar, Indigo Carmine, Carmine', 'Sugar, E120'):
            with self.subTest(ingredients=ingredients):
                self.assert_status(context('Sprinkles', ingredients, official_vegan=1), 'nonvegan')

    def test_vegan_precursors_and_nutrient_effect_are_not_added_collagen(self):
        for ingredients in ('Water, Vegan Pro-Collagen (Amino Acids)',
                            'Vitamin C contributes to normal collagen formation for the normal function of skin'):
            with self.subTest(ingredients=ingredients):
                self.assert_not_nonvegan(context('Supplement', ingredients))
        for ingredients in ('Water, Vegan Pro-Collagen (Amino Acids), Collagen',
                            'Water, Vegan Pro-Collagen (Amino Acids), Gelatine',
                            'Vitamin C contributes to normal collagen formation, Milk'):
            with self.subTest(ingredients=ingredients):
                self.assert_status(context('Supplement', ingredients, official_vegan=1), 'nonvegan')


    def test_corroborated_plain_fish_and_jersey_milk(self):
        for name, full in [('Gold Top Jersey Milk', 'Pasteurised Jersey Full Cream Milk'),
                           ('Black Cod', 'Defrosted Skin-On Sablefish Fillet'),
                           ('Icelandic Red Fish', 'Defrosted Redfish Fillets'),
                           ('British Dover Sole', 'Defrosted Dover Sole'),
                           ('Scottish Monkfish Fillet', 'Skinless Monkfish')]:
            with self.subTest(name=name):
                value = context(name, other_information='Full Product Name: ' + full + ' Storage: Refrigerate.', official_vegan=1)
                result = self.assert_status(value, 'nonvegan')
                self.assertTrue(result.evidence['tag_animal_conflict'])
                for ref in classifier.policy_facts(value)['animal_declarations']:
                    policy.resolve_reference(classifier.product_context_for_llm(value), ref)
        for name, full in [('Vegan Black Cod', 'Plant Based Alternative'),
                           ('Black Cod Toy', 'Black Cod Toy'),
                           ('Dover Sole Recipe Book', 'Dover Sole Recipes'),
                           ('Jersey Oat Milk', 'Jersey Oat Milk'),
                           ('Black Cod', 'Plain Cotton Socks'),
                           ('Red Fish Cushion', 'Redfish Cotton Cushion'),
                           ('Jersey Milk Body Lotion', 'Jersey Milk Body Lotion'),
                           ('Black Cod Painting', 'Black Cod'),
                           ('Black Cod', 'Black Cod Flavour Seasoning')]:
            with self.subTest(name=name):
                self.assert_not_nonvegan(context(name, other_information='Full Product Name: ' + full))



if __name__ == '__main__':
    unittest.main()
