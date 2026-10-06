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
        and run['vegan_recall'] is not None and run['vegan_recall']>=.98
        and all(value['recovered']>=math.ceil(.966*value['eligible']) for value in run['per_route_recall'].values())
        for run in report['runs']))


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
    values=['--fixture',str(options.pop('fixture',args.fixture)),'--output',str(args.output/label),
            '--workers',str(args.workers)]
    for key,value in options.items():values += ['--'+key.replace('_','-'),str(value)]
    result=benchmark.run(benchmark.parser().parse_args(values))
    if result['state']=='quota_exhausted':raise codex_evidence.UsageLimitError(result['error'])
    if result['state']!='completed':raise RuntimeError('Benchmark did not complete: '+label)
    return result


def run(args):
    fixture=benchmark.load_fixture(args.fixture)
    diagnostic_only=getattr(args,'development_only',False)
    if not fixture.get('selection',{}).get('target_complete') and not diagnostic_only:
        raise ValueError('Complete adjudicated development/locked targets are required; --development-only can diagnose a disclosed shortfall')
    args.output.mkdir(parents=True,exist_ok=True)
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
            diagnostic=execute(args,'diagnostic-'+slug,split='development',subset='pilot',mode='primary',model=model,effort=effort)
            entry={'pilot':pilot,'model_only_diagnostic':diagnostic};result['primary'][name]=entry
            if development_eligible(pilot):
                development=execute(args,'development-'+slug,split='development',mode='pipeline',model=model,effort=effort)
                entry['development']=development;reports.append(development)
                if development_eligible(development):eligible.append((model,effort,development))
        if not eligible:
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
    result=run(p.parse_args());print(json.dumps({key:value for key,value in result.items() if key not in {'primary','arbitration','locked','workload'}},indent=2))
    raise SystemExit(0 if result['state'] in {'comparison_completed','development_comparison_completed'} else 1)
