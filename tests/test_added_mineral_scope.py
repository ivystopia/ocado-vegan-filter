"""Added mineral groups require source facts; intrinsic minerals do not."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import vegan_policy as policy
import classify_ocado_vegan as classifier


class AddedMineralScopeTests(unittest.TestCase):
    def context(self, ingredients, **fields):
        return {'product': {'id': '1', 'name': 'Plant based dental sticks',
                            'ingredients': ingredients, 'official_vegan': 0, **fields},
                'flags': [], 'categories': [], 'manufacturer_vegan_evidence': []}

    def mineral_risks(self, context):
        return [r for r in policy.material_risks(context, context['product']['ingredients'])
                if r['rule_id'] == 'unspecified-added-minerals-v1']

    def test_bare_added_group_requires_source_despite_plant_based_marketing(self):
        context = self.context('Potato, vegetable glycerine, minerals, apples',
                               detailed_description='Our plant based dental sticks.')
        risks = self.mineral_risks(context)
        self.assertEqual(len(risks), 1)
        issue = {**risks[0], 'resolution_refs': [policy.reference(
            '/product/detailed_description', 'Our plant based dental sticks.')]}
        self.assertFalse(policy.resolved_risk(risks[0], {'material_issues': [issue]}, context))

    def test_intrinsic_named_and_standard_flour_minerals_are_not_bare_groups(self):
        for ingredients in ('Natural mineral water', 'Carrots',
                            'Water with naturally occurring minerals',
                            'Minerals (calcium carbonate, magnesium oxide)',
                            'Wheat flour (calcium, iron, niacin, thiamin)',
                            'Potato, calcium carbonate, magnesium oxide'):
            with self.subTest(ingredients=ingredients):
                self.assertEqual(self.mineral_risks(self.context(ingredients)), [])

    def test_group_specific_assurance_is_bound_and_qualified(self):
        for text, accepted in [('All minerals are animal-free.', True),
                               ('All minerals are sourced from mined deposits.', True),
                               ('All minerals may be animal-free.', False),
                               ('All minerals are not animal-free.', False),
                               ('Our sticks are plant based.', False)]:
            with self.subTest(text=text):
                context = self.context('Potato, minerals', further_description=text)
                risk = self.mineral_risks(context)[0]
                issue = {**risk, 'resolution_refs': [policy.reference('/product/further_description', text)]}
                self.assertEqual(policy.resolved_risk(risk, {'material_issues': [issue]}, context), accepted)

    def test_applicable_whole_product_claim_preserves_tag_and_manufacturer_routes(self):
        for fields, reason in [({'official_vegan': 1}, 'tagged'),
                               ({'dietary_information': 'Suitable for vegans'}, 'manufacturer')]:
            with self.subTest(reason=reason):
                result = classifier.classify_rule_candidate(self.context('Potato, minerals', **fields))
                self.assertIsNotNone(result)
                self.assertEqual((result.vegan_status, result.vegan_reason), ('vegan', reason))

    def test_affirmative_name_can_resolve_generic_mineral_issue_after_interpretation(self):
        context = self.context('Potato, minerals', name='Vegan dental sticks')
        assessment = policy.base_assessment(context, complete=True)
        assessment['positive_support'] = [{'reason': 'name', 'evidence_refs': [
            policy.reference('/product/name', context['product']['name'])]}]
        result = policy.evaluate(context, animal_matches=[], cleaned_ingredients='Potato, minerals',
            composition_safe=False, ordinary_identity=False, assessment=assessment,
            requested_status='vegan', requested_reason='name')
        self.assertEqual((result['vegan_status'], result['vegan_reason']), ('vegan', 'name'))
