import sys
from pathlib import Path
import re
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import vegan_policy as p


class ReferenceNormalizationCacheTests(unittest.TestCase):
    def test_normalization_matches_uncached_definition_for_all_input_kinds(self):
        for value in (None,False,0,1,{'a':'b'},['Vegan'], '', ' Alpha\n\tBeta ', 'École\u00a0vegan'):
            self.assertEqual(p.normalized(value),re.sub(r'\s+',' ',str(value or '')).strip())

    def test_changed_context_cannot_reuse_a_previously_valid_quote(self):
        ctx={'manufacturer_source_candidates':[{'source_text':'L1: **All products are vegan.**'}]}
        ref={'path':'/manufacturer_source_candidates/0/source_text','quote':'All products are vegan.'}
        self.assertEqual(p.resolve_reference(ctx,ref),ctx['manufacturer_source_candidates'][0]['source_text'])
        ctx['manufacturer_source_candidates'][0]['source_text']='L1: **Some products contain milk.**'
        with self.assertRaisesRegex(ValueError,'quote is absent'):p.resolve_reference(ctx,ref)

    def test_cache_sizes_are_bounded_and_literal_and_visible_refs_stay_valid(self):
        for i in range(1000):
            p.normalized('unique '+str(i));p.visible_source_text('L1: **unique '+str(i)+'**')
        self.assertLessEqual(p._normalized_text.cache_info().currsize,512)
        self.assertLessEqual(p.visible_source_text.cache_info().currsize,128)
        self.assertEqual(p.resolve_reference({'product':{'name':'Alpha\nBeta'}},{'path':'/product/name','quote':'Alpha Beta'}),'Alpha\nBeta')
        with self.assertRaisesRegex(ValueError,'quote is absent'):
            p.resolve_reference({'product':{'name':'**Vegan**'}},{'path':'/product/name','quote':'vegan'})


if __name__=='__main__':unittest.main()
