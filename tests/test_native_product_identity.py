from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import classify_ocado_vegan as classifier
import native_product_identity as identity
import vegan_policy as policy


def context(name, **values):
    return {'product':{'id':'1','name':name,'official_vegan':1,**values},
            'flags':[], 'categories':[], 'manufacturer_vegan_evidence':[]}


class NativeIdentityTests(unittest.TestCase):
    def test_explicit_harvest_period_is_bound_to_current_vintage(self):
        value=context('Example Pinot Noir', current_vintage='2023', country_of_origin='New Zealand',
            vinification_details='HARVEST: Handpicked HARVEST PERIOD: 15th March – 1st April 2021. Ageing in oak.')
        result=classifier.classify_rule_candidate(value)
        self.assertEqual(result.vegan_status,'vegan')
        self.assertIsNone(classifier.classify_by_rules(value))
        for vintage, origin, text in [
            ('2023','New Zealand','HARVEST PERIOD: 15th March – 1st April 2023.'),
            ('2024','Australia','HARVEST PERIOD: October 2023.'),
            ('2024',None,'HARVEST YEAR: 2023.'),
            ('2023','New Zealand','Previous vintage. HARVEST PERIOD: March 2021.'),
            ('2023','New Zealand','Vines planted in 1996. Bottled in 2024.'),
            ('2023','New Zealand','HARVEST PERIOD: December 2022 – January 2023.')]:
            with self.subTest(text=text):
                self.assertEqual(identity.conflict_refs(context('Example Pinot Noir',
                    current_vintage=vintage,country_of_origin=origin,vinification_details=text)),[])
        self.assertTrue(identity.conflict_refs(context('Example Wine', current_vintage='2024',
            country_of_origin='Australia', vinification_details='HARVEST PERIOD: March 2023.')))

    def test_own_syrup_sweetening_must_match_declared_composition(self):
        value=context('Organic Chilli Paste', ingredients='Chilli, vinegar, sea salt, water, guar gum',
                      dietary_information='Suitable for vegans',
                      detailed_description='Naturally sweetened using agave syrup. This sriracha is sure to please.')
        self.assertTrue(identity.sweetening_composition_conflict_refs(value))
        result=classifier.classify_rule_candidate(value)
        self.assertEqual(result.vegan_status, 'vegan')
        self.assertFalse(result.evidence['positive_block'])
        self.assertIsNone(classifier.classify_by_rules(value))
        for description in ('Naturally sweetened using agave syrup.',
                            'This product is sweetened with maple syrup.',
                            'Naturally sweetened using organic date syrup.'):
            with self.subTest(description=description):
                value['product']['detailed_description']=description
                self.assertTrue(identity.sweetening_composition_conflict_refs(value))
        value['product']['ingredients']='Chilli, agave, vinegar, sea salt'
        value['product']['detailed_description']='Naturally sweetened using agave syrup.'
        self.assertEqual(identity.sweetening_composition_conflict_refs(value), [])

    def test_syrup_taste_recipe_other_products_and_missing_composition_are_not_conflicts(self):
        for description in ('A sweet flavour with notes of agave syrup.',
                            'Try it sweetened with agave syrup.',
                            'Our other sauce is sweetened using agave syrup.',
                            'This recipe is sweetened with maple syrup.',
                            'Not sweetened with agave syrup.',
                            'It can be sweetened using agave syrup.'):
            with self.subTest(description=description):
                self.assertEqual(identity.sweetening_composition_conflict_refs(context(
                    'Chilli Paste', ingredients='Chilli, vinegar', detailed_description=description)), [])
        self.assertEqual(identity.sweetening_composition_conflict_refs(context(
            'Chilli Paste', ingredients=None, detailed_description='Naturally sweetened using agave syrup.')), [])

    def test_rhetorical_and_usage_questions_do_not_erase_separate_producer_claim(self):
        for text in ('And guess what? This oil is also vegan!',
                     'And guess what? This oil is also vegan!, INFUSED WITH NUT OIL, Searching for a hair oil? Use it daily.',
                     'This oil is vegan. Searching for a hair oil? Use it daily.',
                     'This oil is vegan! Use it with other products when you need some extra care.',
                     'How do you use it? This product is suitable for vegans.'):
            self.assertTrue(policy.manufacturer_claim_quote(text))
        for text in ('Is this oil vegan?', 'This oil is also vegan?',
                     'Guess what? This oil might be vegan.',
                     'We believe this oil is also vegan!',
                     'Maybe this oil is also vegan!',
                     'Guess what? This oil is also vegan, excluding the filter.',
                     'Guess what? This oil is vegan. Not all products are vegan.',
                     'Guess what? This vegan formula is suitable for vegans.',
                     'Guess what? This formula is also vegan!',
                     'This formulation is vegan!',
                     'Some products are vegan.',
                     'Certain products are suitable for vegans.',
                     'This product is vegan for selected variants.',
                     'Another product is vegan.',
                     'This product is vegan. Except the filter.',
                     'Is this vegan? We do not know.'):
            self.assertFalse(policy.manufacturer_claim_quote(text))

    def test_explicit_vegan_dietary_attribute_with_low_carb_descriptors(self):
        text = 'Low Carb, High Protein, Vegan, Keto, LCHF, No Addes Sugar.'
        ctx = context('Example Almond Flour', official_vegan=0,
            ingredients='Organically grown Almonds.', dietary_information=text)
        result = classifier.classify_rule_candidate(ctx)
        self.assertEqual((result.vegan_status,result.vegan_reason),('vegan','manufacturer'))
        for value in ('Low Carb, High Protein, Keto, LCHF, No Added Sugar.',
                      'Vegan formula, Keto, LCHF', 'Vegan, Keto, except the filter',
                      'Not Vegan, Low Carb, LCHF'):
            self.assertFalse(policy.dietary_attribute_claim(value))
        ctx['product']['ingredients'] = 'Gelatine'
        self.assertEqual(classifier.classify_rule_candidate(ctx).vegan_status,'nonvegan')

    def test_bare_bottle_body_declaration_with_cap_exclusions(self):
        ctx = context('Example Cleansing Water', package_type='Glass Bottle',
            features='COMMITTED TO SUSTAINABILITY: Bottle is made from recycled plastic, excluding cap, label & additives.')
        self.assertTrue(identity.component_material_conflict_refs(ctx))
        self.observed_with_provisional_assurance(ctx)
        for text in ('Bottle is made from recycled glass, excluding cap and label.',
                     'The bottle cap is made from recycled plastic.',
                     'Plastic cap on the glass bottle.',
                     'Another product: Bottle is made from recycled plastic.',
                     'For example: Bottle is made from recycled plastic.',
                     'Use another bottle made from plastic.'):
            self.assertFalse(identity.component_material_conflict_refs({**ctx,'product':{
                **ctx['product'],'features':text}}))
        ctx['product']['ingredients'] = 'Gelatine'
        self.assertEqual(classifier.classify_rule_candidate(ctx).vegan_status, 'nonvegan')

    def test_explicit_two_grain_cereal_blend_conflict(self):
        ctx = context('Example Organic Fibre Flakes',
            ingredients='Cereal Blend* (Brown Rice*66%, Maize Flour*, Maize Bran*, Rice Bran Extract*), Cane Sugar*, Salt*',
            detailed_description='Single-serve crispy flakes made from a rice and buckwheat blend.')
        self.observed_with_provisional_assurance(ctx)
        for text in ('Crispy flakes made from a rice and maize blend.',
                     'Crispy flakes made from a rice and corn blend.',
                     'Crispy flakes made from a rice blend.',
                     'Serve with other flakes made from a rice and buckwheat blend.',
                     'Our other flakes made from a rice and buckwheat blend.',
                     'Our range includes flakes made from a rice and buckwheat blend.'):
            self.assertFalse(identity.cereal_blend_conflict_refs({**ctx,'product':{
                **ctx['product'],'detailed_description':text}}))
        self.assertFalse(identity.cereal_blend_conflict_refs({**ctx,'product':{
            **ctx['product'],'ingredients':'Rice, Buckwheat, Maize, Sugar'}}))
        ctx['product']['ingredients'] = 'Cereal Blend (Rice, Maize), Gelatine'
        ctx['product']['detailed_description'] = 'Crispy flakes made from a rice and maize blend.'
        self.assertEqual(classifier.classify_rule_candidate(ctx).vegan_status, 'nonvegan')

    def test_offered_vintage_cannot_be_bottled_before_harvest_year(self):
        self.observed_with_provisional_assurance(context('Example Riesling Wine', current_vintage='2024',
            country_of_origin='France',
            vinification_details='It was chilled until bottling at the end of 2023.'))
        for vintage, text in [('2024', 'It was bottled in 2024.'),
                              ('2024', 'It was bottled in 2025.'),
                              ('NV', 'It was bottled in 2023.'),
                              ('2024', 'Our previous wine was bottled in 2023.'),
                              ('2024', 'The winery was founded in 2023.'),
                              ('2024', 'The wine is chilled until bottling.')]:
            self.assertFalse(identity.conflict_refs(context('Example Riesling Wine',
                current_vintage=vintage, vinification_details=text)))
        self.assertFalse(identity.conflict_refs(context('Example Riesling Wine',
            country_of_origin='Australia', current_vintage='2024',
            vinification_details='It was chilled until bottling at the end of 2023.')))
        self.assertFalse(identity.conflict_refs(context('Example Riesling Wine',
            current_vintage='2024', vinification_details='It was bottled in 2023.')))
        self.observed_with_provisional_assurance(context('Example Riesling Wine', country_of_origin='Australia',
            current_vintage='2024', vinification_details='It was bottled in 2022.'))

    def test_vegan_attribute_with_independent_testing_attribute_keeps_precedence(self):
        ctx = context('Example Vegan Jellies', official_vegan=0, ingredients='Sugar, Fruit Pectin',
            dietary_information='Suitable for Vegetarians; Suitable for Vegans; Not Tested on Animals',
            features='Suitable for Vegans & Vegetarians')
        result = classifier.classify_rule_candidate(ctx)
        self.assertEqual((result.vegan_status, result.vegan_reason), ('vegan', 'manufacturer'))
        self.assertTrue(policy.manufacturer_claim_quote('Suitable for Vegans & Vegetarians'))
        self.assertTrue(policy.manufacturer_claim_quote('Vegan; Not tested on animals'))
        self.assertFalse(policy.manufacturer_claim_quote('Vegan; Not tested on animals', brand='Vegan'))
        for text in ('Not tested on animals', 'Cruelty-free',
                     'Not suitable for vegans; Not tested on animals',
                     'Vegan formula; Not tested on animals', 'Vegan; Except the filter',
                     'Vegan; Not vegan'):
            self.assertFalse(policy.dietary_attribute_claim(text))
        ctx['product']['ingredients'] = 'Gelatine'
        self.assertEqual(classifier.classify_rule_candidate(ctx).vegan_status, 'nonvegan')

    def test_named_fragrance_range_before_or_after_variant(self):
        ctx = context('Example Nourishing Foaming Wash Lychee and Lotus Flower',
            brand='Example', features='Foaming Body Wash',
            detailed_description='With one pump, Example Pink Peony & Raspberry Foaming Wash transforms into foam.')
        self.observed_with_provisional_assurance(ctx)
        for text in ('Example Lychee & Lotus Foaming Wash transforms into foam.',
                     'Example Foaming Wash Lychee & Lotus transforms into foam.',
                     'With one pump, our other Example Pink Peony & Raspberry Foaming Wash transforms into foam.',
                     'This body wash includes notes of raspberry, peony and vanilla.',
                     'Example Pink Peony & Raspberry Conditioner transforms hair.'):
            self.assertFalse(identity.conflict_refs({**ctx,'product':{**ctx['product'],
                'detailed_description':text}}))

    def test_nominal_bottle_feature_conflicts_with_package_type(self):
        ctx = context('Example Shower Gel', package_type='Glass Bottle',
                      features='Gently cleanses, RECYCLED PACKAGING - Post-consumer recycled plastic bottle')
        self.assertTrue(identity.component_material_conflict_refs(ctx))
        self.observed_with_provisional_assurance(ctx)
        ctx['product']['ingredients'] = 'Gelatine'
        self.assertEqual(classifier.classify_rule_candidate(ctx).vegan_status, 'nonvegan')
        for text in ('Recycled glass bottle', 'Plastic bottle cap',
                     'Use with another plastic bottle', 'Plastic label on glass bottle'):
            self.assertFalse(identity.component_material_conflict_refs(context('Example Gel',
                package_type='Glass Bottle', features=text)))

    def test_named_wearable_material_variant_not_animal_inference(self):
        ctx = context('Example Nude Satin Nipple Covers - 4 Pairs', brand='Example',
                      detailed_description='Feel confident with the Example Silicone Nipple Covers.')
        self.observed_with_provisional_assurance(ctx)
        for text in ('Feel confident with the Example Satin Nipple Covers.',
                     'These satin covers have a silicone adhesive backing.',
                     'Example Silicone Nipple Covers with a satin outer layer.',
                     'Try our other Example Silicone Nipple Covers.'):
            self.assertFalse(identity.conflict_refs(context('Example Nude Satin Nipple Covers - 4 Pairs',
                brand='Example', detailed_description=text)))

    def test_corroborated_mixed_pack_and_different_complete_outline(self):
        ctx = context('Example Mixed Can Pack',
            ingredients='Alpha: Water Bravo: Water Charlie: Water Delta: Water',
            other_information='Box Contents: 3 x Alpha, 3 x Bravo, 3 x Charlie, 3 x Delta Full Product Name: Drink',
            detailed_description='Alpha - Bright Drink Bravo - Fruity Drink Echo - Crisp Drink Foxtrot - Smooth Drink')
        self.observed_with_provisional_assurance(ctx)
        for text in ('Alpha - Bright Drink Bravo - Fruity Drink Charlie - Crisp Drink Delta - Smooth Drink',
                     'Alpha - Bright Drink Bravo - Fruity Drink',
                     'Serve with Alpha - Bright Drink Bravo - Fruity Drink Echo - Crisp Drink Foxtrot - Smooth Drink'):
            changed = {**ctx, 'product': {**ctx['product'], 'detailed_description': text}}
            self.assertFalse(identity.assortment_conflict_refs(changed))
        changed = {**ctx, 'product': {**ctx['product'], 'ingredients': 'Water'}}
        self.assertFalse(identity.assortment_conflict_refs(changed))

    def test_other_catalogue_brand_composition_heading(self):
        self.blocked(context('London Essence Co. Raspberry & Rose Soda',brand='London Essence Co.',
            detailed_description='A floral drink. Fever-Tree Refreshingly Light Raspberry & Rose Lemonade contains 18 kcal per 100ml.'))
        for brand, name, text in [
            ('London Essence Co.', 'London Essence Co. Soda', 'Serve with Fever-Tree Raspberry Lemonade.'),
            ('London Essence Co.', 'London Essence Co. Soda', 'Fever-Tree is another drinks company.'),
            ('London Essence Co.', 'London Essence Co. Soda', 'Fever-Tree products contain natural ingredients.'),
            ('London Essence Co.', 'London Essence Co. Soda', 'Try Fever-Tree Raspberry Lemonade; it contains lemon.'),
            ('M&S', 'M&S Spanish Gazpacho', 'M&S Collection Spanish Gazpacho contains tomato.'),
            ('Blanco Nino', 'Blanco Nino Chips', 'San Miguel de Allende is a town in Mexico.'),
            ('UltraDEX', 'UltraDEX Spray', 'Venture Life Periproducts Ltd is a wholly owned subsidiary.')]:
            self.assertFalse(identity.producer_heading_refs(context(name,brand=brand,detailed_description=text)))
        self.assertFalse(identity.producer_heading_refs(context('Example Soda',brand='Example',
            manufacturer='Made by Fever-Tree Drinks Ltd.',
            detailed_description='Fever-Tree Raspberry Lemonade contains lemon.')))
        owned=context("Kellogg's Rice Krispie Squares Delightfully Chocolate",
            brand="Kellogg's Squares",manufacturer='UK: Kellogg Marketing and Sales Company Limited, UK.',
            detailed_description="Kellogg's Rice Krispies Squares Totally Chocolatey snack bars contains wheat gluten.",
            ingredients='Milk chocolate')
        self.assertFalse(identity.producer_heading_refs(owned))
        self.assertEqual(classifier.classify_rule_candidate(owned).vegan_status,'nonvegan')

    def test_broad_wine_name_with_explicit_own_cultivar(self):
        self.observed_with_provisional_assurance(context('Example Estate Wine',
            detailed_description='Fresh and expressive, this Sauvignon Blanc combines citrus flavours.',
            vinification_details='Our rosé is obtained by draining vats of Pinot Noir juice.'))
        for description, process in [('Serve this Sauvignon Blanc with pasta.', 'Pinot Noir juice is fermented.'),
                                     ('Our other Sauvignon Blanc has citrus notes.', 'Pinot Noir juice is fermented.'),
                                     ('This Sauvignon Blanc is fresh.', 'Sauvignon Blanc juice is fermented.'),
                                     ('This Sauvignon Blanc is fresh.', 'Pinot Noir juice is blended with Sauvignon Blanc.'),
                                     ('This Sauvignon Blanc is fresh.', 'Our other wine uses Pinot Noir juice.')]:
            self.assertFalse(identity.conflict_refs(context('Example Estate Wine',
                detailed_description=description,vinification_details=process)))

    def test_native_specification_component_and_description_grape(self):
        ctx=context('Example Shampoo',package_type='Glass Bottle',
                    specification='Registered vegan. 100% Recycled Bottle / PCR plastic',
                    detailed_description='Please note packaging may vary.')
        self.assertTrue(identity.component_material_conflict_refs(ctx))
        self.observed_with_provisional_assurance(ctx)
        ctx['product']['ingredients']='Gelatine'
        self.assertEqual(classifier.classify_rule_candidate(ctx).vegan_status,'nonvegan')
        for text in ('Bottle / PCR plastic cap', 'Bottle / plastic label', 'The plastic cap is recycled.'):
            self.assertFalse(identity.component_material_conflict_refs(context('Example Shampoo',
                package_type='Glass Bottle',specification=text)))
        self.observed_with_provisional_assurance(context('Example Viognier Wine',
            detailed_description='Estate Winery crafts this elegant Prosecco from 100% Glera.'))
        for text in ('Serve with a wine made from 100% Glera.',
                     'Our other wine is made from 100% Glera.',
                     'This Viognier is blended with Glera.',
                     'A crisp wine with Glera-like notes.'):
            self.assertFalse(identity.conflict_refs(context('Example Viognier Wine',detailed_description=text)))

    def test_named_grape_and_different_unqualified_vinification(self):
        self.observed_with_provisional_assurance(context('Example Sauvignon Blanc Wine',
            vinification_details='The Chardonnay grapes are hand-selected and fermented in steel tanks.'))
        for name, text in [('Example Pinot Grigio Wine', 'The Pinot Gris grapes are pressed.'),
                           ('Example Shiraz Wine', 'The Syrah grapes are fermented.'),
                           ('Example Sauvignon Blanc Wine', 'Our other Chardonnay grapes are pressed.'),
                           ('Example Sauvignon Blanc Wine', 'The Chardonnay grapes are blended with Sauvignon Blanc.'),
                           ('Example Sauvignon Blanc Chardonnay Blend', 'The Chardonnay grapes are pressed.'),
                           ('Example Sauvignon Blanc Wine Vinegar', 'The Chardonnay grapes are pressed.'),
                           ('Example Sauvignon Blanc Wine', 'Serve with Chardonnay grapes.')]:
            self.assertFalse(identity.conflict_refs(context(name,vinification_details=text)))

    def test_corroborated_legume_and_wrong_own_product_subject(self):
        self.observed_with_provisional_assurance(context('Example Baked Beans', ingredients='White Beans, Tomato Juice',
            detailed_description='Our Chickpeas have been carefully selected and cooked to perfection.'))
        self.observed_with_provisional_assurance(context('Example Chickpeas', ingredients='Chickpeas, Water',
            detailed_description='Our Lentils are cooked to perfection.'))
        for text in ('Our Beans have been carefully selected.',
                     'Our other Chickpeas are cooked to perfection.',
                     'Serve with chickpeas or lentils.',
                     'Our Chickpeas are great in another recipe.'):
            self.assertFalse(identity.conflict_refs(context('Example Baked Beans',
                ingredients='White Beans, Tomato Juice', detailed_description=text)))
        self.assertFalse(identity.conflict_refs(context('Example Mixed Beans and Chickpeas',
            ingredients='White Beans, Chickpeas', detailed_description='Our Chickpeas are cooked.')))

    def test_named_variant_subjects_with_introductory_or_unbranded_heading(self):
        self.observed_with_provisional_assurance(context('Example Gold Goddess Foaming Wash',brand='Example',
            detailed_description='With one pump, Example Radiant Florals and Nectar Foaming Wash transforms into foam.'))
        self.observed_with_provisional_assurance(context('Example Curl Control Conditioner',brand='Example',
            detailed_description='Coconut Shine Conditioner, featuring coconut oil, is gentle.'))
        for text in ('Example Curl Control Conditioner is gentle.',
                     'Our other product is Coconut Shine Conditioner.',
                     'This conditioner includes coconut oil.',
                     'Example Natural Curl Control Conditioner is gentle.'):
            self.assertFalse(identity.conflict_refs(context('Example Curl Control Conditioner',
                brand='Example',detailed_description=text)))
        for name, text in [('Example Smooth Peanut Butter','Organically-grown peanuts, smoothly blended to make a rich butter.'),
                           ('Example Extra Virgin Olive Oil','Superior category Olive Oil obtained directly from olives.'),
                           ('Example Frozen Vegetarian Sausages','These classic plant-based sausages are made with soya.'),
                           ('Example Tomato Garlic Pasta Sauce','Simply stir this rich sauce into freshly cooked pasta.')]:
            self.assertFalse(identity.conflict_refs(context(name,brand='Example',detailed_description=text)))

    def test_dry_wine_and_explicit_sweet_ageing_account(self):
        self.observed_with_provisional_assurance(context('Example Pale Dry Sherry',
            vinification_details='Following ageing, must is added to sweeten this wine. It is then aged as a sweet wine for a further year.'))
        for text in ('A dry wine with sweet floral notes.',
                     'Our other wine is then aged as a sweet wine.',
                     'Sweet must is fermented to dryness.'):
            self.assertFalse(identity.conflict_refs(context('Example Dry Wine',vinification_details=text)))

    def test_named_flour_and_different_only_grain(self):
        self.observed_with_provisional_assurance(context('Example Light Rye Flour',ingredients='Wheatflour, Calcium Carbonate, Iron, Niacin, Thiamin'))
        self.observed_with_provisional_assurance(context('Example Rice Flour',ingredients='Wheat Flour'))
        for name, ingredients in [('Example Rye Flour','Rye Flour'),
                                  ('Example Spelt Flour','Wheat Flour'),
                                  ('Example Rye Flour Blend','Wheat Flour'),
                                  ('Example Rye Flour Recipe Book','Wheat Flour')]:
            self.assertFalse(identity.conflict_refs(context(name,ingredients=ingredients)))

    def test_flavour_free_vs_explicit_added_flavour(self):
        self.observed_with_provisional_assurance(context('Example Toothpaste Flavour Free',
            detailed_description='Gentle toothpaste. • Organic Strawberry flavoring • Vegan'))
        self.observed_with_provisional_assurance(context('Example Unflavoured Supplement',
            ingredients='Protein, Natural Vanilla Flavouring'))
        self.observed_with_provisional_assurance(context('Example Flavor Free Toothpaste',features='Natural Strawberry Flavoring, Vegan'))
        for name, text in [('Example Flavour Free Toothpaste','Available in nine fruity flavours.'),
                           ('Example Flavour Free Toothpaste','• No artificial flavouring'),
                           ('Example Flavour Free Toothpaste','Serve with strawberry flavouring.'),
                           ('Example Flavour Free Toothpaste','• Used for strawberry flavouring'),
                           ('Example Artificial Flavour Free Toothpaste','• Organic strawberry flavouring'),
                           ('Example Strawberry Toothpaste','• Organic strawberry flavouring')]:
            self.assertFalse(identity.conflict_refs(context(name,detailed_description=text)))

    def test_explicit_named_single_flavour_in_other_native_fields(self):
        for key in ('detailed_description', 'manufacturer_marketing', 'brand_marketing'):
            self.observed_with_provisional_assurance(context('Example Cranberry Vodka', brand='Example',
                **{key:'Example Grapefruit blends premium vodka with grapefruit.'}))
        for text in ('Example Cranberry blends premium vodka with grapefruit.',
                     'Our other flavour is Example Grapefruit.',
                     'Example Grapefruit was formerly the only flavour.',
                     'Example Grapefruit is another product in our range.',
                     'Serve Example Cranberry with grapefruit juice.'):
            self.assertFalse(identity.conflict_refs(context('Example Cranberry Vodka',
                brand='Example', manufacturer_marketing=text)))

    def test_declared_bottle_body_materials_disagree(self):
        self.observed_with_provisional_assurance(context('Example Conditioner', package_type='Glass Bottle',
            detailed_description='Our bottles are made of 100% recycled plastic.'))
        self.observed_with_provisional_assurance(context('Example Drink', package_type='Plastic Bottle',
            manufacturer_marketing='This bottle is made from glass.'))
        for package, text in [('Glass Bottle','Our bottles are made of recycled glass.'),
                              ('Bottle','Our bottles are made of recycled plastic.'),
                              ('Glass Bottle','Our bottle caps are made of plastic.'),
                              ('Glass Bottle','Recycle plastic bottles locally.')]:
            self.assertFalse(identity.conflict_refs(context('Example Product',
                package_type=package, detailed_description=text)))
            self.assertFalse(identity.component_material_conflict_refs(context('Example Product',
                package_type=package, detailed_description=text)))

        value=context('Example Honey Shampoo', ingredients='Water, Honey',
            package_type='Glass Bottle', brand_marketing='Our bottles are made of recycled plastic.')
        result=classifier.classify_rule_candidate(value)
        self.assertEqual(result.vegan_status,'nonvegan')
        self.assertTrue(result.evidence['copy_discrepancy_refs'])

    def test_article_type_is_not_an_alternate_flavour(self):
        for name, brand, text in [('Example Orange Milk Chocolate','Example','Example Chocolate - Owned by farmers.'),
                                 ('Example Chocolate Peppermint Bark','Example','Example Peppermint Bark')]:
            self.assertFalse(identity.conflict_refs(context(name,brand=brand,detailed_description=text)))

    def test_named_multiple_flavours_conflict_without_brand_exceptions(self):
        value=context('Example Kids Cherries, Strawberries & Apples Smoothies',brand='Example',
                      detailed_description='Example kids pineapples, apples & carrots smoothies are tasty.')
        self.observed_with_provisional_assurance(value)
        self.observed_with_provisional_assurance(context('Other Mango & Pineapple Smoothie',brand='Other',
                             detailed_description='Other strawberry and apple smoothie is tasty.'))
        for description in ('Example kids cherries, strawberries and apples smoothies are tasty.',
                            'Example kids cherries and apples smoothies are tasty.',
                            'Example kids cherries, strawberries, apples and bananas smoothies are tasty.',
                            'Our kids smoothies include pineapple, apple and carrot variants.'):
            self.assertFalse(identity.conflict_refs(context(value['product']['name'],brand='Example',
                                                           detailed_description=description)))

    def test_cordial_and_corroborated_carbonated_recipe_conflict(self):
        values = {'other_information': 'Full Product Name: Raspberry Lemonade is a soft drink Storage: Keep cool',
                  'detailed_description': 'Raspberry Lemonade is a refreshing soft drink.',
                  'ingredients': 'Carbonated Spring Water, Raspberry Juice, Sugar'}
        self.observed_with_provisional_assurance(context('Example Ginger & Lemongrass Cordial', **values))
        for changed in ({'ingredients': 'Water, Ginger, Lemongrass, Sugar'},
                        {'other_information': 'Full Product Name: Ginger Cordial. Serve with lemonade.'},
                        {'detailed_description': 'Ginger Cordial. Serve with lemonade.'}):
            self.assertFalse(identity.conflict_refs(context('Example Ginger Cordial', **{**values, **changed})))
        for name in ('Example Raspberry Lemonade', 'Example Sparkling Cordial Cocktail',
                     'Example Lemonade Cordial'):
            self.assertFalse(identity.conflict_refs(context(name, **values)))

    def blocked(self, value):
        refs=[*identity.conflict_refs(value),*identity.component_material_conflict_refs(value)]
        self.assertTrue(refs)
        for ref in refs: policy.resolve_reference(value,ref)
        result=classifier.classify_rule_candidate(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertTrue(result.evidence['positive_block'])

    def observed_with_provisional_assurance(self, value):
        refs=[*identity.conflict_refs(value), *identity.component_material_conflict_refs(value)]
        self.assertTrue(refs)
        for ref in refs: policy.resolve_reference(value, ref)
        result=classifier.classify_rule_candidate(value)
        self.assertEqual((result.vegan_status, result.vegan_reason), ('vegan', 'tagged'))
        self.assertFalse(result.evidence['positive_block'])
        self.assertTrue(result.evidence['copy_discrepancy_refs'])
        # A provisional tag still requires the two independent semantic readings.
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_country_contradiction_does_not_override_to_animal_content(self):
        self.observed_with_provisional_assurance(context('Example Revolutionary Rose',country_of_origin='Spain',
            brand_marketing='Example Revolutionary Rosé is a refreshing Australian rosé.'))
        for origin,copy in [('Australia','An Australian rosé.'),('UK','English wine.'),
                            ('Spain','Australian-style rosé.'),('Spain','Matured in French oak.')]:
            self.assertFalse(identity.conflict_refs(context('Example Wine',country_of_origin=origin,brand_marketing=copy)))
        self.assertFalse(identity.conflict_refs(context('French Wine Vinegar',country_of_origin='UK',brand_marketing='Made using French wine.')))
        self.assertFalse(identity.conflict_refs(context('Organic Mulled Wine',country_of_origin='UK',detailed_description='Made with Spanish red wine and spices.')))

    def test_dosage_format_vs_explicit_spray_serving(self):
        self.observed_with_provisional_assurance(context('Example Vitamin C Gummies',dietary_information='Each 1 Spray(s) contains Vitamin B12'))
        for text in ('Each 1 gummy contains Vitamin C','Take one capsule with water','Spray the table before preparing food'):
            self.assertFalse(identity.conflict_refs(context('Example Gummies',dietary_information=text)))

    def test_different_branded_heading_requires_review(self):
        self.observed_with_provisional_assurance(context('Example Connie The Caterpillar Flower Power',brand='Example',
            detailed_description='Example colin the caterpillar flower power. Fruit flavour gums.'))
        self.assertFalse(identity.conflict_refs(context('Example Connie The Caterpillar Flower Power',brand='Example',
            detailed_description='Example Connie The Caterpillar Flower Power. Fruit flavour gums.')))
        self.assertFalse(identity.conflict_refs(context('Example Connie The Caterpillar Flower Power',brand='Example',
            detailed_description='Our family includes Colin. This Connie product is suitable for vegans.')))
        for name, text in [('Example Colin The Caterpillar Fruit Gums','Example Colin The Caterpillar™ Fruit Gums'),
                           ('Example Kids Apples & Carrots Smoothie','Example Kids Apples & Carrots Smoothies'),
                           ('Example Hand Made Soap Bar','Example Hand Made Soap Part of our collection.'),
                           ('Example King Hass Avocado Perfectly Ripe','Example Giant Hass Avocado Perfectly Ripe'),
                           ('Example Focaccia & Ciabatta Bread Mix','Example Foccacia & Ciabatta Bread Mix'),
                           ('Example Fairtrade Colombian Coffee Beans','Example Collection Colombian Coffee Beans'),
                           ('Example Apothecary Warmth Hand Wash','Example uplifting Warmth Hand Wash refreshes your hands.')]:
            self.assertFalse(identity.conflict_refs(context(name,brand='Example',detailed_description=text)))

    def test_unscented_name_and_named_heading_not_botanical_ingredients(self):
        self.observed_with_provisional_assurance(context('Example Washing Up Liquid Fragrance Free',
            detailed_description='EXAMPLE dishwashing liquid – Pear nectar is the ideal choice. Its unscented formula is gentle.'))
        for text in ('Contains pear extract. Its unscented formula is gentle.',
                     'EXAMPLE dishwashing liquid – Fragrance Free is the ideal choice.'):
            self.assertFalse(identity.conflict_refs(context('Example Washing Up Liquid Fragrance Free',detailed_description=text)))

    def test_separate_named_production_accounts(self):
        self.observed_with_provisional_assurance(context('Example Fino Sherry',vinification_details='Wine enters the Example solera.',
            other_information='The wines are blended through the Alternate 1847 solera.'))
        self.assertFalse(identity.conflict_refs(context('Example Fino Sherry',vinification_details='Wine enters the Example solera.',
            other_information='Our other wine enters the Alternate 1847 solera.')))

    def test_packaging_attribute_cannot_assert_complete_product(self):
        value=context('Laundry Sheets',official_vegan=0)
        value['manufacturer_vegan_evidence']=[{'field_title':'recyclingInformation',
            'content':'RECYCLABLE PACKAGING. Biodegradable. 80% less plastic. Vegan. Free from preservatives'}]
        ref=policy.reference('/manufacturer_vegan_evidence/0/content','Vegan.')
        self.assertTrue(identity.packaging_attribute(value,ref))
        self.assertFalse(policy.manufacturer_reference_claim(value,ref))
        value['manufacturer_vegan_evidence'][0]['field_title']='features'
        self.assertFalse(identity.packaging_attribute(value,ref))
        value['manufacturer_vegan_evidence'][0]['field_title']='recyclingInformation'
        value['manufacturer_vegan_evidence'][0]['content']='Recyclable packaging. This product is vegan.'
        self.assertFalse(identity.packaging_attribute(value,ref))


if __name__=='__main__': unittest.main()
