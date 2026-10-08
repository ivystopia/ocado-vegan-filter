#!/usr/bin/env python3
"""Run the bounded, resumable model comparison without changing defaults/data.

Only included Codex allowance is used. A recommendation is an output artifact,
not permission to activate it. Locked evaluation never drives automatic tuning.
"""
import argparse
from collections import Counter
from pathlib import Path
import json
import random

import benchmark_whole_product as benchmark
import classify_ocado_vegan as classifier
import codex_evidence
import vegan_policy as policy
import validate_whole_product_candidate as candidate_checks

PRIMARY = [('gpt-6-luna','high')]
EXCLUDED_PRIMARY = {'gpt-5.6-luna/high': {
    'decision': 'User rejected further Luna 5.6 evaluation after retained reliability failures.',
    'decided_on': '2026-10-08'}}
ARBITRATION = [('gpt-6.1-sol','medium'),('gpt-6.1-sol','high'),('gpt-6-astra','medium')]
# Standard-service credit equivalents, checked 2026-10-06. These are NOT
# included-plan quota rates or charges. Reasoning is already in output tokens.
RATES = {'gpt-5.6-luna':(5,.5,30),'gpt-6-luna':(2.5,.25,12.5),
         'gpt-6.1-sol':(50,2.5,250),'gpt-6-astra':(250,25,1250)}
PRICING_SOURCE = 'https://learn.chatgpt.com/docs/pricing'


def credit_equivalent(report):
    total=0
    for key,usage in report.get('usage_by_stage_model',{}).items():
        model=key.split(':')[1];input_rate,cached_rate,output_rate=RATES[model]
        cached=usage['cached_input_tokens']
        total+=((usage['input_tokens']-cached)*input_rate+cached*cached_rate+usage['output_tokens']*output_rate)/1_000_000
    return total


def development_eligible(report):
    return bool(benchmark.successful(report) and all(
        run['vegan_recall'] is not None and run['vegan_recall']>=.98 for run in report['runs']))


FAILURE_FIELDS=('unsafe_positives','operational_errors','missing_cases','mismatches','reason_errors',
                'mandatory_control_failures','reportable_negative_errors','missing_required_issues','false_nonvegan_decisions')


def failed_report(entry):
    controls=entry.get('controls')
    if controls and not benchmark.successful(controls):return controls
    return entry.get('development',entry['pilot'])


def stage_reuse_mapping(args):
    path=getattr(args,'primary_stage_reports',None)
    if not path:return {}
    if getattr(args,'primary_control_reports',None):
        raise ValueError('Use either primary stage reuse or control-only reuse, not both')
    mapping=json.loads(path.read_text())
    names={model+'/'+effort for model,effort in PRIMARY}
    if not mapping or not set(mapping)<=names or any(set(stages)!={'controls','pilot'} for stages in mapping.values()):
        raise ValueError('Stage reuse must preserve controls AND pilot for each supplied inexpensive candidate')
    return mapping


def validated_stage_report(path, fixture, args, model, effort, stage):
    """Runner-only changes may reuse observations, never changed decisions.

    Check all production/persistence/export modules and the exact evidence,
    prompts, schema and call settings. Keep failed reports as failed reports.
    New runner/tests still require their own current preflight manifest.
    """
    report=json.loads(Path(path).read_text())
    split,subset=('synthetic','all') if stage=='controls' else ('development','pilot')
    cases=[case for case in fixture['cases'] if case['split']==split]
    if subset=='pilot':
        selected=set(fixture.get('selection',{}).get('pilot_case_ids',[]))
        cases=[case for case in cases if case['case_id'] in selected]
    expected={'fixture_sha256':classifier.context_hash(fixture),'selected_case_hash':classifier.context_hash(cases),
        'state':'completed','split':split,'subset':subset,'mode':'pipeline','model':model,'effort':effort,
        'policy_version':policy.POLICY_VERSION,'knowledge_version':policy.KNOWLEDGE_VERSION,
        'prompt_version':classifier.PROMPT_VERSION,'arbitration_prompt_version':classifier.ARBITRATION_PROMPT_VERSION,
        'arbitration_model':classifier.ARBITRATION_MODEL,'arbitration_effort':classifier.ARBITRATION_REASONING_EFFORT,
        'schema_sha256':classifier.context_hash(classifier.CODEX_OUTPUT_SCHEMA),
        'workers':args.workers,'batch_size':8,'repetitions':1,'seed':20261006}
    if not cases or any(report.get(key)!=value for key,value in expected.items()):
        raise ValueError('Reused stage has different inputs/configuration or is incomplete')
    required={'vegan_policy.py','classify_ocado_vegan.py','codex_evidence.py','benchmark_whole_product.py','manufacturer_evidence.py','update_userscript_allowlists.py'}
    if set(report.get('implementation_hashes',{}))!=required or any(classifier.context_hash((benchmark.ROOT/'tools'/name).read_text())!=value
            for name,value in report['implementation_hashes'].items()):
        raise ValueError('Reused stage has different classification/persistence/export code')
    if len(report.get('runs',[]))!=1 or report['runs'][0].get('cases')!=len(cases) or report.get('usage',{}).get('missing_usage_calls')!=0:
        raise ValueError('Reused stage lacks complete case/call accounting')
    return report


