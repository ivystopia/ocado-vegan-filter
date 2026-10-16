"""Retained suitability audits permit no source, status or membership change."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import repair_vegan_attribution as repair
import refresh_whole_product as refresh
import benchmark_whole_product as benchmark


class AttributionRepairTests(unittest.TestCase):
    def score(self):
        return {'state':'failed','reference_counts':{'vegan':600},'sampling':{'size':600},
                'issues':[{'case_id':'audit.1','finding':'unsupported_reason','candidate_reason':'name','reference_reason':'manufacturer'}]}

    def test_only_complete_all_vegan_name_to_manufacturer_findings_qualify(self):
        self.assertEqual(repair.attribution_cases(self.score()),['audit.1'])
        for field, value in [('state','passed'),('reference_counts',{'vegan':599,'unknown':1}),('sampling',{'size':599}),('issues',[])]:
            record=self.score();record[field]=value
            with self.assertRaises(ValueError): repair.attribution_cases(record)
        for field,value in [('finding','unknown'),('candidate_reason','ingredients'),('reference_reason','tagged')]:
            record=self.score();record['issues'][0][field]=value
            with self.assertRaises(ValueError): repair.attribution_cases(record)

    def test_signature_allows_only_target_reason_and_classification_audit_metadata(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('CREATE TABLE products(id TEXT,name TEXT,vegan_status TEXT,vegan_reason TEXT,vegan_audit_id INTEGER,vegan_classifier_version TEXT,vegan_classified_at_epoch INTEGER,vegan_positive_block INTEGER,vegan_evidence_hash TEXT,current_on_ocado INTEGER)')
            conn.execute('CREATE TABLE product_vegan_conflicts(product_id TEXT,issue_hash TEXT,details TEXT)')
            conn.executemany('INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?)',[
                ('1','Vegan powder','vegan','name',1,'old',1,0,'hash',1),('2','Carrot','vegan','ingredients',2,'old',1,0,'hash2',0)])
            before=repair.product_signature(conn,target_ids={'1'})
            conn.execute("UPDATE products SET vegan_reason='manufacturer',vegan_audit_id=3,vegan_classifier_version='new',vegan_classified_at_epoch=2 WHERE id='1'")
            self.assertEqual(before,repair.product_signature(conn,target_ids={'1'}))
            for field,value in [('name','Changed'),('vegan_positive_block',1),('vegan_evidence_hash','different'),('current_on_ocado',0)]:
                conn.execute('SAVEPOINT attempt');conn.execute('UPDATE products SET '+field+"=? WHERE id='1'",(value,))
                self.assertNotEqual(before,repair.product_signature(conn,target_ids={'1'}));conn.execute('ROLLBACK TO attempt');conn.execute('RELEASE attempt')
            conn.execute("UPDATE products SET vegan_reason='name' WHERE id='2'")
            self.assertNotEqual(before,repair.product_signature(conn,target_ids={'1'}))
            conn.execute("UPDATE products SET vegan_status='unknown' WHERE id='1'")
            with self.assertRaises(ValueError):repair.product_signature(conn,target_ids={'1'})

    def test_conflict_changes_cannot_hide_inside_reason_metadata(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('CREATE TABLE products(id TEXT,vegan_status TEXT,vegan_reason TEXT)')
            conn.execute('CREATE TABLE product_vegan_conflicts(product_id TEXT,issue_hash TEXT)')
            conn.execute("INSERT INTO products VALUES ('1','vegan','name')")
            before=repair.product_signature(conn,target_ids={'1'})
            conn.execute("INSERT INTO product_vegan_conflicts VALUES ('1','new')")
            self.assertNotEqual(before,repair.product_signature(conn,target_ids={'1'}))

    def test_no_authorisation_never_writes_manifest_or_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);proof=root/'proof.json';proof.write_text('{}')
            manifest=root/'manifest.json';manifest.write_text('{"state":"checkpointed"}')
            before=manifest.read_bytes()
            with self.assertRaisesRegex(ValueError,'authorisation'):repair.repair(root,proof)
            self.assertEqual(manifest.read_bytes(),before)
            self.assertFalse((root/'candidate.sqlite').exists())

    def test_corrected_artifacts_are_bound_alongside_original_score(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in (*repair.FILES,'positive-score.attribution-corrected.json','positive-audit.attribution-corrected.predictions.json','attribution-repair.json'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
            runner=object.__new__(refresh.Refresh);runner.root=root;runner.manifest={'attribution_repair':{'receipt':'attribution-repair.json'}}
            artifacts=runner.artifacts('positive_audit')
            self.assertEqual(len(artifacts),7)
            self.assertIn('positive-score.json',artifacts)
            self.assertIn('positive-score.attribution-corrected.json',artifacts)
            old=artifacts['attribution-repair.json'];(root/'attribution-repair.json').write_text('tampered')
            self.assertNotEqual(old,runner.artifacts('positive_audit')['attribution-repair.json'])

    def test_code_binding_rejects_unrelated_policy_or_transport_changes(self):
        current=benchmark.candidate_hashes()
        previous=copy.deepcopy(current);previous['tools/codex_evidence.py']='changed'
        with self.assertRaisesRegex(ValueError,'Unrelated'):repair.validate_code(previous,current,{})

    def test_same_code_projection_accepts_exact_copies(self):
        current=benchmark.candidate_hashes()
        names=('tools/classify_ocado_vegan.py','tools/vegan_policy.py','tools/refresh_whole_product.py')
        self.assertEqual(repair.validate_code(current,current,{name:str(benchmark.ROOT/name) for name in names}),[])
