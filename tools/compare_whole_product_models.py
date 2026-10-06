#!/usr/bin/env python3
"""Run the bounded, resumable model comparison without changing defaults/data.

Only included Codex allowance is used. A recommendation is an output artifact,
not permission to activate it. Locked evaluation never drives automatic tuning.
"""
import argparse
from collections import Counter
from pathlib import Path
import json
import math
import random

import benchmark_whole_product as benchmark
import classify_ocado_vegan as classifier
import codex_evidence
import vegan_policy as policy
import validate_whole_product_candidate as candidate_checks

PRIMARY = [('gpt-5.6-luna','high'),('gpt-6-luna','high')]
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
    return bool(report['state']=='completed' and all(not run['unsafe_positives'] and not run['operational_errors']
        and not run['missing_cases'] and not run['reason_errors'] and not run['mandatory_control_failures']
        and not run['reportable_negative_errors'] and not run['missing_required_issues']
        and not run['false_nonvegan_decisions']
        and run['vegan_recall'] is not None and run['vegan_recall']>=.98
        and all(value['recovered']>=math.ceil(.966*value['eligible']) for value in run['per_route_recall'].values())
        for run in report['runs']))


FAILURE_FIELDS=('unsafe_positives','operational_errors','missing_cases','mismatches','reason_errors',
                'mandatory_control_failures','reportable_negative_errors','missing_required_issues','false_nonvegan_decisions')


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
    cases={case['case_id']:case for case in fixture['cases'] if case['split']=='development'}
    expected_names={model+'/'+effort for model,effort in PRIMARY}
    if set(review.get('models',{}))!=expected_names:
        raise ValueError('Failure review must cover both inexpensive primary candidates')
    for name in expected_names:
        entry=entries[name]
        report=entry.get('development',entry['pilot'])
        item=review['models'][name]
        if development_eligible(report) or item.get('report_sha256')!=classifier.context_hash(report):
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
    return {'fixture_sha256':classifier.context_hash(fixture),'candidate_hashes':benchmark.candidate_hashes(),
            'checks_sha256':classifier.context_hash(checks),'workers':args.workers,
            'development_only':getattr(args,'development_only',False),'primary':PRIMARY,'arbitration':ARBITRATION}


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
    for key,value in options.items():values += ['--'+key.replace('_','-'),str(value)]
    result=benchmark.run(benchmark.parser().parse_args(values))
    if result['state']=='checkpointed':raise codex_evidence.StopRequestedError(result['error'])
    if result['state']=='quota_exhausted':raise codex_evidence.UsageLimitError(result['error'])
    if result['state']!='completed':raise RuntimeError('Benchmark did not complete: '+label)
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
            'credit_equivalents_are_not_included_quota_or_charges':True,'defaults_changed':False,
            'development_only':diagnostic_only,'reference_shortfalls':{
                key:fixture.get('selection',{}).get(key,{}) for key in ('development_shortfalls','locked_shortfalls')},
            'primary':{},'arbitration':{},'locked':{},'workload':{},'recommendation':None}
    try:
        reports=[];eligible=[]
        for model,effort in PRIMARY:
            name=model+'/'+effort;slug=model+'-'+effort
            pilot=execute(args,'pilot-'+slug,split='development',subset='pilot',mode='pipeline',model=model,effort=effort)
            # Model-only diagnostic uses the same blind inputs and final gate.
            entry={'pilot':pilot};result['primary'][name]=entry
            if not development_eligible(pilot):
                diagnostic=execute(args,'diagnostic-'+slug,split='development',subset='pilot',mode='primary',model=model,effort=effort)
                entry['model_only_diagnostic']=diagnostic
            if development_eligible(pilot):
                development=execute(args,'development-'+slug,split='development',mode='pipeline',model=model,effort=effort)
                entry['development']=development;reports.append(development)
                if development_eligible(development):eligible.append((model,effort,development))
        if not eligible:
            review_path=getattr(args,'primary_failure_review',None)
            if not review_path:
                result.update(state='primary_failure_review_required',next_step='Attribute both inexpensive candidates\' failures to policy, code, references or models. No expensive-primary calls were started; defaults remain unchanged.')
                return result
            review=json.loads(review_path.read_text())
            validate_failure_review(review,result['primary'],fixture)
            result['primary_failure_review_sha256']=classifier.context_hash(review)
            model,effort='gpt-6.1-sol','medium';name=model+'/'+effort
            pilot=execute(args,'pilot-sol-primary',split='development',subset='pilot',mode='pipeline',model=model,effort=effort)
            result['primary'][name]={'pilot':pilot,'conditional_fallback':'both Luna candidates failed development gates'}
            if development_eligible(pilot):
                development=execute(args,'development-sol-primary',split='development',mode='pipeline',model=model,effort=effort)
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
        pair=(primary[0],primary[1],arb[0],arb[1]);baseline=('gpt-5.6-luna','high','gpt-6.1-sol','medium')
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
    p.add_argument('--primary-failure-review',type=Path,help='Frozen-input attribution of both Luna failures; never required when a Luna qualifies')
    result=run(p.parse_args());print(json.dumps({key:value for key,value in result.items() if key not in {'primary','arbitration','locked','workload'}},indent=2))
    raise SystemExit(0 if result['state'] in {'comparison_completed','development_comparison_completed'} else 1)
