#!/usr/bin/env python3
"""Resumable dev-only full-policy refresh; every activation gate fails closed."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import benchmark_whole_product as benchmark
import classify_ocado_vegan as classifier
import manufacturer_evidence as manufacturer
import research_ocado_manufacturers as research
import shadow_vegan_policy as shadow
import sync_ocado_database as sync
import update_userscript_allowlists as exporter
import validate_whole_product_candidate as checks
import vegan_policy as policy

ROOT = benchmark.ROOT
STAGES = ('checks', 'preflight', 'scrape', 'corpus', 'name_census', 'references',
          'qualification', 'assessment', 'research', 'positive_audit', 'activation', 'export')
MODELS = {'primary': 'gpt-6-luna', 'effort': 'high', 'arbitration': 'gpt-6.1-sol',
          'arbitration_effort': 'medium', 'annotation': 'gpt-6.1-sol', 'annotation_effort': 'high'}


class GateError(RuntimeError):
    pass


def read(path):
    return json.loads(Path(path).read_text())


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def tables(conn, schema='main'):
    rows = list(conn.execute('SELECT name,sql FROM ' + schema + '.sqlite_master WHERE type=\'table\' ORDER BY name'))
    virtual = [name for name, sql in rows if sql and 'CREATE VIRTUAL TABLE' in sql.upper()]
    return [(name, sql) for name, sql in rows if not name.startswith(('sqlite_', 'shadow_'))
            and not any(name.startswith(parent + '_') for parent in virtual)]


def fingerprint(conn):
    """Consistent logical database identity, including labels and source history."""
    digest = hashlib.sha256()
    for name, sql in tables(conn):
        if sql and 'CREATE VIRTUAL TABLE' in sql.upper():
            continue  # Derived search indexes are rebuilt, not independent evidence.
        columns = [tuple(row) for row in conn.execute('PRAGMA table_info(' + quote(name) + ')')]
        digest.update(json.dumps([name, columns]).encode())
        order = ','.join(quote(row[1]) for row in sorted(columns, key=lambda row: row[5]) if row[5]) if 'WITHOUT ROWID' in sql.upper() else 'rowid'
        for row in conn.execute('SELECT * FROM ' + quote(name) + ' ORDER BY ' + order):
            encoded = json.dumps(list(row), ensure_ascii=True,
                                 default=lambda value: {'bytes': value.hex()}, separators=(',', ':'))
            digest.update(encoded.encode()); digest.update(b'\n')
    return digest.hexdigest()


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def clone(source, target):
    if target.exists() or source.resolve() == target.resolve():
        raise GateError('Snapshot target must be fresh and different from source')
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(readonly(source)) as original, closing(sqlite3.connect(target)) as dest:
        original.backup(dest)


def validate_candidate(path):
    with closing(readonly(path)) as conn:
        exporter.validate_export_readiness(conn)
        exporter.validate_policy_decisions(conn)
        bad = conn.execute('''SELECT count(*) FROM products p LEFT JOIN product_vegan_classification_audit a
            ON a.id=p.vegan_audit_id WHERE p.vegan_status IS NULL OR p.vegan_policy_version IS NOT ?
            OR a.product_id IS NOT p.id OR a.vegan_status IS NOT p.vegan_status
            OR a.vegan_reason IS NOT p.vegan_reason OR a.validation_error IS NOT NULL
            OR a.policy_version IS NOT ? OR a.knowledge_version IS NOT ? OR a.schema_version IS NOT ?
            OR p.vegan_status NOT IN ('vegan','nonvegan','unknown')
            OR (p.vegan_status!='vegan' AND p.vegan_reason IS NOT NULL)''',
            (policy.POLICY_VERSION,policy.POLICY_VERSION,policy.KNOWLEDGE_VERSION,policy.SCHEMA_VERSION)).fetchone()[0]
        if bad:
            raise GateError(str(bad) + ' missing/stale/invalid all-known decisions')
        for row in conn.execute('SELECT id,vegan_evidence_hash FROM products ORDER BY id'):
            context = classifier.product_context_for_llm(classifier.load_product_context(conn, row['id']))
            if classifier.context_hash(context) != row['vegan_evidence_hash']:
                raise GateError('Stale all-known evidence: ' + row['id'])
        return dict(conn.execute('SELECT count(*) AS all_known,sum(current_on_ocado=1) AS current FROM products').fetchone())


def activate(source, candidate, expected, backup):
    """Copy the validated candidate in one SQLite transaction, never replace live files."""
    validate_candidate(candidate)
    if backup.exists():
        with closing(readonly(backup)) as conn:
            if fingerprint(conn) != expected: raise GateError('Preserved activation backup does not match the baseline')
    else:
        clone(source, backup)
    with closing(sqlite3.connect(source)) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('ATTACH DATABASE ? AS candidate', (str(candidate.resolve()),))
        try:
            conn.execute('BEGIN IMMEDIATE')
            if fingerprint(conn) != expected:
                raise GateError('Canonical database changed; preserve concurrent work and rebuild the candidate')
            for name, sql in tables(conn, 'candidate'):
                if sql and 'CREATE VIRTUAL TABLE' in sql.upper():
                    continue
                exists = conn.execute('SELECT 1 FROM main.sqlite_master WHERE type=\'table\' AND name=?', (name,)).fetchone()
                if not exists:
                    conn.execute(sql)
                else:
                    current = {row[1]: row for row in conn.execute('PRAGMA main.table_info(' + quote(name) + ')')}
                    for row in conn.execute('PRAGMA candidate.table_info(' + quote(name) + ')'):
                        if row[1] not in current:
                            definition = quote(row[1]) + ' ' + row[2]
                            if row[3]: definition += ' NOT NULL'
                            if row[4] is not None: definition += ' DEFAULT ' + row[4]
                            conn.execute('ALTER TABLE ' + quote(name) + ' ADD COLUMN ' + definition)
                    if [row[1] for row in conn.execute('PRAGMA main.table_info(' + quote(name) + ')')] != [row[1] for row in conn.execute('PRAGMA candidate.table_info(' + quote(name) + ')')]:
                        raise GateError('Unmatched columns: ' + name)
                conn.execute('DELETE FROM main.' + quote(name))
                conn.execute('INSERT INTO main.' + quote(name) + ' SELECT * FROM candidate.' + quote(name))
            for name, sql in tables(conn, 'candidate'):
                if sql and 'CREATE VIRTUAL TABLE' in sql.upper():
                    if not conn.execute('SELECT 1 FROM main.sqlite_master WHERE name=?', (name,)).fetchone(): conn.execute(sql)
                    conn.execute('DELETE FROM main.' + quote(name))
                    conn.execute('INSERT INTO main.' + quote(name) + ' SELECT * FROM candidate.' + quote(name))
            for name, sql in conn.execute("SELECT name,sql FROM candidate.sqlite_master WHERE type='index' AND sql IS NOT NULL"):
                if not conn.execute('SELECT 1 FROM main.sqlite_master WHERE name=?', (name,)).fetchone(): conn.execute(sql)
            if conn.execute('PRAGMA foreign_key_check').fetchone() or conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise GateError('Candidate activation integrity check failed')
            conn.commit()
        except BaseException:
            conn.rollback(); raise


def census_gate(annotations):
    candidates = annotations['candidates']
    names = [case for case in candidates if case['adjudication']['state'] == 'accepted'
             and case['expected']['vegan_status']=='vegan' and case['expected']['vegan_reason']=='name']
    return {'state': 'passed' if len(names) >= 50 else 'blocked_natural_quota',
            'examined': len(candidates), 'accepted_name_only': len(names), 'minimum': 50,
            'required_development': 20, 'required_locked': 30,
            'disputed': len(annotations.get('disputed_cases', [])),
            'note': 'Source-cited natural references only. A pass is admission, not qualification; family-separated selection may still have shortfalls.'}


class Refresh:
    def __init__(self, root, source, fixture=benchmark.FIXTURE):
        self.root = Path(root).resolve(); self.source = Path(source).resolve(); self.fixture = Path(fixture).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'manifest.json'
        branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip()
        if branch != 'dev': raise GateError('This refresh must remain on dev')
        config = {'source': str(self.source), 'fixture': str(self.fixture), 'fixture_sha256': classifier.context_hash(read(self.fixture)),
                  'models': MODELS, 'scope': 'all-known', 'code_hashes': benchmark.candidate_hashes(),
                  'policy': policy.POLICY_VERSION, 'rubric': benchmark.ACCEPTANCE_RUBRIC_VERSION}
        if self.path.exists():
            self.manifest = read(self.path)
            expected = self.manifest['configuration']
            generated = self.manifest.get('post_export_code_hashes')
            if generated and self.manifest['stages'].get('activation',{}).get('state')=='completed':
                if {k:v for k,v in generated.items() if k!='ocado-vegan-filter.user.js'} != {k:v for k,v in expected['code_hashes'].items() if k!='ocado-vegan-filter.user.js'}:
                    raise GateError('Non-data code changed during export')
                expected = {**expected, 'code_hashes':generated}
            if expected != config: raise GateError('Resume inputs/code changed; use a fresh root and retain the old run')
        else:
            self.manifest = {'created_utc': datetime.now(timezone.utc).isoformat(), 'configuration': config,
                             'state': 'prepared', 'stages': {}, 'remaining': list(STAGES)}
            self.save()
        self.working = self.root / 'refreshed.sqlite'; self.candidate = self.root / 'candidate.sqlite'

    def save(self):
        self.manifest['remaining'] = [stage for stage in STAGES if self.manifest['stages'].get(stage, {}).get('state') != 'completed']
        benchmark.save(self.path, self.manifest)

    def artifacts(self, stage):
        paths = {'checks':['checks/checks.json'],
                 'preflight':['preflight-controls/report.json','preflight-pilot/report.json'],
                 'corpus':['candidates.json','name-census.json'],
                 'name_census':['name-readings/annotated-candidates.json','name-quota-gate.json'],
                 'references':['fresh-fixture.json','reference-readings/annotated-candidates.json'],
                 'qualification':[name+'/report.json' for name in ('selected-pilot','selected-development','selected-controls','selected-locked','selected-workload')],
                 'assessment':['manufacturer/snapshot.json'],
                 'research':['manufacturer-coverage.json','manufacturer-affected.json'],
                 'positive_audit':['positive-audit.json','positive-audit.predictions.json','positive-readings/annotated-candidates.json','positive-score.json']}
        return {name: hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in paths.get(stage,[])}

    def command(self, name, arguments, python=sys.executable):
        log = self.root / (name + '.log')
        with log.open('a') as output:
            completed = subprocess.run([str(python), *map(str, arguments)], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                       env={**os.environ, 'BROWSE_WITH_FIREFOX_SCRIPTS': str(Path.home() / '.codex/skills/browse-with-firefox/scripts')})
        if completed.returncode:
            raise GateError('Stage command failed/checkpointed: ' + name + '; retained log: ' + str(log))

    def bench(self, name, fixture, split, repetitions=1, pilot=False):
        output = self.root / name
        arguments = [ROOT/'tools/benchmark_whole_product.py', '--fixture', fixture, '--split', split,
                     '--model', MODELS['primary'], '--effort', MODELS['effort'], '--workers', '3', '--batch-size', '8',
                     '--arbitration-model', MODELS['arbitration'], '--arbitration-effort', MODELS['arbitration_effort'],
                     '--repetitions', str(repetitions), '--output', output]
        if pilot: arguments += ['--subset', 'pilot']
        self.command(name, arguments)
        report = read(output/'report.json')
        if not benchmark.successful(report): raise GateError('Benchmark gate failed: ' + name)
        return report

    def step_checks(self):
        if not (self.root/'checks/checks.json').exists():
            self.command('checks', [ROOT/'tools/validate_whole_product_candidate.py', '--output', self.root/'checks'])
        checks.validate_record(read(self.root/'checks/checks.json'))

    def step_preflight(self):
        if self.manifest.get('scrape_repair_origin'):
            for name in ('preflight-controls', 'preflight-pilot'):
                if not benchmark.successful(read(self.root/name/'report.json')):
                    raise GateError('Preserved preflight did not pass')
            return  # Explicitly retained results; no new classifier implementation or claims.
        self.bench('preflight-controls', self.fixture, 'synthetic')
        self.bench('preflight-pilot', self.fixture, 'development', pilot=True)

    def fork_scrape_repair(self, previous):
        """New provenance phase for a sync-only repair, preserving interrupted evidence."""
        previous = Path(previous).resolve()
        old = read(previous/'manifest.json')
        if self.manifest['stages'] or self.working.exists() or previous == self.root:
            raise GateError('Scrape repair requires a fresh root')
        if old['stages'].get('scrape', {}).get('state') != 'checkpointed' or any(
                key not in ('checks', 'preflight', 'scrape') for key in old['stages']):
            raise GateError('Only an interrupted pre-qualification scrape can be forked')
        old_config = old['configuration']; new_config = self.manifest['configuration']
        if {k:v for k,v in old_config.items() if k!='code_hashes'} != {k:v for k,v in new_config.items() if k!='code_hashes'}:
            raise GateError('Scrape repair cannot change models, policy, fixture or source')
        changed = {key for key in old_config['code_hashes'].keys() | new_config['code_hashes'].keys()
                   if old_config['code_hashes'].get(key) != new_config['code_hashes'].get(key)}
        allowed = {'tools/sync_ocado_database.py', 'tools/refresh_whole_product.py',
                   'tests/test_sync_ocado_database.py', 'tests/test_refresh_whole_product.py'}
        if not changed or not changed <= allowed:
            raise GateError('Scrape-only reuse cannot cover classifier or other code changes')
        for stage in ('checks', 'preflight'):
            if old['stages'].get(stage, {}).get('state') != 'completed':
                raise GateError('Previous checks and preflight must have completed')
            for name, digest in old['stages'][stage]['artifacts'].items():
                if hashlib.sha256((previous/name).read_bytes()).hexdigest() != digest:
                    raise GateError('Previous completed artifact changed: '+name)
        for name in ('preflight-controls', 'preflight-pilot'):
            report = read(previous/name/'report.json')
            if not benchmark.successful(report) or report['fixture_sha256'] != new_config['fixture_sha256']:
                raise GateError('Previous preflight is not applicable')
            for filename, digest in report['implementation_hashes'].items():
                if classifier.context_hash((ROOT/'tools'/filename).read_text()) != digest:
                    raise GateError('Preflight classification implementation changed: '+filename)
        with closing(readonly(self.source)) as conn:
            if fingerprint(conn) != old['canonical_baseline']:
                raise GateError('Canonical source changed during repair')
        with closing(readonly(previous/'refreshed.sqlite')) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_context_baseline'").fetchone():
                raise GateError('Interrupted baseline is missing')
        clone(previous/'original.sqlite', self.root/'original.sqlite')
        clone(previous/'refreshed.sqlite', self.working)
        for name in ('preflight-controls', 'preflight-pilot'):
            (self.root/name).mkdir()
            (self.root/name/'report.json').write_bytes((previous/name/'report.json').read_bytes())
        for key in ('canonical_baseline', 'original_population', 'pre_sync_id'):
            self.manifest[key] = old[key]
        self.manifest['scrape_repair_origin'] = {'root': str(previous), 'manifest_sha256':
            hashlib.sha256((previous/'manifest.json').read_bytes()).hexdigest(), 'changed_paths': sorted(changed),
            'note': 'Sync-only repair. Preserve old reports exactly; fresh implementation checks and complete sync are required. No classifier calls repeated or results relabelled.'}
        self.save()

    def step_scrape(self):
        if not self.working.exists():
            clone(self.source, self.root/'original.sqlite')
            with closing(readonly(self.root/'original.sqlite')) as conn:
                self.manifest['canonical_baseline'] = fingerprint(conn)
                self.manifest['original_population'] = dict(conn.execute('SELECT count(*) AS all_known,sum(current_on_ocado=1) AS current FROM products').fetchone())
            self.save(); clone(self.root/'original.sqlite', self.working)
        # The sync makes its own rolling backup; original.sqlite never gets replaced.
        with closing(readonly(self.working)) as conn:
            latest = dict(conn.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone())
        if 'pre_sync_id' not in self.manifest:
            self.manifest['pre_sync_id'] = latest['id']; self.save()
        if latest['id'] <= self.manifest['pre_sync_id'] or latest['status'] != 'completed':
            arguments = [ROOT/'tools/sync_ocado_database.py', '--db', self.working]
            if latest['status'] == 'failed':
                source_run_id = latest.get('resumed_from_run_id') or latest['id']
                with closing(readonly(self.working)) as conn:
                    try:
                        sync.validate_finalization_source(conn, source_run_id)
                    except (ValueError, sqlite3.Error):
                        pass  # Incomplete acquisition needs a full sync, preserving its baseline.
                    else:
                        arguments += ['--finish-run-id', source_run_id]
            self.command('scrape', arguments,
                         python=Path.home()/'.venvs/codex-firefox/bin/python')
        with closing(readonly(self.working)) as conn:
            latest = dict(conn.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone())
            if latest['status'] != 'completed' or latest['category_pages_failed'] or latest['detail_fetch_failed']:
                raise GateError('Incomplete scrape; preserve its baseline and investigate')
            old = self.manifest['original_population']['current']; current = conn.execute('SELECT count(*) FROM products WHERE current_on_ocado=1').fetchone()[0]
            if old and not .8 <= current/old <= 1.2: raise GateError('Catalogue size changed by more than 20%; investigate before reassessment')
            self.manifest['sync_run'] = latest; self.save()
            if latest.get('resumed_from_run_id'):
                self.manifest['sync_acquisition_run'] = dict(conn.execute('SELECT * FROM sync_runs WHERE id=?',
                    (latest['resumed_from_run_id'],)).fetchone()); self.save()

    def step_corpus(self):
        with closing(classifier.connect(self.working)) as conn:
            classifier.ensure_classification_schema(conn, drop_legacy=False); manufacturer.ensure_schema(conn); conn.commit()
        packet = self.root/'candidates.json'
        if not packet.exists():
            self.command('corpus', [ROOT/'tools/prepare_whole_product_corpus.py', '--db', self.working, '--all-name-candidates', '--output', packet])
        data = read(packet)
        census = {**data, 'candidates': [case for case in data['candidates'] if case['sampling_stratum']=='name'], 'workload': []}
        benchmark.save(self.root/'name-census.json', census)
        self.manifest['packet_sha256'] = classifier.context_hash(data); self.save()

    def annotate(self, name, packet, reuse=False):
        arguments = [ROOT/'tools/adjudicate_whole_product_corpus.py', '--packet', packet, '--output', self.root/name,
                     '--model', MODELS['annotation'], '--effort', MODELS['annotation_effort'], '--batch-size', '8', '--workers', '3']
        if reuse: arguments += ['--reuse-packet', self.root/'name-census.json', '--reuse-output', self.root/'name-readings']
        self.command(name, arguments)
        return read(self.root/name/'annotated-candidates.json')

    def step_name_census(self):
        annotations = self.annotate('name-readings', self.root/'name-census.json')
        gate = census_gate(annotations); benchmark.save(self.root/'name-quota-gate.json', gate)
        if gate['state'] != 'passed': raise GateError('Natural name-only quota unmet: '+str(gate['accepted_name_only'])+'/50; no quota relaxation or activation')

    def step_references(self):
        self.annotate('reference-readings', self.root/'candidates.json', reuse=True)
        self.command('assemble', [ROOT/'tools/assemble_whole_product_fixture.py', '--annotations', self.root/'reference-readings/annotated-candidates.json', '--output', self.root/'natural-fixture.json'])
        fixture = read(self.root/'natural-fixture.json'); parent = read(self.fixture)
        fixture['cases'] = [case for case in parent['cases'] if case['split']=='synthetic'] + [case for case in fixture['cases'] if case['split']!='synthetic']
        benchmark.save(self.root/'fresh-fixture.json', fixture)
        if not fixture['selection']['target_complete']: raise GateError('Family-separated natural quotas unmet; preserve the frozen corpus and stop')

    def step_qualification(self):
        fixture = self.root/'fresh-fixture.json'
        self.bench('selected-pilot', fixture, 'development', pilot=True)
        self.bench('selected-development', fixture, 'development')
        self.bench('selected-controls', fixture, 'synthetic', repetitions=3)
        locked = self.bench('selected-locked', fixture, 'locked', repetitions=3)
        if not benchmark.acceptance(locked): raise GateError('Locked qualification failed')
        self.bench('selected-workload', fixture, 'workload')

    def classify(self, name):
        self.command(name, [ROOT/'tools/classify_ocado_vegan.py', '--db', self.candidate, '--preserve-legacy',
                           'classify-all', '--codex', '--scope', 'all-known', '--sync-run-id', self.manifest['sync_run']['id'],
                           '--model', MODELS['primary'], '--reasoning-effort', MODELS['effort'], '--passes', '2',
                           '--workers', '3', '--batch-size', '8', '--retries', '1', '--usage-journal', self.root/(name+'.calls.jsonl')])
        validate_candidate(self.candidate)

    def step_assessment(self):
        if not self.candidate.exists(): shadow.snapshot(self.working, self.candidate, all_products=True)
        self.classify('all-known-assessment')
        root = self.root/'manufacturer'
        if not (root/'snapshot.json').exists(): research.create_snapshot(root, self.candidate, 6)

    def step_research(self):
        bundle = research.read_bundle(self.root/'manufacturer'); status = research.bundle_status(bundle)
        benchmark.save(self.root/'manufacturer-coverage.json', status)
        if status['errors'] or status['complete_products'] != status['total_products']:
            raise GateError('Manufacturer gathering remains incomplete; research every frozen product and retain primary captures before resuming')
        # Store all captures/outcomes, including contrary pages, as unverified
        # associations. The normal two-pass classifier, never the collector,
        # establishes suitability from those complete sources.
        affected = retain_refresh_sources(self.candidate, bundle, self.root/'manufacturer')
        benchmark.save(self.root/'manufacturer-affected.json', affected)
        self.classify('manufacturer-reassessment')
        self.preserve_previous()

    def step_positive_audit(self):
        with closing(readonly(self.candidate)) as conn: current = fingerprint(conn)
        baseline = self.manifest.get('candidate_audit_baseline')
        if baseline and baseline != current: raise GateError('Candidate changed after freezing its positive audit')
        self.manifest['candidate_audit_baseline'] = current; self.save()
        packet = self.root/'positive-audit.json'
        if not packet.exists():
            self.command('positive-sample', [ROOT/'tools/audit_whole_product_positives.py', '--db', self.candidate, '--output', packet])
        self.annotate('positive-readings', packet)
        if not (self.root/'positive-score.json').exists():
            self.command('positive-score', [ROOT/'tools/audit_whole_product_positives.py', '--packet', packet,
                                          '--annotations', self.root/'positive-readings/annotated-candidates.json', '--output', self.root/'positive-score.json'])
        if read(self.root/'positive-score.json')['state'] != 'passed': raise GateError('Positive audit failed; no activation')

    def step_activation(self):
        # This method is also guarded when called directly rather than by run().
        if any(self.manifest['stages'].get(stage, {}).get('state') != 'completed' for stage in STAGES[:STAGES.index('activation')]):
            raise GateError('All qualification/research/audit stages must complete before activation')
        with closing(readonly(self.candidate)) as conn:
            if fingerprint(conn) != self.manifest['candidate_audit_baseline']:
                raise GateError('Candidate changed after its positive audit; no activation')
        self.command('candidate-export-preview', [ROOT/'tools/update_userscript_allowlists.py', '--db', self.candidate, '--dry-run'])
        with closing(readonly(self.candidate)) as conn: candidate_hash = fingerprint(conn)
        with closing(readonly(self.source)) as conn: canonical_hash = fingerprint(conn)
        if canonical_hash != candidate_hash:
            activate(self.source, self.candidate, self.manifest['canonical_baseline'], self.root/'before-activation.sqlite')
        validate_candidate(self.source)

    def preserve_previous(self):
        with closing(readonly(self.root/'original.sqlite')) as original, closing(classifier.connect(self.candidate)) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS vegan_refresh_previous_decisions (refresh_id TEXT,product_id TEXT,decision_json TEXT,PRIMARY KEY(refresh_id,product_id))')
            for row in original.execute('SELECT * FROM products'):
                previous = {key: row[key] for key in ['id', *classifier.CLASSIFICATION_PRODUCT_COLUMNS] if key in row.keys()}
                conn.execute('INSERT OR IGNORE INTO vegan_refresh_previous_decisions VALUES (?,?,?)', (self.root.name, row['id'], json.dumps(previous)))
            conn.commit()

    def step_export(self):
        self.command('export-preview', [ROOT/'tools/update_userscript_allowlists.py', '--db', self.source, '--dry-run'])
        self.command('export', [ROOT/'tools/update_userscript_allowlists.py', '--db', self.source])
        self.manifest['post_export_code_hashes'] = benchmark.candidate_hashes(); self.save()
        self.command('post-export-checks', [ROOT/'tools/validate_whole_product_candidate.py', '--output', self.root/'post-export-checks'])
        self.manifest['post_export_code_hashes'] = benchmark.candidate_hashes()

    def run(self, until=None):
        for stage, record in self.manifest['stages'].items():
            if record.get('state')=='completed' and record.get('artifacts',{}) != self.artifacts(stage):
                raise GateError('Completed stage artifacts changed: '+stage)
        for stage in STAGES:
            if self.manifest['stages'].get(stage, {}).get('state') == 'completed':
                if stage == until: break
                continue
            self.manifest['state'] = 'running'; self.manifest['stages'][stage] = {'state': 'running'}; self.save()
            print('Refresh stage: ' + stage, flush=True)
            try:
                getattr(self, 'step_' + stage)()
                if benchmark.candidate_hashes() != self.manifest['configuration']['code_hashes'] and stage != 'export':
                    raise GateError('Code changed during refresh; retain stages and revalidate on a fresh configuration')
            except BaseException as exc:
                self.manifest['state'] = 'checkpointed'; self.manifest['stages'][stage] = {'state': 'checkpointed', 'error': str(exc)}; self.save()
                raise
            self.manifest['stages'][stage] = {'state': 'completed', 'artifacts':self.artifacts(stage), 'finished_utc': datetime.now(timezone.utc).isoformat()}; self.save()
            if stage == until: break
        self.manifest['state'] = 'completed' if not self.manifest['remaining'] else 'checkpointed'; self.save()


def retain_refresh_sources(db, bundle, root):
    """Import source associations idempotently; contrary text is never discarded."""
    status = research.bundle_status(bundle)
    if status['errors'] or status['complete_products'] != status['total_products']:
        raise GateError('Incomplete manufacturer coverage cannot be imported')
    with closing(classifier.connect(db)) as conn:
        manufacturer.ensure_schema(conn)
        key = classifier.context_hash({'outcomes': bundle['outcomes'], 'sources': bundle['sources']})
        prior = conn.execute('SELECT id FROM manufacturer_research_runs WHERE bundle_hash=?', (key,)).fetchone()
        if prior: return {'run_id': prior[0], 'resumed': True}
        affected = set(bundle['products']); changed = set()
        fresh_sources = {key: {**value, 'id': classifier.context_hash([value['url'],value['content_sha256'],value['retrieved_at']])}
                         for key,value in bundle['sources'].items()}
        refreshed = {row['url']: row for row in fresh_sources.values()}
        with conn:
            for source in fresh_sources.values():
                conn.execute('INSERT OR IGNORE INTO manufacturer_website_sources VALUES (?,?,?,?,?,?,?,?)',
                             tuple(source.get(field, '') for field in ('id','url','title','retrieved_at','official_identity','market_scope','content_sha256','source_text')))
            run_id = conn.execute('INSERT INTO manufacturer_research_runs(started_at,status,scope_json,bundle_hash,model,reasoning_effort,prompt_version) VALUES (?,?,?,?,?,?,?)',
                                  (research.utc_now(), 'completed', json.dumps({'scope':'all initially unknown; raw source acquisition, not an assertion'}), key, 'source-acquisition', 'none', 'refresh-capture-v1')).lastrowid
            associations = {}
            for product_id, outcome in bundle['outcomes'].items():
                context = classifier.load_product_context(conn, product_id)
                identity = classifier.context_hash(manufacturer.catalogue_identity_payload(research.catalogue_payload(context)))
                if identity != bundle['products'][product_id]['context_hash']:
                    raise GateError('Catalogue changed during manufacturer research: '+product_id)
                associations[product_id] = {**outcome, 'source_ids': [fresh_sources[key]['id'] for key in outcome['source_ids']]}
            # Changed pages also need assessment for previously associated
            # products outside the unknown cohort; no brand-wide extrapolation.
            for product_id, url, old_hash in conn.execute('SELECT DISTINCT e.product_id,s.url,s.content_sha256 FROM manufacturer_website_evidence e JOIN manufacturer_website_sources s ON s.id=e.source_id'):
                if url in refreshed and refreshed[url]['content_sha256'] != old_hash:
                    affected.add(product_id)
                    associations.setdefault(product_id, {'outcome':'unverified','source_ids':[]})['source_ids'].append(refreshed[url]['id'])
            for product_id, outcome in associations.items():
                context = classifier.load_product_context(conn, product_id)
                before_hash = classifier.context_hash(classifier.product_context_for_llm(context))
                identity = classifier.context_hash(manufacturer.catalogue_identity_payload(research.catalogue_payload(context)))
                conn.execute('INSERT INTO manufacturer_research_products VALUES (?,?,?,?,?,?,?,?)',
                             (run_id, product_id, context['product'].get('current_on_ocado',0)==1, identity,
                              outcome.get('outcome','unverified'), 'unverified', json.dumps(outcome), None))
                after_hash = classifier.context_hash(classifier.product_context_for_llm(classifier.load_product_context(conn,product_id)))
                if after_hash != before_hash:
                    changed.add(product_id)
                    conn.execute('UPDATE products SET vegan_status=NULL,vegan_reason=NULL,vegan_policy_version=NULL,vegan_evidence_hash=NULL,vegan_audit_id=NULL WHERE id=?', (product_id,))
            conn.execute('UPDATE manufacturer_research_runs SET completed_at=? WHERE id=?', (research.utc_now(),run_id))
        return {'run_id': run_id, 'affected': len(affected), 'changed_evidence':len(changed), 'initially_unknown': len(bundle['products']), 'capture_root': str(root)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=Path(classifier.DEFAULT_DB))
    parser.add_argument('--fixture', type=Path, default=benchmark.FIXTURE)
    parser.add_argument('command', choices=['run','status','fork-scrape-repair'])
    parser.add_argument('--from-root', type=Path)
    parser.add_argument('--until', choices=STAGES)
    args = parser.parse_args()
    if args.command == 'status':
        manifest = read(args.root/'manifest.json')
        print(json.dumps({'state':manifest['state'],'remaining':manifest['remaining'],'stages':manifest['stages']}))
        return 0
    try:
        runner = Refresh(args.root, args.db, args.fixture)
        if args.command == 'fork-scrape-repair':
            if not args.from_root: parser.error('fork-scrape-repair requires --from-root')
            runner.fork_scrape_repair(args.from_root)
        if args.command == 'run': runner.run(args.until)
        print(json.dumps({'state': runner.manifest['state'], 'remaining': runner.manifest['remaining'], 'manifest': str(runner.path)}))
    except (GateError, ValueError) as exc:
        print(str(exc), file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
