"""Exact evidence-set compaction preserves meaning and prevents replay growth."""
from pathlib import Path
import copy
import hashlib
import json
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import vegan_policy as policy


class AssessmentCompactionTests(unittest.TestCase):
    def test_repeated_gate_does_not_amplify_unproven_source_references(self):
        text='This other soap is vegan.'
        context={'product':{'id':'1','name':'Example Soap','official_vegan':0},
                 'flags':[],'categories':[],'manufacturer_vegan_evidence':[],
                 'manufacturer_source_candidates':[{'source_id':'s','source_text':text,
                     'source_sha256':hashlib.sha256(text.encode()).hexdigest(),
                     'title':'Other Soap','url':'https://example.test/soap'}]}
        assessment=policy.base_assessment(context)
        ref=policy.reference('/manufacturer_source_candidates/0/source_text',text)
        assessment['source_applicability']=[{'source_path':'/manufacturer_source_candidates/0','scope':'unresolved',
            **{d:{'status':'insufficient','evidence_refs':[ref]} for d in policy.SOURCE_DIMENSIONS}}]
        for dimension in policy.DIMENSIONS:assessment[dimension]['evidence_refs'].append(ref)
        outputs=[]
        for _ in range(12):
            result=policy.evaluate(context,animal_matches=[],cleaned_ingredients='',
                composition_safe=False,ordinary_identity=False,assessment=assessment,requested_status='unknown')
            self.assertEqual(result['vegan_status'],'unknown')
            assessment=result['assessment'];outputs.append(json.dumps(assessment,sort_keys=True))
        self.assertEqual(len(set(outputs)),1)
        self.assertTrue(any(i['code']=='claim_scope_mismatch' for i in assessment['material_issues']))

    def test_exact_duplicates_only_and_input_unchanged(self):
        a={'path':'/product/name','quote':'Example'}
        b={'path':'/product/name','quote':'example'}
        c={'path':'/product/features','quote':'Example'}
        issue={'code':'identity_conflict','evidence_refs':[a,a,b,c], 'resolution_refs':[b,b]}
        other={**issue,'code':'evidence_conflict'}
        value={'material_issues':[issue,copy.deepcopy(issue),other],
               'source_applicability':[{'source_path':'/one'},{'source_path':'/one'},{'source_path':'/two'}],
               'independent_passes':[{'status':'unknown'},{'status':'unknown'}]}
        original=copy.deepcopy(value);result=policy.compact_assessment(value)
        self.assertEqual(value,original)
        self.assertEqual(result['material_issues'][0]['evidence_refs'],[a,b,c])
        self.assertEqual(result['material_issues'][0]['resolution_refs'],[b])
        self.assertEqual(len(result['material_issues']),2)
        self.assertEqual(len(result['source_applicability']),2)
        self.assertEqual(len(result['independent_passes']),2)
        self.assertEqual(policy.compact_assessment(result),result)

    def test_invalid_reference_is_preserved_for_strict_validation(self):
        bad={'path':'/missing','quote':'invented','extra':'invalid'}
        result=policy.compact_assessment({'identity':{'evidence_refs':[bad,bad]}})
        self.assertEqual(result['identity']['evidence_refs'],[bad])
        with self.assertRaises(ValueError):policy.resolve_reference({'product':{}},bad)

    def test_distinct_resolutions_and_scope_disagreements_are_never_collapsed(self):
        a={'path':'/product/name','quote':'Example'}
        issue={'code':'processing_unresolved','target':'whole_product','rule_id':'test',
               'evidence_refs':[a],'resolution_refs':[],'blocks_claim':True}
        resolved={**issue,'resolution_refs':[a]}
        unresolved_scope={'source_path':'/one','scope':'unresolved'}
        product_scope={'source_path':'/one','scope':'product'}
        result=policy.compact_assessment({'material_issues':[issue,resolved],
                                          'source_applicability':[unresolved_scope,product_scope]})
        self.assertEqual(len(result['material_issues']),2)
        self.assertEqual(len(result['source_applicability']),2)