def primary_controls(args, fixture, model, effort):
    stages=stage_reuse_mapping(args).get(model+'/'+effort)
    if stages:return validated_stage_report(stages['controls'],fixture,args,model,effort,'controls')
    paths=getattr(args,'primary_control_reports',None)
    if not paths:
        return execute(args,'controls-'+model+'-'+effort,split='synthetic',mode='pipeline',model=model,effort=effort)
    mapping=json.loads(paths.read_text())
    if set(mapping)!={name+'/'+level for name,level in PRIMARY}:
        raise ValueError('Control reuse must retain all remaining inexpensive candidates, including failures')
    report=json.loads(Path(mapping[model+'/'+effort]).read_text())
    cases=[case for case in fixture['cases'] if case['split']=='synthetic']
    expected={'selected_case_hash':classifier.context_hash(cases),'state':'completed','split':'synthetic','mode':'pipeline',
        'model':model,'effort':effort,'policy_version':policy.POLICY_VERSION,'knowledge_version':policy.KNOWLEDGE_VERSION,
        'prompt_version':classifier.PROMPT_VERSION,'arbitration_prompt_version':classifier.ARBITRATION_PROMPT_VERSION,
        'arbitration_model':classifier.ARBITRATION_MODEL,'arbitration_effort':classifier.ARBITRATION_REASONING_EFFORT,
        'schema_sha256':classifier.context_hash(classifier.CODEX_OUTPUT_SCHEMA)}
    if any(report.get(key)!=value for key,value in expected.items()):raise ValueError('Reused controls have different inputs/configuration')
    required={'vegan_policy.py','classify_ocado_vegan.py','codex_evidence.py','benchmark_whole_product.py','manufacturer_evidence.py','update_userscript_allowlists.py'}
    if set(report.get('implementation_hashes',{}))!=required or any(classifier.context_hash((benchmark.ROOT/'tools'/name).read_text())!=value
            for name,value in report['implementation_hashes'].items()):
        raise ValueError('Reused controls have different classification/persistence/export code')
    return report


def primary_pilot(args, fixture, model, effort):
    stages=stage_reuse_mapping(args).get(model+'/'+effort)
    if stages:return validated_stage_report(stages['pilot'],fixture,args,model,effort,'pilot')
    return execute(args,'pilot-'+model+'-'+effort,split='development',subset='pilot',mode='pipeline',model=model,effort=effort)


def diagnostic_fixture(fixture, report, output):
    failed={case_id for run in report['runs'] for field in FAILURE_FIELDS for case_id in run[field]}
    cases=[case for case in fixture.get('cases',[]) if case['case_id'] in failed]
    if not failed or {case['case_id'] for case in cases}!=failed:
        raise ValueError('Every diagnostic failure needs its original frozen case')
    result={**fixture,'cases':[{**case,'split':'diagnostic'} for case in cases],
        'diagnostic_selection':{'source_fixture_sha256':classifier.context_hash(fixture),
            'failed_report_sha256':classifier.context_hash(report),'case_ids':sorted(failed),
            'qualification_evaluation':False}}
    benchmark.save(output,result)
    return result


