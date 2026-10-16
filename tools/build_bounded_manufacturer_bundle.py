"""Bind retained brief checks to a frozen unknown cohort, without approving labels.

Only acquisition/accounting artifacts are written. Normal independent whole-product
classification must interpret applicable full captures after import.
"""
from contextlib import closing
from pathlib import Path
import argparse
import hashlib
import json
import sqlite3

import classify_ocado_vegan as classifier
import cursory_research_accounting as accounting
import manufacturer_evidence as manufacturer
import research_ocado_manufacturers as research


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def retained(path, bank, expected=None):
    path = Path(path).resolve()
    require(path.is_relative_to(bank.resolve()) and path.is_file(), 'Missing or outside-bank artifact: ' + str(path))
    raw = path.read_bytes()
    require(expected is None or sha(raw) == expected, 'Changed retained artifact: ' + str(path))
    return path, raw


def capture(path, bank):
    """Verify complete original text and preserve extraction/access provenance."""
    path, raw = retained(path, bank)
    meta = json.loads(raw)
    require(meta.get('state') in {'captured_for_scope_review', 'rendered_capture_for_scope_review',
            'rendered_product_tab_for_scope_review', 'complete_embedded_public_faq_for_scope_review',
            'public_structured_faq_needs_scope_review',
            'pdf_text_capture_needs_visual_scope_review'}, 'Capture is not complete readable evidence')
    require(not meta.get('error'), 'Failed capture cannot become evidence')
    provenance = [(path, raw)]
    if meta.get('raw_sha256'):
        original_path, original_raw = retained(path.parent/'raw.bin', bank, meta['raw_sha256'])
        provenance.append((original_path, original_raw))
    for key in ('parent_metadata_path', 'original_metadata_path', 'original_metadata'):
        if not meta.get(key):
            continue
        original_path = Path(meta[key])
        if not original_path.is_absolute():
            original_path = bank/original_path
        original_path, original_raw = retained(original_path, bank, meta.get(key.replace('_path', '')+'_sha256'))
        original = json.loads(original_raw)
        require(original.get('url') == meta.get('url'), 'Derived capture changed source identity')
        provenance.append((original_path, original_raw))
        if meta.get('raw_sha256'):
            parent_raw_path, parent_raw = retained(original_path.parent/'raw.bin', bank, meta['raw_sha256'])
            provenance.append((parent_raw_path, parent_raw))
    if meta.get('pdf_capture_metadata'):
        original_path, original_raw = retained(meta['pdf_capture_metadata'], bank, meta['pdf_capture_metadata_sha256'])
        original = json.loads(original_raw)
        require(all(meta.get(k) == original.get(k) for k in ('url', 'final_url', 'retrieved_at', 'http_status', 'content_type')),
                'PDF extraction changed retrieval provenance')
        pdf_path, pdf = retained(original_path.parent/'raw.bin', bank, meta['pdf_raw_sha256'])
        require(pdf.startswith(b'%PDF-') and meta.get('no_additional_http_attempt') is True,
                'PDF original or extraction provenance missing')
        require(meta.get('extraction') and meta.get('page_count'), 'PDF extraction metadata missing')
        require((path.parent/'raw.bin').read_bytes() == pdf, 'Copied PDF differs from original')
        provenance += [(original_path, original_raw), (pdf_path, pdf)]
    text_path, text_raw = retained(path.parent/meta['source_text_path'], bank,
                                   meta.get('content_sha256', meta.get('source_sha256')))
    text = text_raw.decode('utf-8')
    require(text.strip() and len(text) <= 120_000, 'Empty or oversized source must not be truncated')
    require(all(isinstance(meta.get(k), str) and meta[k].strip() for k in ('url', 'title', 'retrieved_at')),
            'Capture provenance incomplete')
    provenance.append((text_path, text_raw))
    return meta, text_raw, provenance


