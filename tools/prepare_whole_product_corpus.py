#!/usr/bin/env python3
"""Freeze blind research candidates from read-only SQLite; no inherited labels.

Retained primary-source captures are review material, not existing gold. The
independent adjudicators receive no old classifier output or expected labels.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re
import sqlite3

import classify_ocado_vegan as classifier
import vegan_policy as policy
from benchmark_whole_product import save


def external_sources(conn, product_id, context):
    ids={row['source_id'] for row in context.get('manufacturer_source_candidates',[])}
    ids.update(row[0] for row in conn.execute('SELECT source_id FROM manufacturer_website_evidence WHERE product_id=?',(product_id,)))
    return [dict(conn.execute('SELECT * FROM manufacturer_website_sources WHERE id=?',(source_id,)).fetchone()) for source_id in sorted(ids)]


def prepare(db, output, seed=20261006):
    if output.exists(): raise ValueError('Choose a fresh candidate packet')
    conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    rng=random.Random(seed)
    try:
        ids=[row[0] for row in conn.execute('SELECT id FROM products ORDER BY id')]
        rng.shuffle(ids)
        strata={key:[] for key in ['tagged','manufacturer','name','ordinary','unknown','nonvegan']}
        target={'tagged':100,'manufacturer':140,'name':200,'ordinary':100,'unknown':360,'nonvegan':140}
        records={}
        manufacturer_ids = {row[0] for row in conn.execute('SELECT DISTINCT product_id FROM manufacturer_website_evidence')}
        manufacturer_ids.update(row[0] for row in conn.execute("SELECT DISTINCT product_id FROM manufacturer_vegan_evidence WHERE lower(content) LIKE '%suitable for%vegan%' OR lower(content) LIKE '%certified vegan%' OR lower(content)='vegan'"))
        summaries = {row['id']:dict(row) for row in conn.execute('SELECT id,name,ingredients,official_vegan,brand,dietary_information,features FROM products')}
        for product_id in ids:
            product=summaries[product_id];name=product.get('name') or '';ingredients=product.get('ingredients') or ''
            # Sampling signals only. These are not expected decisions.
            if product.get('official_vegan')==1: group='tagged'
            elif re.search(r'\bvegan\b',name,re.I): group='name'
            elif product_id in manufacturer_ids or re.search(r'suitable for .*vegan|certified vegan|vegan friendly',str(product),re.I): group='manufacturer'
            elif re.search(r'\b(?:gelatine|milk|egg|honey|pork|beef|chicken|fish|shellac|beeswax|E120|E901|E904)\b',ingredients,re.I): group='nonvegan'
            elif re.search(r'\b(?:carrots?|lettuce|spinach|cucumber|tomatoes?|potatoes?|beans|lentils|rice|pasta|flour|broccoli|apples?|bananas?|pears|salad)\b',name,re.I): group='ordinary'
            else: group='unknown'
            if len(strata[group])>=target[group]: continue
            context=classifier.product_context_for_llm(classifier.load_product_context(conn,product_id))
            product=context['product']
            sources=external_sources(conn,product_id,context)
            # A coarse brand group prevents variants crossing development/holdout.
            # Shared source IDs connect otherwise differently branded records.
            brand=re.sub(r'\W+',' ',product.get('brand','').lower()).strip()
            identity=re.sub(r'\b\d+\w*\b','',name.lower())
            records[product_id]={'case_id':'natural.'+product_id,'case_kind':'natural','sampling_stratum':group,
                'current_on_ocado':conn.execute('SELECT current_on_ocado FROM products WHERE id=?',(product_id,)).fetchone()[0],
                'classifier_input':context,'input_sha256':classifier.context_hash(context),
                'family_key':brand or identity,'source_group_keys':[source['id'] for source in sources],
                'external_sources':sources}
            strata[group].append(product_id)
            if all(len(strata[key])>=target[key] for key in target): break
        # Reproducible uniform workload sample separate from curated challenge.
        workload=[]
        for product_id in ids[:200]:
            context=classifier.product_context_for_llm(classifier.load_product_context(conn,product_id))
            workload.append({'case_id':'workload.'+product_id,'case_kind':'natural','split':'workload','leakage_group':'workload.'+product_id,
                'classifier_input':context,'input_sha256':classifier.context_hash(context),'expected':None,
                'adjudication':{'state':'unlabelled','reference_type':'workload-only'}})
        packet={'schema_version':2,'seed':seed,'policy_version':policy.POLICY_VERSION,'candidates':list(records.values()),'workload':workload,
            'sampling_counts':{key:len(value) for key,value in strata.items()},
            'note':'No existing vegan status, classifier output, old adjudication or gold labels were selected.'}
        save(output,packet)
        return packet['sampling_counts']
    finally:conn.close()


def augment(db, original, output):
    """Explicitly freeze added complete website captures; preserve the old packet."""
    if output.exists():raise ValueError('Choose a fresh augmented packet')
    packet=json.loads(original.read_text());changed=[]
    conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        for case in [*packet['candidates'],*packet['workload']]:
            before=case['classifier_input']
            current=classifier.product_context_for_llm(classifier.load_product_context(conn,before['product']['id']))
            # Only the declared website-capture additions are authorised here.
            def previous_projection(context):
                return {**{key:value for key,value in context.items() if key!='manufacturer_source_candidates'},'manufacturer_vegan_evidence':[
                    {key:value for key,value in row.items() if key not in {'source_text','official_identity','market_scope','review_policy_version'}}
                    for row in context['manufacturer_vegan_evidence']]}
            if previous_projection(current)!=previous_projection(before):
                raise ValueError(f'Catalogue changed since freezing {case["case_id"]}; do not silently refresh')
            if current!=before:
                changed.append({'case_id':case['case_id'],'previous_input_sha256':case['input_sha256'],
                                'augmented_input_sha256':classifier.context_hash(current)})
                case.update(classifier_input=current,input_sha256=classifier.context_hash(current))
            if case in packet['candidates']:
                sources=external_sources(conn,before['product']['id'],current)
                case.update(external_sources=sources,source_group_keys=[row['id'] for row in sources])
        packet['augmentation']={'original_packet_sha256':classifier.context_hash(json.loads(original.read_text())),
            'kind':'all associated retained manufacturer captures, publisher, market, applicability and review version','changed_cases':changed}
        save(output,packet)
        return {'changed_cases':len(changed),'unchanged_annotations_may_be_reused':True}
    finally:conn.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,default=Path('ocado_products.sqlite'));p.add_argument('--output',type=Path,required=True)
    p.add_argument('--augment-packet',type=Path)
    args=p.parse_args();print(json.dumps(augment(args.db,args.augment_packet,args.output) if args.augment_packet else prepare(args.db,args.output),indent=2))
