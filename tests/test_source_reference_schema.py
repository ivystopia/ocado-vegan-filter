from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import classify_ocado_vegan as classifier
import vegan_policy as policy


class SourceReferenceSchemaTests(unittest.TestCase):
    def fixture(self):
        context = {'product':{'id':'1','name':'Example Product'},'flags':[],'categories':[],
            'manufacturer_vegan_evidence':[], 'manufacturer_source_candidates':[
                {'source_text':'Example product page'}, {'source_text':'All products are vegan'}]}
        assessment = policy.base_assessment(context, complete=False)
        source = {'source_path':'/manufacturer_source_candidates/0','scope':'product',
            **{dimension:{'status':'unresolved','evidence_refs':[
                policy.reference('/manufacturer_source_candidates/0/source_text','Example product page')]}
                for dimension in policy.SOURCE_DIMENSIONS}}
        assessment['source_applicability']=[source]
        decision = {'product_id':'1','vegan_status':'unknown','vegan_reason':None,
            'confidence':'uncertain','summary':'Unresolved applicability','evidence':[],
            'ambiguity_notes':[],'assessment':assessment}
        return context, {'decisions':[decision]}

    def test_self_source_and_product_identity_are_valid(self):
        context,payload=self.fixture()
        payload['decisions'][0]['assessment']['source_applicability'][0]['product']['evidence_refs'].append(
            policy.reference('/product/name','Example Product'))
        policy.validate_schema(payload,classifier.batch_output_schema({'1'},[context]))

    def test_other_page_cannot_appear_in_a_source_applicability_dimension(self):
        context,payload=self.fixture()
        payload['decisions'][0]['assessment']['source_applicability'][0]['variant']['evidence_refs']=[
            policy.reference('/manufacturer_source_candidates/1/source_text','All products are vegan')]
        with self.assertRaises(ValueError):policy.validate_schema(payload,classifier.batch_output_schema({'1'},[context]))

    def test_invented_nested_product_source_path_is_rejected(self):
        context,payload=self.fixture()
        payload['decisions'][0]['assessment']['identity']['evidence_refs']=[
            policy.reference('/product/manufacturer_source_candidates/0/source_text','Example product page')]
        with self.assertRaises(ValueError):policy.validate_schema(payload,classifier.batch_output_schema({'1'},[context]))

    def test_combining_pages_outside_applicability_remains_allowed(self):
        context,payload=self.fixture()
        payload['decisions'][0]['assessment']['composition']['evidence_refs']=[
            policy.reference('/manufacturer_source_candidates/0/source_text','Example product page'),
            policy.reference('/manufacturer_source_candidates/1/source_text','All products are vegan')]
        policy.validate_schema(payload,classifier.batch_output_schema({'1'},[context]))

    def test_empty_capture_context_cannot_generate_source_applicability(self):
        context,payload=self.fixture();context['manufacturer_source_candidates']=[]
        with self.assertRaises(ValueError):policy.validate_schema(payload,classifier.batch_output_schema({'1'},[context]))

    def test_per_call_constraints_do_not_mutate_shared_schema(self):
        context,_=self.fixture();original=deepcopy(classifier.CODEX_OUTPUT_SCHEMA)
        classifier.batch_output_schema({'1'},[context])
        self.assertEqual(classifier.CODEX_OUTPUT_SCHEMA,original)

    def test_source_metadata_quote_is_exact_not_invented_publisher_name(self):
        context, payload = self.fixture()
        context['manufacturer_source_candidates'][0]['official_identity'] = 'Official manufacturer published site text'
        refs = payload['decisions'][0]['assessment']['source_applicability'][0]['product']['evidence_refs']
        refs[:] = [policy.reference('/manufacturer_source_candidates/0/official_identity', 'Official manufacturer published site text')]
        schema = classifier.batch_output_schema({'1'},[context])
        policy.validate_schema(payload,schema)
        refs[0]['quote'] = 'Example Manufacturer'
        with self.assertRaises(ValueError):policy.validate_schema(payload,schema)