def plan(root, bank, db):
    """Validate the entire frozen population before creating any result files."""
    root, bank, db = Path(root).resolve(), Path(bank).resolve(), Path(db).resolve()
    frozen = research.read_bundle(root)
    require(not frozen['errors'], 'Existing frozen bundle has errors')
    require(all(path.name.startswith('bounded-') for part in root.glob('part-*')
                for path in (part/'results').glob('*.json')),
            'Existing manual research must not be overwritten by acquisition binding')
    rows = {}
    receipts = {}
    for path in sorted(bank.glob('cursory-prechecks-*/product-checks.json')):
        raw = path.read_bytes()
        data = json.loads(raw)
        require(data.get('classification_writes') == 0 and data.get('model_calls') == 0,
                'Acquisition receipts must not contain classification writes')
        receipts[str(path)] = sha(raw)
        for row in data['records'] + data.get('candidates_pending_scope_review', []):
            pid = row['product_id']
            require(pid not in rows, 'Duplicate acquisition receipt: ' + pid)
            rows[pid] = (path, row)
    missing = sorted(set(frozen['products']) - set(rows))
    require(not missing, 'Unattempted frozen products: ' + ','.join(missing[:30]) + ('...' if len(missing) > 30 else ''))
    supplements = {}
    for path in sorted(bank.glob('mandatory-source-companion-*.json')):
        raw = path.read_bytes(); value = json.loads(raw)
        require(value.get('positive_claim_approved') is False and value.get('classification_writes') == 0,
                'Companion is not unapproved acquisition')
        refs, hashes = value['mandatory_source_metadata'], value['metadata_sha256']
        require(len(refs) == len(hashes) and refs, 'Incomplete mandatory companion hashes')
        for pid in value['product_ids']:
            if pid in frozen['products']:
                supplements.setdefault(pid, []).extend((bank/ref, expected, value['scope']) for ref, expected in zip(refs, hashes))
        receipts[str(path)] = sha(raw)
    contexts = {}
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        require(conn.execute('SELECT COUNT(*) FROM products WHERE vegan_status IS NULL').fetchone()[0] == 0,
                'Finish classification before binding the initially unknown cohort')
        for context in classifier.load_product_contexts(conn, sorted(frozen['products'])):
            contexts[str(context['product']['id'])] = context
    require(set(contexts) == set(frozen['products']), 'Frozen products are missing from the candidate')
    entries = {}
    sources = {}
    retained_files = {}
    for pid, product in frozen['products'].items():
        receipt_path, row = rows[pid]; context = contexts[pid]
        require(context['product']['vegan_status'] == 'unknown', 'Initial unknown changed before acquisition binding: '+pid)
        require(classifier.context_hash(classifier.product_context_for_llm(context)) == row['catalogue_input_hash'],
                'Product context changed after brief check: '+pid)
        require(classifier.context_hash(manufacturer.catalogue_identity_payload(research.catalogue_payload(context))) == product['context_hash'],
                'Frozen catalogue identity changed: '+pid)
        require(row.get('classification_changed') is False and row.get('positive_claim_approved') is False,
                'Acquisition must not approve a product: '+pid)
        outcome = {k: row[k] for k in ('product_id', 'notes', 'checked_at', 'queries') if k in row}
        outcome.update(positive_claim_approved=False, source_ids=[], checked_source_ids=[])
        if row.get('retained_search_result'):
            require(accounting.retained_lookup(row, bank), 'Actual successful lookup missing: '+pid)
            p, raw = retained(row['retained_search_result'], bank, row['retained_search_sha256'])
            dest = 'diagnostics/search-'+sha(raw)+'.json'; retained_files[dest] = raw
            outcome.update(retained_search_result=dest, retained_search_sha256=sha(raw))
        if row['outcome'] == 'blocked':
            require(row.get('check_closed') is True and row.get('closure_reason'), 'Access check is not closed: '+pid)
            require(accounting.closed_access_check({**row,'research_complete':True}, receipt_path.parent),
                    'Unsuccessful access provenance missing: '+pid)
            attempts=[]
            for attempt in row['access_attempts']:
                p,raw=retained(receipt_path.parent/attempt['metadata_path'],bank,attempt['metadata_sha256'])
                meta=json.loads(raw);dest='diagnostics/access-'+sha(raw)+'.json';retained_files[dest]=raw
                attempts.append({'url':meta['url'],'metadata_path':dest,'metadata_sha256':sha(raw)})
            outcome.update(outcome='blocked', check_closed=True, closure_reason=row['closure_reason'], access_attempts=attempts)
        else:
            paths=[]
            if row.get('source_metadata'):
                paths.append((Path(row['source_metadata']), None, row.get('publisher_match_notes') or 'Manufacturer page inspected during the bounded check; no applicable claim approved'))
            paths.extend(supplements.get(pid, []))
            source_refs=[]
            for p, expected, association_notes in paths:
                p, _ = retained(p, bank, expected)
                meta, raw, provenance = capture(p, bank)
                if str(p) == row.get('source_metadata'):
                    require(sha(raw) == row.get('source_text_sha256'), 'Receipt source text changed: '+pid)
                    require(meta['url'] == row.get('source_url'), 'Receipt source URL changed: '+pid)
                source = {'url':meta['url'], 'title':meta['title'], 'retrieved_at':meta['retrieved_at'],
                          'official_identity':row.get('publisher_match_notes') or association_notes,
                          'market_scope':'Verify applicability to the exact UK catalogue product, native variant, format and applicable production period; all source restrictions remain in full text.',
                          'content_sha256':sha(raw)}
                sid=research.digest({'url':source['url'],'sha256':source['content_sha256'],'retrieved_at':source['retrieved_at']})
                # Publisher provenance describes the publication, not each SKU.
                if sid not in sources:
                    sources[sid]=(source,raw)
                else:
                    require(sources[sid][0]['url']==source['url'] and sources[sid][1]==raw, 'Conflicting full source capture')
                source_refs.append(sid)
                for pp, rr in provenance:
                    dest='diagnostics/provenance-'+sha(rr)+pp.suffix;retained_files[dest]=rr
            if row['outcome']=='no_confirmation' and not supplements.get(pid):
                outcome.update(outcome='no_confirmation',checked_source_ids=sorted(set(source_refs)))
                require(source_refs or outcome.get('retained_search_result'), 'Completed check has no retained evidence: '+pid)
            elif row['outcome']=='pending' or supplements.get(pid):
                require(source_refs, 'Source candidate has no full capture: '+pid)
                outcome.update(outcome='source_collected', source_ids=sorted(set(source_refs)),classification_review_required=True)
            else:
                raise ValueError('Unsupported acquisition outcome: '+pid)
        entries[pid]=outcome
    return {'entries':entries,'sources':sources,'files':retained_files,'receipt_hashes':receipts,
            'snapshot_sha256':sha((root/'snapshot.json').read_bytes()),'products':frozen['products']}


