"""Policy migration scope, immutable checkpoints and atomic promotion contracts."""
from contextlib import closing
import hashlib
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
    def test_empty_native_recovery_plan_has_no_write_or_fingerprint(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            plan={'changes':[],'unmatched_projection':[]}
            with mock.patch.object(refresh,'fingerprint',side_effect=AssertionError('Unneeded history scan')), \
                 mock.patch.object(refresh.text_recovery,'apply') as apply:
                result=refresh.apply_text_plan_if_needed(conn,plan)
                apply.assert_not_called()
            self.assertEqual(result['state'],'validated-unchanged')
            self.assertEqual(result['changed_products'],0)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.total_changes,0)
            with self.assertRaisesRegex(refresh.GateError,'cannot be matched'):
                refresh.apply_text_plan_if_needed(conn,{'changes':[],'unmatched_projection':['bad']})

    def test_nonempty_native_recovery_retains_concurrent_change_guard(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT)');conn.commit()
            plan={'changes':[{'product_id':'1'}],'unmatched_projection':[]}
            with mock.patch.object(refresh,'fingerprint',side_effect=['before','changed']) as fingerprint:
                with self.assertRaisesRegex(ValueError,'Database changed'):
                    refresh.apply_text_plan_if_needed(conn,plan)
                self.assertEqual(fingerprint.call_count,2)
            conn.rollback()
            self.assertEqual(conn.execute('SELECT count(*) FROM products').fetchone()[0],0)

    def test_audit_repair_requires_failed_audit_and_preserves_frozen_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manufacturer=root/'manufacturer';manufacturer.mkdir()
            snapshot=manufacturer/'snapshot.json';snapshot.write_text('{}')
            bundle={'outcomes':{},'sources':{},'shared_associations':{},'products':{},'brands':0,'errors':[]}
            origin={'root':str(manufacturer.resolve()),'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                    'bundle_hash':refresh.classifier.context_hash({k:bundle[k] for k in ('outcomes','sources','shared_associations')})}
            old={'immutable_manufacturer_root':str(manufacturer),'stages':{
                'assessment':{'state':'completed','artifacts':{'manufacturer/snapshot.json':origin['snapshot_sha256']}},
                'research':{'state':'completed'},'positive_audit':{'state':'checkpointed'}}}
            (root/'positive-score.json').write_text('{"state":"failed"}')
            with mock.patch.object(refresh.research,'read_bundle',return_value=bundle),mock.patch.object(refresh.research,'gathering_complete',return_value=True):
                self.assertEqual(refresh.validate_research_protocol_origin(root,old,{'research_continuation':origin},audit_repair=True),origin)
                with self.assertRaises(refresh.GateError):refresh.validate_research_protocol_origin(root,old,{'research_continuation':origin})
                (root/'positive-score.json').write_text('{"state":"passed"}')
                with self.assertRaisesRegex(refresh.GateError,'failed positive audit'):
                    refresh.validate_research_protocol_origin(root,old,{'research_continuation':origin},audit_repair=True)
                (root/'positive-score.json').write_text('{"state":"failed"}')
                old['stages']['activation']={'state':'completed'}
                with self.assertRaisesRegex(refresh.GateError,'consumed positive audit'):
                    refresh.validate_research_protocol_origin(root,old,{'research_continuation':origin},audit_repair=True)

    def test_policy_repair_refuses_running_origin_and_failed_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); previous = root/'old'; previous.mkdir()
            evidence = root/'repair.json'; evidence.write_text('{}')
            runner = object.__new__(refresh.Refresh)
            runner.root = root/'new'; runner.candidate = runner.root/'candidate.sqlite'
            runner.manifest = {'stages':{}, 'configuration':{}}
            (previous/'manifest.json').write_text(json.dumps({'state':'running','stages':{}}))
            with self.assertRaisesRegex(refresh.GateError, 'writers must drain'):
                runner.fork_policy_repair(previous, evidence)
            old = {'state':'checkpointed','stages':{'scrape':{'state':'completed'}}, 'configuration':{}}
            (previous/'manifest.json').write_text(json.dumps(old))
            with self.assertRaisesRegex(refresh.GateError, 'failed or bound'):
                runner.fork_policy_repair(previous, evidence)

    def test_repair_snapshots_are_read_only_even_if_scrape_is_called_directly(self):
        runner = object.__new__(refresh.Refresh)
        runner.manifest = {'immutable_snapshots':{'original':'retained-original','refreshed':'retained-scrape'}}
        with self.assertRaisesRegex(refresh.GateError, 'cannot be modified'):
            runner.step_scrape()

    def test_assessment_has_no_category_admission_stage_and_safety_gates_cannot_be_waived(self):
        self.assertEqual(refresh.STAGES, ('checks','preflight','scrape','assessment','research','positive_audit','activation','export'))
        runner=object.__new__(refresh.Refresh)
        runner.manifest={'benchmark_reuse':{'authorization':'Assess every product'},'stages':{}}
        for stage in refresh.STAGES:
            runner.manifest['stages'][stage]={'state':'waived_by_user','authorization':'Assess every product'}
            self.assertFalse(runner.satisfied(stage))
        with self.assertRaisesRegex(refresh.GateError,'All validation'): runner.step_activation()

    def test_assessment_continuation_preserves_history_and_does_not_repeat_benchmarks_or_scrape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.sqlite';fixture=root/'fixture.json';fixture.write_text('{}')
            self.make_db(source)
            original_hashes={'tools/refresh_whole_product.py':'old','tools/classify_ocado_vegan.py':'fixed'}
            with mock.patch.object(refresh.subprocess,'check_output',return_value='dev\n'), mock.patch.object(benchmark,'candidate_hashes',return_value=original_hashes):
                old=refresh.Refresh(root/'old',source,fixture)
            refresh.clone(source,old.root/'original.sqlite');refresh.clone(source,old.working)
            with sqlite3.connect(old.working) as conn:
                conn.execute('CREATE TABLE sync_runs(id INTEGER,status TEXT)')
                conn.execute("INSERT INTO sync_runs VALUES (7,'completed')")
            with closing(refresh.readonly(source)) as conn: baseline=refresh.fingerprint(conn)
            report={'fixture_sha256':classifier.context_hash({}), 'implementation_hashes':{'classify_ocado_vegan.py':classifier.context_hash((refresh.ROOT/'tools/classify_ocado_vegan.py').read_text())}}
            for name in ('preflight-controls','preflight-pilot'): benchmark.save(old.root/name/'report.json',report)
            benchmark.save(old.root/'checks/checks.json',{})
            old.manifest.update(canonical_baseline=baseline,original_population={'all_known':1,'current':1},pre_sync_id=4,sync_run={'id':7,'status':'completed'})
            old.manifest['stages']={stage:{'state':'completed','artifacts':old.artifacts(stage)} for stage in ('checks','preflight')}
            old.manifest['stages']['scrape']={'state':'completed','artifacts':{}}
            old.manifest['stages']['name_census']={'state':'checkpointed','error':'Natural quota unmet'};old.save()
            unchanged=old.path.read_bytes();updated={**original_hashes,'tools/refresh_whole_product.py':'new'}
            with mock.patch.object(refresh.subprocess,'check_output',return_value='dev\n'), mock.patch.object(benchmark,'candidate_hashes',return_value=updated), mock.patch.object(benchmark,'successful',return_value=True):
                new=refresh.Refresh(root/'new',source,fixture)
                with self.assertRaisesRegex(refresh.GateError,'explicit user'): new.continue_assessment(old.root,'')
                new.continue_assessment(old.root,'Use completed benchmarks and classify now')
                self.assertEqual(old.path.read_bytes(),unchanged)
                self.assertNotIn('name_census',new.manifest['stages'])
                self.assertEqual(new.manifest['remaining'],['checks','assessment','research','positive_audit','activation','export'])
                self.assertEqual((new.root/'preflight-controls/report.json').read_bytes(),(old.root/'preflight-controls/report.json').read_bytes())
                old.manifest['state']='running';old.save()
                active=refresh.Refresh(root/'active',source,fixture)
                with self.assertRaisesRegex(refresh.GateError,'completed scrape'): active.continue_assessment(old.root,'Proceed')
                old.manifest['state']='checkpointed';old.save()
                refresh.clone(old.working,old.candidate)
                with sqlite3.connect(old.candidate) as conn:
                    conn.execute('ALTER TABLE products ADD COLUMN vegan_status TEXT')
                    conn.execute("UPDATE products SET vegan_status='unknown'")
                    conn.execute("INSERT INTO history VALUES (2,'Completed candidate assessment')")
                    conn.execute("INSERT INTO products VALUES ('pending','Historical product',NULL)")
                benchmark.save(old.root/'manufacturer/snapshot.json',{'frozen_initial_unknown':['1']})
                partial=refresh.Refresh(root/'partial',source,fixture)
                partial.continue_assessment(old.root,'Retire category gates and preserve completed products')
                self.assertTrue(partial.manifest['benchmark_reuse']['reused_candidate'])
                with closing(refresh.readonly(partial.candidate)) as conn:
                    self.assertEqual(conn.execute('SELECT id,vegan_status FROM products ORDER BY id').fetchall()[0]['vegan_status'],'unknown')
                    self.assertEqual(classifier.select_unclassified_product_ids(conn,selection_scope='all-known'),['pending'])
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM history').fetchone()[0],2)
                self.assertEqual((partial.root/'manufacturer/snapshot.json').read_bytes(),(old.root/'manufacturer/snapshot.json').read_bytes())
                (old.root/'frozen-fixture.json').write_text('{}')
                fixture.write_text('{"selection":{"counts":{"synthetic":0}}}')
                metadata=refresh.Refresh(root/'metadata',source,fixture)
                metadata.continue_assessment(old.root,'Remove only obsolete fixture metadata')
                self.assertNotEqual(metadata.manifest['benchmark_reuse']['previous_fixture_sha256'],metadata.manifest['benchmark_reuse']['current_fixture_sha256'])
                fixture.write_text('{"cases":[{"classifier_input":{"product":{"name":"Changed"}}}]}')
                changed_inputs=refresh.Refresh(root/'changed-inputs',source,fixture)
                with self.assertRaisesRegex(refresh.GateError,'fixture inputs'):changed_inputs.continue_assessment(old.root,'Proceed')
                fixture.write_text('{}')
                with mock.patch.object(new,'step_checks'), mock.patch.object(new,'step_assessment'), mock.patch.object(new,'artifacts',return_value={}):
                    for record in new.manifest['stages'].values():record['artifacts']={}
                    with mock.patch.object(new,'bench',side_effect=AssertionError('No new benchmark')), mock.patch.object(new,'step_scrape',side_effect=AssertionError('No repeat scrape')):
                        new.run(until='assessment')
                with mock.patch.object(benchmark,'candidate_hashes',return_value={**updated,'tools/classify_ocado_vegan.py':'changed'}):
                    invalid=refresh.Refresh(root/'invalid',source,fixture)
                    with self.assertRaisesRegex(refresh.GateError,'cannot cover classifier'):invalid.continue_assessment(old.root,'Proceed')

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

    def test_wal_activation_uses_rollback_journal_and_restores_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.sqlite';candidate=root/'candidate.sqlite'
            self.make_db(source)
            with closing(sqlite3.connect(source)) as conn, conn:conn.execute('PRAGMA journal_mode=WAL')
            refresh.clone(source,candidate)
            with closing(refresh.readonly(source)) as conn:baseline=refresh.fingerprint(conn)
            with closing(sqlite3.connect(candidate)) as conn, conn:conn.execute("UPDATE products SET name='New'")
            original_fingerprint=refresh.fingerprint;modes=[]
            def inspect(conn):
                modes.append(conn.execute('PRAGMA main.journal_mode').fetchone()[0])
                return original_fingerprint(conn)
            with mock.patch.object(refresh,'validate_candidate'),mock.patch.object(refresh,'fingerprint',side_effect=inspect):
                refresh.activate(source,candidate,baseline,root/'backup.sqlite')
            self.assertEqual(modes,['delete'])
            with closing(sqlite3.connect(source)) as conn, conn:
                self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0],'wal')
                self.assertEqual(conn.execute('SELECT name FROM products').fetchone()[0],'New')
            with closing(refresh.readonly(root/'backup.sqlite')) as conn:self.assertEqual(original_fingerprint(conn),baseline)

    def test_failed_wal_activation_rolls_back_and_restores_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.sqlite';candidate=root/'candidate.sqlite'
            self.make_db(source)
            with closing(sqlite3.connect(source)) as conn, conn:conn.execute('PRAGMA journal_mode=WAL')
            refresh.clone(source,candidate)
            with closing(refresh.readonly(source)) as conn:baseline=refresh.fingerprint(conn)
            with closing(sqlite3.connect(candidate)) as conn, conn:conn.execute('ALTER TABLE products RENAME COLUMN name TO incompatible')
            with mock.patch.object(refresh,'validate_candidate'):
                with self.assertRaisesRegex(refresh.GateError,'Unmatched columns'):
                    refresh.activate(source,candidate,baseline,root/'backup.sqlite')
            with closing(sqlite3.connect(source)) as conn, conn:
                self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0],'wal')
                self.assertEqual(refresh.fingerprint(conn),baseline)

    def test_activation_is_not_available_before_all_prerequisites(self):
        runner=object.__new__(refresh.Refresh);runner.manifest={'stages':{}}
        with self.assertRaisesRegex(refresh.GateError,'All validation'):
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

    def test_new_exact_contrary_shared_capture_invalidates_existing_positive_outside_unknown_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'candidate.sqlite'
            inputs=[{'classifier_input':classifier.product_context_for_llm({'product':{'id':pid,'name':'Example Bottled Wine'},
                    'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})} for pid in ('1','2')]
            with closing(benchmark.fixture_database(inputs)) as fixture,closing(sqlite3.connect(db)) as target:
                fixture.commit();fixture.backup(target)
            with closing(classifier.connect(db)) as conn:
                conn.execute("UPDATE products SET vegan_status=CASE WHEN id='1' THEN 'unknown' ELSE 'vegan' END")
                conn.execute("UPDATE products SET vegan_reason='manufacturer' WHERE id='2'");conn.commit()
                contexts={pid:classifier.load_product_context(conn,pid) for pid in ('1','2')}
            text='Example Bottled Wine labels contain casein glue.'
            source={'id':'raw','url':'https://example.test/new-faq','title':'Wine FAQ','retrieved_at':'2026-10-10',
                    'official_identity':'Example producer','market_scope':'UK bottles','source_text':text,
                    'content_sha256':refresh.hashlib.sha256(text.encode()).hexdigest()}
            identity=classifier.context_hash(manufacturer.catalogue_identity_payload(refresh.research.catalogue_payload(contexts['1'])))
            association={'product_id':'2','source_ids':['raw'],'positive_claim_approved':False,
                'catalogue_context_hash':classifier.context_hash(classifier.product_context_for_llm(contexts['2'])),
                'scope_match':'Exact native bottled wine, not another brand or can','notes':'New contrary capture requires normal assessment'}
            bundle={'products':{'1':{'context_hash':identity,'current_on_ocado':0}},'sources':{'raw':source},'brands':1,'errors':[],
                    'outcomes':{'1':{'outcome':'no_confirmation','research_complete':True,'source_ids':['raw'],'quote':text}},
                    'shared_associations':{'2':association}}
            result=refresh.retain_refresh_sources(db,bundle,root)
            self.assertEqual(result['changed_evidence'],2)
            with closing(classifier.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM products WHERE vegan_status IS NULL').fetchone()[0],2)
                candidates=classifier.product_context_for_llm(classifier.load_product_context(conn,'2'))['manufacturer_source_candidates']
                self.assertEqual(candidates[0]['source_text'],text)
            self.assertTrue(refresh.retain_refresh_sources(db,bundle,root)['resumed'])

    def test_changed_shared_context_rolls_back_source_and_association_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'candidate.sqlite'
            context=classifier.product_context_for_llm({'product':{'id':'1','name':'Example Wine'},'flags':[],
                'categories':[],'manufacturer_vegan_evidence':[]})
            with closing(benchmark.fixture_database([{'classifier_input':context}])) as fixture,closing(sqlite3.connect(db)) as target:
                fixture.commit();fixture.backup(target)
            with closing(classifier.connect(db)) as conn:
                conn.execute("UPDATE products SET vegan_status='vegan'");conn.commit()
            source={'id':'raw','url':'https://example.test/faq','title':'FAQ','retrieved_at':'2026-10-10',
                'official_identity':'Producer','market_scope':'UK','source_text':'Contains casein.',
                'content_sha256':refresh.hashlib.sha256(b'Contains casein.').hexdigest()}
            bundle={'products':{},'sources':{'raw':source},'brands':0,'errors':[], 'outcomes':{},
                'shared_associations':{'1':{'product_id':'1','source_ids':['raw'],'catalogue_context_hash':'wrong',
                    'positive_claim_approved':False,'scope_match':'Native bottle','notes':'Unapproved contrary source'}}}
            with self.assertRaisesRegex(refresh.GateError,'changed for shared'):refresh.retain_refresh_sources(db,bundle,root)
            with closing(classifier.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT vegan_status FROM products').fetchone()[0],'vegan')
                self.assertEqual(conn.execute('SELECT count(*) FROM manufacturer_website_sources').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT count(*) FROM manufacturer_research_runs').fetchone()[0],0)

    def test_audit_only_checked_page_cannot_invalidate_previously_associated_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'candidate.sqlite'
            inputs=[{'classifier_input':classifier.product_context_for_llm({'product':{'id':pid,'name':'Example Wine'},
                    'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})} for pid in ('1','2')]
            with closing(benchmark.fixture_database(inputs)) as fixture,closing(sqlite3.connect(db)) as target:
                fixture.commit();fixture.backup(target)
            with closing(classifier.connect(db)) as conn:
                manufacturer.ensure_schema(conn)
                conn.execute("UPDATE products SET vegan_status=CASE WHEN id='1' THEN 'unknown' ELSE 'vegan' END")
                conn.execute("INSERT INTO manufacturer_website_sources VALUES ('old','https://example.test/faq','FAQ','2026-10-09','Producer','UK','old-hash','Old claim')")
                conn.execute("INSERT INTO manufacturer_website_evidence(run_id,product_id,source_id,catalogue_binding_hash,statement,scope_match,accepted_at) VALUES(1,'2','old','old-binding','Old claim','Exact product','2026-10-09')")
                conn.commit();context=classifier.load_product_context(conn,'1')
                identity=classifier.context_hash(manufacturer.catalogue_identity_payload(refresh.research.catalogue_payload(context)))
            text='Contact us for more information.'
            source={'id':'checked','url':'https://example.test/faq','title':'FAQ','retrieved_at':'2026-10-10',
                    'official_identity':'Producer','market_scope':'UK','source_text':text,
                    'content_sha256':refresh.hashlib.sha256(text.encode()).hexdigest()}
            bundle={'products':{'1':{'context_hash':identity,'current_on_ocado':0}},'sources':{'checked':source},'brands':1,'errors':[],
                'outcomes':{'1':{'outcome':'no_confirmation','research_complete':True,'source_ids':[],
                    'checked_source_ids':['checked'],'notes':'No applicable claim in the brief check'}},
                'bounded_research_policy_sha256':'fixed','research_root':str(root),
                'bounded_research_policy':{'mode':refresh.research.cursory.MODE,'authorization':'Approved brief checks','positive_validation_unchanged':True}}
            result=refresh.retain_refresh_sources(db,bundle,root)
            self.assertEqual(result['changed_evidence'],0)
            with closing(classifier.connect(db)) as conn:
                self.assertEqual(conn.execute("SELECT vegan_status FROM products WHERE id='2'").fetchone()[0],'vegan')
                self.assertEqual(conn.execute('SELECT count(*) FROM manufacturer_research_products WHERE product_id=\'2\'').fetchone()[0],0)
                self.assertEqual(classifier.product_context_for_llm(classifier.load_product_context(conn,'1')).get('manufacturer_source_candidates',[]),[])
    def test_research_protocol_repair_binds_frozen_cohort_and_captures(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous=Path(tmp);origin=previous/'manufacturer';origin.mkdir();snapshot=origin/'snapshot.json';snapshot.write_text('{"frozen": ["historical", "current"]}')
            digest=refresh.hashlib.sha256(snapshot.read_bytes()).hexdigest()
            old={'stages':{'assessment':{'state':'completed','artifacts':{'manufacturer/snapshot.json':digest}},'research':{'state':'checkpointed'}}}
            bundle={'outcomes':{'historical':{'outcome':'source_collected'}},'sources':{'s':{'source_text':'Full immutable source'}},'shared_associations':{}}
            key=classifier.context_hash(bundle);proof={'research_continuation':{'root':str(origin.resolve()),'snapshot_sha256':digest,'bundle_hash':key}}
            with mock.patch.object(refresh.research,'read_bundle',return_value=bundle),mock.patch.object(refresh.research,'bundle_status',return_value={}),mock.patch.object(refresh.research,'gathering_complete',return_value=True):
                self.assertEqual(refresh.validate_research_protocol_origin(previous,old,proof),proof['research_continuation'])
                bundle['sources']['s']['source_text']='Withdrawn or replaced'
                with self.assertRaisesRegex(refresh.GateError,'exact frozen'):refresh.validate_research_protocol_origin(previous,old,proof)
                snapshot.write_text('{"frozen": ["different"]}')
                with self.assertRaisesRegex(refresh.GateError,'cohort changed'):refresh.validate_research_protocol_origin(previous,old,proof)
    def test_metadata_continuation_rejects_prompt_or_policy_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources = {}; hashes = {}
            for module in ('classify_ocado_vegan.py', 'replay_whole_product_policy.py', 'refresh_whole_product.py'):
                path = Path(tmp)/module
                path.write_bytes((refresh.ROOT/'tools'/module).read_bytes())
                sources[module] = str(path)
                hashes['tools/'+module] = refresh.hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(refresh.validate_metadata_continuation(hashes, hashes, sources), [])
            with self.assertRaisesRegex(refresh.GateError, 'policy, sources'):
                refresh.validate_metadata_continuation(hashes, {**hashes, 'tools/vegan_policy.py':'changed'}, sources)
            path = Path(sources['classify_ocado_vegan.py'])
            text = path.read_text()
            self.assertIn('Do not browse, search, read files or use tools.', text)
            path.write_text(text.replace('Do not browse, search, read files or use tools.', 'A different model instruction.'))
            previous = {**hashes, 'tools/classify_ocado_vegan.py':refresh.hashlib.sha256(path.read_bytes()).hexdigest()}
            with self.assertRaisesRegex(refresh.GateError, 'rules, prompts'):
                refresh.validate_metadata_continuation(previous, hashes, sources)

    def test_transport_repair_during_assessment_preserves_inherited_frozen_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp); origin = previous / 'manufacturer'; origin.mkdir()
            snapshot = origin / 'snapshot.json'; snapshot.write_text('original unknown cohort')
            bundle = {'outcomes': {}, 'sources': {}, 'shared_associations': {}}
            expected = {'root': str(origin.resolve()), 'snapshot_sha256': refresh.hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                        'bundle_hash': classifier.context_hash(bundle)}
            old = {'immutable_manufacturer_root': str(origin), 'research_protocol_origin': expected,
                   'stages': {'assessment': {'state': 'checkpointed'}}}
            proof = {'research_continuation': expected}
            with mock.patch.object(refresh.research, 'read_bundle', return_value=bundle), \
                 mock.patch.object(refresh.research, 'bundle_status', return_value={}), \
                 mock.patch.object(refresh.research, 'gathering_complete', return_value=True):
                self.assertEqual(refresh.validate_research_protocol_origin(previous, old, proof), expected)
                old['research_protocol_origin'] = {**expected, 'bundle_hash': 'changed'}
                with self.assertRaisesRegex(refresh.GateError, 'Inherited frozen'):
                    refresh.validate_research_protocol_origin(previous, old, proof)
                old['research_protocol_origin'] = expected
                old['stages']['positive_audit'] = {'state': 'checkpointed'}
                with self.assertRaisesRegex(refresh.GateError, 'consumed positive audit'):
                    refresh.validate_research_protocol_origin(previous, old, proof)

    def test_research_protocol_repair_cannot_reuse_consumed_positive_audit(self):
        old={'stages':{'assessment':{'state':'completed'},'research':{'state':'checkpointed'},'positive_audit':{'state':'checkpointed'}}}
        with self.assertRaisesRegex(refresh.GateError,'consumed positive audit'):refresh.validate_research_protocol_origin(Path('/tmp/unused'),old,{})
    def test_research_protocol_artifacts_use_original_cohort_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);origin=root/'old-manufacturer';origin.mkdir();(origin/'snapshot.json').write_text('original initially unknown IDs')
            runner=object.__new__(refresh.Refresh);runner.root=root/'new';runner.manufacturer=origin
            self.assertEqual(runner.artifacts('assessment'),{'manufacturer/snapshot.json':refresh.hashlib.sha256((origin/'snapshot.json').read_bytes()).hexdigest()})