def validate_failure_review(review, entries, fixture):
    """No expensive primary on shared code/reference/scope failures.

    A structured supervising review is required, not just two failed scores.
    Citations and hashes are validated here; attribution remains a review.
    """
    if review.get('policy_version')!=policy.POLICY_VERSION or review.get('candidate_hashes')!=benchmark.candidate_hashes():
        raise ValueError('Primary failure review is for another policy or implementation')
    if review.get('fixture_sha256')!=classifier.context_hash(fixture):
        raise ValueError('Primary failure review is for another frozen fixture')
    if not all(review.get(key) is True for key in ('policy_settled','implementation_verified','references_verified')):
        raise ValueError('Resolve policy, implementation and reference problems before expensive-primary comparison')
    cases={case['case_id']:case for case in fixture['cases'] if case['split'] in {'development','synthetic'}}
    expected_names={model+'/'+effort for model,effort in PRIMARY}
    if set(review.get('models',{}))!=expected_names:
        raise ValueError('Failure review must cover all remaining inexpensive primary candidates')
    for name in expected_names:
        entry=entries[name]
        report=failed_report(entry)
        item=review['models'][name]
        if (development_eligible(report) and benchmark.successful(report)) or item.get('report_sha256')!=classifier.context_hash(report):
            raise ValueError('Failure review does not match a failed candidate report')
        failed={case_id for run in report['runs'] for field in FAILURE_FIELDS for case_id in run[field]}
        if not failed or set(item.get('cases',{}))!=failed:
            raise ValueError('Every failed case must have an explicit attribution; no unaccounted recall failure')
        for case_id,attribution in item['cases'].items():
            if attribution.get('cause') not in {'model_reasoning','model_protocol'}:
                raise ValueError('Code, reference, policy, quota and input failures cannot trigger expensive-primary fallback')
            if not attribution.get('rationale','').strip() or not attribution.get('evidence_refs'):
                raise ValueError('Failure attribution needs rationale and frozen-input evidence')
            for ref in attribution['evidence_refs']:
                policy.resolve_reference(cases[case_id]['classifier_input'],ref)


def comparison_manifest(args, fixture, checks):
    control_paths=getattr(args,'primary_control_reports',None)
    stage_paths=stage_reuse_mapping(args)
    return {'fixture_sha256':classifier.context_hash(fixture),'candidate_hashes':benchmark.candidate_hashes(),
            'acceptance_rubric_version':benchmark.ACCEPTANCE_RUBRIC_VERSION,
            'checks_sha256':classifier.context_hash(checks),'workers':args.workers,
            'reused_controls':None if not control_paths else {key:classifier.context_hash(json.loads(Path(path).read_text()))
                for key,path in json.loads(control_paths.read_text()).items()},
            'reused_primary_stages':{name:{stage:classifier.context_hash(json.loads(Path(path).read_text()))
                for stage,path in stages.items()} for name,stages in stage_paths.items()},
            'development_only':getattr(args,'development_only',False),'primary':PRIMARY,
            'excluded_primary_candidates':EXCLUDED_PRIMARY,'arbitration':ARBITRATION}


def arbitration_fixture(fixture, primary_reports, output):
    cases=[case for case in fixture['cases'] if case['split']=='development']
    selected=set();rng=random.Random(20261008)
    for route,count in {'tagged':6,'manufacturer':6,'name':6,'ingredients':6,'unknown':48,'nonvegan':24}.items():
        available=[case for case in cases if (case['expected']['vegan_reason'] or case['expected']['vegan_status'])==route]
        rng.shuffle(available)
        if len(available)<count:raise ValueError('Arbitration contrasts lack required reference classes')
        selected.update(case['case_id'] for case in available[:count])
    for report in primary_reports:
        for run in report.get('runs',[]):selected.update(run['primary_disagreements'])
    result={**fixture,'cases':[{**case,'split':'arbitration'} for case in cases if case['case_id'] in selected]}
    benchmark.save(output,result)
    return result


def execute(args, label, **options):
    candidate_checks.validate_record(json.loads(args.checks.read_text()))
    values=['--fixture',str(options.pop('fixture',args.fixture)),'--output',str(args.output/label),
            '--workers',str(args.workers),'--stop-file',str(args.output/'STOP')]
    for key,value in options.items():
        if isinstance(value,bool):
            if value:values.append('--'+key.replace('_','-'))
        else:values += ['--'+key.replace('_','-'),str(value)]
    result=benchmark.run(benchmark.parser().parse_args(values))
    if result['state']=='checkpointed':raise codex_evidence.StopRequestedError(result['error'])
    if result['state']=='quota_exhausted':raise codex_evidence.UsageLimitError(result['error'])
    if result['state'] not in {'completed','disqualified'}:raise RuntimeError('Benchmark did not complete: '+label)
    return result


