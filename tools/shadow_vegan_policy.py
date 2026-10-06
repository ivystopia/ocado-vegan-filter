#!/usr/bin/env python3
"""Read-only policy impact report; optional isolated snapshot, never activation."""
import argparse
from collections import Counter
from contextlib import closing
import csv
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

import classify_ocado_vegan as classifier
import vegan_policy as policy
from benchmark_whole_product import save


def preview(db, output, *, all_products=False):
    output.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    rule_hashes={name:classifier.context_hash(Path(module.__file__).read_text())
        for name,module in (('classifier',classifier),('policy',policy))}
    transitions=Counter();current_transitions=Counter();records=[];conflicts=[];identity_conflicts=[];semantic=[]
    try:
        conn.execute('BEGIN')  # One consistent read snapshot across the scan.
        population={row['id']:dict(row) for row in conn.execute('SELECT id,vegan_status,vegan_reason,current_on_ocado FROM products')}
        ids=list(population) if all_products else [key for key,row in population.items() if row['vegan_status']=='vegan']
        for start in range(0,len(ids),500):
            for context in classifier.load_product_contexts(conn,ids[start:start+500]):
                product_id=context['product']['id'];old=population[product_id]
                result=classifier.classify_by_rules(context)
                next_status=result.vegan_status if result else 'semantic_review_required'
                next_reason=result.vegan_reason if result else None
                key=f"{old['vegan_status']}/{old['vegan_reason']} -> {next_status}/{next_reason}"
                transitions[key]+=1
                if old['current_on_ocado']==1:current_transitions[key]+=1
                record={'product_id':product_id,'name':context['product']['name'],'url':'https://www.ocado.com/products/'+product_id,
                    'current_on_ocado':old['current_on_ocado'],'previous_status':old['vegan_status'],'previous_reason':old['vegan_reason'],
                    'candidate_status':next_status,'candidate_reason':next_reason,
                    'input_sha256':classifier.context_hash(classifier.product_context_for_llm(context)),
                    'candidate':asdict(result) if result else None}
                records.append(record)
                if not result:semantic.append({'product_id':product_id,'classifier_input':classifier.product_context_for_llm(context),'input_sha256':record['input_sha256']})
                elif result.evidence.get('tag_animal_conflict'):conflicts.append(record)
                elif policy.official_tag(context) and any(issue['code']=='identity_conflict' for issue in result.evidence.get('assessment',{}).get('material_issues',[])):
                    identity_conflicts.append(record)
            print(f'Rule preview: {min(start+500,len(ids))}/{len(ids)}',flush=True)
        if rule_hashes!={name:classifier.context_hash(Path(module.__file__).read_text())
                for name,module in (('classifier',classifier),('policy',policy))}:
            raise ValueError('Rule implementation changed during preview; rerun against a fixed revision')
        report={'policy_version':policy.POLICY_VERSION,'rule_hashes':rule_hashes,'consistent_read_snapshot':True,
            'catalogue_all_known':len(population),'catalogue_current':sum(r['current_on_ocado']==1 for r in population.values()),
            'reviewed_all_known':len(ids),'reviewed_current':sum(population[key]['current_on_ocado']==1 for key in ids),
            'scope':'all-known' if all_products else 'all-existing-positive-labels','transitions_all_known':dict(transitions),'transitions_current':dict(current_transitions),
            'semantic_queue':len(semantic),'tag_animal_conflicts':len(conflicts),'tag_identity_conflicts':len(identity_conflicts),'live_database_modified':False,
            'note':'Rule preview only. Semantic cases are unassessed, not gold unknowns. Historical current flags do not prove current live listing.'}
        save(output/'rule-preview.json',{'summary':report,'products':records})
        save(output/'semantic-queue.json',semantic)
        save(output/'tag-identity-conflicts.json',identity_conflicts)
        with (output/'ocado-tag-conflicts.csv').open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=['product_id','name','url','current_on_ocado','ingredients','animal_evidence','matched_patterns','input_sha256'])
            writer.writeheader()
            for record in conflicts:
                candidate=record['candidate'];refs=candidate['evidence']['assessment']['material_issues']
                ingredients=next((ref['quote'] for issue in refs for ref in issue['evidence_refs'] if ref['path']=='/product/ingredients'),'')
                animal_evidence=[ref for issue in refs if issue['code'] in {'animal_ingredient','animal_processing'} for ref in issue['evidence_refs']]
                writer.writerow({**{key:record[key] for key in ['product_id','name','url','current_on_ocado','input_sha256']},
                    'ingredients':ingredients,'animal_evidence':json.dumps(animal_evidence,ensure_ascii=False),
                    'matched_patterns':json.dumps(candidate['evidence']['matched_patterns'])})
        lines=['# Stored Ocado tag contradictions','', 'Correctly matched ingredient findings under the new policy. Stored evidence may be historical; verify currency and identity before sending. No message has been sent.','']
        for record in conflicts:lines.append(f"- {record['name']} — {record['url']} (stored current flag: {record['current_on_ocado']})")
        (output/'ocado-tag-conflicts.md').write_text('\n'.join(lines)+'\n')
        return report
    finally:conn.close()


def snapshot(source, target, *, all_products=False):
    if source.resolve()==target.resolve() or target.exists():raise ValueError('Shadow target must be fresh and different from source')
    target.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as original, closing(sqlite3.connect(target)) as shadow:
        original.backup(shadow)
    with closing(classifier.connect(target)) as conn:
        # Preserve previous status/evidence before invalidating the isolated copy.
        conn.execute('CREATE TABLE shadow_previous_decisions AS SELECT * FROM products')
        selector='' if all_products else " WHERE vegan_status='vegan'"
        conn.execute('UPDATE products SET vegan_status=NULL,vegan_reason=NULL,vegan_policy_version=NULL,vegan_evidence_hash=NULL,vegan_audit_id=NULL'+selector)
        conn.commit()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,default=Path('ocado_products.sqlite'));p.add_argument('--output',type=Path,required=True)
    p.add_argument('--all-products',action='store_true');p.add_argument('--snapshot',type=Path)
    args=p.parse_args()
    if args.snapshot:snapshot(args.db,args.snapshot,all_products=args.all_products)
    print(json.dumps(preview(args.db,args.output,all_products=args.all_products),indent=2))
