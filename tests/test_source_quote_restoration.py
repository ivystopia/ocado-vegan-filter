from copy import deepcopy
from pathlib import Path
import sys, unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import source_quote_restoration as q

class QuoteRestorationTests(unittest.TestCase):
    def context(self,text='This shampoo is also vegan!'):
        return {'product':{'id':'1','name':'Example'},'manufacturer_source_candidates':[{'source_text':text}]}
    def ref(self,quote):
        return {'path':'/manufacturer_source_candidates/0/source_text','quote':quote}
    def test_exact_reference_unchanged(self):
        ref=self.ref('This shampoo is also vegan!')
        fixed,record=q.restore(self.context(),ref)
        self.assertEqual(fixed,ref);self.assertIsNone(record)
    def test_case_only_restores_literal_source(self):
        fixed,record=q.restore(self.context(),self.ref('this shampoo is also vegan!'))
        self.assertEqual(fixed['quote'],'This shampoo is also vegan!')
        q.policy.resolve_reference(self.context(),fixed)
        self.assertEqual(record['operation'],'restore_unique_literal_ASCII_case_only')
    def test_substantive_changes_still_rejected(self):
        for quote in ('This shampoo is vegan!','This shampoo is also vegan.','This shampoo is not vegan!',
                      'This shampoo is also vegan! 100%','This conditioner is also vegan!'):
            with self.subTest(quote=quote), self.assertRaises(ValueError):q.restore(self.context(),self.ref(quote))
    def test_ambiguous_case_variants_rejected(self):
        with self.assertRaises(ValueError):q.restore(self.context('This is vegan. THIS IS VEGAN.'),self.ref('this is vegan.'))
    def test_unicode_case_is_not_changed(self):
        with self.assertRaises(ValueError):q.restore(self.context('VÉGAN'),self.ref('végan'))
    def test_reversible_encoding_restores_same_field_literal(self):
        fixed,record=q.restore(self.context('This Â® product is NOT vegan.'),self.ref('This ® product is NOT vegan.'))
        self.assertEqual(fixed['quote'],'This Â® product is NOT vegan.')
        self.assertEqual(record['source_value_hash'],q.policy.digest('This Â® product is NOT vegan.'))
        q.policy.resolve_reference(self.context('This Â® product is NOT vegan.'),fixed)
    def test_json_transport_escaping_is_bounded(self):
        import json
        literal='A 10" box and board set'
        escaped=literal
        for depth in range(1,4):
            escaped=json.dumps(escaped,ensure_ascii=False)[1:-1]
            if depth<=2:
                fixed,record=q.restore(self.context(literal),self.ref(escaped))
                self.assertEqual(fixed['quote'],literal)
                self.assertEqual(record['JSON_depth'],depth)
            else:
                with self.assertRaises(ValueError):q.restore(self.context(literal),self.ref(escaped))
    def test_transport_cannot_change_semantics_or_accent_spelling(self):
        for quote in ('This ® product is vegan.','This ® product is NOT vegan!',
                      'This ® product 2 is NOT vegan.'):
            with self.subTest(quote=quote),self.assertRaises(ValueError):
                q.restore(self.context('This Â® product is NOT vegan.'),self.ref(quote))
        with self.assertRaises(ValueError):q.restore(self.context('Pedro Ximènez'),self.ref('Pedro Ximénez'))
    def test_no_other_field_or_product_lookup(self):
        value=self.context(); value['product']['features']='This conditioner is also vegan!'
        with self.assertRaises(ValueError):q.restore(value,self.ref('this conditioner is also vegan!'))
        payload={'decisions':[{'product_id':'1','vegan_status':'unknown','vegan_reason':None,
            'assessment':{'identity':{'evidence_refs':[self.ref('this shampoo is also vegan!')]}}}]}
        other={'product':{'id':'2','name':'Other'},'manufacturer_source_candidates':[{'source_text':'This shampoo is also vegan!'}]}
        with self.assertRaises(ValueError):q.canonicalize(payload,[self.context('Unknown'),other])
    def test_payload_original_and_decisions_preserved(self):
        value={'decisions':[{'product_id':'1','vegan_status':'vegan','vegan_reason':'manufacturer',
            'assessment':{'positive_support':[{'reason':'manufacturer','evidence_refs':[self.ref('this shampoo is also vegan!')]}]}}]}
        before=deepcopy(value);fixed,records=q.canonicalize(value,[self.context()])
        self.assertEqual(value,before);self.assertEqual(len(records),1)
        for field in ('product_id','vegan_status','vegan_reason'):
            self.assertEqual(fixed['decisions'][0][field],before['decisions'][0][field])

if __name__=='__main__': unittest.main()

class CallRecordTests(unittest.TestCase):
    def test_raw_and_parsed_outputs_remain_original(self):
        import json, subprocess
        from unittest import mock
        import codex_evidence as calls
        original={'decisions':[],'quote':'lowercase'}
        def fake(command,**kwargs):
            Path(command[command.index('--output-last-message')+1]).write_text(json.dumps(original))
            event={'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}
            return subprocess.CompletedProcess(command,0,json.dumps(event),'')
        validated={'decisions':[],'quote':'Literal source'}
        repairs=[{'operation':'restore_unique_literal_ASCII_case_only'}]
        seen=[]
        with mock.patch.object(calls.subprocess,'run',side_effect=fake), mock.patch.object(calls,'quota_snapshot',return_value={'auth_type':'chatgpt','ordinary_usage_allowed':True,'buckets':{'codex':{'primary':{'usedPercent':0}}}}):
            record=calls.run('Prompt',{},model='gpt-6-luna',effort='high',codex_bin='codex',
                stage='primary',product_ids=['1'],prompt_version='test',context_hash='hash',
                canonicalizer=lambda value:(validated,repairs),validator=lambda value:seen.append(value))
        self.assertEqual(json.loads(record['raw_response']),original)
        self.assertEqual(record['parsed_response'],original)
        self.assertEqual(record['validated_response'],validated)
        self.assertEqual(record['reference_canonicalizations'],repairs)
        self.assertEqual(seen,[validated])

class ProtocolContinuationTests(unittest.TestCase):
    def test_policy_prompt_invocation_changes_cannot_be_reused(self):
        import hashlib,tempfile
        from unittest import mock
        import refresh_whole_product as refresh
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'tools').mkdir()
            old={};current={};sources={}
            for module,source in [('classify_ocado_vegan.py','import codex_evidence\nCLASSIFIER_VERSION="old"\ndef call_codex_batch(): pass\ndef policy_facts(): return "same"\n'),
                                  ('codex_evidence.py','def invocation(): return "same"\ndef run(): pass\n')]:
                path=root/module;path.write_text(source);sources[module]=str(path)
                old['tools/'+module]=hashlib.sha256(path.read_bytes()).hexdigest()
                (root/'tools'/module).write_text(source)
            current=dict(old)
            with mock.patch.object(refresh,'ROOT',root):
                refresh.validate_protocol_continuation(old,current,sources)
                for module,changed in [('classify_ocado_vegan.py','def policy_facts(): return "changed"\n'),('codex_evidence.py','def invocation(): return "changed"\n')]:
                    path=root/'tools'/module;original=path.read_text();path.write_text(changed)
                    with self.assertRaises(refresh.GateError):refresh.validate_protocol_continuation(old,current,sources)
                    path.write_text(original)
                with self.assertRaises(refresh.GateError):refresh.validate_protocol_continuation(old,{**current,'tools/vegan_policy.py':'changed'},sources)
