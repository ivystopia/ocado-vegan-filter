from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import classify_ocado_vegan as classifier
import native_product_identity as identity
import vegan_policy as policy


class BeerStyleIdentityTests(unittest.TestCase):
    def context(self, **fields):
        return {'product':{'id':'1','name':'Harbour Atlantic American Pale Ale',
            'other_information':'Full Product Name: Atlantic American Pale Ale Storage: Keep cool.',
            'detailed_description':'Harbour Atlantic IPA beer is bold and hoppy.',
            'dietary_information':'Suitable for Vegans', **fields},
            'flags':[], 'categories':[], 'manufacturer_vegan_evidence':[]}

    def test_beer_style_observation_does_not_defeat_independent_vegan_assurance(self):
        context=self.context();refs=identity.declared_beer_style_conflict_refs(context)
        self.assertEqual(len(refs),3)
        for ref in refs:policy.resolve_reference(context,ref)
        result=policy.evaluate(classifier.product_context_for_llm(context),**classifier.policy_facts(context))
        self.assertEqual(result['vegan_status'],'vegan')
        self.assertFalse(result['positive_block'])
        self.assertIsNone(classifier.classify_by_rules(context))

    def test_generic_pale_ale_description_does_not_exclude_an_ipa(self):
        self.assertEqual(identity.declared_beer_style_conflict_refs(self.context(
            name='Harbour Atlantic Pale Ale',other_information='Full Product Name: Atlantic Pale Ale')),[])

    def test_consistent_apa_identity_is_not_a_conflict(self):
        self.assertEqual(identity.declared_beer_style_conflict_refs(self.context(
            detailed_description='Harbour Atlantic American Pale Ale is bold and hoppy.')),[])

    def test_other_product_or_serving_example_is_not_an_own_identity(self):
        for text in ('Our other Atlantic IPA beer is hoppy.',
                     'A recipe using Atlantic IPA beer is delicious.',
                     'Unlike Atlantic IPA beer is a broad comparison.'):
            with self.subTest(text=text):
                self.assertEqual(identity.declared_beer_style_conflict_refs(self.context(detailed_description=text)),[])

    def test_missing_formal_identity_does_not_establish_the_conflict(self):
        self.assertEqual(identity.declared_beer_style_conflict_refs(self.context(other_information='Keep cool.')),[])

    def test_mixed_beer_case_does_not_assert_one_style(self):
        self.assertEqual(identity.declared_beer_style_conflict_refs(self.context(
            name='Mixed Case Including Atlantic American Pale Ale')),[])

    def test_explicit_supplied_rename_leaves_interpretation_to_normal_assessment(self):
        self.assertEqual(identity.declared_beer_style_conflict_refs(self.context(
            detailed_description='Atlantic IPA was formerly known as Atlantic American Pale Ale. Atlantic IPA beer is hoppy.')),[])

    def test_negated_rename_does_not_resolve_the_conflict(self):
        self.assertTrue(identity.declared_beer_style_conflict_refs(self.context(
            detailed_description='Atlantic IPA is not formerly known as Atlantic American Pale Ale. Atlantic IPA beer is hoppy.')))
