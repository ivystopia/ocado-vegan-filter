"""Reference repairs cannot silently change classification or bypass an audit."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import refresh_whole_product as r
import benchmark_whole_product as b
import adjudicate_whole_product_corpus as a


class ReferenceAuditContinuationTests(unittest.TestCase):
    def test_only_reference_instructions_and_continuation_can_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths={};previous={};current={}
            for module in ('adjudicate_whole_product_corpus.py','refresh_whole_product.py'):
                text=(r.ROOT/'tools'/module).read_text()
                if module.startswith('adjudicate'):
                    text=text.replace(f"PROMPT_VERSION='{a.PROMPT_VERSION}'", "PROMPT_VERSION='previous-instruction'").replace("POLICY='''", "POLICY='''Previous instructions. ")
                path=Path(tmp)/module;path.write_text(text);paths[module]=path
                previous['tools/'+module]=hashlib.sha256(path.read_bytes()).hexdigest()
                current['tools/'+module]=hashlib.sha256((r.ROOT/'tools'/module).read_bytes()).hexdigest()
            self.assertIn('tools/adjudicate_whole_product_corpus.py',r.validate_reference_audit_continuation(previous,current,paths))
            for forbidden in ('tools/vegan_policy.py','tools/classify_ocado_vegan.py','tools/codex_evidence.py'):
                changed={**current,forbidden:'different'}
                with self.assertRaisesRegex(r.GateError,'classifier, policy'):
                    r.validate_reference_audit_continuation(previous,changed,paths)
            path=paths['adjudicate_whole_product_corpus.py'];text=path.read_text().replace("if not row['rationale'].strip():", 'if False:')
            path.write_text(text);previous['tools/adjudicate_whole_product_corpus.py']=hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(r.GateError,'validation'):
                r.validate_reference_audit_continuation(previous,current,paths)

    def test_wrong_previous_source_and_activation_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths={};hashes={}
            for module in ('adjudicate_whole_product_corpus.py','refresh_whole_product.py'):
                path=Path(tmp)/module;path.write_bytes((r.ROOT/'tools'/module).read_bytes());paths[module]=path
                hashes['tools/'+module]=hashlib.sha256(path.read_bytes()).hexdigest()
            paths['refresh_whole_product.py'].write_text(paths['refresh_whole_product.py'].read_text().replace("conn.execute('PRAGMA foreign_keys=OFF')", "conn.execute('PRAGMA foreign_keys=ON')"))
            with self.assertRaisesRegex(r.GateError,'exact bound'):
                r.validate_reference_audit_continuation(hashes,hashes,paths)
            previous={**hashes,'tools/refresh_whole_product.py':hashlib.sha256(paths['refresh_whole_product.py'].read_bytes()).hexdigest()}
            with self.assertRaisesRegex(r.GateError,'activation'):
                r.validate_reference_audit_continuation(previous,hashes,paths)

    def test_reference_only_fork_keeps_decisions_research_and_replaces_consumed_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);previous=base/'old';previous.mkdir();new=base/'new';new.mkdir();source=base/'source.sqlite';working=base/'scraped.sqlite';manufacturer=base/'manufacturer';manufacturer.mkdir()
            for path in (source,previous/'candidate.sqlite'):
                with closing(sqlite3.connect(path)) as db:db.execute('CREATE TABLE products(id TEXT PRIMARY KEY,vegan_status TEXT,vegan_reason TEXT)');db.execute("INSERT INTO products VALUES('1','vegan','name')");db.commit()
            with closing(sqlite3.connect(working)) as db:db.execute('CREATE TABLE sync_runs(id INTEGER,status TEXT)');db.execute("INSERT INTO sync_runs VALUES(7,'completed')");db.commit()
            (manufacturer/'snapshot.json').write_text('{}');(previous/'positive-score.json').write_text('{"state":"failed"}');(previous/'positive-audit.json').write_text('{"consumed":true}')
            for name in ('manufacturer-coverage.json','manufacturer-affected.json'):(previous/name).write_text('{"retained":true}')
            context={'product':{'id':'1','name':'Vegan Carrots'},'flags':[],'categories':[],'manufacturer_vegan_evidence':[]}
            ref={'path':'/product/name','quote':'Vegan Carrots'}
            annotation={'case_id':'probe','expected_status':'vegan','expected_reason':'name','required_issue_codes':[], 'input_refs':[ref],'rationale':'Affirmative whole-product vegan name.','external_status':'unknown','source_id':None,'external_quote':'','scope_match':'Native name.','component_assessment':{'status':'covered_by_claim','evidence_refs':[ref]}}
            paired=[{'annotation':annotation,'call_id':str(i),'model':r.MODELS['annotation'],'effort':r.MODELS['annotation_effort'],'prompt_version':a.PROMPT_VERSION,'input_sha256':r.classifier.context_hash(context)} for i in (1,2)]
            config={'code_hashes':{'same':'hash'},'fixture_sha256':None};old={'state':'checkpointed','configuration':config,'canonical_baseline':'canonical','candidate_audit_baseline':'candidate','immutable_snapshots':{'original':str(source),'refreshed':str(working)},'sync_run':{'id':7,'status':'completed'},'positive_audit_seed':9,'stages':{'scrape':{'state':'completed'},'assessment':{'state':'completed','artifacts':{'manufacturer/snapshot.json':hashlib.sha256((manufacturer/'snapshot.json').read_bytes()).hexdigest()}},'research':{'state':'completed','artifacts':{name:hashlib.sha256((previous/name).read_bytes()).hexdigest() for name in ('manufacturer-coverage.json','manufacturer-affected.json')}},'positive_audit':{'state':'checkpointed'}}}
            (previous/'manifest.json').write_text(json.dumps(old));proof={'state':'passed','candidate_hashes':config['code_hashes'],'previous_manifest_sha256':hashlib.sha256((previous/'manifest.json').read_bytes()).hexdigest(),'previous_candidate_fingerprint':'candidate','previous_reference_sources':{}}
            values={'checks':{'state':'passed'},'controls':{'mode':'retained-call-policy-replay','candidate_hashes':config['code_hashes'],'fixture_sha256':None,'case_ids':['s']},'pilot':{'mode':'retained-call-policy-replay','candidate_hashes':config['code_hashes'],'fixture_sha256':None,'case_ids':['n']},'protocol':{'state':'passed','candidate_hashes':config['code_hashes'],'products':['1'],'validated_independent_passes':2},'reference_protocol':{'state':'passed','candidate_hashes':config['code_hashes'],'cases':[{'case_id':'probe','classifier_input':context}],'readings':paired},'native_text_plan':{'changes':[],'unmatched_projection':[]}}
            for key,value in values.items():path=base/(key+'.json');path.write_text(json.dumps(value));proof[key+'_path']=str(path)
            proof['native_text_plan_sha256']=hashlib.sha256(Path(proof['native_text_plan_path']).read_bytes()).hexdigest();evidence=base/'proof.json';evidence.write_text(json.dumps(proof))
            runner=object.__new__(r.Refresh);runner.root=new;runner.path=new/'manifest.json';runner.source=source;runner.candidate=new/'candidate.sqlite';runner.fixture=base/'fixture.json';runner.fixture.write_text('{}');runner.manifest={'configuration':config,'stages':{}}
            origin={'root':str(manufacturer)}
            def copy_bound(src,dest,expected):r.clone(src,dest);return {'logical_fingerprint':expected}
            fixture={'cases':[{'case_id':'s','case_kind':'synthetic'},{'case_id':'n','case_kind':'natural'}]}
            with mock.patch.object(r,'validate_reference_audit_continuation'),mock.patch.object(r,'validate_research_protocol_origin',return_value=origin),mock.patch.object(r.checks,'validate_record'),mock.patch.object(b,'successful',return_value=True),mock.patch.object(b,'load_fixture',return_value=fixture),mock.patch.object(r,'fingerprint',return_value='canonical'),mock.patch.object(r,'clone_bound_candidate',side_effect=copy_bound),mock.patch.object(r.text_recovery,'preview',return_value=values['native_text_plan']),mock.patch.object(r.classifier,'load_product_context',return_value=context),mock.patch.object(r.classifier,'product_context_for_llm',side_effect=lambda value:value),mock.patch.object(r.classifier,'connect',side_effect=AssertionError('Reference repair opened a writer')),mock.patch.object(r.policy_replay,'replay_candidate',side_effect=AssertionError('Reference repair rewrote decisions')):
                runner.fork_policy_repair(previous,evidence,audit_repair=True,reference_only=True)
            self.assertEqual(runner.manifest['remaining'],['positive_audit','activation','export'])
            self.assertNotEqual(runner.manifest['positive_audit_seed'],9)
            self.assertEqual(json.loads((new/'policy-replay.json').read_text())['decision_writes'],0)
            for name in ('manufacturer-coverage.json','manufacturer-affected.json'):self.assertEqual((new/name).read_bytes(),(previous/name).read_bytes())
            with closing(sqlite3.connect(new/'candidate.sqlite')) as db:self.assertEqual(db.execute('SELECT * FROM products').fetchall(),[('1','vegan','name')])
            self.assertEqual((previous/'positive-audit.json').read_text(),'{"consumed":true}')
