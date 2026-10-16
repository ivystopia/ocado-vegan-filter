from unittest.mock import patch
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import classify_ocado_vegan as c
import literal_reference_catalogue as catalogue

class FallbackTests(unittest.TestCase):
 def context(self):return {'product':{'id':'1','name':'Unspecified compound product'},'flags':[],'categories':[],'manufacturer_vegan_evidence':[]}
 def decision(self,context):return {'product_id':'1','vegan_status':'unknown','vegan_reason':None,'confidence':'uncertain','summary':'Composition unspecified','evidence':[],'ambiguity_notes':[],'assessment':c.policy.base_assessment(c.product_context_for_llm(context),complete=False)}
 def run_failure(self,error):
  context=self.context();seen=[];decision=self.decision(context)
  def fake(contexts,**kwargs):
   seen.append(kwargs)
   if len(seen)==1:raise ValueError(error)
   return {'1':decision},'raw','parsed',0
  with patch.object(c,'call_codex_batch',side_effect=fake):
   result=c.classify_with_arbitration([context],passes=2,retries=1,model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')[0]
  return seen,result
 def test_quote_failure_restarts_both_independent_passes_with_catalogue(self):
  seen,result=self.run_failure('Evidence quote is absent at /product/name')
  self.assertEqual([x['literal_catalogue'] for x in seen],[False,True,True]);self.assertEqual(result.vegan_status,'unknown');self.assertIsNone(result.validation_error);self.assertEqual([x['model'] for x in seen],['gpt-6-luna']*3)
 def test_other_invalid_result_does_not_change_reference_protocol(self):
  seen,result=self.run_failure('Codex returned unexpected product_id')
  self.assertEqual([x['literal_catalogue'] for x in seen],[False]*3);self.assertIsNone(result.validation_error)
 def test_exhausted_catalogue_still_errors_and_never_calls_arbitration(self):
  seen=[]
  def fake(contexts,**kwargs):seen.append(kwargs);raise ValueError('Evidence quote is absent at /product/name')
  with patch.object(c,'call_codex_batch',side_effect=fake):r=c.classify_with_arbitration([self.context()],passes=2,retries=1,model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')[0]
  self.assertEqual(len(seen),2);self.assertTrue(r.validation_error);self.assertEqual(r.source,'codex_error');self.assertFalse(any(x['arbitration_policy'] for x in seen))
 def test_native_primary_uses_literal_ids_before_any_copy_failure(self):
  contexts=[self.context(),{**self.context(),'product':{'id':'2','name':'Encoded native product'}}]
  native=[c.product_context_for_llm(x) for x in contexts];refs=catalogue.build(native)
  def wire(value,pid):
   if isinstance(value,dict):
    if set(value)=={'path','quote'}:
     return {'reference_id':next(rid for rid,ref in refs[pid].items() if ref==value)}
    return {k:wire(v,pid) for k,v in value.items()}
   if isinstance(value,list):return [wire(v,pid) for v in value]
   return value
  def fake(prompt,schema,**kwargs):
   self.assertIn(catalogue.VERSION,kwargs['prompt_version'])
   payload={'decisions':[wire({**self.decision(ctx),'product_id':ctx['product']['id']},ctx['product']['id']) for ctx in contexts]}
   hydrated,restorations=kwargs['canonicalizer'](payload)
   kwargs['validator'](hydrated)
   return {'validated_response':hydrated,'parsed_response':payload,'raw_response':'retained wire response','exit_status':0}
  with patch.object(c.codex_evidence,'run',side_effect=fake) as run:
   decisions,_,_,_=c.call_codex_batch(contexts,model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')
  self.assertEqual(set(decisions),{'1','2'});self.assertEqual(run.call_count,1)
if __name__=='__main__':unittest.main()