def run(args):
    fixture=benchmark.load_fixture(args.fixture)
    diagnostic_only=getattr(args,'development_only',False)
    if not fixture.get('selection',{}).get('target_complete') and not diagnostic_only:
        raise ValueError('Complete adjudicated development/locked targets are required; --development-only can diagnose a disclosed shortfall')
    checks_path=getattr(args,'checks',None)
    if not checks_path:raise ValueError('A successful current --checks manifest is required before model comparison')
    checks=json.loads(checks_path.read_text());candidate_checks.validate_record(checks)
    args.output.mkdir(parents=True,exist_ok=True)
    manifest=comparison_manifest(args,fixture,checks)
    manifest_path=args.output/'comparison-manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text())!=json.loads(json.dumps(manifest)):
        raise ValueError('Comparison configuration/code/checks changed; use a fresh output directory')
    benchmark.save(manifest_path,manifest)
    result={'state':'running','pricing_source':PRICING_SOURCE,'pricing_checked':'2026-10-06',
            'acceptance_rubric_version':benchmark.ACCEPTANCE_RUBRIC_VERSION,
            'credit_equivalents_are_not_included_quota_or_charges':True,'defaults_changed':False,
            'reused_primary_stages':manifest['reused_primary_stages'],
            'excluded_primary_candidates':manifest['excluded_primary_candidates'],
            'development_only':diagnostic_only,'reference_shortfalls':{
                key:fixture.get('selection',{}).get(key,{}) for key in ('development_shortfalls','locked_shortfalls')},
            'primary':{},'arbitration':{},'locked':{},'workload':{},'recommendation':None}
    try:
        reports=[];eligible=[];pilot_candidates=[]
        for model,effort in PRIMARY:
            name=model+'/'+effort;slug=model+'-'+effort
            controls=primary_controls(args,fixture,model,effort)
            pilot=primary_pilot(args,fixture,model,effort)
            # Diagnostic failures use the same blind evidence and final gate;
            # do not repeat already passing natural cases to attribute errors.
            entry={'controls':controls,'pilot':pilot};result['primary'][name]=entry
            if not development_eligible(pilot):
                diagnostic_path=args.output/('diagnostic-fixture-'+slug+'.json')
                diagnostic_fixture(fixture,pilot,diagnostic_path)
                diagnostic=execute(args,'diagnostic-'+slug,fixture=diagnostic_path,split='diagnostic',mode='primary',model=model,effort=effort)
                entry['model_only_diagnostic']=diagnostic
            if benchmark.successful(controls) and development_eligible(pilot):
                pilot_candidates.append((model,effort))
        # Qualify remaining inexpensive pilots before a larger stage.
        for model,effort in pilot_candidates:
            development=execute(args,'development-'+model+'-'+effort,split='development',mode='pipeline',
                                model=model,effort=effort,stop_on_disqualification=True)
            result['primary'][model+'/'+effort]['development']=development;reports.append(development)
            if development_eligible(development):eligible.append((model,effort,development))
        if not eligible:
            review_path=getattr(args,'primary_failure_review',None)
            if not review_path:
                result.update(state='primary_failure_review_required',next_step='Attribute remaining inexpensive candidates\' failures to policy, code, references or models. No expensive-primary calls were started; defaults remain unchanged.')
                return result
            review=json.loads(review_path.read_text())
            validate_failure_review(review,result['primary'],fixture)
            result['primary_failure_review_sha256']=classifier.context_hash(review)
            model,effort='gpt-6.1-sol','medium';name=model+'/'+effort
            controls=execute(args,'controls-sol-primary',split='synthetic',mode='pipeline',model=model,effort=effort)
            pilot=execute(args,'pilot-sol-primary',split='development',subset='pilot',mode='pipeline',model=model,effort=effort)
            result['primary'][name]={'controls':controls,'pilot':pilot,'conditional_fallback':'Remaining inexpensive candidates failed attributed development gates; user-excluded candidates were not called.'}
            if benchmark.successful(controls) and development_eligible(pilot):
                development=execute(args,'development-sol-primary',split='development',mode='pipeline',
                                    model=model,effort=effort,stop_on_disqualification=True)
                result['primary'][name]['development']=development;reports.append(development)
                if development_eligible(development):eligible.append((model,effort,development))
        if not eligible:
            result.update(state='no_eligible_primary',next_step='Investigate development failures; retain defaults and canonical labels unchanged.')
            return result
        arb_path=args.output/'arbitration-fixture.json';arbitration_fixture(fixture,reports,arb_path)
        arbiters=[]
        for model,effort in ARBITRATION:
            report=execute(args,'arbitration-'+model+'-'+effort,fixture=arb_path,split='arbitration',mode='arbitration',model=model,effort=effort)
            result['arbitration'][model+'/'+effort]=report
            if development_eligible(report):arbiters.append((model,effort,report))
        if not arbiters:
            result.update(state='no_eligible_arbitration',next_step='Investigate development failures; no activation.')
            return result
        primary=min(eligible,key=lambda item:credit_equivalent(item[2]['runs'][0]))
        arb=min(arbiters,key=lambda item:credit_equivalent(item[2]['runs'][0]))
        pair=(primary[0],primary[1],arb[0],arb[1]);baseline=('gpt-6-luna','high','gpt-6.1-sol','medium')
        paired_controls=execute(args,'controls-selected-pair',split='synthetic',mode='pipeline',model=pair[0],effort=pair[1],
            arbitration_model=pair[2],arbitration_effort=pair[3],repetitions=3)
        result['paired_controls']=paired_controls
        if not benchmark.successful(paired_controls):
            result.update(state='selected_pair_failed_controls',next_step='Investigate retained fixed-pair failures; no default change or activation.')
            return result
        if diagnostic_only:
            result.update(state='development_comparison_completed',recommendation={
                'primary_model':pair[0],'primary_effort':pair[1],'arbitration_model':pair[2],'arbitration_effort':pair[3],
                'development_preference_only':True,'locked_gates_passed':False,'activation_authorised':False,
                'remaining_gate':'Resolve/disclose natural reference shortfalls, locked paired validation, workload, user model selection, shadow migration and 600-positive audit.'})
            return result
        for label,chosen in [('candidate',pair),('baseline',baseline)]:
            if label=='baseline' and pair==baseline:
                result['locked'][label]={'same_as':'candidate'};continue
            report=execute(args,'locked-'+label,split='locked',mode='pipeline',model=chosen[0],effort=chosen[1],
                arbitration_model=chosen[2],arbitration_effort=chosen[3],repetitions=3)
            result['locked'][label]=report
            result['workload'][label]=execute(args,'workload-'+label,split='workload',mode='pipeline',model=chosen[0],effort=chosen[1],
                arbitration_model=chosen[2],arbitration_effort=chosen[3])
        passed=result['locked']['candidate']['release_challenge_passed']
        result['recommendation']={'primary_model':pair[0],'primary_effort':pair[1],'arbitration_model':pair[2],'arbitration_effort':pair[3],
            'locked_gates_passed':passed,'activation_authorised':False,
            'remaining_gate':'User model-pair selection, fixed shadow migration and random 600-positive audit.'}
        result['state']='comparison_completed' if passed else 'candidate_failed_locked_gates'
    except codex_evidence.StopRequestedError as exc:
        result.update(state='checkpointed',error=str(exc),next_step='Remove STOP and resume the identical command, or choose a fresh directory if code changes.')
    except codex_evidence.UsageLimitError as exc:
        result.update(state='quota_exhausted',error=str(exc),next_step='Resume the identical command after included-plan reset; no paid fallback.')
    except Exception as exc:
        result.update(state='failed',error=str(exc),next_step='Investigate retained diagnostics; use a new output directory if the implementation changes.')
        raise
    finally:
        benchmark.save(args.output/'comparison.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fixture',type=Path,default=benchmark.FIXTURE)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=3)
    p.add_argument('--development-only',action='store_true',help='Run development/model diagnostics without locked evaluation or a release recommendation')
    p.add_argument('--checks',type=Path,required=True,help='Current passing validate_whole_product_candidate.py checks.json')
    p.add_argument('--primary-failure-review',type=Path,help='Frozen-input attribution of remaining inexpensive candidates; never required when one qualifies')
    p.add_argument('--primary-control-reports',type=Path,help='JSON model/effort-to-report paths; reuse only identical controlled inputs and classification code, retaining failures')
    p.add_argument('--primary-stage-reports',type=Path,help='JSON model/effort to controls/pilot report paths; preserve both stages including failures, with exact production code/inputs/settings')
    result=run(p.parse_args());print(json.dumps({key:value for key,value in result.items() if key not in {'primary','arbitration','locked','workload'}},indent=2))
    raise SystemExit(0 if result['state'] in {'comparison_completed','development_comparison_completed'} else 1)
