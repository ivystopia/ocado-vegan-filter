"""Whole-product safety contracts independent of model agreement."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import subprocess

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import classify_ocado_vegan as classifier
import vegan_policy as policy
import codex_evidence


def context(name='Plain beans',ingredients='Beans',**fields):
    return {'product':{'id':'1','name':name,'ingredients':ingredients,**fields},'flags':[],'categories':[],'manufacturer_vegan_evidence':[]}


def source_check(value, path='/manufacturer_source_candidates/0', scope='product'):
    row = policy.source_row(value, path)
    source = policy.reference(path + '/source_text', row['source_text'])
    identity = policy.reference('/product/name', value['product']['name'])
    return {'source_path':path, 'scope':scope,
            'product':{'status':'established', 'evidence_refs':[identity,source]},
            **{key:{'status':'not_applicable', 'evidence_refs':[source]} for key in ('variant','market','period')}}


class WholeProductPolicyTests(unittest.TestCase):
    def test_capsule_ingredients_on_a_volume_labelled_liquid_need_identity_resolution(self):
        value=context('Example Oil Blend','Flax seed oil, Gelatin Capsule (North American Source)',pack_size='500ml',official_vegan=1)
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertTrue(result.evidence['positive_block'])
        self.assertFalse(result.evidence['tag_animal_conflict'])
        for name,pack in [('Example Oil Capsules','90 capsules'),('Example Oil Softgels','60 tablets')]:
            result=classifier.classify_by_rules(context(name,value['product']['ingredients'],pack_size=pack,official_vegan=1))
            self.assertEqual(result.vegan_status,'nonvegan')
            self.assertTrue(result.evidence['tag_animal_conflict'])

    def test_pdf_source_markers_do_not_interrupt_exact_visible_quotations(self):
        source='L12@P1: Scope includes fabrics, trims,\nL13@P1: packaging and dyes.\nL14: Exceptions apply.'
        self.assertEqual(policy.visible_source_text(source),'Scope includes fabrics, trims, packaging and dyes. Exceptions apply.')
        self.assertEqual(policy.visible_source_text('Product code L12@P1: remains text.'),'Product code L12@P1: remains text.')
        value=context()
        value['manufacturer_source_candidates']=[{'source_text':source}]
        ref={'path':'/manufacturer_source_candidates/0/source_text','quote':'Scope includes fabrics, trims, packaging and dyes.'}
        self.assertEqual(policy.resolve_reference(value,ref),source)
        with self.assertRaises(ValueError):
            policy.resolve_reference(value,{**ref,'quote':'Scope includes all fabrics, trims, packaging and dyes.'})

    def test_specific_wine_category_supplies_risk_without_generalising_to_spirits(self):
        value=context('Chateau Example','Water')
        value['categories']=['beer-wine-spirits/wine/red-wine']
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        value['categories']=['beer-wine-spirits/spirits/vodka']
        self.assertFalse(any(issue['rule_id']=='wine-fining-v1' for issue in policy.material_risks(value,'Water')))

    def test_interpreted_manufacturer_claim_precedes_a_valid_name(self):
        value=context('Vegan white wine',None,manufacturer_marketing='Our white wine is suitable for vegans.')
        self.assertIsNone(classifier.classify_by_rules(value))
        assessment=policy.base_assessment(value,complete=True)
        refs=[policy.reference('/product/manufacturer_marketing',value['product']['manufacturer_marketing'])]
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':refs}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_reason'],'manufacturer')

    def test_retained_source_quotes_accept_visible_words_but_not_invented_text(self):
        value=context()
        value['manufacturer_source_candidates']=[{'source_text':'L4: **Suitable for vegans.**\nL5: Except the honey variant.'}]
        policy.resolve_reference(value,{'path':'/manufacturer_source_candidates/0/source_text','quote':'Suitable for vegans.'})
        with self.assertRaises(ValueError):policy.resolve_reference(value,{'path':'/manufacturer_source_candidates/0/source_text','quote':'All products are vegan.'})

    def test_associated_manufacturer_assurance_resolves_generic_wine_risk(self):
        value=context('Example white wine',None)
        text='Example white wine is suitable for vegans.'
        value['manufacturer_source_candidates']=[{'source_id':'faq','source_text':text,
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        refs=[policy.reference('/manufacturer_source_candidates/0/source_text',text)]
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':refs}]
        assessment['source_applicability']=[source_check(value)]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','manufacturer'))
        value['manufacturer_source_candidates'][0]['association_status']='catalogue_changed'
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_status'],'unknown')

    def test_website_claim_requires_product_variant_market_and_period_proof(self):
        value=context('Example Candle, Ochre',None)
        text='Example Candle\nColour Pink Mix Out of stock online\nSuitable for vegans.'
        value['manufacturer_source_candidates']=[{'source_id':'source','source_text':text,
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[{'path':'/manufacturer_source_candidates/0/source_text','quote':'Suitable for vegans.'}]}]
        for checks in ([], [source_check(value)]):
            assessment['source_applicability']=checks
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')
            self.assertEqual(result['vegan_status'],'unknown')
            self.assertTrue(result['positive_block'])
            self.assertIn('identity_conflict',{issue['code'] for issue in result['assessment']['material_issues']})
        value['product']['name']='Example Candle, Pink Mix'
        assessment['identity']['evidence_refs']=[policy.reference('/product/name',value['product']['name'])]
        assessment['composition']['evidence_refs']=[policy.reference('/product/name',value['product']['name'])]
        assessment['source_applicability']=[source_check(value)]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_reason'],'manufacturer')
        for dimension in policy.SOURCE_DIMENSIONS:
            incomplete=copy.deepcopy(assessment)
            incomplete['source_applicability'][0][dimension]['evidence_refs']=[]
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=incomplete,requested_status='vegan',requested_reason='manufacturer')
            self.assertEqual(result['vegan_status'],'unknown')

    def test_inclusive_range_can_cover_a_different_colour_but_generic_title_cannot(self):
        value=context('Example Candle, Ochre',None)
        text='Example Candle\nColour Pink Mix\nAll Example Candle colours are suitable for vegans.'
        value['manufacturer_source_candidates']=[{'source_text':text,'source_id':'source',
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[{'path':'/manufacturer_source_candidates/0/source_text','quote':'All Example Candle colours are suitable for vegans.'}]}]
        assessment['source_applicability']=[source_check(value,scope='range')]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')['vegan_reason'],'manufacturer')

    def test_irrelevant_wrong_variant_page_does_not_defeat_an_applicable_tag(self):
        value=context('Example Candle, Ochre',None,official_vegan=1)
        text='Example Candle\nColour Pink Mix\nSuitable for vegans.'
        value['manufacturer_source_candidates']=[{'source_text':text,'source_id':'source',
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[policy.reference('/manufacturer_source_candidates/0/source_text',text)]}]
        assessment['source_applicability']=[source_check(value)]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')['vegan_reason'],'tagged')

    def test_vintage_context_invalidates_identity_hash_and_source_applicability(self):
        value=context('Example Wine',None,current_vintage='2024',package_type='Bottle')
        text='Example Wine. Vintage: 2023. Suitable for vegans.'
        value['manufacturer_source_candidates']=[{'source_text':text,'source_id':'source',
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        before=classifier.context_hash(classifier.product_context_for_llm(value))
        self.assertEqual(classifier.product_context_for_llm(value)['product']['package_type'],'Bottle')
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[policy.reference('/manufacturer_source_candidates/0/source_text',text)]}]
        assessment['source_applicability']=[source_check(value)]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')['vegan_status'],'unknown')
        value['product']['current_vintage']='2023'
        self.assertNotEqual(before,classifier.context_hash(classifier.product_context_for_llm(value)))
        assessment['source_applicability']=[source_check(value)]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')['vegan_reason'],'manufacturer')

    def test_batch_schema_prevents_unknown_with_a_positive_reason(self):
        decision={'product_id':'1','vegan_status':'unknown','vegan_reason':'ingredients','confidence':'uncertain',
            'summary':'Insufficient','evidence':[],'ambiguity_notes':[],'assessment':policy.base_assessment(context())}
        with self.assertRaises(ValueError):policy.validate_schema({'decisions':[decision]},classifier.batch_output_schema({'1'}))

        decision['vegan_reason']=None
        policy.validate_schema({'decisions':[decision]},classifier.batch_output_schema({'1'}))
        decision['product_id']='other'
        with self.assertRaises(ValueError):policy.validate_schema({'decisions':[decision]},classifier.batch_output_schema({'1'}))

    def test_second_pass_scope_doubt_survives_status_reason_agreement(self):
        value=context('Example Wine',None)
        text='Example Wine is suitable for vegans.'
        value['manufacturer_source_candidates']=[{'source_text':text,'source_id':'source',
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[policy.reference('/manufacturer_source_candidates/0/source_text',text)]}]
        assessment['source_applicability']=[source_check(value)]
        first={'product_id':'1','vegan_status':'vegan','vegan_reason':'manufacturer','assessment':assessment}
        second=copy.deepcopy(first)
        second['assessment']['source_applicability'][0]['period']['status']='unresolved'
        merged=classifier.merge_pass_decisions([{'1':first},{'1':second}],'1')
        self.assertEqual(merged['vegan_status'],'vegan')  # Raw agreement remains recorded.
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=merged['assessment'],
            requested_status='vegan',requested_reason='manufacturer')['vegan_status'],'unknown')

    def test_wrong_variant_animal_processing_is_not_definite_negative_proof(self):
        value=context('Example Wine',None,current_vintage='2024')
        text='Example Wine. Vintage: 2023. Fined using isinglass.'
        value['manufacturer_source_candidates']=[{'source_text':text,'source_id':'source',
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        assessment=policy.base_assessment(value,complete=True)
        assessment['source_applicability']=[source_check(value)]
        assessment['material_issues']=[{'code':'animal_processing','target':'wine','rule_id':'wine-fining-v1',
            'evidence_refs':[{'path':'/manufacturer_source_candidates/0/source_text','quote':'Fined using isinglass.'}],
            'resolution_refs':[],'blocks_claim':True}]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='nonvegan')['vegan_status'],'unknown')

    def test_explicit_all_vintages_assurance_can_resolve_a_page_vintage(self):
        value=context('Example Wine',None,current_vintage='2024')
        text='Example Wine. Vintage: 2023. All vintages are suitable for vegans.'
        value['manufacturer_source_candidates']=[{'source_text':text,'source_id':'source',
            'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'association_status':'catalogue_match'}]
        assessment=policy.base_assessment(value,complete=True)
        assessment['source_applicability']=[source_check(value)]
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[policy.reference('/manufacturer_source_candidates/0/source_text',text)]}]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')['vegan_reason'],'manufacturer')

    def test_manufactured_nonfood_single_material_is_not_whole_article_proof(self):
        value=context('Body Cream','Butyrospermum Parkii (Shea) Butter')
        assessment=policy.base_assessment(value,complete=True)
        refs=[policy.reference('/product/ingredients',value['product']['ingredients'])]
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':refs}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='ingredients')
        self.assertEqual(result['vegan_status'],'unknown')

    def test_risky_ingredients_are_not_universal_vegan_evidence(self):
        for ingredient in ['Sugar','E471','E 422','Lecithin','Raising agent','Firming agent','Acidity regulator','Vegetable suet, Water']:
            with self.subTest(ingredient=ingredient):
                decision=classifier.classify_by_rules(context('Example product',ingredient))
                self.assertTrue(decision is None or decision.vegan_status=='unknown')

    def test_an_ambiguous_term_cannot_resolve_its_own_source(self):
        value=context('Example bread','Wheat flour, E471')
        assessment=policy.base_assessment(value,complete=True)
        refs=[policy.reference('/product/ingredients','Wheat flour, E471')]
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':refs}]
        assessment['material_issues']=[{'code':'source_unresolved','target':'E471','rule_id':'source-dependent-additives-v1','evidence_refs':refs,'resolution_refs':refs,'blocks_claim':False}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='ingredients')
        self.assertEqual(result['vegan_status'],'unknown')

    def test_definite_animal_processing_overrides_tag_and_is_reportable(self):
        value=context('White wine',None,official_vegan=1,vinification_details='Not vegan. Fined using isinglass.')
        assessment=policy.base_assessment(value)
        refs=[policy.reference('/product/vinification_details','Fined using isinglass.')]
        assessment['material_issues']=[{'code':'animal_processing','target':'wine','rule_id':'wine-fining-v1','evidence_refs':refs,'resolution_refs':[],'blocks_claim':True}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='nonvegan')
        self.assertEqual(result['vegan_status'],'nonvegan')
        self.assertTrue(result['tag_animal_conflict'])

    def test_an_affirmative_animal_declaration_outside_ingredients_can_be_negative_proof(self):
        for text,expected in [('Contains gelatine.','nonvegan'),('Contains coconut milk.','unknown'),('May contain milk.','unknown')]:
            with self.subTest(text=text):
                value=context('Example product',None,official_vegan=1,detailed_description=text)
                assessment=policy.base_assessment(value)
                refs=[policy.reference('/product/detailed_description',text)]
                assessment['material_issues']=[{'code':'animal_ingredient','target':'whole_product','rule_id':'source-declaration','evidence_refs':refs,'resolution_refs':[],'blocks_claim':True}]
                result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='nonvegan')
                self.assertEqual(result['vegan_status'],expected)
        result=classifier.classify_by_rules(context('Example product',None,official_vegan=1,detailed_description='Contains gelatine.'))
        self.assertEqual(result.vegan_status,'nonvegan')
        self.assertIsNone(classifier.classify_by_rules(context('Potatoes',None,detailed_description='Recipe: a mash made with butter.')))
        for text in ['This body cream contains vitamin E and natural jojoba oil.',
                     'Made with 98% pure organic coconut milk and our special blend of live vegan cultures to create an alternative to Greek style yoghurt.',
                     'This cream is made with shea butter and natural oils.']:
            value=context('Example Vegan Product',None,official_vegan=1,detailed_description=text)
            result=classifier.classify_by_rules(value)
            self.assertTrue(result is None or result.vegan_status!='nonvegan')

    def test_primary_pass_count_is_exactly_two(self):
        for passes in (1,3):
            with self.assertRaisesRegex(ValueError,'exactly two'):
                classifier.classify_codex_contexts([context()],passes=passes,retries=0,model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')

    def test_each_positive_route_can_resolve_generic_wine_risk(self):
        for fields,reason in [({'official_vegan':1},'tagged'),({'dietary_information':'Suitable for vegans'},'manufacturer'),({'name':'Vegan white wine'},'name')]:
            with self.subTest(reason=reason):
                value=context('White wine',None);value['product'].update(fields)
                result=classifier.classify_by_rules(value)
                self.assertEqual((result.vegan_status,result.vegan_reason),('vegan',reason))
                risks=result.evidence['assessment']['material_issues']
                self.assertTrue(all(issue['resolution_refs'] for issue in risks))

    def test_always_animal_e_numbers_override_claims(self):
        for ingredient in ['E120','E 901','E904','E 1105','Gelatine','Marine collagen (fish)']:
            with self.subTest(ingredient=ingredient):
                result=classifier.classify_by_rules(context('Vegan capsules',ingredient,official_vegan=1))
                self.assertEqual(result.vegan_status,'nonvegan')
                self.assertTrue(result.evidence['tag_animal_conflict'])

    def test_inci_butters_component_headings_and_cultures_are_not_animal_proof(self):
        for ingredient in ['Butyrospermum Parkii (Shea) Butter', 'Theobroma Cacao (Cocoa) Seed Butter',
                           'Butyrospermum Parkii (Shea Butter)', 'Tiger Nut Butter',
                           'Ice-Cream (55%): Soy Extract, Water, Sugar', 'Yogurt Cultures (S. Thermophilus)']:
            with self.subTest(ingredient=ingredient):
                result=classifier.classify_by_rules(context('Example product',ingredient,official_vegan=1))
                self.assertEqual(result.vegan_status,'vegan')
        result=classifier.classify_by_rules(context('Example cream','Ice-Cream (55%): Milk, Sugar',official_vegan=1))
        self.assertEqual(result.vegan_status,'nonvegan')

    def test_absence_and_warning_spans_cannot_hide_a_new_ingredient_assertion(self):
        for ingredient in ['Rice. Free from milk but contains egg', 'Rice; May contain milk, Ingredients: Egg', 'Rice; May contain milk but contains gelatine']:
            with self.subTest(ingredient=ingredient):
                result=classifier.classify_by_rules(context('Example food',ingredient,official_vegan=1))
                self.assertEqual(result.vegan_status,'nonvegan')
        result=classifier.classify_by_rules(context('Example supplement','Spirulina. Free from: fillers, binders, lactose, milk products.',official_vegan=1))
        self.assertEqual(result.vegan_status,'vegan')

    def test_missing_identity_is_not_supplied_by_category(self):
        value=context('Butter Gold Bakers',None);value['categories']=['Vegetables/Potatoes']
        result=classifier.classify_by_rules(value)
        self.assertTrue(result is None or result.vegan_status=='unknown')
        for name in ['Roasted Carrots','Carrots Gnocchi','Carrots Cleaning Spray']:
            value=context(name,None);value['categories']=['fresh-chilled-food/vegetables/carrot']
            result=classifier.classify_by_rules(value)
            self.assertTrue(result is None or result.vegan_status=='unknown')
        value=context('Chicken and Carrots',None);value['categories']=['fresh-chilled-food/vegetables/carrot']
        result=classifier.classify_by_rules(value)
        self.assertTrue(result is None or result.vegan_status=='unknown')

    def test_conflicting_ordinary_identities_are_blocked_even_when_both_vegan(self):
        result=classifier.classify_by_rules(context('Carrots','Apples',official_vegan=1))
        self.assertEqual(result.vegan_status,'unknown')
        self.assertTrue(result.evidence['positive_block'])

    def test_full_claim_text_after_old_truncation_boundary(self):
        text='Suitable for vegans. '+('Product information. '*80)+' Not suitable for vegans.'
        result=classifier.classify_by_rules(context(dietary_information=text))
        self.assertEqual(result.vegan_status,'unknown')
        self.assertTrue(result.evidence['positive_block'])

    def test_component_sibling_negated_and_nonfood_claims_do_not_promote(self):
        for name in ['Chicken with vegan dressing','Not vegan beans','Vegan leather boots','For vegans recipe book','Hairbrush vegan bristles','Vegan Collagen Plumping Serum']:
            with self.subTest(name=name):
                result=classifier.classify_by_rules(context(name,None))
                self.assertTrue(result is None or result.vegan_status=='unknown')
        result=classifier.classify_by_rules(context('Cotton pads','Cotton'))
        self.assertTrue(result is None or result.vegan_status=='unknown')
        for text in ['Is this vegan-friendly? No.','Suitable for vegans?','Our apple drink is suitable for vegans.']:
            result=classifier.classify_by_rules(context('Example product',None,dietary_information=text))
            self.assertTrue(result is None or result.vegan_status=='unknown')
        for name in ['No vegan certification wine','Is this vegan?']:
            result=classifier.classify_by_rules(context(name,None))
            self.assertTrue(result is None or result.vegan_status=='unknown')
        value=context('Not Vegan Wine',None,official_vegan=1)
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertTrue(result.evidence['positive_block'])
        for value in [context('The Vegan Company Classic Product',None,brand='The Vegan Company'),
                      context('A Vegan’s Guide to Gardening',None),context('Vegan Cookbook',None)]:
            result=classifier.classify_by_rules(value)
            self.assertTrue(result is None or result.vegan_status=='unknown')
        value=context('The Vegan Company Vegan Beans',None,brand='The Vegan Company')
        self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'name')

    def test_a_whole_product_dressing_name_is_a_positive_claim(self):
        result=classifier.classify_by_rules(context('Example Vegan Caesar Dressing',None))
        self.assertEqual((result.vegan_status,result.vegan_reason),('vegan','name'))

    def test_an_uncertain_model_positive_cannot_become_certain_at_the_gate(self):
        value=context();assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':[policy.reference('/product/ingredients','Beans')]}]
        result=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='ingredients',confidence='uncertain',summary='Uncertain',source='codex',evidence={'assessment':assessment})
        self.assertEqual(classifier.apply_policy_gate(value,result).vegan_status,'unknown')

    def test_sugar_identity_without_ingredients_still_has_a_process_issue(self):
        value=context('Refined Caster Sugar',None)
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertIn('processing_unresolved',{issue['code'] for issue in result.evidence['assessment']['material_issues']})

    def test_second_pass_generic_doubt_survives_agreed_positive(self):
        value=context()
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':[policy.reference('/product/ingredients','Beans')]}]
        first={'product_id':'1','vegan_status':'vegan','vegan_reason':'ingredients','assessment':assessment}
        second=copy.deepcopy(first)
        second['assessment']['sourcing']={'status':'unresolved','evidence_refs':[policy.reference('/product/ingredients','Beans')]}
        second['assessment']['material_issues']=[{'code':'source_unresolved','target':'ingredient','rule_id':'case-specific','evidence_refs':[policy.reference('/product/ingredients','Beans')],'resolution_refs':[],'blocks_claim':False}]
        merged=classifier.merge_pass_decisions([{'1':first},{'1':second}],'1')
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=merged['assessment'],requested_status='vegan',requested_reason='ingredients')
        self.assertEqual(result['vegan_status'],'unknown')

    def test_fabricated_reference_duplicate_and_extra_fields_rejected(self):
        value=context();assessment=policy.base_assessment(value,complete=True)
        assessment['composition']['evidence_refs']=[{'path':'/product/ingredients','quote':'Gelatine'}]
        with self.assertRaisesRegex(ValueError,'absent'):policy.validate_assessment(assessment,value)
        decision={'product_id':'1','vegan_status':'unknown','vegan_reason':None,'confidence':'uncertain','summary':'Missing evidence','evidence':[],'ambiguity_notes':[],'assessment':policy.base_assessment(value)}
        with self.assertRaisesRegex(ValueError,'Duplicate|duplicate'):classifier.validate_codex_payload({'decisions':[decision,decision]},{'1'})
        with self.assertRaises(ValueError):classifier.validate_codex_payload({'decisions':[{**decision,'unexpected':True}]},{'1'})


class CodexMeteringTests(unittest.TestCase):
    def test_stop_file_prevents_a_call_without_inventing_unmetered_spend(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            stop=Path(tmp)/'STOP';stop.touch()
            records=[];token=codex_evidence.CALL_RECORDS.set(records)
            try:
                with mock.patch.object(codex_evidence,'STOP_FILE',stop), mock.patch.object(codex_evidence,'quota_snapshot',side_effect=AssertionError('No new call')):
                    with self.assertRaises(codex_evidence.StopRequestedError):
                        codex_evidence.run('Prompt',{},model='gpt-6-luna',effort='high',codex_bin='codex',
                            stage='test',product_ids=['1'],prompt_version='test',context_hash='hash')
            finally:codex_evidence.CALL_RECORDS.reset(token)
            self.assertEqual(codex_evidence.usage_totals(records)['missing_usage_calls'],0)
            self.assertEqual(codex_evidence.usage_totals(records)['guard_rejections'],1)

    def test_included_limit_never_falls_back_to_credits(self):
        for snapshot in [{'ordinary_usage_allowed':False,'buckets':{}},
                         {'buckets':{'codex':{'primary':{'usedPercent':100},'credits':{'hasCredits':False}}}},
                         {'buckets':{'codex':{'secondary':{'usedPercent':90},'credits':{'hasCredits':True}}}}]:
            snapshot={'auth_type':'chatgpt','ordinary_usage_allowed':True,**snapshot}
            with self.subTest(snapshot=snapshot), self.assertRaises(codex_evidence.UsageLimitError):
                codex_evidence.require_included_allowance(snapshot)

    def test_unknown_or_api_authentication_cannot_use_an_unverified_allowance(self):
        valid={'auth_type':'chatgpt','ordinary_usage_allowed':True,'buckets':{'codex':{'primary':{'usedPercent':84},'credits':{'hasCredits':False}}}}
        codex_evidence.require_included_allowance(valid)
        for snapshot in [{**valid,'auth_type':'apiKey'},{**valid,'ordinary_usage_allowed':None},{**valid,'buckets':{}}]:
            with self.subTest(snapshot=snapshot),self.assertRaises(codex_evidence.UsageLimitError):codex_evidence.require_included_allowance(snapshot)

    def test_observed_tool_use_halts_an_invocation(self):
        def fake(command,**kwargs):
            Path(command[command.index('--output-last-message')+1]).write_text('{}')
            events=[{'type':'item.completed','item':{'type':'command_execution','command':'cat gold.json'}},
                    {'type':'turn.completed','usage':{'input_tokens':99,'output_tokens':17}}]
            return subprocess.CompletedProcess(command,0,'\n'.join(json.dumps(e) for e in events),'')
        with mock.patch.object(codex_evidence.subprocess,'run',side_effect=fake), mock.patch.object(codex_evidence,'quota_snapshot',return_value={'auth_type':'chatgpt','ordinary_usage_allowed':True,'buckets':{'codex':{'primary':{'usedPercent':0}}}}):
            with self.assertRaises(codex_evidence.IsolationViolationError):
                codex_evidence.run('Prompt',{},model='gpt-6-luna',effort='high',codex_bin='codex',stage='test',product_ids=['1'],prompt_version='test',context_hash='hash')

    def test_reasoning_is_not_added_again_to_output_usage(self):
        record={'call_id':'one','error':None,'usage':{'input_tokens':100,'cached_input_tokens':10,'output_tokens':50,'reasoning_output_tokens':20}}
        result=codex_evidence.usage_totals([record,record])
        self.assertEqual(result['calls'],1);self.assertEqual(result['output_tokens'],50)

    def test_isolation_disables_tools_without_changing_personal_configuration(self):
        command=codex_evidence.invocation('codex',Path('/temporary'),Path('/schema'),Path('/output'),'gpt-6-luna','high')
        self.assertIn('--ignore-user-config',command);self.assertIn('--ignore-rules',command)
        self.assertIn('features.shell_tool=false',command);self.assertIn('web_search="disabled"',command)
        self.assertIn('forced_login_method="chatgpt"',command)
        self.assertNotIn('--with-api-key',command)

    def test_failed_validation_keeps_usage_and_attempt_record(self):
        records=[];token=codex_evidence.CALL_RECORDS.set(records)
        self.addCleanup(codex_evidence.CALL_RECORDS.reset,token)
        def fake(command,**kwargs):
            Path(command[command.index('--output-last-message')+1]).write_text('{"invalid":true}')
            return subprocess.CompletedProcess(command,0,json.dumps({'type':'turn.completed','usage':{'input_tokens':99,'output_tokens':17}}),'')
        with mock.patch.object(codex_evidence.subprocess,'run',side_effect=fake), mock.patch.object(codex_evidence,'quota_snapshot',return_value={'auth_type':'chatgpt','ordinary_usage_allowed':True,'buckets':{'codex':{'primary':{'usedPercent':0}}}}):
            with self.assertRaises(ValueError):
                codex_evidence.run('Prompt',{},model='gpt-6-luna',effort='high',codex_bin='codex',stage='test',product_ids=['1'],prompt_version='test',context_hash='hash',validator=lambda value:policy.validate_schema(value,{'type':'object','additionalProperties':False,'properties':{}}))
        self.assertEqual(records[0]['usage']['output_tokens'],17)
        self.assertTrue(records[0]['error'])
