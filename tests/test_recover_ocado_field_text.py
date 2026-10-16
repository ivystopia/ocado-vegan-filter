from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import unittest
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import build_ocado_database as importer
import classify_ocado_vegan as classifier
import recover_ocado_field_text as recovery
import refresh_whole_product as refresh
import sync_ocado_database as sync


class RetainedTextRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        importer.create_schema(self.conn);sync.ensure_sync_schema(self.conn)
        classifier.ensure_classification_schema(self.conn,drop_legacy=False)
        raw={'product':{'retailerProductId':'1','name':'Example Drink','brand':'Example'},
             'bopData':{'fields':[{'title':'ingredients','content':'<table><tr><td>Water, <5% Milk. Suitable for vegans.</td></tr></table>'}]}}
        data=json.dumps(raw).encode();self.raw_hash=hashlib.sha256(data).hexdigest()
        self.conn.execute("INSERT INTO products(id,name,brand,ingredients,vegan_status,vegan_reason,vegan_audit_id,current_on_ocado) VALUES ('1','Example Drink','Example',?,'vegan','ingredients',42,1)",
                          (recovery.legacy_text(raw['bopData']['fields'][0]['content']),))
        self.conn.execute('INSERT INTO product_detail_raw_latest VALUES (?,?,?,?,?)',('1',7,self.raw_hash,zlib.compress(data),123))
        self.conn.commit()

    def tearDown(self):self.conn.close()

    def test_recover_complete_text_invalidate_and_retain_prior_decision(self):
        before=refresh.fingerprint(self.conn);plan=recovery.preview(self.conn)
        self.assertEqual(plan['changed_products'],1);self.assertEqual(plan['http_attempts'],0)
        self.conn.execute('BEGIN IMMEDIATE');r=recovery.apply(self.conn,plan,before)
        row=self.conn.execute('SELECT ingredients,vegan_status,vegan_audit_id FROM products').fetchone()
        self.assertEqual(tuple(row),('Water, <5% Milk. Suitable for vegans.',None,None))
        saved=json.loads(self.conn.execute('SELECT previous_and_recovered_json FROM product_text_projection_audit').fetchone()[0])
        self.assertEqual(saved['previous_audit_id'],42);self.assertEqual(saved['raw_sha256'],self.raw_hash)
        self.assertEqual(r['new_model_calls'],0)
        self.assertEqual(self.conn.execute('SELECT content FROM manufacturer_vegan_evidence').fetchone()[0],row['ingredients'])
        self.conn.rollback();self.assertEqual(refresh.fingerprint(self.conn),before)

    def test_concurrent_change_rejected_before_writing(self):
        before=refresh.fingerprint(self.conn);plan=recovery.preview(self.conn)
        self.conn.execute("UPDATE products SET name='Concurrent Edit'");self.conn.commit()
        self.conn.execute('BEGIN IMMEDIATE')
        with self.assertRaisesRegex(ValueError,'Database changed'):recovery.apply(self.conn,plan,before)
        self.conn.rollback();self.assertEqual(self.conn.execute('SELECT name FROM products').fetchone()[0],'Concurrent Edit')

    def test_already_recovered_native_text_is_not_repaired_or_invalidated_again(self):
        before=refresh.fingerprint(self.conn);plan=recovery.preview(self.conn)
        self.conn.execute('BEGIN IMMEDIATE');recovery.apply(self.conn,plan,before);self.conn.commit()
        self.conn.execute("UPDATE products SET vegan_status='unknown'");self.conn.commit()
        repeated=recovery.preview(self.conn)
        self.assertEqual(repeated['changed_products'],0)
        self.assertEqual(repeated['unmatched_projection'],[])
        before=refresh.fingerprint(self.conn)
        self.conn.execute('BEGIN IMMEDIATE');recovery.apply(self.conn,repeated,before);self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM product_text_projection_audit').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT vegan_status FROM products').fetchone()[0],'unknown')

    def test_changed_raw_capture_and_nonmatching_projection_fail_closed(self):
        self.conn.execute("UPDATE products SET ingredients='User correction'");self.conn.commit()
        self.assertEqual(recovery.preview(self.conn)['unmatched_projection'],[{'product_id':'1','fields':['ingredients']}])
        self.conn.execute("UPDATE product_detail_raw_latest SET response_sha256='bad'");self.conn.commit()
        with self.assertRaisesRegex(ValueError,'hash mismatch'):recovery.preview(self.conn)


if __name__=='__main__':unittest.main()
