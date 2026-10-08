"""Policy migration scope, immutable checkpoints and atomic promotion contracts."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import classify_ocado_vegan as classifier
import refresh_whole_product as refresh
import benchmark_whole_product as benchmark
import manufacturer_evidence as manufacturer


class RefreshTests(unittest.TestCase):
    def test_historical_category_recovery_preserves_current_evidence_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original.sqlite';working=root/'working.sqlite'
            with closing(sqlite3.connect(original)) as conn:
                conn.executescript("CREATE TABLE product_categories(product_id TEXT,category_path TEXT,PRIMARY KEY(product_id,category_path)); INSERT INTO product_categories VALUES ('old','wine'),('current','stale');")
            with closing(sqlite3.connect(working)) as conn:
                conn.executescript("CREATE TABLE products(id TEXT,current_on_ocado INTEGER);INSERT INTO products VALUES ('old',0),('current',1); CREATE TABLE product_categories(product_id TEXT,category_path TEXT,PRIMARY KEY(product_id,category_path)); INSERT INTO product_categories VALUES ('current','fresh');")
            self.assertEqual(refresh.restore_historical_categories(working,original),1)
            self.assertEqual(refresh.restore_historical_categories(working,original),0)
            with closing(sqlite3.connect(working)) as conn:
                self.assertEqual(conn.execute('SELECT * FROM product_categories ORDER BY product_id').fetchall(),[('current','fresh'),('old','wine')])

    def test_scrape_repair_preserves_baseline_and_reuses_only_unchanged_classifier_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.sqlite';fixture=root/'fixture.json';fixture.write_text('{}')
            self.make_db(source)
            original_hashes={'tools/sync_ocado_database.py':'old','tools/classify_ocado_vegan.py':'fixed'}
            with mock.patch.object(refresh.subprocess,'check_output',return_value='dev\n'), mock.patch.object(benchmark,'candidate_hashes',return_value=original_hashes):
                old=refresh.Refresh(root/'old',source,fixture)
            refresh.clone(source,old.root/'original.sqlite');refresh.clone(source,old.working)
            with sqlite3.connect(old.working) as conn:
                conn.execute('CREATE TABLE sync_context_baseline(product_id TEXT,context_json TEXT)')
                conn.execute("INSERT INTO sync_context_baseline VALUES ('1','preserved')")
            with closing(refresh.readonly(source)) as conn: baseline=refresh.fingerprint(conn)
            report={'fixture_sha256':classifier.context_hash({}), 'implementation_hashes':{'classify_ocado_vegan.py':classifier.context_hash((refresh.ROOT/'tools/classify_ocado_vegan.py').read_text())}}
            for name in ('preflight-controls','preflight-pilot'):
                benchmark.save(old.root/name/'report.json',report)
            benchmark.save(old.root/'checks/checks.json',{})
            old.manifest.update(canonical_baseline=baseline,original_population={'all_known':1,'current':1},pre_sync_id=4)
            old.manifest['stages']={stage:{'state':'completed','artifacts':old.artifacts(stage)} for stage in ('checks','preflight')}
            old.manifest['stages']['scrape']={'state':'checkpointed','error':'verified sitemap omission'};old.save()
            updated={**original_hashes,'tools/sync_ocado_database.py':'new'}
            with mock.patch.object(refresh.subprocess,'check_output',return_value='dev\n'), mock.patch.object(benchmark,'candidate_hashes',return_value=updated), mock.patch.object(benchmark,'successful',return_value=True):
                new=refresh.Refresh(root/'new',source,fixture);new.fork_scrape_repair(old.root)
                self.assertEqual(new.manifest['canonical_baseline'],baseline)
                self.assertEqual(new.manifest['scrape_repair_origin']['changed_paths'],['tools/sync_ocado_database.py'])
                with closing(refresh.readonly(new.working)) as conn:
                    self.assertEqual(conn.execute('SELECT context_json FROM sync_context_baseline').fetchone()[0],'preserved')
                with mock.patch.object(new,'bench',side_effect=AssertionError('must not repeat models')): new.step_preflight()
            with mock.patch.object(refresh.subprocess,'check_output',return_value='dev\n'), mock.patch.object(benchmark,'candidate_hashes',return_value={**updated,'tools/classify_ocado_vegan.py':'changed'}):
                invalid=refresh.Refresh(root/'invalid',source,fixture)
                with self.assertRaisesRegex(refresh.GateError,'cannot cover classifier'): invalid.fork_scrape_repair(old.root)

    def test_all_known_scope_includes_unchanged_and_historical_with_sync_attribution(self):
        conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
        conn.executescript('''CREATE TABLE products(id TEXT,vegan_status TEXT,current_on_ocado INTEGER);
            CREATE TABLE sync_product_context_changes(run_id INTEGER,product_id TEXT,change_kind TEXT);
            INSERT INTO products VALUES ('new',NULL,1),('unchanged',NULL,1),('historical',NULL,0),('finished','unknown',1);
            INSERT INTO sync_product_context_changes VALUES (5,'new','new');''')
        self.assertEqual(classifier.select_unclassified_product_ids(conn, sync_run_id=5), ['new'])
        self.assertEqual(classifier.select_unclassified_product_ids(conn, sync_run_id=5,selection_scope='all-known'), ['historical','new','unchanged'])
        with self.assertRaises(ValueError): classifier.select_unclassified_product_ids(conn,selection_scope='current-only')
        args = classifier.build_parser().parse_args(['--preserve-legacy','classify-all','--scope','all-known','--sync-run-id','5'])
        self.assertEqual((args.scope,args.sync_run_id,args.preserve_legacy),('all-known',5,True))
        conn.close()

    def test_run_scope_is_recorded_independently_of_sync_id(self):
        conn=sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE products(id TEXT PRIMARY KEY)')
        classifier.ensure_classification_schema(conn,drop_legacy=False)
        run=classifier.start_run(conn,mode='rules',sync_run_id=5,selection_scope='all-known')
        self.assertEqual(conn.execute('SELECT sync_run_id,selection_scope FROM vegan_classification_runs WHERE id=?',(run,)).fetchone(),(5,'all-known'))
        conn.close()

    def test_name_census_does_not_count_manufacturer_unknown_or_disputed_cases(self):
        def case(status='vegan',reason='name',state='accepted'):
            return {'adjudication':{'state':state},'expected':{'vegan_status':status,'vegan_reason':reason} if state=='accepted' else None}
        packet={'candidates':[case() for _ in range(49)]+[case(reason='manufacturer'),case('unknown',None),case(state='disputed')], 'disputed_cases':['one']}
        self.assertEqual(refresh.census_gate(packet)['state'],'blocked_natural_quota')
        packet['candidates'].append(case())
        self.assertEqual(refresh.census_gate(packet)['accepted_name_only'],50)

    def make_db(self,path):
        with sqlite3.connect(path) as conn:
            conn.executescript('CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT); CREATE TABLE history(id INTEGER PRIMARY KEY,note TEXT); INSERT INTO products VALUES (\'1\',\'Old\'); INSERT INTO history VALUES (1,\'previous audit\');')

    def test_fingerprint_covers_labels_sources_and_without_rowid_metadata(self):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        conn.execute('CREATE TABLE records(id TEXT PRIMARY KEY,value BLOB) WITHOUT ROWID')
        conn.execute('INSERT INTO records VALUES (?,?)',('one',b'original'))
        before=refresh.fingerprint(conn)
        self.assertEqual(before,refresh.fingerprint(conn))
        conn.execute('UPDATE records SET value=?',(b'changed',))
        self.assertNotEqual(before,refresh.fingerprint(conn));conn.close()

    def test_activation_preserves_history_adds_schema_and_rejects_concurrent_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.sqlite';candidate=root/'candidate.sqlite'
            self.make_db(source);refresh.clone(source,candidate)
            with closing(refresh.readonly(source)) as conn: baseline=refresh.fingerprint(conn)
            with sqlite3.connect(candidate) as conn:
                conn.execute('ALTER TABLE products ADD COLUMN status TEXT')
                conn.execute("UPDATE products SET name='New',status='unknown'")
                conn.execute("INSERT INTO history VALUES (2,'fresh audit')")
            with mock.patch.object(refresh,'validate_candidate'):
                refresh.activate(source,candidate,baseline,root/'backup.sqlite')
            with sqlite3.connect(source) as conn:
                self.assertEqual(conn.execute('SELECT * FROM products').fetchone(),('1','New','unknown'))
                self.assertEqual(conn.execute('SELECT count(*) FROM history').fetchone()[0],2)
                baseline=refresh.fingerprint(conn)
                conn.execute("UPDATE products SET name='Concurrent'")
            with mock.patch.object(refresh,'validate_candidate'):
                with self.assertRaisesRegex(refresh.GateError,'changed'):
                    refresh.activate(source,candidate,baseline,root/'concurrent-backup.sqlite')
            with sqlite3.connect(source) as conn:self.assertEqual(conn.execute('SELECT name FROM products').fetchone()[0],'Concurrent')

    def test_activation_failure_rolls_back_previous_tables_and_new_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.sqlite';candidate=root/'candidate.sqlite'
            self.make_db(source);refresh.clone(source,candidate)
            with closing(refresh.readonly(source)) as conn: baseline=refresh.fingerprint(conn)
            with sqlite3.connect(candidate) as conn:
                conn.execute('CREATE TABLE aaa_new(id INTEGER)')
                conn.execute('ALTER TABLE products RENAME COLUMN name TO unsupported_name')
                conn.execute("UPDATE products SET unsupported_name='New'")
            with mock.patch.object(refresh,'validate_candidate'):
                with self.assertRaisesRegex(refresh.GateError,'Unmatched columns'):
                    refresh.activate(source,candidate,baseline,root/'backup.sqlite')
            with sqlite3.connect(source) as conn:
                self.assertEqual(refresh.fingerprint(conn),baseline)
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='aaa_new'").fetchone())

    def test_activation_is_not_available_before_all_prerequisites(self):
        runner=object.__new__(refresh.Refresh);runner.manifest={'stages':{}}
        with self.assertRaisesRegex(refresh.GateError,'All qualification'):
            runner.step_activation()

    def test_resume_rejects_source_or_code_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture=root/'fixture.json';fixture.write_text('{}')
            with mock.patch.object(refresh.subprocess,'check_output',return_value='dev\n'), mock.patch.object(refresh.benchmark,'candidate_hashes',return_value={'test':'one'}):
                runner=refresh.Refresh(root/'run',root/'source.sqlite',fixture)
                refresh.Refresh(root/'run',root/'source.sqlite',fixture)
                with self.assertRaisesRegex(refresh.GateError,'Resume inputs'):
                    refresh.Refresh(root/'run',root/'different.sqlite',fixture)
                runner.manifest['configuration']['code_hashes']={'test':'changed'};runner.save()
                with self.assertRaisesRegex(refresh.GateError,'Resume inputs'):
                    refresh.Refresh(root/'run',root/'source.sqlite',fixture)

    def test_raw_negative_capture_is_retained_without_collector_promotion_and_import_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'candidate.sqlite'
            context=classifier.product_context_for_llm({'product':{'id':'1','name':'Example Wine'},'flags':[], 'categories':[], 'manufacturer_vegan_evidence':[]})
            with closing(benchmark.fixture_database([{'classifier_input':context}])) as fixture, closing(sqlite3.connect(db)) as target:
                fixture.commit()
                fixture.backup(target)
            with closing(classifier.connect(db)) as conn:
                conn.execute("UPDATE products SET vegan_status='unknown'");conn.commit()
                context=classifier.load_product_context(conn,'1')
                identity=classifier.context_hash(manufacturer.catalogue_identity_payload(refresh.research.catalogue_payload(context)))
            text='Example Wine uses animal-derived casein during manufacture.'
            source={'id':'raw','url':'https://example.test/wine','title':'Wine','retrieved_at':'2026-10-08',
                    'official_identity':'Example manufacturer','market_scope':'UK','source_text':text,
                    'content_sha256':refresh.hashlib.sha256(text.encode()).hexdigest()}
            bundle={'products':{'1':{'context_hash':identity,'current_on_ocado':0}},'sources':{'raw':source},'brands':1,'errors':[],
                    'outcomes':{'1':{'outcome':'no_confirmation','research_complete':True,'source_ids':['raw'],'quote':text}}}
            refresh.retain_refresh_sources(db,bundle,root)
            with closing(classifier.connect(db)) as conn:
                context=classifier.load_product_context(conn,'1')
                rows=classifier.product_context_for_llm(context)['manufacturer_source_candidates']
                self.assertEqual(rows[0]['source_text'],text)
                self.assertEqual(rows[0]['association_status'],'catalogue_match')
                self.assertEqual(rows[0]['retrieved_at'],'2026-10-08')
                self.assertIsNone(conn.execute('SELECT vegan_status FROM products').fetchone()[0])
            self.assertTrue(refresh.retain_refresh_sources(db,bundle,root)['resumed'])
            bundle['outcomes']['1']['research_complete']=False
            with self.assertRaisesRegex(refresh.GateError,'Incomplete manufacturer'):
                refresh.retain_refresh_sources(db,bundle,root)
