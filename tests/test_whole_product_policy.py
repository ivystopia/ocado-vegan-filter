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


def faq_merge_context(source):
    value=context('Example Wine','Grapes',brand='Example Winery',package_type='Bottle')
    value['manufacturer_source_candidates']=[{'source_id':'test-faq','source_url':'https://example.test/faq',
        'source_text':source,'source_sha256':__import__('hashlib').sha256(source.encode()).hexdigest(),
        'association_status':'catalogue_match','market_scope':'UK','official_identity':'Example Winery',
        'retrieved_at':'2026-10-07T00:00:00+00:00'}]
    return value


def faq_reading(value, quotes):
    assessment=policy.base_assessment(value,complete=True)
    assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
        policy.reference('/manufacturer_source_candidates/0/source_text',quote) for quote in quotes]}]
    assessment['source_applicability']=[source_check(value)]
    return {'product_id':'1','vegan_status':'vegan','vegan_reason':'manufacturer',
        'confidence':'certain','assessment':assessment}


class WholeProductPolicyTests(unittest.TestCase):
    def test_hyphenated_food_scope_remains_a_component_only_assurance(self):
        answer='No animal-derived materials or processes are used.'
        for qualifier in ('food-only','food‐only','food‑only','contents-only','drink-only'):
            with self.subTest(qualifier=qualifier):
                restriction='This assurance is '+qualifier+'.'
                self.assertTrue(policy.component_only_claim(restriction))
                value=faq_merge_context('We produce Example Wine.\nIs this product vegan?\n'+answer+' '+restriction)
                first=faq_reading(value,['Is this product vegan?']);second=faq_reading(value,[answer])
                for readings in ([first,second],[second,first]):
                    merged=classifier.merge_pass_decisions([{'1':item} for item in readings],'1')
                    result=policy.evaluate(value,**classifier.policy_facts(value),assessment=merged['assessment'],
                        requested_status='vegan',requested_reason='manufacturer')
                    self.assertEqual(result['vegan_status'],'unknown')
                value=context('Example Wine','Grapes',package_type='Bottle',
                    features='This product is suitable for vegans. '+restriction)
                ref=policy.reference('/product/features','This product is suitable for vegans.')
                self.assertFalse(policy.manufacturer_reference_claim(value,ref))

    def test_pooled_faq_citations_cannot_discard_reply_scope_or_exceptions(self):
        assurance='No animal-derived materials or processes are used.'
        for reply,quote in [
            (assurance+' This assurance covers food only; packaging is not assessed.',assurance),
            ('No. Animal-derived fining agents are used.','No. Animal-derived fining agents are used.'),
            (assurance+' Except animal-derived fining aids.',assurance),
        ]:
            with self.subTest(reply=reply):
                value=faq_merge_context('We produce Example Wine.\nIs this product vegan?\n'+reply)
                first=faq_reading(value,['Is this product vegan?']);second=faq_reading(value,[quote])
                for readings in ([first,second],[second,first]):
                    merged=classifier.merge_pass_decisions([{'1':item} for item in readings],'1')
                    assessment=merged['assessment']
                    for stage in ('model','audit','export'):
                        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                            requested_status='vegan',requested_reason='manufacturer')
                        self.assertEqual(result['vegan_status'],'unknown',stage)
                        self.assertFalse(result['tag_animal_conflict'])
                        assessment=result['assessment']

    def test_faq_reply_qualification_is_bound_to_its_own_answer_section(self):
        answer='No animal-derived materials or processes are used.'
        value=faq_merge_context('We produce Example Wine.\nIs this product vegan?\n'+answer+
            '\nIs the separately sold recipe vegan?\nThis assurance covers food only; packaging is not assessed.')
        first=faq_reading(value,[answer]);second=faq_reading(value,['Is this product vegan?\n'+answer])
        for readings in ([first,second],[second,first]):
            merged=classifier.merge_pass_decisions([{'1':item} for item in readings],'1')
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=merged['assessment'],
                requested_status='vegan',requested_reason='manufacturer')
            self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','manufacturer'))

    def test_agreed_pass_positive_evidence_survives_either_reading_order(self):
        answer='No animal-derived materials or processes are used.'
        combined='Is this product vegan?\n'+answer
        value=faq_merge_context('We produce Example Wine.\n'+combined)
        first=faq_reading(value,[answer]);second=faq_reading(value,[combined])
        original=copy.deepcopy([first,second])
        for readings in ([first,second],[second,first]):
            merged=classifier.merge_pass_decisions([{'1':item} for item in readings],'1')
            assessment=merged['assessment']
            for stage in ('model','audit','export'):
                result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                    requested_status=merged['vegan_status'],requested_reason=merged['vegan_reason'])
                self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','manufacturer'),stage)
                assessment=result['assessment']
        self.assertEqual([first,second],original)

    def test_positive_evidence_merge_cannot_erase_doubt_or_invent_a_question(self):
        answer='No animal-derived materials or processes are used.'
        value=faq_merge_context('We produce Example Wine.\nIs this product vegan?\n'+answer)
        for blocker in ('processing','identity','source_scope','missing_question'):
            with self.subTest(blocker=blocker):
                first=faq_reading(value,[answer]);second=faq_reading(value,['Is this product vegan?',answer])
                if blocker in {'processing','identity'}:
                    second['assessment'][blocker]['status']='unresolved'
                elif blocker=='source_scope':
                    second['assessment']['source_applicability'][0]['scope']='component'
                else:
                    second=copy.deepcopy(first)
                for readings in ([first,second],[second,first]):
                    merged=classifier.merge_pass_decisions([{'1':item} for item in readings],'1')
                    result=policy.evaluate(value,**classifier.policy_facts(value),assessment=merged['assessment'],
                        requested_status='vegan',requested_reason='manufacturer')
                    self.assertEqual(result['vegan_status'],'unknown')

    def test_positive_evidence_merge_cannot_borrow_another_faq_answer_or_override_animals(self):
        answer='No animal-derived materials or processes are used.'
        value=faq_merge_context('We produce Example Wine.\nIs this product vegan?\nAsk our team.\nIs this bottle vegan?\n'+answer)
        first=faq_reading(value,['Is this product vegan?']);second=faq_reading(value,[answer])
        for readings in ([first,second],[second,first]):
            merged=classifier.merge_pass_decisions([{'1':item} for item in readings],'1')
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=merged['assessment'],
                requested_status='vegan',requested_reason='manufacturer')
            self.assertEqual(result['vegan_status'],'unknown')
        value['product'].update(ingredients='Grapes, Milk',official_vegan=1)
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=merged['assessment'],
            requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_status'],'nonvegan')
        self.assertTrue(result['tag_animal_conflict'])

    def test_quantified_ingredient_selection_does_not_restrict_product_vegan_claim(self):
        description="This amazing tasting vegan protein shake is curated from a unique combination of some of the world's finest vegan protein sources."
        value=context('Example Vegan Protein Shake','Pea Protein, Vitamin D3',detailed_description=description)
        for quote in [description,'This amazing tasting vegan protein shake',
                      'This amazing tasting vegan protein shake is curated from a unique combination']:
            with self.subTest(quote=quote):
                ref=policy.reference('/product/detailed_description',quote)
                self.assertTrue(policy.manufacturer_reference_claim(value,ref))
                assessment=policy.base_assessment(value,complete=True)
                assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[ref]},
                    {'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
                for stage in ('model','audit','export'):
                    result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                        requested_status='vegan',requested_reason='manufacturer')
                    self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','manufacturer'),stage)
                    assessment=result['assessment']

    def test_quantified_ingredient_selection_cannot_supply_or_expand_a_vegan_claim(self):
        for description in [
            "Some of our vegan shakes are curated from a unique combination of some of the world's finest vegan protein sources.",
            "This shake is curated from a unique combination of some of the world's finest vegan protein sources.",
            "This vegan formula is curated from a unique combination of some of the world's finest vegan protein sources.",
            "This vegan protein shake is curated from a unique combination of some of the world's finest vegan protein sources, excluding the packaging.",
        ]:
            with self.subTest(description=description):
                value=context('Example Vegan Protein Shake','Pea Protein',detailed_description=description)
                self.assertFalse(policy.manufacturer_reference_claim(value,
                    policy.reference('/product/detailed_description',description)))

    def test_invalid_preferred_reason_does_not_erase_supplied_valid_composition_proof(self):
        value = context('Example Cider Vinegar', 'Apples, Water',
            features='All production stages including ingredient and precursor production are animal-free.')
        process = policy.reference('/product/features', value['product']['features'])
        ingredients = policy.reference('/product/ingredients', value['product']['ingredients'])
        assessment = policy.base_assessment(value, complete=True)
        assessment['positive_support'] = [
            {'reason': 'manufacturer', 'evidence_refs': [process]},
            {'reason': 'ingredients', 'evidence_refs': [ingredients, process]}]
        assessment['material_issues'] = [{**risk, 'resolution_refs': [process]}
            for risk in policy.material_risks(value)]
        raw = {'product_id': '1', 'vegan_status': 'vegan', 'vegan_reason': 'manufacturer',
               'confidence': 'certain', 'assessment': assessment}
        merged = classifier.merge_pass_decisions([{'1': raw}, {'1': copy.deepcopy(raw)}], '1')
        self.assertFalse(policy.manufacturer_reference_claim(value, process))
        for stage in ('model', 'audit', 'export'):
            result = policy.evaluate(value, **classifier.policy_facts(value), assessment=merged['assessment'],
                requested_status='vegan', requested_reason='manufacturer')
            self.assertEqual((result['vegan_status'], result['vegan_reason']), ('vegan', 'ingredients'), stage)
            merged['assessment'] = result['assessment']
        value['product']['dietary_information'] = 'Suitable for vegans.'
        self.assertEqual(policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
            requested_status='vegan', requested_reason='ingredients')['vegan_reason'], 'manufacturer')

    def test_alternative_proof_still_requires_all_safety_and_positive_contracts(self):
        original = context('Example Cider Vinegar', 'Apples, Water',
            features='All production stages including ingredient and precursor production are animal-free.')
        for change, expected in [('missing_support', 'unknown'), ('unresolved_processing', 'unknown'),
                                 ('unresolved_components', 'unknown'), ('incomplete_dimension', 'unknown'),
                                 ('unknown_proposal', 'unknown'), ('animal_ingredient', 'nonvegan')]:
            with self.subTest(change=change):
                value = copy.deepcopy(original)
                if change == 'animal_ingredient':
                    value['product']['ingredients'] = 'Apples, Water, Gelatine'
                process = policy.reference('/product/features', value['product']['features'])
                assessment = policy.base_assessment(value, complete=True)
                assessment['positive_support'] = [
                    {'reason': 'manufacturer', 'evidence_refs': [process]},
                    {'reason': 'ingredients', 'evidence_refs': [policy.reference(
                        '/product/ingredients', value['product']['ingredients'])]}]
                assessment['material_issues'] = [{**risk, 'resolution_refs': [process]}
                    for risk in policy.material_risks(value)]
                if change == 'missing_support':
                    assessment['positive_support'].pop()
                elif change == 'unresolved_processing':
                    for issue in assessment['material_issues']:
                        issue['resolution_refs'] = []
                elif change == 'unresolved_components':
                    value['product']['package_type'] = 'Bottle'
                elif change == 'incomplete_dimension':
                    assessment['composition']['status'] = 'insufficient'
                result = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                    requested_status='unknown' if change == 'unknown_proposal' else 'vegan',
                    requested_reason=None if change == 'unknown_proposal' else 'manufacturer')
                self.assertEqual(result['vegan_status'], expected)

    def test_uncertain_process_facts_cannot_clear_wine_or_sugar_risks(self):
        for name, ingredients, rule, weak, certain, cropped in [
            ('Example Wine', 'Grapes', 'wine-fining-v1',
             'This wine may use animal-free fining agents.',
             'This wine uses animal-free fining agents.', 'animal-free fining agents'),
            ('Example Sugar', 'Sugar', 'sugar-refining-v1',
             'This sugar might be refined without bone char.',
             'This sugar is refined without bone char.', 'refined without bone char'),
        ]:
            for text, expected in [(weak, 'unknown'), (certain, 'vegan')]:
                for quote in (text, cropped):
                    with self.subTest(name=name, text=text, quote=quote):
                        value = context(name, ingredients, features=text)
                        risk = next(r for r in policy.material_risks(value) if r['rule_id'] == rule)
                        assessment = policy.base_assessment(value, complete=True)
                        assessment['positive_support'] = [{'reason': 'ingredients', 'evidence_refs': [
                            policy.reference('/product/ingredients', ingredients)]}]
                        assessment['material_issues'] = [{**risk, 'resolution_refs': [
                            policy.reference('/product/features', quote)]}]
                        self.assertEqual(policy.resolved_risk(risk, assessment, value), expected == 'vegan')
                        for stage in ('model', 'audit', 'export'):
                            result = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                                requested_status='vegan', requested_reason='ingredients')
                            # A certain fining fact resolves fining only; OIV
                            # documents other animal-derived wine inputs.
                            final_expected = 'unknown' if name == 'Example Wine' else expected
                            self.assertEqual(result['vegan_status'], final_expected, stage)
                            assessment = result['assessment']

    def test_fining_only_facts_do_not_prove_all_wine_production_inputs(self):
        for text in ('This wine is unfined.', 'This wine uses animal-free fining agents.',
                     'This wine uses animal-free fining agents. May contain milk.',
                     'This wine uses animal-free fining agents and can be enjoyed with dinner.'):
            with self.subTest(text=text):
                value = context('Example Wine', 'Grapes', features=text)
                assessment = policy.base_assessment(value, complete=True)
                assessment['positive_support'] = [{'reason': 'ingredients', 'evidence_refs': [
                    policy.reference('/product/ingredients', 'Grapes')]}]
                process = policy.reference('/product/features', text)
                assessment['material_issues'] = [{**risk, 'resolution_refs': [process]}
                    for risk in policy.material_risks(value)]
                fining = next(r for r in assessment['material_issues'] if r['rule_id'] == 'wine-fining-v1')
                other = next(r for r in assessment['material_issues'] if r['rule_id'] == 'wine-other-processing-v1')
                self.assertTrue(policy.resolved_risk(fining, assessment, value))
                self.assertFalse(policy.resolved_risk(other, assessment, value))
                for stage in ('model', 'audit', 'export'):
                    outcome = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                        requested_status='vegan', requested_reason='ingredients')
                    self.assertEqual(outcome['vegan_status'], 'unknown', stage)
                    assessment = outcome['assessment']

    def test_complete_wine_process_proof_and_applicable_claims_remain_eligible(self):
        complete = 'All production inputs and processing aids from grapes through must to the finished wine are animal-free.'
        for text, expected in ((complete, 'vegan'), (complete.replace('are animal-free', 'may be animal-free'), 'unknown')):
            value = context('Example Wine', 'Grapes', features=text)
            assessment = policy.base_assessment(value, complete=True)
            assessment['positive_support'] = [{'reason': 'ingredients', 'evidence_refs': [
                policy.reference('/product/ingredients', 'Grapes')]}]
            process = policy.reference('/product/features', text)
            assessment['material_issues'] = [{**risk, 'resolution_refs': [process]}
                for risk in policy.material_risks(value)]
            for stage in ('model', 'audit', 'export'):
                outcome = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                    requested_status='vegan', requested_reason='ingredients')
                self.assertEqual(outcome['vegan_status'], expected, (text, stage))
                assessment = outcome['assessment']
        for reason in ('tagged', 'manufacturer', 'name'):
            value = context('Example Vegan Wine' if reason == 'name' else 'Example Wine', 'Grapes',
                features='This wine is unfined.', official_vegan=int(reason == 'tagged'))
            if reason == 'manufacturer':
                value['product']['dietary_information'] = 'Suitable for vegans.'
            path = '/product/official_vegan' if reason == 'tagged' else '/product/dietary_information' if reason == 'manufacturer' else '/product/name'
            ref = policy.reference(path, value['product'][path.rsplit('/', 1)[1]])
            assessment = policy.base_assessment(value, complete=True)
            assessment['positive_support'] = [{'reason': reason, 'evidence_refs': [ref]}]
            outcome = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                requested_status='vegan', requested_reason=reason)
            self.assertEqual((outcome['vegan_status'], outcome['vegan_reason']), ('vegan', reason))
            value['product']['ingredients'] = 'Grapes, egg white'
            outcome = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                requested_status='vegan', requested_reason=reason)
            self.assertEqual(outcome['vegan_status'], 'nonvegan')

    def test_process_resolution_binds_website_excerpt_to_full_capture(self):
        for prefix, expected in [('This wine may use ', False), ('This wine uses ', True)]:
            value = context('Example Wine', 'Grapes')
            quote = 'animal-free fining agents'
            value['manufacturer_vegan_evidence'] = [{'source_kind': 'manufacturer_website',
                'content': quote, 'source_text': 'Example Wine. ' + prefix + quote + '.'}]
            risk = next(r for r in policy.material_risks(value) if r['rule_id'] == 'wine-fining-v1')
            assessment = {'material_issues': [{**risk, 'resolution_refs': [
                policy.reference('/manufacturer_vegan_evidence/0/content', quote)]}]}
            self.assertEqual(policy.resolved_risk(risk, assessment, value), expected)

    def test_definite_egg_white_processing_overrides_tag_without_hiding_identity_doubt(self):
        original = 'This wine is processed with egg white during must preparation.'
        for text, identity, expected in (
            (original, 'established', 'nonvegan'),
            (original.replace('is processed', 'is not processed'), 'established', 'unknown'),
            (original.replace('is processed', 'may be processed'), 'established', 'unknown'),
            (original.replace('This wine', 'Another wine'), 'established', 'unknown'),
            (original, 'conflicted', 'unknown'),
        ):
            with self.subTest(text=text, identity=identity):
                value = context('Example Wine', 'Grapes', official_vegan=1, features=text)
                ref = policy.reference('/product/features', text)
                flag = policy.reference('/product/official_vegan', 1)
                assessment = policy.base_assessment(value, complete=True)
                assessment['identity']['status'] = identity
                assessment['material_issues'] = [
                    {'code': 'animal_processing', 'target': 'must preparation', 'rule_id': 'wine-other-processing-v1',
                     'evidence_refs': [ref], 'resolution_refs': [], 'blocks_claim': True},
                    {'code': 'tag_animal_conflict', 'target': 'official flag versus processing', 'rule_id': 'animal-overrides-tag-v1',
                     'evidence_refs': [flag, ref], 'resolution_refs': [], 'blocks_claim': True},
                ]
                for stage in ('model', 'audit', 'export'):
                    outcome = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                        requested_status='nonvegan', requested_reason=None)
                    self.assertEqual(outcome['vegan_status'], expected, stage)
                    if expected == 'nonvegan':
                        self.assertTrue(outcome['tag_animal_conflict'], stage)
                    assessment = outcome['assessment']

    def test_definite_animal_proof_is_not_vetoed_by_other_generic_unknowns(self):
        original = 'This wine is processed with egg white during must preparation.'
        for text, identity, conflict, expected in (
            (original, 'established', False, 'nonvegan'),
            (original.replace('is processed', 'is not processed'), 'established', False, 'unknown'),
            (original.replace('is processed', 'may be processed'), 'established', False, 'unknown'),
            (original.replace('This wine', 'Another wine'), 'established', False, 'unknown'),
            (original, 'conflicted', False, 'unknown'),
            (original, 'established', True, 'unknown'),
        ):
            with self.subTest(text=text, identity=identity, conflict=conflict):
                value = context('Example Wine', 'Grapes, Glycerol', official_vegan=1,
                    features=text, package_type='Bottle')
                ref = policy.reference('/product/features', text)
                name = policy.reference('/product/name', 'Example Wine')
                ingredients = policy.reference('/product/ingredients', 'Glycerol')
                package = policy.reference('/product/package_type', 'Bottle')
                assessment = policy.base_assessment(value, complete=True)
                assessment['identity']['status'] = identity
                for dimension, source in [('sourcing', ingredients), ('processing', ref), ('components', package)]:
                    assessment[dimension] = {'status': 'unresolved', 'evidence_refs': [source]}
                assessment['material_issues'] = [
                    {'code': 'animal_processing', 'target': 'must preparation', 'rule_id': 'wine-other-processing-v1',
                     'evidence_refs': [ref], 'resolution_refs': [], 'blocks_claim': True},
                    {'code': 'tag_animal_conflict', 'target': 'official flag versus processing', 'rule_id': 'animal-overrides-tag-v1',
                     'evidence_refs': [policy.reference('/product/official_vegan', 1), ref], 'resolution_refs': [], 'blocks_claim': True},
                    {'code': 'processing_unresolved', 'target': 'other wine processing', 'rule_id': 'wine-fining-v1',
                     'evidence_refs': [name], 'resolution_refs': [], 'blocks_claim': True},
                    {'code': 'source_unresolved', 'target': 'glycerol source', 'rule_id': 'source-dependent-ingredient-v1',
                     'evidence_refs': [ingredients], 'resolution_refs': [], 'blocks_claim': True},
                    {'code': 'composition_incomplete', 'target': 'bottle materials', 'rule_id': 'supplied-components-composition-v1',
                     'evidence_refs': [package], 'resolution_refs': [], 'blocks_claim': True},
                ]
                if conflict:
                    value['product']['other_information'] = 'Processing statement may belong to another variant.'
                    assessment['material_issues'].append({'code': 'evidence_conflict', 'target': 'animal statement applicability',
                        'rule_id': 'declaration-applicability-v1', 'evidence_refs': [policy.reference('/product/other_information',
                        value['product']['other_information'])], 'resolution_refs': [], 'blocks_claim': True})
                for stage in ('model', 'audit', 'export'):
                    outcome = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                        requested_status='nonvegan', requested_reason=None)
                    self.assertEqual(outcome['vegan_status'], expected, stage)
                    if expected == 'nonvegan':
                        self.assertTrue(outcome['tag_animal_conflict'], stage)
                    assessment = outcome['assessment']

    def test_process_fact_is_not_qualified_by_separate_allergen_or_usage_text(self):
        for text in [
            'This wine uses animal-free fining agents. May contain milk.',
            'This wine uses animal-free fining agents and may contain milk.',
            'This wine uses animal-free fining agents and can be enjoyed with dinner.',
        ]:
            value = context('Example Wine', 'Grapes', features=text)
            risk = next(r for r in policy.material_risks(value) if r['rule_id'] == 'wine-fining-v1')
            assessment = {'material_issues': [{**risk, 'resolution_refs': [
                policy.reference('/product/features', text)]}]}
            self.assertTrue(policy.resolved_risk(risk, assessment, value), text)

    def test_hedged_claim_cannot_cover_processing_in_model_audit_or_export(self):
        for text in [
            'This product may be suitable for vegans.',
            'This product might still be suitable for vegans.',
            'This product could be vegan-friendly.',
            'This product should be suitable for vegetarians and vegans.',
            'This is probably vegan.',
            'This is possibly suitable for vegans.',
            'This is usually vegan.',
            'This is generally suitable for vegans.',
            'Maybe this product is suitable for vegans.',
            'We think this product is vegan.',
            'We think this wine is suitable for vegans.',
            'This product appears to be suitable for vegans.',
        ]:
            with self.subTest(text=text):
                value = context('Example Wine', 'Grapes', features=text)
                ref = policy.reference('/product/features', 'suitable for vegans' if 'suitable for vegans' in text else text)
                assessment = policy.base_assessment(value, complete=True)
                for dim in policy.DIMENSIONS:
                    if dim != 'identity':
                        assessment[dim] = {'status': 'covered_by_claim', 'evidence_refs': [ref]}
                assessment['positive_support'] = [{'reason': 'manufacturer', 'evidence_refs': [ref]}]
                raw = {'product_id': '1', 'vegan_status': 'vegan', 'vegan_reason': 'manufacturer',
                       'confidence': 'certain', 'assessment': assessment}
                merged = classifier.merge_pass_decisions([{'1': raw}, {'1': copy.deepcopy(raw)}], '1')
                for stage in ('model', 'audit', 'export'):
                    outcome = policy.evaluate(value, **classifier.policy_facts(value),
                        assessment=merged['assessment'], requested_status='vegan', requested_reason='manufacturer')
                    self.assertEqual((outcome['vegan_status'], outcome['vegan_reason']), ('unknown', None), stage)
                self.assertFalse(policy.manufacturer_reference_claim(value, ref))

    def test_website_excerpt_cannot_erase_suitability_uncertainty(self):
        value = context('Example Wine', 'Grapes')
        quote = 'suitable for vegans'
        for prefix, expected in [('This product may be ', False), ('This product is ', True)]:
            text = 'We produce Example Wine. ' + prefix + quote + '.'
            value['manufacturer_vegan_evidence'] = [{'source_kind': 'manufacturer_website',
                'content': quote, 'source_text': text, 'verified_status': 'vegan',
                'review_policy_version': policy.POLICY_VERSION}]
            value['manufacturer_source_candidates'] = [{'source_text': text}]
            for path in ('/manufacturer_vegan_evidence/0/content', '/manufacturer_source_candidates/0/source_text'):
                with self.subTest(prefix=prefix, path=path):
                    self.assertEqual(policy.manufacturer_reference_claim(value,
                        policy.reference(path, quote)), expected)

    def test_unrelated_modal_and_allergen_words_do_not_qualify_a_vegan_claim(self):
        for text in [
            'This product is suitable for vegans. May contain milk.',
            'This product may contain milk but is suitable for vegans.',
            'This vegan wine can be enjoyed with dinner.',
            'We think this vegan wine tastes delightful.',
        ]:
            with self.subTest(text=text):
                value = context('Example Wine', 'Grapes', features=text)
                self.assertTrue(policy.manufacturer_reference_claim(value,
                    policy.reference('/product/features', text)))
        self.assertTrue(policy.affirmative_name('Maybe Vegan Foods Vegan Wine', 'Maybe Vegan Foods'))
        for text in ['Normally Vegan Wine', 'Generally Vegan Wine', 'Mostly Vegan Wine', 'Nearly Vegan Wine']:
            self.assertFalse(policy.affirmative_name(text), text)

    def test_plural_pouches_remain_the_known_packaging_component(self):
        value=context('Example Wine','Grapes',package_type='Pouch')
        self.assertFalse(policy.whole_product_vegan_question('Are the pouches vegan?',value))
        for claim in ('The pouches are vegan.','Our pouches are vegan.'):
            value['product']['features']=claim
            self.assertFalse(policy.manufacturer_reference_claim(value,
                policy.reference('/product/features',claim)))

    def test_affirmative_faq_reply_retains_its_question_target(self):
        for separator in ('\n',' '):
            for question,expected in [('Is the packaging vegan?','unknown'),
                                      ('Is this product vegan?','vegan')]:
                for answer in ('Yes, it is vegan.','Vegan'):
                    text=separator.join(['We produce Example Wine.',question,answer])
                    for quote in (answer,'vegan' if 'vegan' in answer else answer,text,separator.join([question,answer])):
                        with self.subTest(question=question,answer=answer,quote=quote,separator=separator):
                            value=context('Example Wine','Grapes',package_type='Bottle')
                            value['manufacturer_source_candidates']=[{'source_id':'capture',
                                'source_url':'https://example.test/wine','source_text':text,
                                'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),
                                'association_status':'catalogue_match'}]
                            assessment=policy.base_assessment(value,complete=True)
                            assessment['source_applicability']=[source_check(value)]
                            assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
                                policy.reference('/manufacturer_source_candidates/0/source_text',quote)]}]
                            for stage in ('model','audit','export'):
                                result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                                    requested_status='vegan',requested_reason='manufacturer')
                                self.assertEqual(result['vegan_status'],expected,stage)
                                assessment=result['assessment']

    def test_independent_whole_product_assertion_after_faq_still_qualifies(self):
        value=context('Example Wine','Grapes',package_type='Bottle')
        value['manufacturer_source_candidates']=[{'source_id':'capture','source_url':'https://example.test/wine',
            'source_text':'Is the packaging vegan?\nYes, it is vegan.\nThis product is vegan.'}]
        self.assertTrue(policy.manufacturer_reference_claim(value,
            policy.reference('/manufacturer_source_candidates/0/source_text','This product is vegan.')))

    def test_faq_excerpt_and_answer_cannot_cross_question_scope(self):
        value=context('Example Wine','Grapes',package_type='Bottle')
        for text,question in [
                ('Example Wine.\nIs the packaging vegan?\nNo animal-derived materials are used.', 'vegan'),
                ('Example Wine.\nIs this product vegan?\nWe have not confirmed that.\nIs the packaging vegan?\nNo animal-derived materials are used.', 'Is this product vegan?')]:
            value['manufacturer_source_candidates']=[{'source_id':'capture',
                'source_url':'https://example.test/wine','source_text':text,
                'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),
                'association_status':'catalogue_match'}]
            assessment=policy.base_assessment(value,complete=True)
            assessment['source_applicability']=[source_check(value)]
            assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
                policy.reference('/manufacturer_source_candidates/0/source_text',question),
                policy.reference('/manufacturer_source_candidates/0/source_text','No animal-derived materials are used.')]}]
            for stage in ('model','audit','export'):
                result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                    requested_status='vegan',requested_reason='manufacturer')
                self.assertEqual(result['vegan_status'],'unknown',stage)
                assessment=result['assessment']

    def test_website_content_excerpt_uses_the_retained_capture(self):
        value=context('Example Wine','Grapes',package_type='Bottle')
        value['manufacturer_vegan_evidence']=[{'source_kind':'manufacturer_website',
            'content':'vegan','source_text':'Example Wine. The packaging is vegan.'}]
        ref=policy.reference('/manufacturer_vegan_evidence/0/content','vegan')
        self.assertFalse(policy.manufacturer_reference_claim(value,ref))
        value['manufacturer_vegan_evidence'][0]['source_text']='Example Wine. This product is vegan.'
        self.assertTrue(policy.manufacturer_reference_claim(value,ref))

    def test_packaging_vegan_faq_cannot_assure_wine(self):
        value=context('Example Wine','Grapes',package_type='Bottle')
        for question,expected in [('Is the packaging vegan?','unknown'),
                                  ('Is the bottle vegan?','unknown'),
                                  ('Is the recipe vegan?','unknown'),
                                  ('Is this product vegan?','vegan'),
                                  ('Is this product, including its packaging, vegan?','vegan')]:
            answer='No animal-derived materials or processes are used.'
            text='Example Wine. '+question+' '+answer
            value['manufacturer_source_candidates']=[{'source_id':'capture',
                'source_url':'https://example.test/wine','source_text':text,
                'source_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),
                'association_status':'catalogue_match'}]
            assessment=policy.base_assessment(value,complete=True)
            assessment['source_applicability']=[source_check(value)]
            assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
                policy.reference('/manufacturer_source_candidates/0/source_text',question),
                policy.reference('/manufacturer_source_candidates/0/source_text',answer)]}]
            for stage in ('model','audit','export'):
                result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                    requested_status='vegan',requested_reason='manufacturer')
                self.assertEqual(result['vegan_status'],expected,(question,stage))
                assessment=result['assessment']

    def test_faq_topic_scope_is_preserved(self):
        for question in ['Is the packaging vegan?', 'Is the wrapper vegan?',
                         'Is the recipe vegan?', 'Is the coating vegan?',
                         'Are the ingredients vegan?']:
            self.assertFalse(policy.whole_product_vegan_question(question))
        for question in ['Is this product vegan?', 'Are all your products vegan?',
                         'Is this complete product, including all its packaging, vegan?']:
            self.assertTrue(policy.whole_product_vegan_question(question))

    def test_short_manufacturer_quote_cannot_discard_component_subject(self):
        for claim in ['The packaging is recyclable, vegan.', 'The packaging is vegan.',
                      'Vegan packaging is recyclable.', 'The bottle is vegan.']:
            for quote in [claim, 'vegan' if 'vegan' in claim else 'Vegan']:
                with self.subTest(claim=claim,quote=quote):
                    value=context('Example Wine','Grapes',features=claim,package_type='Bottle')
                    ref=policy.reference('/product/features',quote)
                    self.assertFalse(policy.manufacturer_reference_claim(value,ref))
                    assessment=policy.base_assessment(value,complete=True)
                    assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[ref]}]
                    for stage in ('model','audit','export'):
                        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                            requested_status='vegan',requested_reason='manufacturer')
                        self.assertEqual(result['vegan_status'],'unknown',stage)
                        assessment=result['assessment']

    def test_short_manufacturer_quote_preserves_its_qualification(self):
        for claim in ['Probably vegan.', 'Not vegan.', 'Another product is vegan.',
                      'Our vegan recipe is delicious.', 'Vegan formula is gentle.']:
            value=context('Example Wine','Grapes',features=claim)
            self.assertFalse(policy.manufacturer_reference_claim(value,
                policy.reference('/product/features','vegan' if 'vegan' in claim else 'Vegan')),claim)

    def test_short_manufacturer_quote_can_use_its_actual_whole_product_sentence(self):
        for claim in ['This product is vegan.', 'The packaging is recyclable. This product is vegan.',
                      'Ingredients: Rice\nVegan and gluten free, source of protein\nNutrition: High protein']:
            value=context('Example Wine','Grapes',features=claim)
            quote='vegan' if 'vegan' in claim else 'Vegan'
            self.assertTrue(policy.manufacturer_reference_claim(value,
                policy.reference('/product/features',quote)),claim)

    def test_recipe_introduction_keeps_an_independent_certified_product_assertion(self):
        for claim in ['Made with our oat-based recipe, this flavour is 100% vegan certified and indulgent.',
                      'Made using a new recipe, this complete product is vegan.']:
            value=context('Example Ice Cream','Oats, Sugar',detailed_description=claim)
            ref=policy.reference('/product/detailed_description','vegan')
            self.assertTrue(policy.manufacturer_reference_claim(value,ref),claim)
            value['product']['detailed_description']=claim.rstrip('.')+', except its packaging.'
            self.assertFalse(policy.manufacturer_reference_claim(value,ref))

    def test_manufacturer_website_quote_keeps_visible_text_and_source_scope(self):
        value=context('Example Wine','Grapes',package_type='Bottle')
        for claim,expected in [('L20: **This product is vegan.**',True),
                               ('L20: **The packaging is vegan.**',False)]:
            value['manufacturer_source_candidates']=[{'source_text':claim}]
            ref=policy.reference('/manufacturer_source_candidates/0/source_text','vegan')
            self.assertEqual(policy.manufacturer_reference_claim(value,ref),expected)

    def test_mixed_feature_fragment_cannot_turn_packaging_claim_into_vegan_wine(self):
        for field in ('features','dietary_information'):
            value=context('Example Wine','Grapes',package_type='Bottle',
                          **{field:'The packaging is recyclable, vegan.'})
            self.assertFalse(policy.direct_claim(value['product'][field],'/product/'+field))
            result=classifier.classify_by_rules(value)
            self.assertTrue(result is None or result.vegan_status!='vegan')
            assessment=policy.base_assessment(value,complete=True)
            assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
                policy.reference('/product/'+field,value['product'][field])]}]
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                requested_status='vegan',requested_reason='manufacturer')
            self.assertEqual(result['vegan_status'],'unknown')

    def test_mixed_product_features_preserve_semantic_manufacturer_precedence(self):
        text = 'Recyclable Paper Wrapper, Vegan, 98% Natural origin, 98% biodegradable soap formula, Dermatologically tested'
        self.assertFalse(policy.direct_claim(text, '/product/features'))
        for path in ('/product/features', '/manufacturer_vegan_evidence/0/content'):
            with self.subTest(path=path):
                value = context('Vegan Example Bar Soap', None)
                value['product']['features'] = text
                value['manufacturer_vegan_evidence'] = [{'field_title': 'features', 'content': text}]
                self.assertIsNone(classifier.classify_by_rules(value))
                ref = policy.reference(path, text)
                self.assertTrue(policy.manufacturer_reference_claim(value, ref))
                assessment = policy.base_assessment(value, complete=True)
                assessment['positive_support'] = [
                    {'reason': 'manufacturer', 'evidence_refs': [ref]},
                    {'reason': 'name', 'evidence_refs': [policy.reference('/product/name', value['product']['name'])]}]
                for stage in ('model acceptance', 'audit write', 'export'):
                    result = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                                             requested_status='vegan', requested_reason='manufacturer')
                    self.assertEqual((result['vegan_status'], result['vegan_reason']), ('vegan', 'manufacturer'), stage)
                    assessment = result['assessment']
                value['product']['ingredients'] = 'Gelatine'
                result = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                                         requested_status='vegan', requested_reason='manufacturer')
                self.assertEqual(result['vegan_status'], 'nonvegan')

    def test_mixed_feature_attributes_preserve_component_and_narrative_scope(self):
        for text in ('Packaging: Recyclable Paper Wrapper, Vegan',
                     'Recyclable Paper Wrapper, Vegan soap formula, Dermatologically tested',
                     'Recyclable Vegan Paper Wrapper, Dermatologically tested',
                     'Vegan, recipe book', 'Vegan, ingredients and components',
                     'Recyclable Paper Wrapper, Vegan, packaging is not covered',
                     'Recyclable Paper Wrapper, Vegan, except some variants'):
            with self.subTest(text=text):
                value = context('Example Bar Soap', None, features=text)
                ref = policy.reference('/product/features', text)
                self.assertFalse(policy.mixed_product_feature_attribute(text))
                self.assertFalse(policy.manufacturer_reference_claim(value, ref))
        text = 'Recyclable Paper Wrapper, Vegan, 98% Natural origin, 98% biodegradable soap formula, Dermatologically tested'
        value = context('Example Bar Soap', None, specification=text)
        self.assertFalse(policy.manufacturer_reference_claim(value, policy.reference('/product/specification', text)))

    def test_only_complete_attribute_lists_take_the_deterministic_shortcut(self):
        for claim in ['Vegan, gluten free','Suitable for vegans, dairy free',
                      'Vegan; gluten free; source of protein']:
            self.assertTrue(policy.direct_claim(claim,'/product/features'),claim)
        for claim in ['Vegan, recipe book','Packaging: recyclable, vegan',
                      'Vegan, factory also makes other products','Vegan, manufacturer contact details']:
            self.assertFalse(policy.direct_claim(claim,'/product/features'),claim)
        # The coordinated statement is interpreted with a full assessment;
        # recognising its grammar does not create another rules-only shortcut.
        self.assertFalse(policy.direct_claim('Vegan and gluten free, source of protein','/product/dietary_information'))
        self.assertTrue(policy.manufacturer_claim_quote('Vegan and gluten free, source of protein'))

    def test_coordinated_dietary_attributes_preserve_manufacturer_precedence(self):
        for claim in ['Vegan and gluten free, source of protein',
                      'Gluten-free & vegan', 'Vegan, dairy free and source of fibre']:
            for path in ('/product/dietary_information','/manufacturer_vegan_evidence/0/content'):
                with self.subTest(claim=claim,path=path):
                    value=context('Example Vegan Patties','Rice, E471',
                                  brand='The Vegan Patty Lady',dietary_information=claim,
                                  package_type='Cardboard Box')
                    value['manufacturer_vegan_evidence']=[{'field_title':'dietaryInformation','content':claim}]
                    assessment=policy.base_assessment(value,complete=True)
                    assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[policy.reference(path,claim)]},
                        {'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
                    for stage in ('model acceptance','audit write','export'):
                        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                            requested_status='vegan',requested_reason='manufacturer')
                        self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','manufacturer'),stage)
                        assessment=result['assessment']

    def test_coordinated_claim_grammar_rejects_topics_restrictions_and_qualifiers(self):
        for claim in ['Vegan and gluten free ingredients','Vegan and gluten free formula',
                      'Vegan and gluten free recipe','Vegan and gluten free cookbook',
                      'Probably vegan and gluten free','Not vegan and gluten free',
                      'Vegan and gluten free, except packaging',
                      'Another product is vegan and gluten free',
                      'The Vegan Patty Lady and gluten free']:
            with self.subTest(claim=claim):
                self.assertFalse(policy.manufacturer_claim_quote(claim,brand='The Vegan Patty Lady'))
                value=context('Example Patties','Rice, E471',dietary_information=claim,package_type='Box')
                assessment=policy.base_assessment(value,complete=True)
                assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
                    policy.reference('/product/dietary_information',claim)]}]
                result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                    requested_status='vegan',requested_reason='manufacturer')
                self.assertNotEqual(result['vegan_status'],'vegan')

    def test_coordinated_website_claim_still_requires_current_applicability(self):
        value=context('Example Patties','Rice, E471')
        claim='Vegan and gluten free, source of protein'
        value['manufacturer_source_candidates']=[{'source_id':'capture','source_url':'https://example.test/patties',
            'source_text':claim,'association_status':'catalogue_match'}]
        row=value['manufacturer_source_candidates'][0]
        row['source_sha256']=__import__('hashlib').sha256(claim.encode()).hexdigest()
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
            policy.reference('/manufacturer_source_candidates/0/source_text',claim)]}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_status'],'unknown')
        assessment['source_applicability']=[source_check(value)]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','manufacturer'))
        row['association_status']='wrong_product'
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')['vegan_status'],'unknown')

    def test_unused_recipe_claim_does_not_restrict_an_independent_name(self):
        recipe='Made to a vegan recipe produced in a factory that also handles animal products.'
        value=context('Example Vegan Bird Food','Maize, Rapeseed Oil',
                      features='Paper-based packaging',allergens=recipe,package_type='Bag')
        value['manufacturer_vegan_evidence']=[{'field_title':'allergens','content':recipe}]
        name=policy.reference('/product/name',value['product']['name'])
        narrower=policy.reference('/manufacturer_vegan_evidence/0/content',recipe)
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'name','evidence_refs':[name]}]
        issue={'code':'claim_scope_mismatch','target':'unused recipe-only statement',
               'rule_id':'whole-product-claim-scope-v1','evidence_refs':[narrower],
               'resolution_refs':[],'blocks_claim':True}
        assessment['material_issues']=[issue]
        blocked=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                                requested_status='vegan',requested_reason='name')
        self.assertEqual(blocked['vegan_status'],'unknown')
        self.assertTrue(blocked['positive_block'])
        assessment['material_issues']=[{**issue,'resolution_refs':[narrower,name]}]
        for stage in ('model acceptance','audit write','export'):
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                                   requested_status='vegan',requested_reason='name')
            self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','name'),stage)
            assessment=result['assessment']

    def test_actual_scope_restriction_and_animal_content_still_defeat_name(self):
        value=context('Example Vegan Bird Food','Maize, Rapeseed Oil',
                      features='The vegan claim applies only to the food; the bag is not covered.',
                      package_type='Bag')
        name=policy.reference('/product/name',value['product']['name'])
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'name','evidence_refs':[name]}]
        assessment['material_issues']=[{'code':'claim_scope_mismatch','target':'whole-product name',
            'rule_id':'whole-product-claim-scope-v1',
            'evidence_refs':[policy.reference('/product/features',value['product']['features'])],
            'resolution_refs':[],'blocks_claim':True}]
        blocked=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                                requested_status='vegan',requested_reason='name')
        self.assertEqual(blocked['vegan_status'],'unknown')
        self.assertTrue(blocked['positive_block'])
        value['product']['ingredients']='Maize, Gelatine'
        value['product']['official_vegan']=1
        assessment['composition']['evidence_refs']=[
            policy.reference('/product/ingredients',value['product']['ingredients'])]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                               requested_status='vegan',requested_reason='name')
        self.assertEqual(result['vegan_status'],'nonvegan')
        self.assertTrue(result['tag_animal_conflict'])

    def test_unfamiliar_product_information_claims_receive_semantic_assessment(self):
        for field in ('dietary_information','features','detailed_description','further_description',
                      'manufacturer_marketing','brand_marketing','other_information','specification','vinification_details'):
            with self.subTest(field=field):
                value=context('Example Shampoo','Water, Glycerol',
                              **{field:'Example Shampoo is vegan.'})
                self.assertIsNone(classifier.classify_by_rules(value))
        value=context('Example Shampoo','Water, Glycerol')
        value['manufacturer_vegan_evidence']=[{
            'field_title':'Product Information','content':'Example Shampoo is suitable for vegans.'}]
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_claim_candidate_routing_preserves_definite_animal_and_identity_blocks(self):
        value=context('Example Sweets','Sugar, Gelatine',official_vegan=1,
                      detailed_description='Example Sweets are vegan.')
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')
        value=context("Vegan Cow's Milk",None,
                      detailed_description='This product is vegan.',
                      other_information="Full Product Name: Cow's Milk.")
        result=classifier.classify_by_rules(value)
        self.assertIsNotNone(result)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertTrue(result.evidence['positive_block'])

    def test_missing_source_facts_without_a_claim_still_finish_as_unknown(self):
        value=context('Example Shampoo','Water, Glycerol',detailed_description='Lightly fragranced shampoo.')
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertFalse(result.evidence['positive_block'])
        value['product']['detailed_description']='Another variant is suitable for vegans.'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'unknown')
        value['product']['detailed_description']='Another variant is suitable for vegans. This shampoo is vegan too.'
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_brand_only_producer_description_cannot_establish_suitability(self):
        claim='Vegan Foods Shampoo is lightly fragranced.'
        value=context('Vegan Foods Shampoo','Water, Glycerol',
                      brand='Vegan Foods',detailed_description=claim)
        for quote in [claim,'Vegan Foods Shampoo','Vegan Foods']:
            with self.subTest(quote=quote):
                ref=policy.reference('/product/detailed_description',quote)
                self.assertFalse(policy.manufacturer_reference_claim(value,ref))
                assessment=policy.base_assessment(value,complete=True)
                assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[ref]}]
                raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='manufacturer',
                    confidence='certain',summary='Brand-led producer description',source='codex',
                    evidence={'assessment':assessment})
                result=classifier.apply_policy_gate(value,raw)
                self.assertEqual(result.vegan_status,'unknown')
                self.assertIsNone(result.vegan_reason)

    def test_independent_product_claim_survives_a_vegan_brand_name(self):
        for claim in ['Vegan Foods Shampoo is vegan.',
                      'Vegan Foods Shampoo. Suitable for vegans.',
                      'Vegan Foods Vegan Shampoo is lightly fragranced.',
                      'Vegan Foods Shampoo. Vegan Society Approved.']:
            with self.subTest(claim=claim):
                value=context('Vegan Foods Shampoo','Water, Glycerol',
                              brand='Vegan Foods',detailed_description=claim)
                ref=policy.reference('/product/detailed_description',claim)
                self.assertTrue(policy.manufacturer_reference_claim(value,ref))
                assessment=policy.base_assessment(value,complete=True)
                assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[ref]}]
                raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='manufacturer',
                    confidence='certain',summary='Independent product assertion',source='codex',
                    evidence={'assessment':assessment})
                self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_reason,'manufacturer')

    def test_brand_removal_preserves_scope_and_definite_animal_overrides(self):
        for claim in ['Vegan Foods Shampoo is vegan, except its packaging.',
                      'Vegan Foods Shampoo is not vegan.',
                      'Vegan Foods Shampoo has a vegan formula.']:
            value=context('Vegan Foods Shampoo','Water, Glycerol',
                          brand='Vegan Foods',detailed_description=claim)
            self.assertFalse(policy.manufacturer_reference_claim(value,
                policy.reference('/product/detailed_description',claim)),claim)
        value=context('Vegan Foods Vegan Sweets','Sugar, Gelatine',brand='Vegan Foods',
                      detailed_description='Vegan Foods Vegan Sweets are colourful.',official_vegan=1)
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
            policy.reference('/product/detailed_description',value['product']['detailed_description'])]}]
        raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='manufacturer',
            confidence='certain',summary='Producer assertion conflicts with actual ingredients',source='codex',
            evidence={'assessment':assessment})
        self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_status,'nonvegan')

    def test_both_classification_stages_receive_the_exact_producer_assertion_policy(self):
        value=context('Example Vegan Product','Rice')
        primary=classifier.build_codex_prompt([value])
        arbitration=classifier.build_arbitration_prompt([value])
        self.assertIn(policy.PRODUCER_ASSERTION_POLICY,primary)
        self.assertIn(policy.PRODUCER_ASSERTION_POLICY,arbitration)
        self.assertIn('a quoted title, a serving suggestion',policy.PRODUCER_ASSERTION_POLICY)

    def test_catalogue_provenance_is_explicit_without_inventing_product_market(self):
        value=context('Example Wine','Grapes',country_of_origin='United States')
        value['manufacturer_source_candidates']=[{'source_url':'https://example.test/uk',
            'source_text':'Our UK product is vegan.','market_scope':'UK'}]
        original=copy.deepcopy(value)
        for prompt in (classifier.build_codex_prompt([value]),classifier.build_arbitration_prompt([value])):
            payload=json.loads(prompt.split('Product JSON:\n',1)[1])
            self.assertEqual(payload['catalogue_provenance'],{'retailer':'Ocado','market':'UK'})
            self.assertEqual(payload['products'][0]['product'],original['product'])
            self.assertIn(policy.CATALOGUE_CONTEXT_POLICY,prompt)
            self.assertIn('different-market restriction still needs applicable resolution',prompt)
        self.assertEqual(value,original)
        with self.assertRaises(ValueError):
            policy.resolve_reference(value,{'path':'/product/market','quote':'UK'})
        with self.assertRaises(ValueError):
            policy.resolve_reference(value,{'path':'/catalogue_provenance/market','quote':'UK'})

    def test_declarative_producer_description_preserves_manufacturer_precedence(self):
        for claim in [
            'Skittles Fruits Vegan Chewy Sweets are a colourful mix of chewy sweets with a crisp sugar shell.',
            'Soft, creamy, and delicious, our Boursin Garlic & Herbs vegan alternative is a must-try for anyone seeking great taste.',
        ]:
            with self.subTest(claim=claim):
                value=context('Example Vegan Product','Rice',detailed_description=claim)
                assessment=policy.base_assessment(value,complete=True)
                assessment['positive_support']=[{'reason':'name','evidence_refs':[
                    policy.reference('/product/name',value['product']['name']),
                    policy.reference('/product/detailed_description',claim)]}]
                raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='name',
                    confidence='certain',summary='Cited affirmative product assertions',source='codex',evidence={'assessment':assessment})
                result=classifier.apply_policy_gate(value,raw)
                self.assertEqual((result.vegan_status,result.vegan_reason),('vegan','manufacturer'))

    def test_short_self_description_cites_a_predicate_in_the_same_retained_sentence(self):
        quote='Freja natural Norwegian vegan broth'
        value=context('Freja Classic Vegan Sipping Broth','Vegetables',
                      detailed_description=quote+' is packed full of flavour and essential nutrients.')
        ref=policy.reference('/product/detailed_description',quote)
        self.assertTrue(policy.manufacturer_reference_claim(value,ref))
        for text in [quote+'. A different product is vegan.',
                     quote+' is packed full of flavour, except that its packaging is not assessed.',
                     'Our vegan broth recipe is included.', 'Our vegan cookbook is available.']:
            with self.subTest(text=text):
                value['product']['detailed_description']=text
                claim=quote if quote in text else text
                self.assertFalse(policy.manufacturer_reference_claim(value,policy.reference('/product/detailed_description',claim)))

    def test_cited_producer_self_description_corrects_a_lower_priority_name_reason(self):
        claim='We created a vegan version of our shake. This is the perfect vegan weight loss shake.'
        value=context('Example Vegan Shake','Rice',detailed_description=claim)
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[
            {'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]},
            {'reason':'name','evidence_refs':[policy.reference('/product/detailed_description',claim)]}]
        raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='name',
            confidence='certain',summary='Cited name and producer information',source='codex',evidence={'assessment':assessment})
        result=classifier.apply_policy_gate(value,raw)
        self.assertEqual((result.vegan_status,result.vegan_reason),('vegan','manufacturer'))
        value['product']['ingredients']='Rice, Gelatine'
        self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_status,'nonvegan')

    def test_quantified_nutrition_does_not_erase_manufacturer_precedence(self):
        assertion = ('this is the perfect vegan weight loss shake for a meal-replacement '
                     'that can also be used post-workout or as a delicious low-calorie snack.')
        full = ('High in protein with at least 24g of protein per recommended serving '
                'and less than 210 calories per serving, ' + assertion)
        value = context('Example Vegan Shake', 'Rice', detailed_description=full)
        ref = policy.reference('/product/detailed_description', assertion)
        self.assertTrue(policy.manufacturer_reference_claim(value, ref))
        assessment = policy.base_assessment(value, complete=True)
        assessment['positive_support'] = [
            {'reason': 'manufacturer', 'evidence_refs': [ref]},
            {'reason': 'name', 'evidence_refs': [policy.reference('/product/name', value['product']['name'])]}]
        for stage in ('model', 'audit', 'export'):
            outcome = policy.evaluate(value, **classifier.policy_facts(value), assessment=assessment,
                                      requested_status='vegan', requested_reason='manufacturer')
            self.assertEqual((outcome['vegan_status'], outcome['vegan_reason']),
                             ('vegan', 'manufacturer'), stage)
            assessment = outcome['assessment']

    def test_nutrition_figures_do_not_supply_or_broaden_a_vegan_assertion(self):
        for text in [
            'This shake is vegan per serving.',
            'Serving suggestion: This vegan shake provides 24g of protein per serving.',
            'This vegan formula provides 24g of protein per serving.',
            'This vegan shake provides 24g of protein per serving. Packaging is not assessed.',
            'Another product is vegan and provides 24g of protein per serving.',
            '24g of protein per serving.',
        ]:
            with self.subTest(text=text):
                value = context('Example Vegan Shake', 'Rice', detailed_description=text)
                self.assertFalse(policy.manufacturer_reference_claim(value,
                    policy.reference('/product/detailed_description', text)))

    def test_protective_atmosphere_does_not_invent_a_manufactured_component(self):
        value=context('Colombian Coffee Beans','Coffee',
                      other_information='Roasted arabica coffee beans. Packaged in a protective atmosphere for freshness.')
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':[
            policy.reference('/product/name',value['product']['name']),
            policy.reference('/product/ingredients','Coffee')]}]
        raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='ingredients',
            confidence='certain',summary='Established ordinary food with no evidenced component concern',
            source='codex',evidence={'assessment':assessment})
        self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_status,'vegan')
        value['product']['package_type']='Bag'
        result=classifier.apply_policy_gate(value,raw)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertIn('composition_incomplete',{issue['code'] for issue in result.evidence['assessment']['material_issues']})

    def test_manufacturer_self_description_keeps_precedence_over_the_name(self):
        for claim in ['This is the perfect vegan weight loss shake.','This vegan shower gel.',
                      'Vegan, High Fibre, Source of Protein.',
                      'created a Vegan version of our best-selling weight loss shake',
                      '*Pawtato Chews. Made without meat, fish, dairy, eggs, wheat, corn or soya. Ideal low fat alternative to rawhide. *Vegetarian Society and Vegan Society Approved.']:
            value=context('Example Vegan Product','Rice',detailed_description=claim)
            assessment=policy.base_assessment(value,complete=True)
            assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[policy.reference('/product/detailed_description',claim)]},
                {'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                requested_status='vegan',requested_reason='manufacturer')
            self.assertEqual(result['vegan_reason'],'manufacturer',claim)
        for claim in ['This is not the perfect vegan shake.','This is the perfect vegan lining.',
                      'This vegan formula.','This is the perfect vegan cookbook.',
                      'Another product is the perfect vegan shake.','Is this the perfect vegan shake?',
                      'created a Vegan version of our other shake',
                      'created a Vegan version of our shake. Packaging is not assessed.',
                      'created a Vegan version of our shake. Selected variants only.',
                      'Made without milk. Vegan ingredients.',
                      'Vegan Society Approved ingredients.',
                      'Vegan Society Approved recipe.',
                      'Vegan Society Approved. Except the packaging.',
                      'Vegan Society Approved. Another product contains animal ingredients.',
                      'Vegan Society Approved. Non-vegan components.']:
            self.assertFalse(policy.manufacturer_claim_quote(claim),claim)

    def test_cited_certification_is_manufacturer_evidence_not_an_ocado_tag(self):
        for claim in ['Vegan Society Approved.','Kosher, Halal & Vegan certified.']:
            value=context('Example Vegan Treat','Rice',features=claim)
            assessment=policy.base_assessment(value,complete=True)
            ref=policy.reference('/product/features',claim)
            assessment['positive_support']=[{'reason':'tagged','evidence_refs':[ref]},
                {'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                requested_status='vegan',requested_reason='tagged')
            self.assertEqual(result['vegan_reason'],'manufacturer')
            value['product']['official_vegan']=1
            self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                requested_status='vegan',requested_reason='tagged')['vegan_reason'],'tagged')
            value['product']['ingredients']='Rice, Gelatine'
            self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                requested_status='vegan',requested_reason='tagged')['vegan_status'],'nonvegan')

    def test_planting_seed_identity_requires_treatment_assessment(self):
        value=context('Wildflower Seeds',None,preparation_and_usage='Sow outdoors in prepared soil.')
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':[policy.reference('/product/name','Wildflower Seeds')]}]
        raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='ingredients',confidence='certain',
            summary='Seed origin alone',source='codex',evidence={'assessment':assessment})
        self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_status,'unknown')
        risk=next(issue for issue in policy.material_risks(value) if issue['rule_id']=='planting-seed-treatment-v1')
        for text,expected in [('Native-grown organic seeds',False),('Untreated seeds',False),
                              ('Untreated and uncoated seeds',True),('Not all seed treatments are animal-free.',False),
                              ('All seed treatments are animal-free.',True)]:
            with self.subTest(text=text):
                a={'material_issues':[{**risk,'resolution_refs':[policy.reference('/product/features',text)]}]}
                self.assertEqual(policy.resolved_risk(risk,a),expected)
        for fields in ({'official_vegan':1},{'dietary_information':'Suitable for vegans.'}):
            self.assertEqual(classifier.classify_by_rules(context('Wildflower Seeds',None,
                preparation_and_usage='Sow outdoors.',**fields)).vegan_status,'vegan')
        value['product']['features']='Untreated and uncoated seeds.'
        assessment['material_issues']=[{**risk,'resolution_refs':[policy.reference('/product/features',value['product']['features'])]}]
        self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_status,'vegan')
        value['product']['ingredients']='Seeds, Gelatine'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')

    def test_seed_treatment_mechanism_does_not_invent_a_food_or_tool_risk(self):
        for name,preparation in [('Pumpkin Seeds','Sprinkle over salad.'),('Roasted Edible Seeds','Sow if desired.'),
                                 ('Seed Sowing Tool','Sow outdoors using this tool.'),('Carrots','')]:
            value=context(name,None,preparation_and_usage=preparation)
            self.assertNotIn('planting-seed-treatment-v1',{i['rule_id'] for i in policy.material_risks(value)},name)

    def test_explicit_meat_alternatives_preserve_adjacent_animal_ingredients(self):
        for ingredients in ['No Beef Protein Pieces (Mycoprotein, Rapeseed Protein), Carrots',
                            'Mycoprotein based alternative to beef, Carrots',
                            'Non-Dairy Ice Cream (Water, Coconut Fat)']:
            with self.subTest(ingredients=ingredients):
                value=context('Example Vegan Product',ingredients,dietary_information='Suitable for vegans.')
                self.assertFalse(classifier.policy_facts(value)['animal_matches'])
                self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'manufacturer')
                for animal in ['Beef','Milk','Gelatine']:
                    value['product']['ingredients']=ingredients+', '+animal
                    self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')

    def test_egg_alternative_description_preserves_separate_animal_declarations(self):
        description='Our creamy mayo is made with citrus fibre, a plant-based alternative to eggs that is usually thrown away. Tastes great? Good. Fights food waste too? Gooder.'
        value=context('Rubies in the Rubble Squeezy Classic Vegan Mayonnaise',
            'Sunflower Oil, Water, Spirit Vinegar, Sugar, Modified Starch, Salt, Lemon Juice Concentrate, Citrus Fibre, Natural Flavouring',
            detailed_description=description,dietary_information='Suitable for Vegans',package_type='Plastic Bottle')
        facts=classifier.policy_facts(value)
        self.assertFalse(facts['animal_declarations'])
        self.assertFalse(facts['qualified_animal_declarations'])
        self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'manufacturer')
        for animal in ['Eggs','Milk','Gelatine']:
            with self.subTest(animal=animal):
                changed=copy.deepcopy(value)
                changed['product']['ingredients']+=', '+animal
                changed['product']['official_vegan']=1
                decision=classifier.classify_by_rules(changed)
                self.assertEqual(decision.vegan_status,'nonvegan')
                self.assertTrue(decision.evidence['tag_animal_conflict'])
        value['product']['detailed_description']+=' Contains Gelatine.'
        decision=classifier.classify_by_rules(value)
        self.assertTrue(decision is None or decision.vegan_status!='vegan')

    def test_egg_alternative_token_cannot_erase_adjacent_or_nested_animal_content(self):
        for text in ['A plant-based alternative to eggs, Milk',
                     'Substitute for egg (Gelatine)',
                     'Alternative to eggs with egg whites',
                     'Alternative to egg, Contains Eggs']:
            with self.subTest(text=text):
                value=context('Example Vegan Product',text,official_vegan=1)
                self.assertTrue(classifier.policy_facts(value)['animal_matches'])
                self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')
        value=context('Example Product',None,
            detailed_description='Made with an alternative to eggs.')
        decision=classifier.classify_by_rules(value)
        self.assertTrue(decision is None or decision.vegan_status!='vegan')

    def test_declaration_boundaries_do_not_create_animal_content(self):
        for key,text in [('features','Made without meat, fish, dairy, eggs.'),
                         ('features','Made from soy, The Example Southern Fried Chicken is our vegan alternative.'),
                         ('features','This vegan ice cream contains Chocolate Non-Dairy Ice Cream with Brownie Pieces.'),
                         ('allergens','Contains Barley, May Contain Eggs, May Contain Milk, Contains Wheat Not suitable for those with a Milk or Egg allergy because these allergens are present in the environment.')]:
            with self.subTest(text=text):
                value=context('Example Vegan Product','Rice',dietary_information='Suitable for vegans.',**{key:text})
                facts=classifier.policy_facts(value)
                self.assertFalse(facts['animal_declarations'])
                self.assertFalse(facts['qualified_animal_declarations'])
                self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'manufacturer')
                value['product'][key]+=' Contains Gelatine.'
                decision=classifier.classify_by_rules(value)
                self.assertTrue(decision is None or decision.vegan_status!='vegan')

    def test_volume_labelled_opaque_title_does_not_validate_solid_meat_context(self):
        value=context('Example Gers','Pork (89%), Ham (2%)',pack_size='75cl',official_vegan=1,
                      other_information='Full Product Name: Chopped Pork and Ham Storage: Best Before: See Base of Can')
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertTrue(result.evidence['positive_block'])
        self.assertFalse(result.evidence['tag_animal_conflict'])
        for name,full in [('Chopped Pork and Ham','Chopped Pork and Ham'),('Example Soup','Pork Soup')]:
            value['product']['name']=name;value['product']['other_information']='Full Product Name: '+full+' Storage: Refrigerate'
            self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')

    def test_liquid_processing_risks_block_agreed_unsupported_positives(self):
        for name,ingredients in [('Cider Vinegar',None),('Cider','Apple Juice'),('Lager','Water, Malt, Hops'),
                                 ('Apple Juice','Apple Juice'),('Maple Syrup','Maple Syrup')]:
            value=context(name,ingredients)
            assessment=policy.base_assessment(value,complete=True)
            assessment['positive_support']=[{'reason':'ingredients','evidence_refs':[policy.reference('/product/name',name)]}]
            raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='ingredients',
                confidence='certain',summary='Unsupported ordinary-food inference',source='codex',evidence={'assessment':assessment})
            result=classifier.apply_policy_gate(value,raw)
            self.assertEqual(result.vegan_status,'unknown',name)
            self.assertIn('processing_unresolved',{issue['code'] for issue in result.evidence['assessment']['material_issues']})

    def test_liquid_risk_is_not_a_negative_verdict_and_claims_can_resolve_it(self):
        for name in ['Cider Vinegar','Cider','Lager','Apple Juice','Maple Syrup']:
            for fields,reason in [({'official_vegan':1},'tagged'),({'dietary_information':'Suitable for vegans.'},'manufacturer')]:
                result=classifier.classify_by_rules(context(name,None,**fields))
                self.assertEqual((result.vegan_status,result.vegan_reason),('vegan',reason),name)
            value=context('Vegan '+name,None)
            self.assertIsNone(classifier.classify_by_rules(value))
            assessment=policy.base_assessment(value,complete=True)
            assessment['positive_support']=[{'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
            raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='name',confidence='certain',
                summary='Applicable whole-product assertion',source='codex',evidence={'assessment':assessment})
            self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_reason,'name')

    def test_clarification_resolution_must_cover_relevant_production_not_just_final_filtering(self):
        value=context('Cider Vinegar',None)
        risk=next(issue for issue in policy.material_risks(value) if issue['rule_id']=='liquid-clarification-v1')
        for text,expected in [('Unfiltered cider vinegar',False),('Distilled vinegar',False),
                              ('All stages of bottle production are animal-free.',False),
                              ('All production stages are animal-free.',False),
                              ('All production stages including ingredient and precursor production are animal-free.',True),
                              ('Not all production stages including ingredient and precursor production are animal-free.',False),
                              ('Animal-free from whole fruit to finished product.',True)]:
            assessment={'material_issues':[{**risk,'resolution_refs':[policy.reference('/product/features',text)]}]}
            self.assertEqual(policy.resolved_risk(risk,assessment),expected,text)

    def test_specific_liquid_taxonomy_catches_names_without_the_beverage_word(self):
        value=context('Example Orchard Blend',None);value['categories']=['beer-wine-spirits/ciders']
        self.assertIn('liquid-clarification-v1',{issue['rule_id'] for issue in policy.material_risks(value)})
        value['categories']=['beer-wine-spirits']
        self.assertNotIn('liquid-clarification-v1',{issue['rule_id'] for issue in policy.material_risks(value)})

    def test_hypothetical_qualified_names_are_not_guaranteed_positive_labels(self):
        for name in ['Never Vegan Wine','Almost Vegan Wine','Probably Vegan Wine','Possibly Vegan Wine','Usually Vegan Wine']:
            value=context(name,None)
            result=classifier.classify_by_rules(value)
            self.assertTrue(result is None or result.vegan_status=='unknown',name)
            self.assertFalse(policy.affirmative_name(name))
            assessment=policy.base_assessment(value,complete=True)
            assessment['positive_support']=[{'reason':'name','evidence_refs':[policy.reference('/product/name',name)]}]
            raw=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='name',confidence='certain',
                summary='Unsupported whole-product assertion',source='codex',evidence={'assessment':assessment})
            self.assertEqual(classifier.apply_policy_gate(value,raw).vegan_status,'unknown')

    def test_each_positive_pass_must_cite_its_own_positive_basis(self):
        value=context('Vegan White Wine',None)
        assessment=policy.base_assessment(value,complete=True)
        decision={'product_id':'1','vegan_status':'vegan','vegan_reason':'name','confidence':'certain',
                  'summary':'A whole-product assertion','evidence':[],'ambiguity_notes':[],'assessment':assessment}
        def invoke(prompt,schema,**kwargs):
            kwargs['validator']({'decisions':[decision]})
            return {'parsed_response':{'decisions':[decision]},'raw_response':'{}','exit_status':0}
        with mock.patch.object(codex_evidence,'run',side_effect=invoke):
            with self.assertRaisesRegex(ValueError,'Each positive pass'):
                classifier.call_codex_batch([value],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')
            assessment['positive_support']=[{'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
            results=classifier.call_codex_batch([value],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')
            self.assertEqual(results[0]['1']['vegan_reason'],'name')

    def test_positive_website_binding_requires_source_evidence_before_acceptance(self):
        value=context('Example Wine','Grapes',brand='Example Winery',package_type='Pouch')
        body='We produce Example Wine. It is vegan.'
        value['manufacturer_source_candidates']=[{'source_id':'test-capture','source_url':'https://example.test/wine',
            'source_text':body,'source_sha256':__import__('hashlib').sha256(body.encode()).hexdigest(),
            'retrieved_at':'2026-10-07T00:00:00+00:00','official_identity':'Example Winery',
            'market_scope':'UK','association_status':'catalogue_match'}]
        source=policy.reference('/manufacturer_source_candidates/0/source_text',body)
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[source]}]
        assessment['source_applicability']=[source_check(value)]
        binding=assessment['source_applicability'][0]
        binding['variant']={'status':'established','evidence_refs':[policy.reference('/product/name','Example Wine')]}
        decision={'product_id':'1','vegan_status':'vegan','vegan_reason':'manufacturer','confidence':'certain',
            'summary':'Named-product assurance','evidence':[],'ambiguity_notes':[],'assessment':assessment}
        def invoke(prompt,schema,**kwargs):
            kwargs['validator']({'decisions':[decision]})
            return {'parsed_response':{'decisions':[decision]},'raw_response':'{}','exit_status':0}
        with mock.patch.object(codex_evidence,'run',side_effect=invoke):
            with self.assertRaisesRegex(ValueError,'variant: a conclusive website binding'):
                classifier.call_codex_batch([value],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')
            binding['variant']['evidence_refs'].append(source)
            rows=classifier.call_codex_batch([value],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')[0]
            result=classifier.apply_policy_gate(value,classifier.decision_to_result(rows['1'],source='codex',
                model='gpt-6-luna',reasoning_effort='high',context_hash_value='test',raw_response='{}',
                parsed_response_json='{}',exit_status=0))
            self.assertEqual((result.vegan_status,result.vegan_reason),('vegan','manufacturer'))
            binding['variant']={'status':'unresolved','evidence_refs':[policy.reference('/product/name','Example Wine')]}
            decision.update(vegan_status='unknown',vegan_reason=None,confidence='uncertain')
            rows=classifier.call_codex_batch([value],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')[0]
            self.assertEqual(rows['1']['vegan_status'],'unknown')

    def test_origin_and_manufacturing_declarations_override_positive_claims(self):
        for text in ['Derived from milk.','Obtained from gelatine.','Isinglass is used during manufacture.',
                     'Animal-derived milk is used during manufacture.','Bone char is used during refining.']:
            for fields in ({},{'official_vegan':1},{'dietary_information':'Suitable for vegans.'}):
                decision=classifier.classify_by_rules(context('Example Product',None,features=text,**fields))
                self.assertEqual(decision.vegan_status,'nonvegan',text)
                self.assertEqual(decision.evidence['tag_animal_conflict'],bool(fields.get('official_vegan')))
        value=context('Example Product',None,official_vegan=1,features='Derived from coconut milk.')
        self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'tagged')
        value['product']['features']='Another variant is derived from milk.'
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_vegan_title_cannot_erase_contrary_full_animal_food_identity(self):
        for fields in ({},{'official_vegan':1},{'dietary_information':'Suitable for vegans.'}):
            value=context("Vegan Cow's Milk",None,other_information="Full Product Name: Cow's Milk.",**fields)
            decision=classifier.classify_by_rules(value)
            self.assertEqual(decision.vegan_status,'unknown')
            self.assertTrue(decision.evidence['positive_block'])
            self.assertIn('identity_conflict',{issue['code'] for issue in decision.evidence['assessment']['material_issues']})
        value=context("Vegan Cow's Milk",None,other_information="Full Product Name: Vegan Cow's Milk.")
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_unsupported_negative_is_rejected_without_promoting_agreed_unknown(self):
        value=context('Example Product',None,official_vegan=1)
        for status in ['nonvegan','unknown']:
            result=classifier.ClassificationResult(product_id='1',vegan_status=status,vegan_reason=None,
                confidence='certain',summary='No supporting contrary evidence',source='codex',
                evidence={'assessment':policy.base_assessment(value,complete=True)})
            self.assertEqual(classifier.apply_policy_gate(value,result).vegan_status,'unknown')

    def test_model_negative_claim_is_a_conflict_but_animal_proof_overrides(self):
        value=context('Example Product',None,official_vegan=1,features='Not vegan.')
        assessment=policy.base_assessment(value,complete=True)
        assessment['material_issues']=[{'code':'explicit_nonvegan','target':'whole_product','rule_id':'explicit-negative',
            'evidence_refs':[policy.reference('/product/features','Not vegan.')],'resolution_refs':[],'blocks_claim':True}]
        outcome=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='nonvegan')
        self.assertEqual(outcome['vegan_status'],'unknown')
        self.assertTrue(outcome['positive_block'])
        value['product']['features']='Isinglass is used during manufacture.'
        assessment['material_issues']=[{'code':'animal_processing','target':'whole_product','rule_id':'animal-input',
            'evidence_refs':[policy.reference('/product/features',value['product']['features'])],'resolution_refs':[],'blocks_claim':True}]
        outcome=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='nonvegan')
        self.assertEqual(outcome['vegan_status'],'nonvegan')
        self.assertTrue(outcome['tag_animal_conflict'])

    def test_punctuated_negation_cannot_be_an_affirmative_name(self):
        for name in ['Not-Vegan Wine','Not: Vegan Wine','Not (Vegan) Wine','Non: Vegan Wine']:
            for fields in ({},{'official_vegan':1},{'dietary_information':'Suitable for vegans.'}):
                decision=classifier.classify_by_rules(context(name,None,**fields))
                self.assertEqual(decision.vegan_status,'unknown',name)
                self.assertTrue(decision.evidence['positive_block'])
        value=context('Not Certified Vegan Wine',None,official_vegan=1)
        self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'tagged')

    def test_formula_name_does_not_assert_whole_product_suitability(self):
        for name in ['Vegan Formula Shampoo','Shampoo Formula Vegan']:
            self.assertFalse(policy.affirmative_name(name))
            self.assertEqual(classifier.classify_by_rules(context(name,None)).vegan_status,'unknown')
            self.assertEqual(classifier.classify_by_rules(context(name,None,official_vegan=1)).vegan_reason,'tagged')
            self.assertEqual(classifier.classify_by_rules(context(name,None,dietary_information='Suitable for vegans.')).vegan_reason,'manufacturer')

    def test_postal_addresses_do_not_establish_supplied_boxes(self):
        for address in ['PO Box 3339','P.O. Box 3339','P O Box 3339']:
            value=context('Carrots',None,other_information='Return to: '+address+', Chester.')
            self.assertFalse(policy.material_risks(value))
            self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'ingredients')
            value['product']['package_type']='Box'
            self.assertEqual(classifier.classify_by_rules(value).vegan_status,'unknown')

    def test_missing_packaging_and_modal_can_do_not_invent_components(self):
        value=context('Carrots',None,detailed_description='These carrots can be steamed or roasted.')
        self.assertFalse(policy.material_risks(value))
        self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'ingredients')
        value['product']['package_type']='Can'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'unknown')
        for description in ['Supplied in a box.','Supplied in boxes.']:
            value=context('Carrots',None,detailed_description=description)
            self.assertEqual(classifier.classify_by_rules(value).vegan_status,'unknown')

    def test_ingredient_only_faq_does_not_confirm_the_complete_product(self):
        value=context('Example Coffee Pods','Coffee')
        value['manufacturer_source_candidates']=[{'source_id':'capture','source_url':'https://example.test/faq',
            'source_text':'Are the ingredients vegan? No animal ingredients are used.',
            'association_status':'catalogue_match'}]
        value['manufacturer_source_candidates'][0]['source_sha256']=__import__('hashlib').sha256(
            value['manufacturer_source_candidates'][0]['source_text'].encode()).hexdigest()
        assessment=policy.base_assessment(value,complete=True)
        assessment['source_applicability']=[source_check(value,scope='product')]
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
            policy.reference('/manufacturer_source_candidates/0/source_text','Are the ingredients vegan?'),
            policy.reference('/manufacturer_source_candidates/0/source_text','No animal ingredients are used.')]}]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')['vegan_status'],'unknown')
        value['manufacturer_source_candidates'][0]['source_text']='Are the products suitable for vegans? No animal ingredients are used.'
        value['manufacturer_source_candidates'][0]['source_sha256']=__import__('hashlib').sha256(
            value['manufacturer_source_candidates'][0]['source_text'].encode()).hexdigest()
        assessment['source_applicability']=[source_check(value,scope='product')]
        assessment['positive_support'][0]['evidence_refs'][0]=policy.reference('/manufacturer_source_candidates/0/source_text','Are the products suitable for vegans?')
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')['vegan_reason'],'manufacturer')

    def test_registry_resolution_survives_repeated_policy_gates(self):
        value=context('Example Coffee Pods','Coffee',features='Complete component composition: aluminium pod and lid, plant-derived adhesive. All components manufactured without animal inputs.')
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':[policy.reference('/product/ingredients','Coffee')]}]
        assessment['material_issues']=[{**issue,'resolution_refs':[policy.reference('/product/features',value['product']['features'])]}
            for issue in policy.material_risks(value)]
        for stage in ('model acceptance','audit write','export'):
            result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                requested_status='vegan',requested_reason='ingredients')
            self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','ingredients'),stage)
            assessment=result['assessment']
            self.assertTrue(all(issue['resolution_refs'] for issue in assessment['material_issues']))
        assessment['material_issues'].append({**policy.material_risks(value)[0],'target':'unresolved concern raised by second reading'})
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='ingredients')['vegan_status'],'unknown')

    def test_full_identity_cannot_add_unassessed_food_components(self):
        for identity in ['Carrots with Butter','Mango containing Gelatine','Carrots in Honey']:
            value=context(identity.split()[0],None,other_information='Full Product Name: '+identity+'.')
            self.assertFalse(classifier.policy_facts(value)['ordinary_identity'])
            decision=classifier.classify_by_rules(value)
            self.assertTrue(decision is None or decision.vegan_status!='vegan')

    def test_model_negative_cannot_turn_mixed_factory_warning_into_intentional_milk(self):
        value=context('Sea Salted Popcorn','Wholegrain Corn, Rapeseed Oil, Sea Salt',official_vegan=1,
            allergens='Contains Milk Made in a factory that handles Milk.')
        ref=policy.reference('/product/allergens',value['product']['allergens'])
        assessment=policy.base_assessment(value,complete=True)
        assessment['material_issues']=[{'code':'evidence_conflict','target':'whole_product','rule_id':'animal-declaration-context-v1',
            'evidence_refs':[ref],'resolution_refs':[ref,policy.reference('/product/name',value['product']['name'])],'blocks_claim':True},
            {'code':'animal_ingredient','target':'whole_product','rule_id':'model-animal-content','evidence_refs':[ref],
                'resolution_refs':[],'blocks_claim':True}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='nonvegan')
        self.assertEqual(result['vegan_status'],'unknown')
        self.assertFalse(result['tag_animal_conflict'])

    def test_proposed_tag_accusation_requires_accepted_animal_evidence(self):
        for warning in ['Contains Milk Made in a factory that handles Milk.',
                        'Contains Soya Produced in a factory that handles Milk. Contains Eggs.']:
            with self.subTest(warning=warning):
                value=context('Example Vegan Ham','Pea Protein, Soya Protein, Salt',
                    official_vegan=1,allergens=warning)
                ref=policy.reference('/product/allergens',warning)
                assessment=policy.base_assessment(value,complete=True)
                assessment['material_issues']=[
                    {'code':'animal_ingredient','target':'whole_product','rule_id':'model-animal-content',
                        'evidence_refs':[ref],'resolution_refs':[],'blocks_claim':True},
                    {'code':'tag_animal_conflict','target':'whole_product','rule_id':'model-tag-conflict',
                        'evidence_refs':[ref],'resolution_refs':[],'blocks_claim':True}]
                for stage in ('model','audit','export'):
                    result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                        requested_status='nonvegan')
                    self.assertEqual(result['vegan_status'],'unknown',stage)
                    self.assertTrue(result['positive_block'],stage)
                    self.assertFalse(result['tag_animal_conflict'],stage)
                    self.assertNotIn('tag_animal_conflict',{i['code'] for i in result['assessment']['material_issues']},stage)
                    self.assertIn('evidence_conflict',{i['code'] for i in result['assessment']['material_issues']},stage)
                    assessment=result['assessment']
                value['product']['ingredients']+=', Milk'
                result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                    requested_status='nonvegan')
                self.assertEqual(result['vegan_status'],'nonvegan')
                self.assertTrue(result['tag_animal_conflict'])

    def test_ordinary_food_does_not_clear_explicit_packaging(self):
        for package in ['Plastic Bag','Carton','Glass Jar','Heat Sealed Tray','Blister']:
            value=context('Carrots',None,package_type=package)
            result=classifier.classify_by_rules(value)
            self.assertEqual(result.vegan_status,'unknown')
            self.assertIn('supplied-components-composition-v1',{
                issue['rule_id'] for issue in result.evidence['assessment']['material_issues']})
            value['product']['dietary_information']='Suitable for vegans.'
            self.assertEqual(classifier.classify_by_rules(value).vegan_reason,'manufacturer')
        self.assertEqual(classifier.classify_by_rules(context('Carrots',None,package_type='Loose')).vegan_reason,'ingredients')

    def test_fruit_flavour_names_do_not_prove_plain_fruit(self):
        examples = [
            context('Example Pineapple & Ginger',None,brand='Example',
                other_information='Full Product Name: Pineapple & Ginger Gin Liqueur. Storage: Cool.'),
            context('Example Mango',None,brand='Example',
                features='Sparkling Water with Alcohol and a Hint of Natural Flavours, Contains Alcohol'),
            context('Example Mango',None,brand='Example',detailed_description='A triple distilled spirit hard seltzer.'),
        ]
        category_only=context('Example Mango',None,brand='Example')
        category_only['categories']=['beer-wine-spirits/cocktails/individual-serving']
        examples.append(category_only)
        for value in examples:
            self.assertFalse(classifier.policy_facts(value)['ordinary_identity'])
            decision=classifier.classify_by_rules(value)
            self.assertTrue(decision is None or decision.vegan_status!='vegan')
        plain=context('Example Mango',None,brand='Example',other_information='Full Product Name: Fresh Mango. Storage: Cool.')
        self.assertTrue(classifier.policy_facts(plain)['ordinary_identity'])
        self.assertEqual(classifier.classify_by_rules(plain).vegan_reason,'ingredients')

    def test_mixed_factory_warning_is_not_definite_animal_content(self):
        value=context('Sea Salted Popcorn','Wholegrain Corn, Rapeseed Oil, Sea Salt',official_vegan=1,
            allergens='Contains Milk Made in a factory that handles Milk.')
        facts=classifier.policy_facts(value)
        self.assertFalse(facts['animal_declarations'])
        self.assertTrue(facts['qualified_animal_declarations'])
        decision=classifier.classify_by_rules(value)
        self.assertTrue(decision is None or decision.vegan_status=='unknown')
        value['product']['ingredients']+=' , Milk'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')
        value['product']['ingredients']='Wholegrain Corn, Rapeseed Oil, Sea Salt'
        value['product']['allergens']='Contains Milk.'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')
        value['product']['allergens']='May contain Milk.'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'vegan')

    def test_plant_allergen_declaration_does_not_absorb_factory_handling_warning(self):
        warnings = [
            'May Contain Eggs, May Contain Milk, May Contain Nuts, Contains Soya Produced in a factory that handles Milk, Nuts and Eggs.',
            'Contains Soya Produced in a factory that handles Milk.',
            'Contains Soya. Produced in a factory that handles Milk.',
            'Contains Soya Made in a facility which also handles Eggs.',
            'Contains Soya Packed in the same factory that handles Milk.',
        ]
        for warning in warnings:
            with self.subTest(warning=warning):
                value=context('Example Vegan Ham','Pea Protein, Soya Protein, Salt',
                    dietary_information='Suitable for Vegans',allergens=warning)
                assessment=policy.base_assessment(value,complete=True)
                assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
                    policy.reference('/product/dietary_information','Suitable for Vegans')]}]
                for stage in ('model','audit','export'):
                    result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                        requested_status='vegan',requested_reason='manufacturer')
                    self.assertEqual((result['vegan_status'],result['vegan_reason']),('vegan','manufacturer'),stage)
                    self.assertFalse(result['positive_block'],stage)
                    assessment=result['assessment']

    def test_factory_warning_boundary_retains_actual_animal_and_mixed_declarations(self):
        for declaration in ['Contains Milk Made in a factory that handles Milk.',
                            'Contains Soya and Milk Produced in a factory that handles Eggs.']:
            with self.subTest(declaration=declaration):
                value=context('Example Vegan Ham','Pea Protein, Soya Protein, Salt',
                    official_vegan=1,allergens=declaration)
                facts=classifier.policy_facts(value)
                self.assertTrue(facts['qualified_animal_declarations'])
                result=classifier.classify_by_rules(value)
                self.assertTrue(result is None or result.vegan_status=='unknown')
                value['product']['ingredients']+=', Milk'
                result=classifier.classify_by_rules(value)
                self.assertEqual(result.vegan_status,'nonvegan')
                self.assertTrue(result.evidence['tag_animal_conflict'])
        value=context('Example Vegan Ham','Pea Protein, Soya Protein, Salt',official_vegan=1,
            allergens='Contains Soya Produced in a factory that handles Milk. Contains Eggs.')
        self.assertTrue(classifier.policy_facts(value)['qualified_animal_declarations'])
        result=classifier.classify_by_rules(value)
        self.assertTrue(result is None or result.vegan_status=='unknown')
        value['product']['allergens']='Contains Soya and Milk.'
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')

    def test_supplied_components_are_assessed_even_when_not_eaten(self):
        value=context('Example Cup Filters Coffee','Coffee',features='Filters made from sugar cane fibres.')
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        self.assertIn('supplied-components-composition-v1',{
            issue['rule_id'] for issue in result.evidence['assessment']['material_issues']})
        self.assertNotIn('sugar-refining-v1',{
            issue['rule_id'] for issue in result.evidence['assessment']['material_issues']})
        for fields,reason in [({'official_vegan':1},'tagged'),
                              ({'dietary_information':'Suitable for vegans.'},'manufacturer'),
                              ({'name':'Vegan Cup Filters Coffee'},'name')]:
            claimed=copy.deepcopy(value);claimed['product'].update(fields)
            if reason=='name':self.assertIsNone(classifier.classify_by_rules(claimed))
            else:self.assertEqual(classifier.classify_by_rules(claimed).vegan_reason,reason)
        value['product']['specification']='Wrapper: Gelatine.'
        value['product']['official_vegan']=1
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'nonvegan')
        self.assertTrue(result.evidence['tag_animal_conflict'])

    def test_food_only_claim_cannot_clear_component_uncertainty(self):
        value=context('Example Coffee Pods','Coffee',dietary_information='Suitable for vegans; packaging not assessed.')
        result=classifier.classify_by_rules(value)
        self.assertEqual(result.vegan_status,'unknown')
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[
            policy.reference('/product/dietary_information','Suitable for vegans')]}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_status'],'unknown')
        value['product']['dietary_information']='Suitable for vegans.'
        assessment['components']={'status':'unresolved','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_status'],'unknown')

    def test_full_field_and_identity_are_not_a_reassurance_against_an_animal_declaration(self):
        value=context('Example Coffee Pods','Coffee',features='Wrapper: Gelatine. This wrapper is part of the supplied product and is used with every cup.')
        refs=[policy.reference('/product/features',value['product']['features']),policy.reference('/product/name',value['product']['name'])]
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'ingredients','evidence_refs':[policy.reference('/product/ingredients','Coffee')]}]
        assessment['material_issues']=[{'code':'evidence_conflict','target':'wrapper','rule_id':'animal-declaration-context-v1',
            'evidence_refs':refs,'resolution_refs':refs,'blocks_claim':True}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='ingredients')
        self.assertNotEqual(result['vegan_status'],'vegan')

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

    def test_raw_product_information_is_not_mistaken_for_an_external_website(self):
        value=context('Vegan Body Cream',None)
        text='Store in a cool place. Cruelty Free Suitable for vegans'
        value['manufacturer_vegan_evidence']=[{'field_title':'specification','content':text}]
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[{'path':'/manufacturer_vegan_evidence/0/content','quote':'Suitable for vegans'}]}]
        result=policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,requested_status='vegan',requested_reason='manufacturer')
        self.assertEqual(result['vegan_reason'],'manufacturer')
        self.assertFalse(result['positive_block'])
        for quote in ['Another variant is vegan.', 'This body cream is vegan?']:
            value['manufacturer_vegan_evidence'][0]['content']=quote
            assessment['positive_support'][0]['evidence_refs'][0]['quote']=quote
            value['product']['name']='Body Cream'
            assessment=policy.base_assessment(value,complete=True)
            assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[policy.reference('/manufacturer_vegan_evidence/0/content',quote)]}]
            self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
                requested_status='vegan',requested_reason='manufacturer')['vegan_status'],'unknown')

    def test_declared_animal_fining_agent_overrides_an_official_tag(self):
        for text in ['Fining agent: Isinglass.', 'Fining agents: Gelatine, casein.', 'Refining aid: Bone char.']:
            value=context('Wine',None,official_vegan=1,vinification_details=text)
            result=classifier.classify_by_rules(value)
            self.assertEqual(result.vegan_status,'nonvegan')
            self.assertTrue(result.evidence['tag_animal_conflict'])
            self.assertIn('animal_processing',{issue['code'] for issue in result.evidence['assessment']['material_issues']})

    def test_malformed_botanical_descriptor_is_uncertain_not_dairy_proof(self):
        value=context('Beard Balm','Vitis vinifera seed oil, butyrospermum parkiiy, butter, parfum',
            specification='Product texture: Balm. Suitable for vegans')
        self.assertIsNone(classifier.classify_by_rules(value))
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'manufacturer','evidence_refs':[{'path':'/product/specification','quote':'Suitable for vegans'}]}]
        self.assertEqual(policy.evaluate(value,**classifier.policy_facts(value),assessment=assessment,
            requested_status='vegan',requested_reason='manufacturer')['vegan_reason'],'manufacturer')
        value['product']['specification']=None
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'unknown')
        value['product']['ingredients']+=', Milk, Gelatine'
        value['product']['official_vegan']=1
        self.assertEqual(classifier.classify_by_rules(value).vegan_status,'nonvegan')

    def test_corroborated_animal_food_identity_and_intentional_allergens_are_negative_proof(self):
        for name, full in [('Example Goat’s Milk','Pasteurised Goat’s Milk'),
                           ('Example Ribeye Steak','British Beef Ribeye Steak')]:
            value=context(name,None,official_vegan=1,other_information='Full Product Name: '+full+' Storage: Keep refrigerated.')
            result=classifier.classify_by_rules(value)
            self.assertEqual(result.vegan_status,'nonvegan')
            self.assertTrue(result.evidence['tag_animal_conflict'])
        for name in ['Vegan Ribeye Steak','Ribeye Steak Flavour Crisps','Ribeye Steak Recipe Book']:
            result=classifier.classify_by_rules(context(name,None,other_information='Full Product Name: '+name))
            self.assertTrue(result is None or result.vegan_status!='nonvegan')
        self.assertEqual(classifier.classify_by_rules(context('Example Cheese',None,allergens='Contains Milk Contains Milk.')).vegan_status,'nonvegan')
        result=classifier.classify_by_rules(context('Example Vegan Beans',None,allergens='May contain milk.'))
        self.assertIsNone(result)

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

    def test_claim_coverage_is_not_a_source_applicability_state(self):
        value=context('Example Wine','Grapes')
        value['manufacturer_source_candidates']=[{'source_id':'capture',
            'source_text':'Example Wine. This product is vegan.',
            'association_status':'catalogue_match'}]
        assessment=policy.base_assessment(value,complete=True)
        claim=policy.reference('/manufacturer_source_candidates/0/source_text','This product is vegan.')
        for dimension in ('composition','sourcing','processing','components'):
            assessment[dimension]={'status':'covered_by_claim','evidence_refs':[claim]}
        assessment['source_applicability']=[source_check(value)]
        policy.validate_assessment(assessment,value)
        for dimension in policy.SOURCE_DIMENSIONS:
            with self.subTest(dimension=dimension):
                invalid=copy.deepcopy(assessment)
                invalid['source_applicability'][0][dimension]['status']='covered_by_claim'
                with self.assertRaisesRegex(ValueError,'covered_by_claim'):
                    policy.validate_assessment(invalid,value)

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
        assessment['components']['evidence_refs']=[policy.reference('/product/name',value['product']['name'])]
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
                if reason=='name':
                    self.assertIsNone(result)
                    assessment=policy.base_assessment(value,complete=True)
                    assessment['positive_support']=[{'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
                    result=classifier.apply_policy_gate(value,classifier.ClassificationResult(product_id='1',vegan_status='vegan',
                        vegan_reason='name',confidence='certain',summary='Whole-product assertion assessed',source='codex',
                        evidence={'assessment':assessment}))
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
        self.assertIsNone(classifier.classify_by_rules(value))

    def test_a_whole_product_dressing_name_is_a_positive_claim(self):
        value=context('Example Vegan Caesar Dressing',None)
        self.assertIsNone(classifier.classify_by_rules(value))
        assessment=policy.base_assessment(value,complete=True)
        assessment['positive_support']=[{'reason':'name','evidence_refs':[policy.reference('/product/name',value['product']['name'])]}]
        result=classifier.ClassificationResult(product_id='1',vegan_status='vegan',vegan_reason='name',confidence='certain',
            summary='Whole-product name assessed',source='codex',evidence={'assessment':assessment})
        final=classifier.apply_policy_gate(value,result)
        self.assertEqual((final.vegan_status,final.vegan_reason),('vegan','name'))
        shortcut=classifier.apply_policy_gate(value,__import__('dataclasses').replace(result,source='rule'))
        self.assertEqual(shortcut.vegan_status,'unknown')
        assessment['positive_support']=[]
        self.assertEqual(classifier.apply_policy_gate(value,result).vegan_status,'unknown')

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
