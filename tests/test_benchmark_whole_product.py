"""Frozen benchmark, provenance and gate contracts (no authenticated calls)."""
from dataclasses import asdict
import copy
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
import validate_whole_product_candidate as candidate_checks
import adjudicate_whole_product_corpus as adjudication
import refresh_whole_product_references as refresh
import audit_whole_product_positives as positive_audit
from types import SimpleNamespace


class WholeProductBenchmarkTests(unittest.TestCase):
    def issue_fixture(self, kind='natural'):
        context={'product':{'name':'Cotton tops','preparation_and_usage':'100% cotton (exclusive of trimmings)'}}
        case={'case_id':'a','case_kind':kind,'classifier_input':context,
              'expected':{'vegan_status':'unknown','vegan_reason':None,
                          'required_issue_codes':['composition_incomplete','identity_conflict']}}
        issue={'code':'composition_incomplete','resolution_refs':[],
               'evidence_refs':[{'path':'/product/preparation_and_usage','quote':'exclusive of trimmings'}]}
        decision={'vegan_status':'unknown','vegan_reason':None,'validation_error':None,
                  'evidence':{'assessment':{'material_issues':[issue]}}}
        return case,decision

    def test_natural_unknown_accepts_a_grounded_sufficient_blocker_and_reports_completeness(self):
        case,decision=self.issue_fixture()
        report=benchmark.metrics([case],{'a':decision})
        self.assertEqual(report['missing_required_issues'],[])
        self.assertEqual(report['incomplete_reference_issue_reporting'],{'a':['identity_conflict']})
        self.assertEqual(report['issue_rubric_version'],benchmark.ISSUE_RUBRIC_VERSION)

    def test_unknown_blocker_must_be_unresolved_cited_and_reference_supported(self):
        case,decision=self.issue_fixture()
        for field,value in [('resolution_refs',[{'path':'/product/name','quote':'Cotton tops'}]),
                            ('evidence_refs',[]),('code','processing_unresolved'),
                            ('evidence_refs',[{'path':'/product/name','quote':'Invented quotation'}])]:
            changed=copy.deepcopy(decision)
            changed['evidence']['assessment']['material_issues'][0][field]=value
            self.assertEqual(benchmark.metrics([case],{'a':changed})['missing_required_issues'],['a'])

    def test_controlled_contracts_still_require_every_expected_issue(self):
        case,decision=self.issue_fixture('synthetic')
        self.assertEqual(benchmark.metrics([case],{'a':decision})['missing_required_issues'],['a'])

    def test_one_unknown_blocker_cannot_excuse_an_unsafe_promotion_or_false_animal_allegation(self):
        case,decision=self.issue_fixture()
        for status in ('vegan','nonvegan'):
            changed=copy.deepcopy(decision);changed['vegan_status']=status
            changed['vegan_reason']='ingredients' if status=='vegan' else None
            report=benchmark.metrics([case],{'a':changed})
            self.assertEqual(report['missing_required_issues'],['a'])
            self.assertEqual(report['unsafe_positives'] if status=='vegan' else report['false_nonvegan_decisions'],['a'])

    def test_identity_only_reference_cannot_be_replaced_with_an_unrelated_composition_issue(self):
        case,decision=self.issue_fixture();case['expected']['required_issue_codes']=['identity_conflict']
        self.assertEqual(benchmark.metrics([case],{'a':decision})['missing_required_issues'],['a'])

    def test_annotation_schema_rejects_uncited_component_assessment(self):
        row={'case_id':'a','expected_status':'nonvegan','expected_reason':None,
            'required_issue_codes':[],'input_refs':[{'path':'/product/ingredients','quote':'Milk'}],
            'rationale':'Intentional milk.','external_status':'unknown','source_id':None,
            'external_quote':'','scope_match':'','component_assessment':{'status':'insufficient','evidence_refs':[]}}
        with self.assertRaises(ValueError):policy.validate_schema(row,adjudication.ROW_SCHEMA)
        row['component_assessment']['evidence_refs']=[{'path':'/product/name','quote':'Milk'}]
        policy.validate_schema(row,adjudication.ROW_SCHEMA)

    def test_positive_audit_does_not_hide_disputes_or_changed_inputs(self):
        context={'product':{'id':'1','name':'Carrots'}}
        case={'case_id':'a','classifier_input':context,'input_sha256':classifier.context_hash(context)}
        packet={'policy_version':policy.POLICY_VERSION,'candidates':[case],'sampling':{'size':1}}
        predictions={'packet_sha256':classifier.context_hash(packet),'predictions':{'a':{'vegan_status':'vegan'}}}
        reading={**case,'expected':None,'adjudication':{'state':'disputed','policy_version':policy.POLICY_VERSION}}
        annotations={'policy_version':policy.POLICY_VERSION,'candidates':[reading]}
        report=positive_audit.score(packet,predictions,annotations)
        self.assertEqual(report['state'],'failed')
        self.assertEqual(report['reference_counts'],{'disputed':1})
        self.assertIsNone(report['zero_error_binomial_upper_95'])
        annotations['candidates']=[reading,reading]
        with self.assertRaisesRegex(ValueError,'duplicate'):positive_audit.score(packet,predictions,annotations)
        annotations['candidates']=[{**reading,'input_sha256':'changed'}]
        with self.assertRaisesRegex(ValueError,'different evidence'):positive_audit.score(packet,predictions,annotations)
        annotations['candidates']=[{**reading,'expected':{'vegan_status':'vegan','vegan_reason':'ingredients'},
            'adjudication':{'state':'accepted','policy_version':policy.POLICY_VERSION}}]
        with self.assertRaisesRegex(ValueError,'two distinct'):positive_audit.score(packet,predictions,annotations)

    def test_refresh_packet_excludes_old_labels_and_adjudications(self):
        case={'case_id':'a','case_kind':'natural','sampling_stratum':'ordinary','current_on_ocado':1,
            'classifier_input':{'product':{'id':'1','name':'Carrots','brand':'Example'}},'input_sha256':'frozen',
            'source_metadata':[],'expected':{'vegan_status':'nonvegan'},'adjudication':{'old_model':'wrong'}}
        packet=refresh.blind_case(case)
        self.assertNotIn('expected',packet)
        self.assertNotIn('adjudication',packet)
        self.assertEqual(packet['classifier_input'],case['classifier_input'])

    def test_explicit_reference_crosswalk_preserves_parent_and_disputes(self):
        case={'case_id':'a','case_kind':'natural','sampling_stratum':'ordinary','current_on_ocado':1,
            'classifier_input':{'product':{'id':'1','name':'Example Cup Filters Coffee'}},
            'input_sha256':'frozen','split':'development','source_metadata':[],
            'expected':{'vegan_status':'vegan','vegan_reason':'ingredients','required_issue_codes':[]}}
        parent={'policy_version':'whole-product-v1','cases':[case],
            'selection':{'targets':{'locked':{'name':30}},'pilot_case_ids':['a']}}
        candidate=refresh.blind_case(case)
        candidate.update(expected={'vegan_status':'unknown','vegan_reason':None,'required_issue_codes':['composition_incomplete']},
            adjudication={'state':'accepted','policy_version':policy.POLICY_VERSION,
                'readings':[{'annotation':{'rationale':'Supplied filter composition is unresolved.'}}]*2})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'parent.json';source.write_text(json.dumps(parent));original=source.read_bytes()
            annotations=root/'annotations.json';annotations.write_text(json.dumps({'policy_version':policy.POLICY_VERSION,
                'candidates':[candidate],'workload':[]}))
            refresh.build_fixture(source,annotations,root/'current.json',root/'crosswalk.json')
            self.assertEqual(source.read_bytes(),original)
            crosswalk=json.loads((root/'crosswalk.json').read_text())
            self.assertEqual(crosswalk['reference_changes'][0]['before']['vegan_status'],'vegan')
            self.assertEqual(crosswalk['reference_changes'][0]['after']['vegan_status'],'unknown')
            self.assertFalse(json.loads((root/'current.json').read_text())['selection']['target_complete'])
    def test_stale_or_failed_checks_block_comparison(self):
        record={'state':'passed','candidate_hashes':benchmark.candidate_hashes(),
            'checks':[{'name':name,'exit_code':0} for name in ('unittest','userscript-syntax','diff-check')]}
        candidate_checks.validate_record(record)
        for changed in ({**record,'state':'failed'},{**record,'candidate_hashes':{}},
                        {**record,'checks':[{'name':'unittest','exit_code':0}]}):
            with self.assertRaises(ValueError):candidate_checks.validate_record(changed)

    def test_two_failed_lunas_do_not_automatically_start_an_expensive_primary(self):
        failed={'state':'completed','runs':[{'unsafe_positives':[],'operational_errors':[],
            'missing_cases':[],'reason_errors':[],'mandatory_control_failures':[],
            'reportable_negative_errors':[],'missing_required_issues':[],
            'mismatches':['a'],'false_nonvegan_decisions':[],'vegan_recall':0,'per_route_recall':{}}]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);checks=root/'checks.json';checks.write_text('{}')
            args=SimpleNamespace(fixture=root/'fixture.json',checks=checks,output=root/'results',
                workers=1,development_only=True,primary_failure_review=None)
            with mock.patch.object(benchmark,'load_fixture',return_value={'selection':{'target_complete':False},
                    'cases':[{'case_id':'a','split':'development'}]}),\
                 mock.patch.object(candidate_checks,'validate_record'),\
                 mock.patch.object(comparison,'execute',return_value=failed) as execute:
                result=comparison.run(args)
            self.assertEqual(result['state'],'primary_failure_review_required')
            self.assertEqual({call.kwargs['model'] for call in execute.call_args_list},
                {'gpt-5.6-luna','gpt-6-luna'})

    def test_passing_natural_pilots_cannot_hide_unsafe_control_results(self):
        passing={'state':'completed','runs':[{**{field:[] for field in comparison.FAILURE_FIELDS},
            'vegan_recall':1,'per_route_recall':{}}]}
        failed=copy.deepcopy(passing);failed['runs'][0]['unsafe_positives']=['culture']
        failed['runs'][0]['mismatches']=['culture']
        labels=[]
        def execute(args,label,**options):
            labels.append(label)
            return failed if label.startswith('controls-') else passing
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);checks=root/'checks.json';checks.write_text('{}')
            args=SimpleNamespace(fixture=root/'fixture.json',checks=checks,output=root/'results',
                workers=1,development_only=True,primary_failure_review=None)
            with mock.patch.object(benchmark,'load_fixture',return_value={'selection':{'target_complete':False}}),\
                 mock.patch.object(candidate_checks,'validate_record'),mock.patch.object(comparison,'execute',side_effect=execute):
                result=comparison.run(args)
        self.assertEqual(result['state'],'primary_failure_review_required')
        self.assertFalse(any(label.startswith(('development-','arbitration-')) for label in labels))

    def test_control_reuse_cannot_drop_a_failed_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'controls.json';path.write_text(json.dumps({'gpt-6-luna/high':'passing.json'}))
            with self.assertRaisesRegex(ValueError,'both inexpensive'):
                comparison.primary_controls(SimpleNamespace(primary_control_reports=path),{'cases':[]},'gpt-6-luna','high')

    def test_failure_diagnostic_preserves_inputs_and_excludes_passing_cases(self):
        cases=copy.deepcopy(assemble.synthetic_cases()[:2])
        fixture={'cases':cases}
        run={field:[] for field in comparison.FAILURE_FIELDS}
        run.update(mismatches=[cases[0]['case_id']],unsafe_positives=[cases[0]['case_id']])
        report={'runs':[run]}
        with tempfile.TemporaryDirectory() as tmp:
            result=comparison.diagnostic_fixture(fixture,report,Path(tmp)/'diagnostic.json')
        self.assertEqual(len(result['cases']),1)
        self.assertEqual(result['cases'][0]['classifier_input'],cases[0]['classifier_input'])
        self.assertEqual(result['cases'][0]['expected'],cases[0]['expected'])
        self.assertFalse(result['diagnostic_selection']['qualification_evaluation'])
        self.assertEqual(fixture['cases'][0]['split'],cases[0]['split'])
        report['runs'][0]['missing_cases']=['missing']
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'original frozen'):
                comparison.diagnostic_fixture(fixture,report,Path(tmp)/'diagnostic.json')

    def test_stage_reuse_cannot_omit_candidate_controls_or_accept_unknown_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'stages.json'
            args=SimpleNamespace(primary_stage_reports=path,primary_control_reports=None)
            for value in ({'gpt-5.6-luna/high':{'pilot':'pilot.json'}},
                          {'gpt-6.1-sol/medium':{'controls':'controls.json','pilot':'pilot.json'}}):
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'controls AND pilot'):
                    comparison.stage_reuse_mapping(args)

    def test_stage_reuse_retains_failure_and_rejects_changed_code_or_inputs(self):
        case=assemble.synthetic_cases()[0]
        fixture={'cases':[case]}
        args=SimpleNamespace(workers=3)
        report={'fixture_sha256':classifier.context_hash(fixture),'selected_case_hash':classifier.context_hash([case]),
            'state':'completed','split':'synthetic','subset':'all','mode':'pipeline',
            'model':'gpt-5.6-luna','effort':'high','policy_version':policy.POLICY_VERSION,
            'knowledge_version':policy.KNOWLEDGE_VERSION,'prompt_version':classifier.PROMPT_VERSION,
            'arbitration_prompt_version':classifier.ARBITRATION_PROMPT_VERSION,
            'arbitration_model':classifier.ARBITRATION_MODEL,'arbitration_effort':classifier.ARBITRATION_REASONING_EFFORT,
            'schema_sha256':classifier.context_hash(classifier.CODEX_OUTPUT_SCHEMA),
            'workers':3,'batch_size':8,'repetitions':1,'seed':20261006,
            'implementation_hashes':{name:classifier.context_hash((benchmark.ROOT/'tools'/name).read_text()) for name in
                ['vegan_policy.py','classify_ocado_vegan.py','codex_evidence.py','benchmark_whole_product.py','manufacturer_evidence.py','update_userscript_allowlists.py']},
            'runs':[{'cases':1,'unsafe_positives':[case['case_id']]}],'usage':{'missing_usage_calls':0}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json';path.write_text(json.dumps(report))
            actual=comparison.validated_stage_report(path,fixture,args,'gpt-5.6-luna','high','controls')
            self.assertEqual(actual['runs'][0]['unsafe_positives'],[case['case_id']])
            for field,value in [('selected_case_hash','changed'),('state','checkpointed'),('workers',1),
                                ('implementation_hashes',{}),('usage',{'missing_usage_calls':1})]:
                changed={**report,field:value};path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    comparison.validated_stage_report(path,fixture,args,'gpt-5.6-luna','high','controls')

    def test_failure_review_requires_current_reports_and_model_attribution(self):
        case={'case_id':'a','split':'development','classifier_input':{'product':{'name':'Carrots'}}}
        fixture={'cases':[case]}
        run={field:[] for field in comparison.FAILURE_FIELDS}
        run.update(mismatches=['a'],vegan_recall=0,per_route_recall={})
        report={'state':'completed','runs':[run]}
        entries={model+'/'+effort:{'pilot':report} for model,effort in comparison.PRIMARY}
        review={'policy_version':policy.POLICY_VERSION,'candidate_hashes':benchmark.candidate_hashes(),
            'fixture_sha256':classifier.context_hash(fixture),'policy_settled':True,
            'implementation_verified':True,'references_verified':True,
            'models':{name:{'report_sha256':classifier.context_hash(report),'cases':{'a':{
                'cause':'model_reasoning','rationale':'The frozen ordinary-food identity was rejected.',
                'evidence_refs':[{'path':'/product/name','quote':'Carrots'}]}}} for name in entries}}
        comparison.validate_failure_review(review,entries,fixture)
        for item in review['models'].values():item['cases']['a']['cause']='implementation'
        with self.assertRaisesRegex(ValueError,'cannot trigger'):comparison.validate_failure_review(review,entries,fixture)

    def test_annotation_positive_requires_a_resolved_component_assessment(self):
        value=classifier.product_context_for_llm({'product':{'id':'1','name':'Vegan Coffee Pods'},
            'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})
        row={'expected_status':'vegan','expected_reason':'name','rationale':'Affirmative whole-product claim.',
            'input_refs':[{'path':'/product/name','quote':'Vegan Coffee Pods'}],
            'component_assessment':{'status':'unresolved','evidence_refs':[{'path':'/product/name','quote':'Vegan Coffee Pods'}]}}
        with self.assertRaisesRegex(ValueError,'unresolved'):adjudication.validate_basis(row,value)
        row['component_assessment']['status']='covered_by_claim'
        adjudication.validate_basis(row,value)

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
        # Conclusive rule contracts only; name candidates require models.
        ids={'synthetic.00.0','synthetic.00.1','synthetic.00.2','synthetic.00.4','synthetic.18.0'}
        cases=[case for case in assemble.synthetic_cases() if case['case_id'] in ids]
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
        self.assertFalse(benchmark.successful({'state':'completed','runs':[result]}))

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
