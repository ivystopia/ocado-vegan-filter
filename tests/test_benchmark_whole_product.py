"""Frozen benchmark, provenance and gate contracts (no authenticated calls)."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import benchmark_whole_product as benchmark
import assemble_whole_product_fixture as assemble
import classify_ocado_vegan as classifier
import vegan_policy as policy
import codex_evidence
import compare_whole_product_models as comparison


class WholeProductBenchmarkTests(unittest.TestCase):
    def test_complete_associated_source_roundtrip_and_transport(self):
        case=assemble.synthetic_cases()[0]
        context=case['classifier_input']
        body='Example product vegan assurance. Other products have exceptions.'
        context['manufacturer_source_candidates']=[{'source_id':'retained','source_url':'https://example.test/faq',
            'source_text':body,'source_sha256':__import__('hashlib').sha256(body.encode()).hexdigest(),
            'retrieved_at':'2026-10-06','official_identity':'Manufacturer','market_scope':'UK','association_status':'catalogue_match'}]
        conn=benchmark.fixture_database([case]);self.addCleanup(conn.close)
        actual=classifier.product_context_for_llm(classifier.load_product_context(conn,context['product']['id']))
        self.assertEqual(actual,context)
        prompt=classifier.build_codex_prompt([context,context])
        self.assertEqual(prompt.count(body),1)
        self.assertIn('shared_source_texts',prompt)

    def fixture(self,path):
        cases=assemble.synthetic_cases()[:5]
        path.write_text(json.dumps({'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':cases}))
        return cases

    def test_frozen_hash_and_duplicate_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.json';cases=self.fixture(path)
            benchmark.load_fixture(path)
            cases[0]['classifier_input']['product']['name']='Changed evidence'
            path.write_text(json.dumps({'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':cases}))
            with self.assertRaisesRegex(ValueError,'altered'):benchmark.load_fixture(path)

    def test_pipeline_rules_persistence_export_without_model_or_live_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'fixture.json';self.fixture(path)
            args=benchmark.parser().parse_args(['--fixture',str(path),'--split','synthetic','--output',str(root/'results')])
            with mock.patch.object(classifier,'classify_with_arbitration',side_effect=AssertionError('Unnecessary model call')):
                result=benchmark.run(args)
            self.assertEqual(result['state'],'completed')
            self.assertEqual(result['runs'][0]['exact_matches'],5)
            self.assertFalse(result['release_challenge_passed'])
            self.assertIn('implementation_hashes',result)

    def test_all_unknown_fails_release_gate(self):
        cases=[];decisions={}
        for index,reason in enumerate(policy.REASONS):
            case={'case_id':str(index),'expected':{'vegan_status':'vegan','vegan_reason':reason}}
            cases.append(case);decisions[str(index)]={'vegan_status':'unknown','vegan_reason':None,'evidence':{},'validation_error':None}
        result=benchmark.metrics(cases,decisions)
        self.assertEqual(result['vegan_recall'],0)
        self.assertFalse(benchmark.acceptance({'split':'locked','runs':[result]*3}))

    def test_resume_rejects_changed_inputs_or_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'fixture.json';self.fixture(path)
            args=benchmark.parser().parse_args(['--fixture',str(path),'--split','synthetic','--mode','rules','--output',str(root/'results')])
            benchmark.run(args);args.model='gpt-5.6-luna'
            with self.assertRaisesRegex(ValueError,'Resume configuration'):benchmark.run(args)

    def test_synthetic_contrasts_are_not_natural_error_rate_observations(self):
        cases=assemble.synthetic_cases()
        self.assertGreaterEqual(len(cases),200)
        self.assertTrue(all(case['case_kind']=='synthetic' for case in cases))
        self.assertLess(len({case['leakage_group'] for case in cases}),len(cases))

    def test_quota_stop_preserves_completed_cases_and_refuses_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'fixture.json';cases=self.fixture(path)
            args=benchmark.parser().parse_args(['--fixture',str(path),'--split','synthetic','--batch-size','1','--workers','1','--output',str(root/'results')])
            original=benchmark.execute_batch
            def execute(batch,args,journal):
                if batch[0]['case_id']==cases[2]['case_id']:raise codex_evidence.UsageLimitError('Included limit')
                return original(batch,args,journal)
            with mock.patch.object(benchmark,'execute_batch',side_effect=execute):result=benchmark.run(args)
            self.assertEqual(result['state'],'quota_exhausted')
            self.assertFalse(result['release_challenge_passed'])
            checkpoint=json.loads((args.output/'run-1.json').read_text())
            self.assertTrue(checkpoint['decisions'])
            self.assertNotIn(cases[2]['case_id'],checkpoint['decisions'])
            resumed=benchmark.run(args)
            self.assertEqual(resumed['runs'][0]['exact_matches'],5)

    def test_credit_comparison_counts_cached_input_and_reasoning_once(self):
        report={'usage_by_stage_model':{'primary:gpt-6-luna:high':{'input_tokens':1_000_000,'cached_input_tokens':200_000,'output_tokens':100_000,'reasoning_output_tokens':50_000}}}
        self.assertAlmostEqual(comparison.credit_equivalent(report),3.3)

    def test_operational_failure_does_not_inflate_positive_recall(self):
        cases=[{'case_id':'a','expected':{'vegan_status':'vegan','vegan_reason':'name'}}]
        result=benchmark.metrics(cases,{'a':{'validation_error':'Failed call','evidence':{}}})
        self.assertEqual(result['vegan_recall'],0)
        self.assertEqual(result['operational_errors'],['a'])

    def test_final_gate_and_raw_model_safety_are_reported_separately(self):
        cases=[{'case_id':'a','expected':{'vegan_status':'unknown','vegan_reason':None}}]
        decisions={'a':{'vegan_status':'unknown','vegan_reason':None,'validation_error':None,
            'evidence':{'independent_passes':[{'vegan_status':'vegan'},{'vegan_status':'vegan'}]}}}
        result=benchmark.metrics(cases,decisions)
        self.assertEqual(result['unsafe_positives'],[])
        self.assertEqual(result['raw_agreed_unsafe_positives'],[{'case_id':'a','stage':'primary'}])

    def test_scarce_reference_classes_preserve_targets_and_disjoint_pilot(self):
        candidates=[]
        for route,count in {'tagged':50,'manufacturer':50,'name':17,'ingredients':50,'unknown':293,'nonvegan':100}.items():
            for index in range(count):
                candidates.append({'case_id':f'{route}.{index}','family_key':f'{route}.{index}',
                    'external_sources':[],'source_group_keys':[],
                    'expected':{'vegan_status':'vegan' if route in policy.REASONS else route,'vegan_reason':route if route in policy.REASONS else None},
                    'adjudication':{'state':'accepted'}})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'annotations.json'
            path.write_text(json.dumps({'candidates':candidates,'workload':[],'disputed_cases':[]}))
            result=assemble.assemble(path,root/'fixture.json')
            fixture=json.loads((root/'fixture.json').read_text())
        self.assertFalse(result['target_complete'])
        self.assertEqual(result['development_shortfalls'],{'name':13,'unknown':3})
        self.assertEqual(result['locked_shortfalls'],{'name':20,'unknown':4})
        self.assertEqual(len(result['pilot_case_ids']),96)
        self.assertFalse({c['leakage_group'] for c in fixture['cases'] if c['split']=='development'} &
            {c['leakage_group'] for c in fixture['cases'] if c['split']=='locked'})

    def test_arbitration_selection_includes_actual_primary_disagreements(self):
        cases=[]
        for route,count in {'tagged':20,'manufacturer':20,'name':20,'ingredients':20,'unknown':120,'nonvegan':40}.items():
            for index in range(count):
                cases.append({'case_id':f'{route}.{index}','split':'development','expected':{'vegan_status':'vegan' if route in policy.REASONS else route,'vegan_reason':route if route in policy.REASONS else None}})
        with tempfile.TemporaryDirectory() as tmp:
            result=comparison.arbitration_fixture({'cases':cases},[{'runs':[{'primary_disagreements':['unknown.119']}]}],Path(tmp)/'fixture.json')
        self.assertGreaterEqual(len(result['cases']),96)
        self.assertIn('unknown.119',{case['case_id'] for case in result['cases']})
        self.assertTrue(all(case['split']=='arbitration' for case in result['cases']))
