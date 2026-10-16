"""Frozen-cohort acquisition binding cannot approve or lose product decisions."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import build_bounded_manufacturer_bundle as builder
import build_ocado_database as database
import classify_ocado_vegan as classifier
import research_ocado_manufacturers as research
import sync_ocado_database as sync


class AcquisitionBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.bank=self.base/'bank';self.bank.mkdir()
        self.root=self.base/'research';self.db=self.base/'candidate.sqlite'
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row;database.create_schema(conn);sync.ensure_sync_schema(conn);classifier.ensure_classification_schema(conn)
            conn.executemany('INSERT INTO products(id,name,brand,current_on_ocado,vegan_status) VALUES(?,?,?,?,?)',
                             [('1','Example Wine','Example',1,'unknown'),('2','Second Wine','Second',0,'unknown')]);conn.commit()
        research.create_snapshot(self.root,self.db,2)
        source=self.bank/'source';source.mkdir();text=b'Example wine is vegan, but some bottles use animal glue.'
        (source/'source.txt').write_bytes(text)
        self.meta={'url':'https://example.test/faq','title':'Producer FAQ','retrieved_at':'2026-10-10T00:00:00Z',
                   'state':'captured_for_scope_review','source_text_path':'source.txt','content_sha256':hashlib.sha256(text).hexdigest()}
        self.save(source/'metadata.json',self.meta)
        self.rows=[]
        with closing(classifier.connect(self.db)) as conn:
            for pid in ('1','2'):
                ctx=classifier.load_product_context(conn,pid)
                self.rows.append({'product_id':pid,'brand':ctx['product']['brand'],'name':ctx['product']['name'],
                    'catalogue_input_hash':classifier.context_hash(classifier.product_context_for_llm(ctx)),
                    'classification_changed':False,'positive_claim_approved':False,'outcome':'pending',
                    'source_metadata':str(source/'metadata.json'),'source_url':self.meta['url'],
                    'source_text_sha256':self.meta['content_sha256'],'publisher_match_notes':'Exact producer website',
                    'notes':'Unapproved source; full claim and component qualification must both be assessed',
                    'checked_at':'2026-10-10T01:00:00Z'})
        self.receipt=self.bank/'cursory-prechecks-test'/'product-checks.json';self.receipt.parent.mkdir()
        self.receipt_save()

    def save(self,path,value):path.write_text(json.dumps(value))
    def receipt_save(self):self.save(self.receipt,{'records':[],'candidates_pending_scope_review':self.rows,'classification_writes':0,'model_calls':0})

    def test_current_and_historical_sources_bind_without_approving_labels(self):
        result=builder.build(self.root,self.bank,self.db,'Approved brief checks',write=True)
        self.assertEqual(result['outcomes']['source_collected'],2)
        self.assertEqual(result['accounting']['current_products'],1)
        self.assertEqual(result['sources'],1)
        self.assertTrue(research.gathering_complete(result['accounting']))
        bundle=research.read_bundle(self.root)
        self.assertEqual(len(bundle['sources']),1)
        self.assertIn('animal glue',next(iter(bundle['sources'].values()))['source_text'])
        with sqlite3.connect(self.db) as conn:self.assertEqual(conn.execute('SELECT DISTINCT vegan_status FROM products').fetchall(),[('unknown',)])

    def test_interrupted_result_writes_resume_idempotently(self):
        first=builder.build(self.root,self.bank,self.db,'Approved brief checks',write=True)
        (self.root/'bounded-research-policy.json').unlink()
        second=builder.build(self.root,self.bank,self.db,'Approved brief checks',write=True)
        self.assertEqual(first,second)

    def test_missing_individual_check_refuses_all_writes(self):
        self.rows.pop();self.receipt_save()
        with self.assertRaisesRegex(ValueError,'Unattempted frozen'):builder.build(self.root,self.bank,self.db,'Approved',write=True)
        self.assertFalse((self.root/'bounded-research-policy.json').exists())
        self.assertFalse(any(self.root.glob('part-*/results/*.json')))

    def test_context_change_refuses_binding(self):
        with sqlite3.connect(self.db) as conn:conn.execute("UPDATE products SET name='Different Wine' WHERE id='2'")
        with self.assertRaisesRegex(ValueError,'context changed'):builder.build(self.root,self.bank,self.db,'Approved',write=True)

    def test_unclassified_product_prevents_final_cohort_binding(self):
        with sqlite3.connect(self.db) as conn:conn.execute("UPDATE products SET vegan_status=NULL WHERE id='2'")
        with self.assertRaisesRegex(ValueError,'Finish classification'):builder.build(self.root,self.bank,self.db,'Approved')

    def test_source_change_cannot_be_rehashed_into_accepted_evidence(self):
        (self.bank/'source/source.txt').write_text('All products are vegan.')
        with self.assertRaisesRegex(ValueError,'Changed retained'):builder.build(self.root,self.bank,self.db,'Approved')

    def test_retained_provenance_change_blocks_import(self):
        builder.build(self.root,self.bank,self.db,'Approved brief checks',write=True)
        next((self.root/'diagnostics').glob('provenance-*.json')).write_text('{}')
        status=research.bundle_status(research.read_bundle(self.root))
        self.assertFalse(research.gathering_complete(status))
        self.assertTrue(any('provenance changed' in error for error in status['errors']))

    def test_no_authorization_cannot_bind_acquisition(self):
        with self.assertRaisesRegex(ValueError,'authorization'):builder.build(self.root,self.bank,self.db,'',write=True)

    def test_extracted_faq_preserves_parent_raw_and_rejects_tampering(self):
        parent=self.bank/'parent';parent.mkdir()
        original=b'<script type="application/ld+json">published FAQ</script>'
        (parent/'raw.bin').write_bytes(original)
        self.save(parent/'metadata.json',{'url':self.meta['url']})
        source=self.bank/'source'
        (source/'raw.bin').write_bytes(original)
        metadata={**self.meta,'state':'public_structured_faq_needs_scope_review',
                  'parent_metadata_path':'parent/metadata.json','raw_sha256':hashlib.sha256(original).hexdigest()}
        self.save(source/'metadata.json',metadata)
        result=builder.build(self.root,self.bank,self.db,'Approved brief checks',write=True)
        self.assertTrue(research.gathering_complete(result['accounting']))
        raw_files=list((self.root/'diagnostics').glob('provenance-*.bin'))
        self.assertTrue(any(p.read_bytes()==original for p in raw_files))
        (parent/'raw.bin').write_text('Changed original source')
        with self.assertRaisesRegex(ValueError,'Changed retained'):builder.build(self.root,self.bank,self.db,'Approved')

    def test_mandatory_companion_retains_opposing_vintages_without_latest_capture_precedence(self):
        companion=self.bank/'companion';companion.mkdir()
        raw=b'2021 Example Wine is not suitable for vegans.'
        (companion/'source.txt').write_bytes(raw)
        metadata={**self.meta,'content_sha256':hashlib.sha256(raw).hexdigest(),'retrieved_at':'2026-10-10T02:00:00Z'}
        self.save(companion/'metadata.json',metadata)
        self.save(self.bank/'mandatory-source-companion-test.json',{'product_ids':['1'],
            'mandatory_source_metadata':['source/metadata.json','companion/metadata.json'],
            'metadata_sha256':[hashlib.sha256((self.bank/p).read_bytes()).hexdigest() for p in ('source/metadata.json','companion/metadata.json')],
            'scope':'Both variants apply only to their respective vintage; native wine has no vintage',
            'positive_claim_approved':False,'classification_writes':0})
        result=builder.build(self.root,self.bank,self.db,'Approved',write=True)
        self.assertTrue(research.gathering_complete(result['accounting']))
        bundle=research.read_bundle(self.root)
        self.assertEqual(len(bundle['outcomes']['1']['source_ids']),2)
        texts=[bundle['sources'][sid]['source_text'] for sid in bundle['outcomes']['1']['source_ids']]
        self.assertTrue(any('not suitable' in text for text in texts))
        self.assertTrue(any('animal glue' in text for text in texts))