def build(root, bank, db, authorization, write=False):
    require(isinstance(authorization,str) and authorization.strip(), 'Explicit bounded-search authorization required')
    root=Path(root).resolve(); value=plan(root,bank,db)
    summary={'total_products':len(value['entries']),'sources':len(value['sources']),
             'outcomes':{kind:sum(row['outcome']==kind for row in value['entries'].values())
                         for kind in ('source_collected','no_confirmation','blocked')},
             'classification_writes':0,'model_calls':0,'snapshot_sha256':value['snapshot_sha256']}
    if not write:return summary
    for rel,raw in value['files'].items():
        path=root/rel;path.parent.mkdir(exist_ok=True)
        require(not path.exists() or path.read_bytes()==raw,'Concurrent diagnostic change')
        if not path.exists():path.write_bytes(raw)
    for part in sorted(root.glob('part-*')):
        catalogue=json.loads((part/'catalogue.json').read_text())
        for brand,products in catalogue.items():
            ids=[row['product_id'] for row in products]; source_ids={sid for pid in ids for key in ('source_ids','checked_source_ids') for sid in value['entries'][pid][key]}
            sources=[]
            for sid in sorted(source_ids):
                source,raw=value['sources'][sid];path=part/'sources'/(sid+'.txt')
                require(not path.exists() or path.read_bytes()==raw,'Concurrent source change')
                if not path.exists():path.write_bytes(raw)
                sources.append({**source,'source_text_path':str(path)})
            result=part/'results'/('bounded-'+sha(brand.encode())+'.json')
            record={'brand':brand,'research_complete':True,'sources':sources,
                    'products':[value['entries'][pid] for pid in ids]}
            require(not result.exists() or json.loads(result.read_text())==record,
                    'Refusing to overwrite changed acquisition results')
            if not result.exists():research.write_json(result,record)
    research.write_json(root/'bounded-acquisition-manifest.json',summary|{'receipt_hashes':value['receipt_hashes'],
        'diagnostic_hashes':{rel:sha(raw) for rel,raw in value['files'].items()}})
    research.write_json(root/'bounded-research-policy.json',{'mode':accounting.MODE,'authorization':authorization,
                        'positive_validation_unchanged':True})
    status=research.bundle_status(research.read_bundle(root))
    require(research.gathering_complete(status),'Completed acquisition does not pass bounded accounting')
    return summary|{'accounting':status}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--bank',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True);parser.add_argument('--authorization',required=True)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    print(json.dumps(build(args.root,args.bank,args.db,args.authorization,args.write)))


if __name__=='__main__':main()
