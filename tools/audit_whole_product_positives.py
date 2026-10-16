#!/usr/bin/env python3
"""Freeze a uniform shadow-positive sample and score blind source readings.

No model calls, catalogue writes or activation. The candidate must already be
assessed under the current policy. Annotate the resulting packet separately.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sqlite3

import benchmark_whole_product as benchmark
import classify_ocado_vegan as classifier
import prepare_whole_product_corpus as prepare
import vegan_policy as policy
import update_userscript_allowlists as exporter
import adjudicate_whole_product_corpus as adjudication


def make_packet(db, output, size=600, seed=20261012):
    if output.exists() or output.with_suffix('.predictions.json').exists():raise ValueError('Preserve the frozen audit; choose a fresh output')
    if size<600:raise ValueError('The approved positive audit requires at least 600 distinct products')
    conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        conn.execute('BEGIN')
        exporter.validate_export_readiness(conn)
        exporter.validate_policy_decisions(conn)
        rows=[dict(row) for row in conn.execute("SELECT id,vegan_reason,current_on_ocado,vegan_policy_version,vegan_evidence_hash,vegan_audit_id FROM products WHERE vegan_status='vegan' ORDER BY id")]
        if len(rows)<size:raise ValueError('Positive population is smaller than the required audit')
        if any(row['vegan_policy_version']!=policy.POLICY_VERSION or not row['vegan_audit_id'] for row in rows):
            raise ValueError('Revalidate the entire candidate positive population before sampling')
        selected=random.Random(seed).sample(rows,size)
        candidates=[];predictions={}
        for row in selected:
            context=classifier.product_context_for_llm(classifier.load_product_context(conn,row['id']))
            evidence_hash=classifier.context_hash(context)
            if evidence_hash!=row['vegan_evidence_hash']:raise ValueError('Candidate evidence changed: '+row['id'])
            sources=prepare.external_sources(conn,row['id'],context)
            case_id='positive-audit.'+row['id']
            candidates.append({'case_id':case_id,'case_kind':'natural','sampling_stratum':'uniform-audit',
                'current_on_ocado':row['current_on_ocado'],'classifier_input':context,'input_sha256':evidence_hash,
                'family_key':context['product'].get('brand') or context['product']['name'],
                'source_group_keys':[source['id'] for source in sources],'external_sources':sources})
            predictions[case_id]={'vegan_status':'vegan','vegan_reason':row['vegan_reason'],'audit_id':row['vegan_audit_id']}
        packet={'schema_version':2,'policy_version':policy.POLICY_VERSION,'candidates':candidates,'workload':[],
            'sampling':{'seed':seed,'size':size,'population_all_known':len(rows),
                'population_current':sum(row['current_on_ocado']==1 for row in rows),'population_sha256':classifier.context_hash(rows),
                'design':'uniform without replacement from all known candidate positive labels'},
            'note':'Candidate labels are excluded from blind model inputs; stored current flags do not prove live availability.'}
        benchmark.save(output,packet)
        benchmark.save(output.with_suffix('.predictions.json'),{'packet_sha256':classifier.context_hash(packet),'predictions':predictions})
        return packet['sampling']
    finally:conn.close()


def score(packet, predictions, annotations):
    if packet.get('policy_version')!=policy.POLICY_VERSION or annotations.get('policy_version')!=policy.POLICY_VERSION:
        raise ValueError('Audit policy is stale')
    if predictions.get('packet_sha256')!=classifier.context_hash(packet):raise ValueError('Predictions do not belong to this sample')
    cases={case['case_id']:case for case in packet['candidates']}
    reads=annotations.get('candidates',[])
    ids=[case['case_id'] for case in reads]
    if len(ids)!=len(set(ids)) or set(ids)!=set(cases) or set(predictions['predictions'])!=set(cases):
        raise ValueError('Missing, duplicate or unexpected audit cases')
    counts=Counter();issues=[]
    for case in reads:
        original=cases[case['case_id']]
        if case['input_sha256']!=original['input_sha256'] or case['classifier_input']!=original['classifier_input']:
            raise ValueError('Audit reading used different evidence')
        if case['adjudication'].get('policy_version')!=policy.POLICY_VERSION:
            raise ValueError('Audit reading used a superseded policy')
        accepted=case['adjudication']['state']=='accepted'
        if accepted:
            readings=case['adjudication'].get('readings',[])
            if len(readings)!=2 or len({reading.get('call_id') for reading in readings})!=2:
                raise ValueError('Accepted audit reference requires two distinct source readings')
            for reading in readings:
                annotation=reading['annotation']
                policy.validate_schema(annotation,adjudication.ROW_SCHEMA)
                if annotation['case_id']!=case['case_id']:raise ValueError('Source reading belongs to another audit case')
                for ref in annotation['input_refs']:policy.resolve_reference(original['classifier_input'],ref)
                adjudication.validate_basis(annotation,original['classifier_input'])
                if (annotation['expected_status'],annotation['expected_reason'])!=(case['expected']['vegan_status'],case['expected']['vegan_reason']):
                    raise ValueError('Accepted reference disagrees with its source readings')
        status=case['expected']['vegan_status'] if accepted else 'disputed'
        counts[status]+=1
        if status!='vegan':issues.append({'case_id':case['case_id'],'finding':status,'adjudication':case['adjudication']})
        elif predictions['predictions'][case['case_id']].get('vegan_reason') != case['expected']['vegan_reason']:
            issues.append({'case_id':case['case_id'],'finding':'unsupported_reason',
                'candidate_reason':predictions['predictions'][case['case_id']].get('vegan_reason'),
                'reference_reason':case['expected']['vegan_reason'],'adjudication':case['adjudication']})
    size=len(cases)
    passed=size>=600 and not issues
    return {'policy_version':policy.POLICY_VERSION,'state':'passed' if passed else 'failed',
        'packet_sha256':classifier.context_hash(packet),'sampling':packet['sampling'],
        'reference_counts':dict(counts),'issues':issues,
        'zero_error_binomial_upper_95':1-0.05**(1/size) if passed else None,
        'note':'Automated source-backed evidence-sufficiency audit; shared model/reference errors remain possible. The conditional binomial calculation is not an unconditional physical guarantee and does not account for those errors. No activation is authorised.'}


def score_early_failure(packet, predictions, readings):
    """Fail a consumed sample after two blind readings of one actual defect.

    This cannot pass an audit, extrapolate incomplete observations or supply a
    confidence bound. All original attempts remain available for inspection.
    """
    if packet.get('policy_version') != policy.POLICY_VERSION:
        raise ValueError('Audit policy is stale')
    if predictions.get('packet_sha256') != classifier.context_hash(packet):
        raise ValueError('Predictions do not belong to this sample')
    if len(readings) != 2 or len({r.get('call_id') for r in readings}) != 2 or any(not r.get('call_id') for r in readings):
        raise ValueError('Early failure requires two distinct blind readings')
    ids = {r['annotation']['case_id'] for r in readings}
    if len(ids) != 1:
        raise ValueError('Failure readings concern different cases')
    case_id = next(iter(ids))
    cases = {case['case_id']: case for case in packet['candidates']}
    if case_id not in cases or predictions['predictions'][case_id]['vegan_status'] != 'vegan':
        raise ValueError('Failure must concern a sampled positive')
    case = cases[case_id]
    outcomes = set()
    for reading in readings:
        row = reading['annotation']
        if reading.get('input_sha256') != case['input_sha256']:
            raise ValueError('Failure reading used different evidence')
        policy.validate_schema(row, adjudication.ROW_SCHEMA)
        for ref in row['input_refs']:
            policy.resolve_reference(case['classifier_input'], ref)
        adjudication.validate_basis(row, case['classifier_input'])
        outcomes.add((row['expected_status'], row['expected_reason']))
    disputed = len(outcomes) > 1
    agreed_status, agreed_reason = next(iter(outcomes))
    candidate_reason = predictions['predictions'][case_id].get('vegan_reason')
    reason_defect = (not disputed and agreed_status == 'vegan'
                     and candidate_reason in policy.REASONS and agreed_reason != candidate_reason)
    if not disputed and agreed_status not in {'unknown', 'nonvegan'} and not reason_defect:
        raise ValueError('Early failure requires an agreed unsupported positive')
    status = 'disputed' if disputed else agreed_status
    return {'state': 'failed', 'policy_version': packet['policy_version'],
            'mode': ('early-stop-after-reference-dispute' if disputed else
                     'early-stop-after-confirmed-reason-defect' if reason_defect else
                     'early-stop-after-confirmed-defect'),
            'packet_sha256': classifier.context_hash(packet), 'sampling': packet['sampling'],
            'completed_cases': 1, 'reference_counts': {status: 1},
            'issues': [{'case_id': case_id, 'finding': 'unsupported_reason' if reason_defect else status,
                        'candidate_reason': candidate_reason, 'readings': readings}],
            'zero_error_binomial_upper_95': None,
            'note': ('Incomplete audit stopped after two distinct blind source-cited readings disagreed. This is an unresolved reference finding, not proof of a nonvegan product or a confirmed unsupported positive.' if disputed else
                     'Incomplete audit stopped after an incorrect positive reason confirmed by two distinct blind source-cited readings; both references remain vegan.' if reason_defect else
                     'Incomplete audit stopped after an actual unsupported positive confirmed by two distinct blind source-cited readings.') +
                    ' This is a failed candidate, never a completed 600-case audit. Preserve all other attempted readings; resolve the policy/implementation finding and audit a fresh fixed candidate.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path);parser.add_argument('--packet',type=Path)
    parser.add_argument('--annotations',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--early-failure-readings',type=Path)
    parser.add_argument('--seed',type=int,default=20261012)
    args=parser.parse_args()
    if args.db:
        if args.packet or args.annotations or args.early_failure_readings:parser.error('Use --db for preparation or --packet/--annotations for scoring')
        result=make_packet(args.db,args.output,seed=args.seed)
    else:
        if not args.packet or bool(args.annotations)==bool(args.early_failure_readings):parser.error('Scoring requires --packet and exactly one of --annotations or --early-failure-readings')
        if args.output.exists():raise ValueError('Preserve earlier audit reports')
        packet=json.loads(args.packet.read_text());predictions=json.loads(args.packet.with_suffix('.predictions.json').read_text())
        result=(score_early_failure(packet,predictions,json.loads(args.early_failure_readings.read_text())) if args.early_failure_readings else
                score(packet,predictions,json.loads(args.annotations.read_text())))
        benchmark.save(args.output,result)
    print(json.dumps(result,indent=2))
