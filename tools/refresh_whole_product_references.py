#!/usr/bin/env python3
"""Blind development-only reference refresh; preserve the earlier holdout.

The parent remains unchanged. Old labels/readings never enter the packet.
Supplementary name/ordinary/unknown signals are sampling signals, not labels.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re
import sqlite3

import benchmark_whole_product as benchmark
import classify_ocado_vegan as classifier
import prepare_whole_product_corpus as prepare
import assemble_whole_product_fixture as assemble
import vegan_policy as policy



def family(context):
    product=context['product']
    return re.sub(r'\W+',' ',(product.get('brand') or product['name']).lower()).strip()


def retained_sources(case):
    context=case['classifier_input']
    candidates=context.get('manufacturer_source_candidates',[])+context.get('manufacturer_vegan_evidence',[])
    sources=[]
    for metadata in case.get('source_metadata',[]):
        match=next((row for row in candidates if row.get('source_url')==metadata['url']
            and row.get('source_sha256')==metadata['content_sha256']),None)
        if not match or not match.get('source_text'):
            raise ValueError('Complete frozen source is missing: '+case['case_id'])
        sources.append({**metadata,'source_text':match['source_text']})
    return sources


def blind_case(case):
    sources=retained_sources(case)
    return {key:case[key] for key in ('case_id','case_kind','sampling_stratum','current_on_ocado','classifier_input','input_sha256')}|{
        'family_key':family(case['classifier_input']),'source_group_keys':[row['id'] for row in sources],
        'external_sources':sources}


def make_packet(parent_path,db,output):
    if output.exists():raise ValueError('Choose a fresh blind packet')
    parent=json.loads(parent_path.read_text())
    candidates=[blind_case(case) for case in parent['cases'] if case['split']=='development']
    protected=[case for case in parent['cases'] if case['split']=='locked']
    protected_families={family(case['classifier_input']) for case in protected}
    protected_sources={row['url'] for case in protected for row in case.get('source_metadata',[])}
    used={case['classifier_input']['product']['id'] for case in parent['cases']}
    added=Counter();rejected=Counter()
    conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        rows=list(conn.execute('SELECT id,name,ingredients,official_vegan,current_on_ocado FROM products ORDER BY id'))
        random.Random(20261011).shuffle(rows)
        name_signals=sum(bool(re.search(r'\bvegan\b',row['name'] or '',re.I)) and row['official_vegan']!=1 for row in rows)
        for row in rows:
            if row['id'] in used:continue
            name=row['name'] or ''
            if re.search(r'\bvegan\b',name,re.I) and row['official_vegan']!=1:signal='name'
            elif re.search(r'\b(?:carrots?|spinach|lettuce|rice|beans|lentils|broccoli|potatoes?)\b',name,re.I):signal='ordinary'
            elif re.search(r'\b(?:wine|sugar|shampoo|candles?|cotton|paper plates|coffee pods|tea bags)\b',name,re.I):signal='unknown'
            else:continue
            if signal!='name' and added[signal]>=30:continue
            context=classifier.product_context_for_llm(classifier.load_product_context(conn,row['id']))
            if family(context) in protected_families:
                rejected['locked_family']+=1;continue
            sources=prepare.external_sources(conn,row['id'],context)
            if any(source['url'] in protected_sources for source in sources):
                rejected['locked_source']+=1;continue
            candidates.append({'case_id':'natural.'+row['id'],'case_kind':'natural','sampling_stratum':signal,
                'current_on_ocado':row['current_on_ocado'],'classifier_input':context,'input_sha256':classifier.context_hash(context),
                'family_key':family(context),'source_group_keys':[source['id'] for source in sources],'external_sources':sources})
            added[signal]+=1
    finally:conn.close()
    packet={'schema_version':2,'policy_version':policy.POLICY_VERSION,'parent_sha256':classifier.context_hash(parent),
        'candidates':candidates,'workload':[case for case in parent['cases'] if case['split']=='workload'],
        'sampling':{'existing_development':sum(case['split']=='development' for case in parent['cases']),
            'added_signals':dict(added),'rejected':dict(rejected),'all_known_standalone_name_signals_without_official_flag':name_signals},
        'note':'Old expectations/readings excluded. Original locked families/sources protected; new samples are development only. Names are signals, never gold.'}
    benchmark.save(output,packet)
    return packet['sampling']


def build_fixture(parent_path,annotations_path,output,crosswalk_path):
    if output.exists() or crosswalk_path.exists():raise ValueError('Preserve prior fixtures/crosswalks; choose fresh outputs')
    parent=json.loads(parent_path.read_text());annotations=json.loads(annotations_path.read_text())
    if annotations.get('policy_version')!=policy.POLICY_VERSION:raise ValueError('Fresh current-policy readings are required')
    previous={case['case_id']:case for case in parent['cases'] if case['split']=='development'}
    cases=[];changes=[];disputes=[]
    for candidate in annotations['candidates']:
        old=previous.get(candidate['case_id'])
        if old and old['input_sha256']!=candidate['input_sha256']:raise ValueError('Original frozen development evidence changed')
        if candidate['adjudication'].get('policy_version')!=policy.POLICY_VERSION:raise ValueError('Stale annotation policy')
        if candidate['adjudication']['state']!='accepted':disputes.append(candidate['case_id']);continue
        source_metadata=[{key:value for key,value in source.items() if key!='source_text'} for source in candidate['external_sources']]
        cases.append({key:value for key,value in candidate.items() if key not in {'external_sources','family_key','source_group_keys'}}|{
            'split':'development','leakage_group':'family.'+candidate['family_key'],'source_metadata':source_metadata,
            'mandatory_control':bool(old and old.get('mandatory_control'))})
        if old and old['expected']!=candidate['expected']:
            changes.append({'case_id':candidate['case_id'],'input_sha256':candidate['input_sha256'],
                'before':old['expected'],'after':candidate['expected'],
                'rationale':[reading['annotation']['rationale'] for reading in candidate['adjudication']['readings']]})
    counts=Counter(case['expected']['vegan_reason'] or case['expected']['vegan_status'] for case in cases)
    pilot=[case['case_id'] for case in cases][:96]
    cases+=assemble.synthetic_cases()+annotations.get('workload',[])
    fixture={'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':cases,'selection':{
        'pilot_case_ids':pilot,'counts':dict(Counter(case['split'] for case in cases)),
        'disputed_cases':disputes,'annotation_hash':classifier.context_hash(annotations),'parent_fixture_hash':classifier.context_hash(parent),
        'note':'Current-policy development diagnostic only. Original locked evidence remains protected and is not eligible under superseded references. No release approval.'}}
    benchmark.save(output,fixture)
    benchmark.save(crosswalk_path,{'policy_before':parent['policy_version'],'policy_after':policy.POLICY_VERSION,
        'parent_fixture_hash':classifier.context_hash(parent),'candidate_fixture_hash':classifier.context_hash(fixture),
        'reference_changes':changes,'disputes':disputes,
        'note':'Fresh blind source readings; no previous labels supplied. Original contexts/results preserved. Disputes stay unscored and prevent release qualification.'})
    return fixture['selection']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=Path,default=benchmark.FIXTURE)
    parser.add_argument('--db',type=Path,default=Path('ocado_products.sqlite'))
    parser.add_argument('--annotations',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--crosswalk',type=Path)
    args=parser.parse_args()
    if args.annotations:
        if not args.crosswalk:parser.error('--crosswalk is required when assembling references')
        result=build_fixture(args.parent,args.annotations,args.output,args.crosswalk)
    else:result=make_packet(args.parent,args.db,args.output)
    print(json.dumps(result,indent=2))
