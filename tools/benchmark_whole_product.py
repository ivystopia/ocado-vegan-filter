#!/usr/bin/env python3
"""Checkpointed whole-product benchmark: frozen evidence, real production gates.

Never opens the live catalogue. Gold/adjudication fields are never passed to a
model. A blocked/disputed annotation is not silently scored as gold.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import statistics

import build_ocado_database as builder
import classify_ocado_vegan as classifier
import codex_evidence as calls
import manufacturer_evidence as manufacturer
import vegan_policy as policy
import update_userscript_allowlists as exporter

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'benchmarks/vegan-classifier-v2/cases.json'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=True,indent=2)+'\n')
    temporary.replace(path)


def load_fixture(path):
    fixture = json.loads(path.read_text())
    if fixture.get('schema_version') != 2 or fixture.get('policy_version') != policy.POLICY_VERSION:
        raise ValueError('Fixture policy/schema does not match the candidate')
    seen, groups, product_ids = set(), {}, set()
    for case in fixture['cases']:
        case_id = case['case_id']
        if case_id in seen or classifier.context_hash(case['classifier_input']) != case['input_sha256']:
            raise ValueError(f'Duplicate or altered frozen case: {case_id}')
        seen.add(case_id)
        product_key = (case['split'],case['classifier_input']['product']['id'])
        if product_key in product_ids:
            raise ValueError(f'Duplicate product within split: {case_id}')
        product_ids.add(product_key)
        expected = case.get('expected')
        if expected and (expected['vegan_status'] not in classifier.VALID_STATUSES or
                         ((expected.get('vegan_reason') in policy.REASONS) != (expected['vegan_status']=='vegan'))):
            raise ValueError(f'Invalid expected decision: {case_id}')
        if case['case_kind']=='natural':
            group = case['leakage_group']
            if group in groups and groups[group]!=case['split']:
                raise ValueError(f'Natural evidence/family leakage: {group}')
            groups[group] = case['split']
    return fixture


def fixture_database(cases):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    builder.create_schema(conn)
    classifier.ensure_classification_schema(conn)
    manufacturer.ensure_schema(conn)
    columns = classifier.table_columns(conn,'products')
    for case in cases:
        context = case['classifier_input']
        product = {key:value for key,value in context['product'].items() if key in columns}
        conn.execute('INSERT INTO products ('+','.join(product)+') VALUES ('+','.join('?' for _ in product)+')',tuple(product.values()))
        product_id = product['id']
        for flag in context['flags']:
            conn.execute('INSERT OR IGNORE INTO product_flags(product_id,flag) VALUES (?,?)',(product_id,flag))
        for category in context.get('categories',[]):
            conn.execute('INSERT OR IGNORE INTO product_categories(product_id,category_path) VALUES (?,?)',(product_id,category))
        for row in context['manufacturer_vegan_evidence']:
            if row.get('source_kind')!='manufacturer_website':
                conn.execute('INSERT INTO manufacturer_vegan_evidence(product_id,field_title,content) VALUES (?,?,?)',
                             (product_id,row['field_title'],row['content']))
        catalogue = classifier.product_context_for_llm(classifier.load_product_context(conn,product_id))
        catalogue_hash=classifier.context_hash(catalogue)
        binding = manufacturer.binding_hash(catalogue,product.get('pack_size'),**{key:product.get(key) for key in manufacturer.PUBLISHER_FIELDS})
        for row in context.get('manufacturer_source_candidates',[]):
            conn.execute('INSERT OR IGNORE INTO manufacturer_website_sources VALUES (?,?,?,?,?,?,?,?)',
                (row['source_id'],row['source_url'],'Fixture retained page',row['retrieved_at'],row['official_identity'],row['market_scope'],row['source_sha256'],row['source_text']))
        grouped={}
        for row in context.get('manufacturer_source_candidates',[]):
            grouped.setdefault(row['association_status'],[]).append(row['source_id'])
        for index,(state,source_ids) in enumerate(grouped.items(),1):
            conn.execute('INSERT INTO manufacturer_research_products VALUES (?,?,?,?,?,?,?,?)',
                (-index,product_id,0,catalogue_hash if state=='catalogue_match' else 'fixture.old-catalogue','unverified','unverified',json.dumps({'source_ids':source_ids}),None))
        for row in context['manufacturer_vegan_evidence']:
            if row.get('source_kind')=='manufacturer_website':
                source_id = next((source['source_id'] for source in context.get('manufacturer_source_candidates',[]) if
                    source['source_url']==row['source_url'] and source['source_sha256']==row['source_sha256']),
                    classifier.context_hash([row.get('source_url'),row.get('source_sha256'),row['content']]))
                conn.execute('INSERT OR IGNORE INTO manufacturer_website_sources VALUES (?,?,?,?,?,?,?,?)',
                    (source_id,row['source_url'],'Fixture retained claim',row['retrieved_at'],row.get('official_identity','Verified publisher'),row.get('market_scope','Applicable frozen market'),row['source_sha256'],row.get('source_text',row['content'])))
                conn.execute('INSERT INTO manufacturer_website_evidence (run_id,product_id,source_id,catalogue_binding_hash,statement,scope_match,accepted_at,assertion_status,review_policy_version) VALUES (?,?,?,?,?,?,?,?,?)',
                    (int(row.get('research_run_id',0)),product_id,source_id,binding,row['content'],row['scope_match'],row['retrieved_at'],row.get('verified_status','vegan'),row.get('review_policy_version')))
        actual = classifier.product_context_for_llm(classifier.load_product_context(conn,product_id))
        if actual != context:
            raise ValueError(f'Fixture database roundtrip altered evidence: {case["case_id"]}')
    return conn


def metrics(cases, decisions):
    by_id = {case['case_id']:case for case in cases}
    valid = {key:value for key,value in decisions.items() if not value.get('validation_error')}
    scored = {key:value for key,value in valid.items() if by_id[key].get('expected')}
    unsafe, unsupported, contradicted, false_negatives = [], [], [], []
    mismatches, reason_errors, missing_issues, mandatory_failures, reportable_errors = [], [], [], [], []
    recall = Counter(); eligible = Counter(case['expected']['vegan_reason'] for case in cases
        if case.get('expected',{} ) and case['expected']['vegan_status']=='vegan')
    negative_ids={case['case_id'] for case in cases if case.get('expected') and case['expected']['vegan_status']=='nonvegan'}
    raw_unsafe=[];raw_agreed_unsafe=[];external_known={};external_disputed=[]
    for case in cases:
        readings=case.get('adjudication',{}).get('readings',[])
        external=[row['annotation']['external_status'] for row in readings]
        if len(external)==2 and external[0]==external[1] and external[0]!='unknown':external_known[case['case_id']]=external[0]
        elif len(external)==2 and external[0]!=external[1]:external_disputed.append(case['case_id'])
    for key, result in scored.items():
        expected = by_id[key]['expected']
        status = expected['vegan_status']
        if status=='vegan':
            if result['vegan_status']=='vegan': recall[expected['vegan_reason']] += 1
        if result['vegan_status']=='vegan' and status!='vegan':
            unsafe.append(key)
            (unsupported if status=='unknown' else contradicted).append(key)
        if result['vegan_status']=='nonvegan' and status!='nonvegan': false_negatives.append(key)
        if result['vegan_status']!=status: mismatches.append(key)
        if result['vegan_status']=='vegan' and result['vegan_reason']!=expected.get('vegan_reason'): reason_errors.append(key)
        issues = {issue['code'] for issue in result['evidence'].get('assessment',{}).get('material_issues',[])}
        if not set(expected.get('required_issue_codes',[])) <= issues: missing_issues.append(key)
        if by_id[key].get('mandatory_control') and (result['vegan_status'],result['vegan_reason'])!=(status,expected['vegan_reason']):
            mandatory_failures.append(key)
        if 'tag_animal_conflict' in issues and status!='nonvegan': reportable_errors.append(key)
        arbitration=result['evidence'].get('arbitration')
        stages={'primary':arbitration['primary']['evidence'] if arbitration else result['evidence']}
        if arbitration:stages['arbitration']=arbitration['assessment']['evidence']
        if status!='vegan':
            for stage,evidence in stages.items():
                passes=evidence.get('independent_passes',[])
                positive=[index+1 for index,value in enumerate(passes) if value['vegan_status']=='vegan']
                if positive:raw_unsafe.append({'case_id':key,'stage':stage,'passes':positive})
                if len(passes)==2 and len(positive)==2:raw_agreed_unsafe.append({'case_id':key,'stage':stage})
    positives = sum(value['vegan_status']=='vegan' for value in scored.values())
    errors = [key for key,value in decisions.items() if value.get('validation_error')]
    missing = sorted(set(by_id)-set(decisions))
    mandatory_failures += [key for key in [*errors,*missing] if by_id[key].get('mandatory_control')]
    calls_list = [call for result in decisions.values() for call in result.get('model_calls',[])]
    unique_calls = list({call['call_id']:call for call in calls_list}.values())
    latency = sorted(call['duration_seconds'] for call in unique_calls if 'duration_seconds' in call)
    stage_usage = {}
    for call in unique_calls:
        key = f"{call['stage']}:{call['model']}:{call['reasoning_effort']}"
        stage_usage.setdefault(key,[]).append(call)
    return {
        'cases':len(cases),'scored_cases':len(scored),'operational_errors':errors,'missing_cases':missing,
        'exact_matches':len(scored)-len(mismatches),'mismatches':mismatches,'unsafe_positives':unsafe,
        'unsupported_promotions':unsupported,'contradicted_positives':contradicted,
        'unsupported_promotion_rate':len(unsupported)/positives if positives else None,
        'contradicted_positive_rate':len(contradicted)/positives if positives else None,
        'unsafe_positive_rate':len(unsafe)/positives if positives else None,
        'false_nonvegan_decisions':false_negatives,'reportable_negative_errors':reportable_errors,
        'nonvegan_recall':sum(key in valid and valid[key]['vegan_status']=='nonvegan' for key in negative_ids)/len(negative_ids) if negative_ids else None,
        'raw_pass_unsafe_positives':raw_unsafe,'raw_agreed_unsafe_positives':raw_agreed_unsafe,
        'external_reference':{'known_cases':len(external_known),'disputed_cases':external_disputed,
            'matches':sum(key in valid and valid[key]['vegan_status']==status for key,status in external_known.items()),
            'mismatches':[key for key,status in external_known.items() if key in valid and valid[key]['vegan_status']!=status],
            'note':'Separately scoped retained-source reference B; unknown evidence decisions are not physical-status errors.'},
        'mandatory_control_failures':mandatory_failures,
        'reason_errors':reason_errors,'missing_required_issues':missing_issues,
        'vegan_recall':sum(recall.values())/sum(eligible.values()) if eligible else None,
        'per_route_recall':{key:{'eligible':eligible[key],'recovered':recall[key], 'recall':recall[key]/eligible[key]} for key in eligible},
        'outcomes':dict(Counter(value['vegan_status'] for value in valid.values())),
        'primary_disagreements':[key for key,value in decisions.items() if 'arbitration' in value['evidence'] or 'independent_codex_disagreement' in value['evidence'].get('ambiguity_notes',[])],
        'arbitrated_cases':[key for key,value in decisions.items() if 'arbitration' in value['evidence']],
        'usage':calls.usage_totals(calls_list),
        'usage_by_stage_model':{key:calls.usage_totals(value) for key,value in stage_usage.items()},
        'call_latency_seconds':{'median':statistics.median(latency) if latency else None,
                                'p95':latency[max(0,int(.95*len(latency))-1)] if latency else None},
    }


def acceptance(report):
    # Locked release gates; diagnostics/development never constitute approval.
    runs = report['runs']
    return bool(report['split']=='locked' and len(runs)==3 and all(
        not run['missing_cases'] and not run['operational_errors'] and not run['unsafe_positives'] and not run['reason_errors']
        and not run['missing_required_issues'] and not run.get('mandatory_control_failures') and not run.get('reportable_negative_errors')
        and run['vegan_recall'] is not None and run['vegan_recall']>=.98
        and all(value['eligible']==30 and value['recovered']>=29 for value in run['per_route_recall'].values())
        and len(run['per_route_recall'])==4 and run['cases']==360
        for run in runs))


def execute_batch(batch, args, journal):
    token = calls.USAGE_JOURNAL.set(journal)
    try:
        generated, unresolved = {}, []
        for case in batch:
            result = classifier.classify_by_rules(case['classifier_input']) if args.mode in {'pipeline','rules'} else None
            if result: generated[result.product_id]=result
            elif args.mode=='rules':
                generated[case['classifier_input']['product']['id']] = classifier.ClassificationResult(
                    product_id=case['classifier_input']['product']['id'],vegan_status='unknown',vegan_reason=None,
                    confidence='uncertain',summary='Rules deferred semantic assessment',source='rule-deferred',evidence={})
            else: unresolved.append(case['classifier_input'])
        if unresolved:
            options = dict(passes=2,retries=args.retries,model=args.model,reasoning_effort=args.effort,codex_bin=args.codex_bin)
            if args.mode=='pipeline':
                results = classifier.classify_with_arbitration(unresolved,**options,arbitration_model=args.arbitration_model,arbitration_reasoning_effort=args.arbitration_effort)
            else:
                results = classifier.classify_codex_contexts(unresolved,**options,arbitration_policy=args.mode=='arbitration')
            generated.update((result.product_id,result) for result in results)
        return generated
    finally:
        calls.USAGE_JOURNAL.reset(token)


def run(args):
    fixture = load_fixture(args.fixture)
    cases = [case for case in fixture['cases'] if case['split']==args.split]
    if args.subset=='pilot':
        pilot=set(fixture.get('selection',{}).get('pilot_case_ids',[]))
        if len(pilot)!=96:raise ValueError('A complete balanced 96-case pilot is required')
        cases=[case for case in cases if case['case_id'] in pilot]
    if args.limit: cases = cases[:args.limit]
    if not cases: raise ValueError('No selected cases')
    if any(case.get('adjudication',{}).get('state')!='accepted' for case in cases if case.get('expected')):
        raise ValueError('Disputed/unreviewed annotations cannot be scored')
    args.output.mkdir(parents=True,exist_ok=True)
    manifest = {'fixture_sha256':classifier.context_hash(fixture),'selected_case_hash':classifier.context_hash(cases),
        'implementation_hashes':{path.name:classifier.context_hash(path.read_text()) for path in (ROOT/'tools'/name for name in ['vegan_policy.py','classify_ocado_vegan.py','codex_evidence.py','benchmark_whole_product.py','manufacturer_evidence.py','update_userscript_allowlists.py'])},
        'policy_version':policy.POLICY_VERSION,'knowledge_version':policy.KNOWLEDGE_VERSION,
        'prompt_version':classifier.PROMPT_VERSION,'arbitration_prompt_version':classifier.ARBITRATION_PROMPT_VERSION,
        'schema_sha256':classifier.context_hash(classifier.CODEX_OUTPUT_SCHEMA),
        'mode':args.mode,'model':args.model,'effort':args.effort,'arbitration_model':args.arbitration_model,
        'arbitration_effort':args.arbitration_effort,'batch_size':args.batch_size,'repetitions':args.repetitions,'seed':args.seed,'split':args.split,'subset':args.subset,'workers':args.workers}
    path = args.output/'manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest: raise ValueError('Resume configuration/input changed; choose a fresh output directory')
    save(path,manifest)
    report = {**manifest,'runs':[],'state':'running'}
    try:
        for repetition in range(args.repetitions):
            result_path = args.output/f'run-{repetition+1}.json'
            decisions = json.loads(result_path.read_text()).get('decisions',{}) if result_path.exists() else {}
            ordered = list(cases); random.Random(args.seed+repetition).shuffle(ordered)
            pending = [case for case in ordered if case['case_id'] not in decisions]
            token = calls.USAGE_JOURNAL.set(args.output/'calls.jsonl')
            try:
                batches = [pending[start:start+args.batch_size] for start in range(0,len(pending),args.batch_size)]
                quota_error = None
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = {pool.submit(execute_batch,batch,args,args.output/'calls.jsonl'):batch for batch in batches}
                    for future in as_completed(futures):
                        batch = futures[future]
                        if future.cancelled(): continue
                        try:
                            generated = future.result()
                        except (calls.UsageLimitError,calls.IsolationViolationError) as exc:
                            quota_error = exc
                            for queued in futures: queued.cancel()
                            continue
                        with closing(fixture_database(batch)) as conn:
                            run_id = classifier.start_run(conn,mode='benchmark',model=args.model,reasoning_effort=args.effort)
                            for case in batch:
                                result = generated[case['classifier_input']['product']['id']]
                                result = classifier.apply_policy_gate(case['classifier_input'],result)
                                classifier.write_classification(conn,run_id,result)
                                decisions[case['case_id']] = asdict(result)
                            # An operational error must fail export rather than appear complete.
                            if not any(result.validation_error for result in generated.values()): exporter.load_allowlists(conn)
                        save(result_path,{'decisions':decisions,'metrics':metrics(cases,decisions)})
                        print(f'{args.model}/{args.effort} {args.mode} run {repetition+1}: {len(decisions)}/{len(cases)}',flush=True)
                if quota_error is not None: raise quota_error
                report['runs'].append(metrics(cases,decisions))
            finally: calls.USAGE_JOURNAL.reset(token)
        report['state']='completed'
    except calls.UsageLimitError:
        report['state']='quota_exhausted'
        report['error']='Included-plan limit reached; resume identical command after reset. No paid fallback.'
    except BaseException as exc:
        report['state']='failed'
        report['error']=str(exc)
        raise
    finally:
        journal = args.output/'calls.jsonl'
        all_calls = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
        report['usage']=calls.usage_totals(all_calls)
        stages={}
        for call in all_calls:
            key=f"{call['stage']}:{call['model']}:{call['reasoning_effort']}"
            stages.setdefault(key,[]).append(call)
        report['usage_by_stage_model']={key:calls.usage_totals(value) for key,value in stages.items()}
        report['release_challenge_passed']=acceptance(report)
        save(args.output/'calls.json',all_calls)
        save(args.output/'report.json',report)
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path,default=FIXTURE)
    p.add_argument('--split',default='development')
    p.add_argument('--subset',choices=['all','pilot'],default='all')
    p.add_argument('--mode',choices=['pipeline','primary','arbitration','rules'],default='pipeline')
    p.add_argument('--model',default='gpt-6-luna'); p.add_argument('--effort',default='high')
    p.add_argument('--arbitration-model',default='gpt-6.1-sol'); p.add_argument('--arbitration-effort',default='medium')
    p.add_argument('--workers',type=int,default=3);p.add_argument('--batch-size',type=int,default=8);p.add_argument('--retries',type=int,default=1)
    p.add_argument('--repetitions',type=int,default=1);p.add_argument('--seed',type=int,default=20261006)
    p.add_argument('--limit',type=int);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--codex-bin',default='codex')
    return p


if __name__=='__main__':
    args = parser().parse_args()
    if args.batch_size<1 or args.repetitions<1 or args.workers<1: raise SystemExit('Positive batch size/repetitions/workers required')
    result = run(args)
    print(json.dumps({key:value for key,value in result.items() if key not in {'runs'}},indent=2))
    raise SystemExit(0 if result['state']=='completed' and all(not r['unsafe_positives'] and not r['operational_errors'] and not r['missing_cases'] for r in result['runs']) else 1)
