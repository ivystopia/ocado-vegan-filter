from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import literal_reference_catalogue as q
import vegan_policy as p

class CatalogueTests(unittest.TestCase):
 def fixture(self):return {'product':{'id':'1','name':'Example wine','ingredients':'Water, gelatine. May contain nuts.','official_vegan':0},'flags':[],'categories':['wine'],'manufacturer_source_candidates':[{'source_text':'Our wine is not vegan.\nOur other variant is vegan.'}]}
 def test_all_generated_refs_are_actual_literals(self):
  context=self.fixture()
  for ref in q.build([context])['1'].values():p.resolve_reference(context,ref)
 def test_product_ids_do_not_share_refs(self):
  x=self.fixture();y=deepcopy(x);y['product']['id']='2';a=q.build([x,y]);self.assertFalse(set(a['1'])&set(a['2']))
 def test_bad_reference_rejected(self):
  x=self.fixture();a=q.build([x]);schema={'type':'object','properties':{'decisions':{'type':'array','items':{'type':'object','properties':{'product_id':{'type':'string'},'assessment':{'type':'object','properties':{'evidence_refs':{'type':'array','items':deepcopy(p.REFERENCE_SCHEMA)}}}}}}}};wire=q.wire_schema(schema,a)
  valid={'decisions':[{'product_id':'1','assessment':{'evidence_refs':[{'reference_id':next(iter(a['1']))}]}}]};r,records=q.hydrate(valid,a,wire);self.assertTrue(records);p.resolve_reference(x,r['decisions'][0]['assessment']['evidence_refs'][0]);self.assertIn('reference_id',valid['decisions'][0]['assessment']['evidence_refs'][0])
  valid['decisions'][0]['assessment']['evidence_refs'][0]['reference_id']='fabricated'
  with self.assertRaises(ValueError):q.hydrate(valid,a,wire)
 def test_cross_product_reference_rejected_even_if_wire_allows_id(self):
  x=self.fixture();y=deepcopy(x);y['product']['id']='2';a=q.build([x,y]);schema={'type':'object'}
  with self.assertRaises(ValueError):q.hydrate({'decisions':[{'product_id':'1','assessment':{'x':[{'reference_id':next(iter(a['2']))}]}}]},a,schema)
 def test_no_words_or_negations_removed_within_excerpt(self):
  context=self.fixture();a=q.build([context]);source=[r['quote'] for r in a['1'].values() if r['path'].endswith('/source_text')];self.assertIn('Our wine is not vegan.', ' '.join(source));self.assertNotIn('Our wine is vegan.', ' '.join(source))
 def test_long_raw_fields_remain_untouched(self):
  x=self.fixture();x['product']['ingredients']=' '.join(['Water,']*1000)+ ' Gelatine.';before=deepcopy(x);a=q.build([x]);self.assertEqual(x,before);self.assertTrue(any('Gelatine.' in r['quote'] for r in a['1'].values()))
 def test_segment_transport_preserves_complete_unicode_native_fields(self):
  x=self.fixture();x['manufacturer_source_candidates'][0]['source_text']='\n  Not vegan — gelatin.\n'*100+'Last qualification: other variant only.'
  before=deepcopy(x);catalogue=q.build([x]);transport=q.prompt_transport([x],catalogue)
  self.assertEqual(q.restore_transport(transport['products']),[before]);self.assertEqual(x,before)
  self.assertIn('Last qualification: other variant only.',str(transport))
 def test_compaction_preserves_distinct_constraints(self):
  item={'type':'object','properties':{'x':{'type':'string'}}};other=deepcopy(item);other['properties']['x']['enum']=['a']
  output=q.compact_schema({'anyOf':[item,deepcopy(item),other]})
  self.assertEqual(output,{'anyOf':[item,other]});self.assertEqual(q.schema_property_count(output),2)
 def test_oversize_schema_splits_before_model_invocation(self):
  from unittest.mock import patch
  import classify_ocado_vegan as c
  x=self.fixture();x['manufacturer_vegan_evidence']=[];y=deepcopy(x);y['product']['id']='2'
  with patch.object(q,'schema_property_count',return_value=5001),patch.object(c.codex_evidence,'run') as run:
   with self.assertRaisesRegex(ValueError,'split this batch'):c.call_codex_batch([x,y],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')
   run.assert_not_called()
 def test_native_factoring_preserves_paths_without_mutating_strict_schema(self):
  options=[{'type':'object','additionalProperties':False,'required':['path','quote'],'properties':{'path':{'type':'string','enum':[path]},'quote':{'type':'string','enum':[quote]}}} for path,quote in [('/product/name','Example'),('/product/ingredients','Water')]]
  strict={'anyOf':options};before=deepcopy(strict);wire=q.native_wire_schema(strict)
  self.assertEqual(strict,before)
  for option in options:p.validate_schema({'path':option['properties']['path']['enum'][0],'quote':option['properties']['quote']['enum'][0]},wire)
  with self.assertRaises(ValueError):p.validate_schema({'path':'/foreign','quote':'Example'},wire)
 def test_factored_generation_does_not_accept_fabricated_native_quotation(self):
  strict={'anyOf':[{'type':'object','additionalProperties':False,'required':['path','quote'],'properties':{'path':{'type':'string','enum':['/product/name']},'quote':{'type':'string','enum':['Example']}}}]}
  payload={'path':'/product/name','quote':'fabricated'}
  p.validate_schema(payload,q.native_wire_schema(strict))
  with self.assertRaises(ValueError):p.validate_schema(payload,strict)
 def test_native_field_mask_rejects_fabricated_product_field(self):
  schema={'type':'object','required':['path','quote'],'properties':{'path':{'type':'string','pattern':r'^/(?:product(?:/[^/]+)?|categories(?:/\d+)?)$'},'quote':{'type':'string'}}}
  before=deepcopy(schema);wire=q.constrain_product_fields(schema,[self.fixture()])
  self.assertEqual(schema,before)
  p.validate_schema({'path':'/product/ingredients','quote':'Water'},wire)
  p.validate_schema({'path':'/categories','quote':'wine'},wire)
  with self.assertRaises(ValueError):p.validate_schema({'path':'/product/categories','quote':'wine'},wire)
 def test_small_native_generation_keeps_exact_metadata_quotation_enum(self):
  from unittest.mock import patch
  import classify_ocado_vegan as c
  context=self.fixture();context['manufacturer_vegan_evidence']=[];context['manufacturer_source_candidates'][0]['market_scope']='Exact UK market scope'
  payload={'decisions':[{'product_id':'1','vegan_status':'unknown','vegan_reason':None,'confidence':'uncertain','summary':'Unresolved','evidence':[],'ambiguity_notes':[],'assessment':c.policy.base_assessment(c.product_context_for_llm(context),complete=False)}]}
  schemas=[]
  def fake(prompt,schema,**kwargs):
   schemas.append(schema);kwargs['validator'](payload)
   return {'parsed_response':payload,'raw_response':'raw','exit_status':0}
  with patch.object(c.codex_evidence,'run',side_effect=fake):c.call_codex_batch([context],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex',prefer_literal_sources=False)
  matches=[]
  def find(node):
   if isinstance(node,dict):
    if node.get('required')==['path','quote'] and node['properties']['path'].get('enum')==['/manufacturer_source_candidates/0/market_scope']:matches.append(node['properties']['quote'])
    for value in node.values():find(value)
   elif isinstance(node,list):
    for value in node:find(value)
  find(schemas[0]);self.assertTrue(matches);self.assertTrue(all(value.get('enum')==['Exact UK market scope'] for value in matches))
 def constrained_source_fixture(self):
  context=self.fixture();context['manufacturer_source_candidates'][0]['official_identity']='  Manufacturer\n UK  '+('Long identity '*25)
  other=deepcopy(context);other['product']['id']='2'
  catalogue=q.build([context,other]);reference=deepcopy(p.REFERENCE_SCHEMA);reference['properties']['path']['enum']=['/manufacturer_source_candidates/0/source_text']
  schema={'type':'object','properties':{'decisions':{'type':'array','items':{'type':'object','properties':{'product_id':{'type':'string'},'assessment':{'type':'object','properties':{'evidence_refs':{'type':'array','items':reference}}}}}}}}
  wire=q.wire_schema(schema,catalogue)
  return context,other,catalogue,wire
 def test_literal_generation_preserves_native_field_and_product_boundaries(self):
  context,other,catalogue,wire=self.constrained_source_fixture()
  make=lambda rid:{'decisions':[{'product_id':'1','assessment':{'evidence_refs':[{'reference_id':rid}]}}]}
  source=next(k for k,v in catalogue['1'].items() if v['path'].endswith('/source_text'))
  category=next(k for k,v in catalogue['1'].items() if v['path']=='/categories/0')
  foreign=next(k for k,v in catalogue['2'].items() if v['path'].endswith('/source_text'))
  q.hydrate(make(source),catalogue,wire)
  for invalid in (category,foreign,True,float(source),-1,999999):
   with self.subTest(reference_id=invalid),self.assertRaises(ValueError):q.hydrate(make(invalid),catalogue,wire)
 def test_source_metadata_preserves_long_raw_exact_quote(self):
  context,other,catalogue,wire=self.constrained_source_fixture();raw=context['manufacturer_source_candidates'][0]['official_identity']
  self.assertIn({'path':'/manufacturer_source_candidates/0/official_identity','quote':raw},list(catalogue['1'].values()))
  self.assertEqual(q.restore_transport(q.prompt_transport([context,other],catalogue))['products'],[context,other])
 def test_source_context_uses_literal_ids_by_default(self):
  from unittest.mock import patch
  import classify_ocado_vegan as c
  context=self.fixture();context['manufacturer_vegan_evidence']=[];schemas=[]
  payload={'decisions':[{'product_id':'1','vegan_status':'unknown','vegan_reason':None,'confidence':'uncertain','summary':'Unresolved','evidence':[],'ambiguity_notes':[],'assessment':c.policy.base_assessment(c.product_context_for_llm(context),complete=False)}]}
  def fake(prompt,schema,**kwargs):
   schemas.append(schema);self.assertIsNotNone(kwargs['canonicalizer']);self.assertIn(q.VERSION,kwargs['prompt_version']);kwargs['validator'](payload)
   return {'parsed_response':payload,'raw_response':'raw','exit_status':0}
  with patch.object(c.codex_evidence,'run',side_effect=fake):c.call_codex_batch([context],model='gpt-6-luna',reasoning_effort='high',codex_bin='codex')
  self.assertIn('reference_id',str(schemas[0]));self.assertNotIn("'required': ['path', 'quote']",str(schemas[0]))

class NativeArbitrationFormatTests(unittest.TestCase):
 def test_source_arbitration_uses_native_quotes_without_changing_primary(self):
  from unittest.mock import patch
  import classify_ocado_vegan as c
  context={'product':{'id':'1','name':'Example wine','ingredients':None,'official_vegan':0},'flags':[],'categories':['wine'],'manufacturer_vegan_evidence':[],'manufacturer_source_candidates':[{'source_text':'Our wine is vegan.','market_scope':'United Kingdom'}]}
  payload={'decisions':[{'product_id':'1','vegan_status':'unknown','vegan_reason':None,'confidence':'uncertain','summary':'Insufficient evidence','evidence':[],'ambiguity_notes':[],'assessment':c.policy.base_assessment(c.product_context_for_llm(context),complete=False)}]}
  schemas=[]
  def fake(prompt,schema,**kwargs):
   schemas.append(schema);self.assertEqual(kwargs['stage'],'arbitration');self.assertNotIn(q.VERSION,kwargs['prompt_version']);kwargs['validator'](payload)
   return {'parsed_response':payload,'raw_response':'raw','exit_status':0}
  with patch.object(c.codex_evidence,'run',side_effect=fake):c.call_codex_batch([context],model='gpt-6.1-sol',reasoning_effort='medium',codex_bin='codex',arbitration_policy=True)
  self.assertNotIn('reference_id',str(schemas[0]));self.assertIn("'required': ['path', 'quote']",str(schemas[0]))

if __name__=='__main__':unittest.main()
