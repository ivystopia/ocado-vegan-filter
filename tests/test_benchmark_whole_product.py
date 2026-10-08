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
    def test_different_catalogue_provenance_fails_before_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.json'
            cases=self.fixture(path)
            fixture=json.loads(path.read_text())
            fixture['catalogue_provenance']={'retailer':'Ocado','market':'US'}
            path.write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError,'catalogue provenance'):
                benchmark.load_fixture(path)

    def test_current_frozen_evidence_roundtrips_before_model_evaluation(self):
        root=Path(__file__).resolve().parents[1]/'benchmarks/vegan-classifier-v2'
        fixture=benchmark.load_fixture(root/'ethical-cases-v21.json')
        self.assertEqual(fixture['catalogue_provenance'],policy.CATALOGUE_PROVENANCE)
        for split in {case['split'] for case in fixture['cases']}:
            with self.subTest(split=split):
                cases=[case for case in fixture['cases'] if case['split']==split]
                conn=benchmark.fixture_database(cases)
                conn.close()
        parent=benchmark.load_fixture(root/'ethical-cases-v20.json')
        for old,new in zip(parent['cases'],fixture['cases']):
            self.assertEqual(old['expected'] if 'expected' in old else None,
                             new['expected'] if 'expected' in new else None)
            for field in ('manufacturer_vegan_evidence','manufacturer_source_candidates'):
                old_rows=old['classifier_input'].get(field,[])
                new_rows=new['classifier_input'].get(field,[])
                self.assertEqual([row.get('source_text') for row in old_rows],
                                 [row.get('source_text') for row in new_rows])

    def test_malformed_website_fixture_stops_before_any_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'fixture.json';cases=self.fixture(path)
            cases[0]['classifier_input']['manufacturer_source_candidates']=[{
                'source_id':'incomplete','source_url':'https://example.test/faq',
                'source_text':'This product is vegan.','source_sha256':'0'*64,
                'retrieved_at':'2026-10-07','market_scope':'UK','association_status':'catalogue_match'}]
            cases[0]['input_sha256']=classifier.context_hash(cases[0]['classifier_input'])
            path.write_text(json.dumps({'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':cases}))
            args=benchmark.parser().parse_args(['--fixture',str(path),'--split','synthetic','--output',str(root/'results')])
            with mock.patch.object(benchmark,'execute_batch') as execute:
                with self.assertRaisesRegex(KeyError,'official_identity'):
                    benchmark.run(args)
                execute.assert_not_called()
            report=json.loads((root/'results/report.json').read_text())
            self.assertEqual(report['state'],'failed')
            self.assertEqual(report['usage']['calls'],0)
            self.assertEqual(report['runs'],[])

    def test_v6_cited_reason_crosswalk_preserves_controls_and_original_pilot(self):
        root=Path(__file__).resolve().parents[1]/'benchmarks/vegan-classifier-v2'
        parent=json.loads((root/'ethical-cases-v5.json').read_text())
        current=benchmark.load_fixture(root/'ethical-cases-v6.json')
        old={case['case_id']:case for case in parent['cases']}
        new={case['case_id']:case for case in current['cases']}
        self.assertEqual(len(new)-len(old),7)
        self.assertEqual(current['selection']['pilot_case_ids'],parent['selection']['pilot_case_ids'])
        changed=[]
        for key,case in old.items():
            self.assertEqual(new[key]['classifier_input'],case['classifier_input'],key)
            self.assertEqual(new[key]['input_sha256'],case['input_sha256'],key)
            if case['split'] in {'synthetic','workload'}:self.assertEqual(new[key],case,key)
            if new[key]['expected']!=case['expected']:changed.append(key)
        self.assertEqual(changed,['natural.634499011'])
        self.assertEqual(new[changed[0]]['expected']['vegan_reason'],'manufacturer')
        for reading in new[changed[0]]['adjudication']['readings']:
            self.assertEqual(reading['prompt_version'],'blind-evidence-adjudication-v11')
            self.assertEqual(reading['annotation']['expected_reason'],'manufacturer')
            for ref in reading['annotation']['input_refs']:
                policy.resolve_reference(new[changed[0]]['classifier_input'],ref)
        self.assertEqual(current['selection']['development_shortfalls'],{'name':13})
        self.assertEqual(current['selection']['pilot_route_counts'],
            {'tagged':6,'manufacturer':7,'name':5,'ingredients':6,'unknown':48,'nonvegan':24})
        self.assertFalse(current['selection']['target_complete'])

    def test_v5_reference_crosswalk_preserves_frozen_inputs_and_the_pilot(self):
        root=Path(__file__).resolve().parents[1]/'benchmarks/vegan-classifier-v2'
        parent=json.loads((root/'ethical-cases-v4.json').read_text())
        current=benchmark.load_fixture(root/'ethical-cases-v5.json')
        old={case['case_id']:case for case in parent['cases']}
        new={case['case_id']:case for case in current['cases']}
        self.assertEqual(len(new)-len(old),28)
        self.assertEqual(current['selection']['pilot_case_ids'],parent['selection']['pilot_case_ids'])
        changed=[]
        for key,case in old.items():
            self.assertEqual(new[key]['classifier_input'],case['classifier_input'],key)
            self.assertEqual(new[key]['input_sha256'],case['input_sha256'],key)
            if new[key]['expected']!=case['expected']:changed.append(key)
        self.assertEqual(changed,['natural.622692011'])
        self.assertEqual(new[changed[0]]['expected']['vegan_reason'],'manufacturer')
        for reading in new[changed[0]]['adjudication']['readings']:
            self.assertEqual(reading['prompt_version'],'blind-evidence-adjudication-v10')
            self.assertEqual(reading['annotation']['expected_reason'],'manufacturer')
            for ref in reading['annotation']['input_refs']:
                policy.resolve_reference(new[changed[0]]['classifier_input'],ref)
        self.assertEqual(current['selection']['development_shortfalls'],{'name':12,'ingredients':1})

    def test_blind_annotation_receives_mechanisms_without_old_reference_labels(self):
        value={'product':{'id':'1','name':'Example Apple Juice','ingredients':'Apple Juice (100%)'},
               'flags':[],'categories':[],'manufacturer_vegan_evidence':[]}
        case={'case_id':'natural.1','classifier_input':value,'input_sha256':classifier.context_hash(value),
              'source_group_keys':[],'external_sources':[],
              'expected':{'old_label':'DO_NOT_LEAK_EXPECTATION'},
              'adjudication':{'rationale':'DO_NOT_LEAK_PREVIOUS_READING'}}
        row={'case_id':case['case_id'],'expected_status':'unknown','expected_reason':None,
             'required_issue_codes':['processing_unresolved'],
             'input_refs':[policy.reference('/product/name','Example Apple Juice')],
             'rationale':'Clarification facts are absent.','external_status':'unknown','source_id':None,
             'external_quote':'','scope_match':'No external product source.',
             'component_assessment':{'status':'established','evidence_refs':[policy.reference('/product/name','Example Apple Juice')]}}
        def fake_call(prompt,schema,**kwargs):
            self.assertNotIn('DO_NOT_LEAK',prompt)
            self.assertIn(policy.CATALOGUE_CONTEXT_POLICY,prompt)
            payload_input=json.loads(prompt[prompt.index('{"catalogue_provenance":'):])
            self.assertEqual(payload_input['catalogue_provenance'],{'retailer':'Ocado','market':'UK'})
            self.assertEqual(payload_input['products'][0]['classifier_input']['product'],value['product'])
            self.assertIn(policy.PRODUCER_ASSERTION_POLICY,prompt)
            self.assertIn('manufacturer evidence even when its subject repeats the vegan SKU designation',prompt)
            self.assertIn('A name mentioned without an assertion, a quoted title, a serving suggestion',prompt)
            for mechanism in policy.RISK_REGISTRY:
                self.assertIn(mechanism['id'],prompt)
                self.assertIn(mechanism['source'],prompt)
            payload={'annotations':[row]}
            kwargs['validator'](payload)
            return {'parsed_response':payload,'call_id':'isolated-test'}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(codex_evidence,'run',side_effect=fake_call):
            self.assertEqual(adjudication.review_batch([case],Path(directory),1,'gpt-6.1-sol','high'),1)

    def test_reference_audit_changes_only_two_references_and_preserves_all_inputs(self):
        root=Path(__file__).resolve().parents[1]/'benchmarks/vegan-classifier-v2'
        previous=json.loads((root/'ethical-cases-v3.json').read_text())
        current=benchmark.load_fixture(root/'ethical-cases-v4.json')
        old={case['case_id']:case for case in previous['cases']};new={case['case_id']:case for case in current['cases']}
        reviewed={'natural.565984011','natural.680838011'}
        for key,case in old.items():
            self.assertEqual(case['classifier_input'],new[key]['classifier_input'],key)
            self.assertEqual(case['input_sha256'],new[key]['input_sha256'],key)
            if key not in reviewed:self.assertEqual(case,new[key],key)
            else:
                self.assertEqual(new[key]['expected']['vegan_status'],'unknown')
                for reading in new[key]['adjudication']['readings']:
                    self.assertEqual(reading['prompt_version'],'blind-evidence-adjudication-v8')
                    for ref in reading['annotation']['input_refs']:policy.resolve_reference(case['classifier_input'],ref)
        self.assertEqual(current['selection']['pilot_case_ids'],previous['selection']['pilot_case_ids'])
        self.assertEqual(len(new)-len(old),9)
        self.assertEqual(current['selection']['counts']['synthetic'],374)
        self.assertEqual({case['case_id']:case for case in assemble.synthetic_cases()},
                         {key:case for key,case in new.items() if case['split']=='synthetic'})

    def test_new_processing_controls_preserve_every_frozen_predecessor_case(self):
        root=Path(__file__).resolve().parents[1]/'benchmarks/vegan-classifier-v2'
        previous=json.loads((root/'ethical-cases-v2.json').read_text())
        current=json.loads((root/'ethical-cases-v3.json').read_text())
        old={case['case_id']:case for case in previous['cases']}
        new={case['case_id']:case for case in current['cases']}
        self.assertEqual(len(new)-len(old),38)
        for key,value in old.items():self.assertEqual(new[key],value,key)
        generated={case['case_id']:case for case in assemble.synthetic_cases() if not case['case_id'].startswith('synthetic.planting-seeds.')}
        controlled={key:case for key,case in new.items() if case['case_kind']=='synthetic'}
        self.assertEqual(generated,controlled)
        self.assertEqual(current['selection']['counts']['synthetic'],365)
        self.assertFalse(current['selection']['target_complete'])

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

    def test_failed_remaining_luna_does_not_automatically_start_an_expensive_primary(self):
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
                {'gpt-6-luna'})
            self.assertIn('gpt-5.6-luna/high',result['excluded_primary_candidates'])

    def test_remaining_cheap_pilot_precedes_expanded_development(self):
        passing={'state':'completed','runs':[{**{field:[] for field in comparison.FAILURE_FIELDS},
            'vegan_recall':1,'per_route_recall':{}}]}
        failed={**passing,'state':'disqualified','unrun_cases':['later']}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);checks=root/'checks.json';checks.write_text('{}')
            args=SimpleNamespace(fixture=root/'fixture.json',checks=checks,output=root/'results',
                workers=1,development_only=True,primary_failure_review=None)
            def execute(args,label,**kwargs):return failed if label.startswith('development-') else passing
            with mock.patch.object(benchmark,'load_fixture',return_value={'selection':{'target_complete':False},'cases':[]}),\
                 mock.patch.object(candidate_checks,'validate_record'),\
                 mock.patch.object(comparison,'execute',side_effect=execute) as run:
                result=comparison.run(args)
            labels=[call.args[1] for call in run.call_args_list]
            self.assertEqual([label.split('-')[0] for label in labels],['controls','pilot','development'])
            self.assertTrue(run.call_args_list[-1].kwargs['stop_on_disqualification'])
            self.assertEqual(result['state'],'primary_failure_review_required')

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

    def test_conditional_sol_development_uses_the_same_failure_stop(self):
        passing={'state':'completed','runs':[{**{field:[] for field in comparison.FAILURE_FIELDS},
            'vegan_recall':1,'per_route_recall':{}}]}
        failed=copy.deepcopy(passing);failed['runs'][0]['reason_errors']=['a']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);checks=root/'checks.json';checks.write_text('{}')
            review=root/'review.json';review.write_text('{}')
            args=SimpleNamespace(fixture=root/'fixture.json',checks=checks,output=root/'results',
                workers=1,development_only=True,primary_failure_review=review)
            def execute(args,label,**kwargs):
                return passing if label in {'controls-sol-primary','pilot-sol-primary'} else failed
            with mock.patch.object(benchmark,'load_fixture',return_value={'selection':{'target_complete':False},'cases':[]}),\
                 mock.patch.object(candidate_checks,'validate_record'),\
                 mock.patch.object(comparison,'diagnostic_fixture'),\
                 mock.patch.object(comparison,'validate_failure_review') as validate,\
                 mock.patch.object(comparison,'execute',side_effect=execute) as run:
                result=comparison.run(args)
            validate.assert_called_once()
            development=[call for call in run.call_args_list if call.args[1]=='development-sol-primary']
            self.assertEqual(len(development),1)
            self.assertTrue(development[0].kwargs['stop_on_disqualification'])
            self.assertEqual(result['state'],'no_eligible_primary')

    def test_control_reuse_cannot_drop_a_failed_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'controls.json';path.write_text(json.dumps({'gpt-5.6-luna/high':'rejected.json'}))
            with self.assertRaisesRegex(ValueError,'all remaining inexpensive'):
                comparison.primary_controls(SimpleNamespace(primary_control_reports=path),{'cases':[]},'gpt-6-luna','high')

    def test_user_excluded_primary_is_bound_to_comparison_manifest(self):
        args=SimpleNamespace(workers=3,development_only=True)
        manifest=comparison.comparison_manifest(args,{'cases':[]},{})
        self.assertEqual(manifest['primary'],[('gpt-6-luna','high')])
        self.assertEqual(manifest['excluded_primary_candidates'],comparison.EXCLUDED_PRIMARY)
        self.assertEqual(comparison.EXCLUDED_PRIMARY['gpt-5.6-luna/high']['decided_on'],'2026-10-08')

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

    def test_disqualification_stops_new_batches_and_cannot_qualify_or_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'fixture.json';cases=self.fixture(path)
            fixture=json.loads(path.read_text())
            for case in fixture['cases']:
                case['split']='development'
                case['expected']={'vegan_status':'unknown','vegan_reason':None,'required_issue_codes':[]}
            path.write_text(json.dumps(fixture))
            args=benchmark.parser().parse_args(['--fixture',str(path),'--split','development','--workers','1',
                '--batch-size','1','--stop-on-disqualification','--output',str(root/'results')])
            with mock.patch.object(benchmark,'execute_batch',wraps=benchmark.execute_batch) as execute:
                report=benchmark.run(args)
                called=execute.call_count
                self.assertEqual(report['state'],'disqualified')
                self.assertGreater(len(report['unrun_cases']),0)
                self.assertLess(called,len(cases))
                self.assertFalse(benchmark.successful(report))
                self.assertFalse(report['release_challenge_passed'])
                self.assertEqual(benchmark.run(args),report)
                self.assertEqual(execute.call_count,called)
            self.assertIn('implementation_hashes',report)

    def test_all_unknown_fails_release_gate(self):
        cases=[];decisions={}
        for index,reason in enumerate(policy.REASONS):
            case={'case_id':str(index),'expected':{'vegan_status':'vegan','vegan_reason':reason}}
            cases.append(case);decisions[str(index)]={'vegan_status':'unknown','vegan_reason':None,'evidence':{},'validation_error':None}
        result=benchmark.metrics(cases,decisions)
        self.assertEqual(result['vegan_recall'],0)
        self.assertFalse(benchmark.acceptance({'split':'locked','runs':[result]*3}))
        self.assertFalse(benchmark.successful({'state':'completed','runs':[result]}))

    def test_conservative_misses_use_recall_without_erasing_control_failures(self):
        cases=[{'case_id':str(index),'mandatory_control':True,
                'expected':{'vegan_status':'vegan','vegan_reason':'manufacturer'}} for index in range(100)]
        decisions={case['case_id']:{'vegan_status':'vegan','vegan_reason':'manufacturer','evidence':{}}
                   for case in cases}
        for misses,passed in [(1,True),(2,True),(3,False)]:
            with self.subTest(misses=misses):
                current=copy.deepcopy(decisions)
                for index in range(misses):
                    current[str(index)].update(vegan_status='unknown',vegan_reason=None)
                result=benchmark.metrics(cases,current)
                self.assertEqual(result['conservative_misses'],[str(index) for index in range(misses)])
                self.assertEqual(result['mismatches'],result['conservative_misses'])
                self.assertFalse(result['mandatory_control_failures'])
                self.assertFalse(result['critical_status_failures'])
                report={'state':'completed','runs':[result]}
                self.assertEqual(benchmark.successful(report),passed)
                self.assertEqual(comparison.development_eligible(report),passed)

    def test_recall_allowance_preserves_all_safety_and_reason_contracts(self):
        cases=[{'case_id':str(index),'mandatory_control':True,
                'expected':{'vegan_status':'vegan','vegan_reason':'manufacturer'}} for index in range(100)]
        decisions={case['case_id']:{'vegan_status':'vegan','vegan_reason':'manufacturer','evidence':{}}
                   for case in cases}
        for expected,actual,reason in [('unknown','vegan','manufacturer'),('nonvegan','vegan','manufacturer'),
                                       ('nonvegan','unknown',None),('vegan','nonvegan',None),('vegan','vegan','name')]:
            with self.subTest(expected=expected,actual=actual,reason=reason):
                current_cases=copy.deepcopy(cases);current=copy.deepcopy(decisions)
                current_cases[0]['expected']={'vegan_status':expected,
                    'vegan_reason':'manufacturer' if expected=='vegan' else None}
                current['0'].update(vegan_status=actual,vegan_reason=reason)
                result=benchmark.metrics(current_cases,current)
                self.assertFalse(benchmark.successful({'state':'completed','runs':[result]}))
                self.assertFalse(comparison.development_eligible({'state':'completed','runs':[result]}))
        result=benchmark.metrics(cases,decisions)
        for field in ('operational_errors','missing_cases','missing_required_issues','reportable_negative_errors'):
            broken=copy.deepcopy(result);broken[field]=['0']
            self.assertFalse(benchmark.successful({'state':'completed','runs':[broken]}),field)

    def test_per_route_recall_and_historical_results_remain_explicit(self):
        cases=[];decisions={}
        for reason in policy.REASONS:
            for index in range(30):
                key=reason+str(index)
                cases.append({'case_id':key,'mandatory_control':True,
                              'expected':{'vegan_status':'vegan','vegan_reason':reason}})
                decisions[key]={'vegan_status':'vegan','vegan_reason':reason,'evidence':{}}
        decisions['manufacturer0'].update(vegan_status='unknown',vegan_reason=None)
        result=benchmark.metrics(cases,decisions)
        self.assertTrue(benchmark.successful({'state':'completed','runs':[result]}))
        old=copy.deepcopy(result);old.pop('acceptance_rubric_version');old.pop('critical_status_failures')
        self.assertFalse(benchmark.successful({'state':'completed','runs':[old]}))
        decisions['manufacturer1'].update(vegan_status='unknown',vegan_reason=None)
        result=benchmark.metrics(cases,decisions)
        self.assertGreater(result['vegan_recall'],.98)
        self.assertFalse(benchmark.successful({'state':'completed','runs':[result]}))

    def test_current_defaults_choose_the_approved_model_and_fixture(self):
        args=benchmark.parser().parse_args(['--output','unused'])
        self.assertEqual(args.model,classifier.DEFAULT_CODEX_MODEL)
        self.assertEqual(args.model,'gpt-6-luna')
        self.assertEqual(args.effort,'high')
        self.assertEqual(args.arbitration_model,'gpt-6.1-sol')
        self.assertEqual(args.arbitration_effort,'medium')
        self.assertEqual(args.fixture.name,'ethical-cases-v29.json')

    def test_locked_gate_budgets_only_conservative_misses(self):
        cases=[];decisions={}
        for reason in policy.REASONS:
            for index in range(30):
                key=reason+str(index)
                cases.append({'case_id':key,'mandatory_control':True,
                              'expected':{'vegan_status':'vegan','vegan_reason':reason}})
                decisions[key]={'vegan_status':'vegan','vegan_reason':reason,'evidence':{}}
        for status,count in [('unknown',180),('nonvegan',60)]:
            for index in range(count):
                key=status+str(index)
                cases.append({'case_id':key,'expected':{'vegan_status':status,'vegan_reason':None}})
                decisions[key]={'vegan_status':status,'vegan_reason':None,'evidence':{}}
        for misses,passed in [(['manufacturer0'],True),(['manufacturer0','name0'],True),
                              (['manufacturer0','manufacturer1'],False),
                              (['manufacturer0','name0','ingredients0'],False)]:
            current=copy.deepcopy(decisions)
            for key in misses:current[key].update(vegan_status='unknown',vegan_reason=None)
            result=benchmark.metrics(cases,current)
            self.assertEqual(benchmark.acceptance({'split':'locked','runs':[result]*3}),passed,misses)
        current=copy.deepcopy(decisions);current['nonvegan0'].update(vegan_status='unknown')
        result=benchmark.metrics(cases,current)
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
