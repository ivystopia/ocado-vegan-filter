#!/usr/bin/env python3
"""Recover complete native text from retained SQLite responses, without fetching.

Preview first. Application requires an isolated database, its exact fingerprint,
and an unchanged preview; previous fields, decisions and raw hashes are retained.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

import build_ocado_database as importer
import classify_ocado_vegan as classifier
import sync_ocado_database as sync


def legacy_text(value):
    text=importer.BR_RE.sub(' ',value or '')
    text=importer.P_BREAK_RE.sub(' ',text)
    return importer.normalize_space(importer.TAG_RE.sub(' ',text))


def preview(conn):
    columns={r[1] for r in conn.execute('PRAGMA table_info(products)')}
    changes=[];unmatched=[];captured=0
    for row in conn.execute('SELECT * FROM product_detail_raw_latest ORDER BY product_id'):
        captured+=1
        raw=zlib.decompress(row['response_json_zlib'])
        if hashlib.sha256(raw).hexdigest()!=row['response_sha256']:
            raise ValueError('Retained response hash mismatch: '+row['product_id'])
        data=json.loads(raw);product=data.get('product') or {};bop=data.get('bopData') or {}
        if str(product.get('retailerProductId'))!=str(row['product_id']):
            raise ValueError('Retained response identity mismatch: '+row['product_id'])
        fields={}
        for field in ('name','brand'):
            value=product.get(field)
            if isinstance(value,str):fields[field]=(value,'/product/'+field,None)
        if isinstance(bop.get('detailedDescription'),str):
            fields['detailed_description']=(bop['detailedDescription'],'/bopData/detailedDescription',None)
        for i,field in enumerate(bop.get('fields') or []):
            title=field.get('title');column=importer.field_column(title)
            if column in columns and column not in {'brand','nutrition_plain_text'} and isinstance(field.get('content'),str):
                fields[column]=(field['content'],f'/bopData/fields/{i}/content',title)
        differences={k:{'before':legacy_text(v),'after':importer.html_to_text(v),'raw_path':path,'field_title':title}
            for k,(v,path,title) in fields.items() if legacy_text(v)!=importer.html_to_text(v)}
        if not differences:continue
        stored=conn.execute('SELECT * FROM products WHERE id=?',(row['product_id'],)).fetchone()
        if stored is None:raise ValueError('Capture without catalogue product')
        # A successor may already contain the verified recovered projection.
        # Leave it intact; this is neither a new repair nor a concurrent edit.
        differences={k:v for k,v in differences.items() if (stored[k] or '')!=v['after']}
        if not differences:continue
        stale=[k for k,v in differences.items() if (stored[k] or '')!=v['before']]
        if stale:
            unmatched.append({'product_id':row['product_id'],'fields':stale});continue
        evidence=[dict(v) for v in conn.execute('SELECT field_title,content FROM manufacturer_vegan_evidence WHERE product_id=? ORDER BY field_title,content',(row['product_id'],))]
        changes.append({'product_id':row['product_id'],'name':stored['name'],'current_on_ocado':stored['current_on_ocado'],
            'previous_status':stored['vegan_status'],'previous_audit_id':stored['vegan_audit_id'],
            'previous_decision':{k:stored[k] for k in classifier.CLASSIFICATION_PRODUCT_COLUMNS},
            'raw_sha256':row['response_sha256'],'capture_run_id':row['run_id'],'fields':differences,
            'previous_native_evidence':evidence})
    total=conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
    return {'version':'retained-native-html-recovery-v1','captures_checked':captured,'products_without_capture':total-captured,
            'changed_products':len(changes),'changes':changes,'unmatched_projection':unmatched,
            'http_attempts':0,'note':'Only fields whose previous stored value matches the legacy projection may change. Historical products without captures retain their last-known evidence; no missing text is invented.'}


def apply(conn, plan, expected_fingerprint):
    from refresh_whole_product import fingerprint
    if not conn.in_transaction:raise ValueError('Caller must hold a write transaction')
    if fingerprint(conn)!=expected_fingerprint:raise ValueError('Database changed before text recovery')
    current=preview(conn)
    if current!=plan or current['unmatched_projection']:
        raise ValueError('Preview changed or cannot match the retained native projection')
    conn.execute('CREATE TABLE IF NOT EXISTS product_text_projection_audit '
        '(id INTEGER PRIMARY KEY,product_id TEXT NOT NULL,raw_sha256 TEXT NOT NULL,repair_version TEXT NOT NULL,previous_and_recovered_json TEXT NOT NULL)')
    for row in plan['changes']:
        conn.execute('INSERT INTO product_text_projection_audit(product_id,raw_sha256,repair_version,previous_and_recovered_json) VALUES (?,?,?,?)',
            (row['product_id'],row['raw_sha256'],plan['version'],json.dumps(row,ensure_ascii=False)))
        for field,value in row['fields'].items():
            conn.execute('UPDATE products SET "'+field+'"=? WHERE id=?',(value['after'],row['product_id']))
            title=value['field_title']
            if title:
                conn.execute('DELETE FROM manufacturer_vegan_evidence WHERE product_id=? AND field_title=?',(row['product_id'],title))
                if 'vegan' in value['after'].lower():
                    conn.execute('INSERT INTO manufacturer_vegan_evidence(product_id,field_title,content) VALUES (?,?,?)',
                                 (row['product_id'],title,value['after']))
        assignments=','.join('"'+k+'"=NULL' for k in classifier.CLASSIFICATION_PRODUCT_COLUMNS if k!='vegan_positive_block')
        conn.execute('UPDATE products SET '+assignments+',vegan_positive_block=0 WHERE id=?',(row['product_id'],))
    return {'state':'applied','changed_products':len(plan['changes']),'classification_decisions_written':0,
            'new_model_calls':0,'http_attempts':0,'plan_sha256':classifier.context_hash(plan)}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--apply-plan',type=Path)
    p.add_argument('--expected-fingerprint');a=p.parse_args()
    if a.output.exists():raise ValueError('Preserve existing text-recovery evidence')
    if a.apply_plan:
        if a.db.resolve()==Path(classifier.DEFAULT_DB).resolve():raise ValueError('Recover only an isolated candidate')
        if not a.expected_fingerprint:raise ValueError('Application requires the exact isolated database fingerprint')
        with closing(sqlite3.connect(a.db)) as c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN IMMEDIATE')
            result=apply(c,json.loads(a.apply_plan.read_text()),a.expected_fingerprint);c.commit()
    else:
        with closing(sqlite3.connect(a.db.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.row_factory=sqlite3.Row;c.execute('BEGIN');result=preview(c)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in {'changes'}}))


if __name__=='__main__':main()
