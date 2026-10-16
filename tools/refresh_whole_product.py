#!/usr/bin/env python3
"""Resumable dev-only full-policy refresh; every activation gate fails closed."""
import argparse
import ast
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
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
import replay_whole_product_policy as policy_replay
import recover_ocado_field_text as text_recovery

ROOT = benchmark.ROOT
STAGES = ('checks', 'preflight', 'scrape', 'assessment', 'research',
          'positive_audit', 'activation', 'export')
MODELS = {'primary': 'gpt-6-luna', 'effort': 'high', 'arbitration': 'gpt-6.1-sol',
          'arbitration_effort': 'medium', 'annotation': 'gpt-6.1-sol', 'annotation_effort': 'high'}


class GateError(RuntimeError):
    pass


def validate_reference_audit_continuation(previous_hashes, current_hashes, previous_sources):
    """Only audit instructions and their guarded continuation may change.

    Keep classifier, policy, schemas, quotation validation, invocation and
    persisted decisions unchanged. Reference output/validation code cannot
    change merely to make a failed sample pass.
    """
    allowed = {'tools/adjudicate_whole_product_corpus.py', 'tools/refresh_whole_product.py',
               'tests/test_refresh_whole_product.py', 'tests/test_reference_audit_continuation.py'}
    changed = {key for key in previous_hashes.keys() | current_hashes.keys()
               if previous_hashes.get(key) != current_hashes.get(key)}
    if not changed <= allowed:
        raise GateError('Reference continuation changed classifier, policy or unrelated implementation')
    def projection(text, module):
        tree = ast.parse(text)
        if module == 'adjudicate_whole_product_corpus.py':
            tree.body = [node for node in tree.body if not (
                isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id in {'POLICY', 'PROMPT_VERSION'} for target in node.targets))]
        else:
            tree.body = [node for node in tree.body if not (isinstance(node, ast.FunctionDef)
                and node.name in {'validate_reference_audit_continuation', 'main'})]
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == 'Refresh':
                    node.body = [method for method in node.body if not (
                        isinstance(method, ast.FunctionDef) and method.name == 'fork_policy_repair')]
        return ast.dump(tree, include_attributes=False)
    for module in ('adjudicate_whole_product_corpus.py', 'refresh_whole_product.py'):
        path = Path(previous_sources[module])
        if hashlib.sha256(path.read_bytes()).hexdigest() != previous_hashes['tools/' + module]:
            raise GateError('Previous reference/coordinator source is not the exact bound implementation')
        if projection(path.read_text(), module) != projection((ROOT/'tools'/module).read_text(), module):
            raise GateError('Reference continuation changed validation, activation or unrelated execution')
    return sorted(changed)


def validate_protocol_continuation(previous_hashes, current_hashes, previous_sources):
    """Only quotation transport may change; accepted literal evidence stays valid."""
    allowed = {'tools/classify_ocado_vegan.py', 'tools/codex_evidence.py',
               'tools/source_quote_restoration.py', 'tools/refresh_whole_product.py',
               'tests/test_source_quote_restoration.py', 'tests/test_refresh_whole_product.py',
               'tools/literal_reference_catalogue.py', 'tests/test_literal_reference_catalogue.py',
               'tests/test_literal_reference_fallback.py'}
    changed = {key for key in previous_hashes.keys() | current_hashes.keys()
               if previous_hashes.get(key) != current_hashes.get(key)}
    if not changed <= allowed:
        raise GateError('Protocol continuation changed policy or unrelated implementation')
    def projection(text, module):
        tree = ast.parse(text)
        excluded = {'call_codex_batch'} if module == 'classify_ocado_vegan.py' else {'run'}
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in excluded:
                continue
            if module == 'classify_ocado_vegan.py':
                if isinstance(node, ast.Import) and all(item.name == 'source_quote_restoration' for item in node.names):
                    continue
                if isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == 'CLASSIFIER_VERSION' for item in node.targets):
                    continue
            nodes.append(node)
        return ast.dump(ast.Module(body=nodes, type_ignores=[]), include_attributes=False)
    for module in ('classify_ocado_vegan.py', 'codex_evidence.py'):
        path = Path(previous_sources[module])
        if hashlib.sha256(path.read_bytes()).hexdigest() != previous_hashes['tools/' + module]:
            raise GateError('Previous protocol source is not the exact bound implementation')
        if projection(path.read_text(), module) != projection((ROOT/'tools'/module).read_text(), module):
            raise GateError('Protocol continuation changed decision rules, prompts, schemas or invocation')
    return sorted(changed)


def validate_metadata_continuation(previous_hashes, current_hashes, previous_sources):
    """Keep rules and model inputs unchanged for a targeted unknown-block repair."""
    allowed = {'tools/classify_ocado_vegan.py', 'tools/refresh_whole_product.py',
               'tools/replay_whole_product_policy.py', 'tests/test_unknown_promotion_block.py',
               'tests/test_refresh_whole_product.py', 'tests/test_replay_whole_product_policy.py'}
    changed = {key for key in previous_hashes.keys() | current_hashes.keys()
               if previous_hashes.get(key) != current_hashes.get(key)}
    if not changed <= allowed:
        raise GateError('Metadata continuation changed policy, sources or unrelated implementation')
    excluded = {'classify_ocado_vegan.py': {'merge_pass_decisions', 'apply_policy_gate', '_merged_pass_assessment'},
                'replay_whole_product_policy.py': {'replay_unknown_metadata'},
                'refresh_whole_product.py': {'validate_metadata_continuation'}}
    def projection(text, module):
        tree = ast.parse(text); nodes = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in excluded[module]:
                continue
            if module == 'classify_ocado_vegan.py' and isinstance(node, ast.Assign) and any(
                    isinstance(item, ast.Name) and item.id == 'CLASSIFIER_VERSION' for item in node.targets):
                continue
            if module == 'refresh_whole_product.py' and isinstance(node, ast.ClassDef) and node.name == 'Refresh':
                node.body = [item for item in node.body if not (
                    isinstance(item, ast.FunctionDef) and item.name in {'classify', 'fork_policy_repair'})]
            nodes.append(node)
        return ast.dump(ast.Module(body=nodes, type_ignores=[]), include_attributes=False)
    for module in excluded:
        path = Path(previous_sources[module])
        if hashlib.sha256(path.read_bytes()).hexdigest() != previous_hashes['tools/' + module]:
            raise GateError('Previous metadata source differs from the bound checkpoint')
        if projection(path.read_text(), module) != projection((ROOT/'tools'/module).read_text(), module):
            raise GateError('Metadata continuation changed rules, prompts, schemas, model inputs or activation')
    return sorted(changed)


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


def apply_text_plan_if_needed(conn, plan):
    """An empty validated plan has no write to guard or database to rehash.

    Actual repairs retain the full before/inside-transaction comparison and
    native projection checks. Later candidate/audit/activation guards remain.
    """
    if plan['unmatched_projection']:
        raise GateError('Retained native projection cannot be matched')
    if not plan['changes']:
        return {'state':'validated-unchanged','changed_products':0,
                'classification_decisions_written':0,'new_model_calls':0,'http_attempts':0,
                'plan_sha256':classifier.context_hash(plan)}
    baseline = fingerprint(conn)
    conn.execute('BEGIN IMMEDIATE')
    result = text_recovery.apply(conn, plan, baseline)
    conn.commit()
    return result


def clone(source, target):
    if target.exists() or source.resolve() == target.resolve():
        raise GateError('Snapshot target must be fresh and different from source')
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(readonly(source)) as original, closing(sqlite3.connect(target)) as dest:
        original.backup(dest)


def database_byte_identity(path):
    """Exact main/WAL bytes; SHM is a derived coordination cache."""
    result = {}
    for key, suffix in (('main', ''), ('wal', '-wal')):
        member = Path(str(path) + suffix)
        if not member.exists() or (key == 'wal' and member.stat().st_size == 0):
            result[key] = None
            continue
        before = member.stat(); digest = hashlib.sha256()
        with member.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(block)
        after = member.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise GateError('Database changed while hashing snapshot bytes')
        result[key] = {'bytes': after.st_size, 'sha256': digest.hexdigest()}
    return result


def clone_bound_candidate(source, target, expected):
    """Verify legacy logical identity once and prevent writes through copying.

    A reserved SQLite transaction blocks other writers, while the backup API
    still reads committed data. Exact bytes catch out-of-band file changes.
    Nothing is committed to the predecessor; interrupted clones still need
    their complete logical fingerprint checked before reuse.
    """
    with closing(sqlite3.connect(source)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        try:
            before = fingerprint(conn)
            if before != expected:
                raise GateError('Previous candidate changed after validation')
            identity = database_byte_identity(source)
            if target.exists():
                with closing(readonly(target)) as copied:
                    if fingerprint(copied) != before:
                        raise GateError('Interrupted fork clone is not the exact preserved candidate')
            else:
                clone(source, target)
            if database_byte_identity(source) != identity:
                raise GateError('Concurrent previous-candidate change')
            return {'logical_fingerprint': before, 'source_byte_identity': identity,
                    'writer_reservation': True, 'predecessor_decision_writes': 0}
        finally:
            conn.rollback()


def restore_historical_categories(working, original):
    """Recover last-known evidence erased by the historical full-table replacement."""
    with closing(sqlite3.connect(working)) as conn:
        conn.execute('ATTACH DATABASE ? AS original', (str(original.resolve()),))
        with conn:
            before=conn.total_changes
            conn.execute('''INSERT OR IGNORE INTO main.product_categories(product_id,category_path)
                SELECT c.product_id,c.category_path FROM original.product_categories c
                JOIN main.products p ON p.id=c.product_id WHERE p.current_on_ocado=0''')
            return conn.total_changes-before


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


@contextmanager
def activation_connection(source):
    """Avoid a candidate-sized WAL plus main-file copy during bulk activation.

    Keep the rollback journal and transaction atomicity; restore the caller's
    WAL preference after either commit or rollback. Busy readers/writers fail
    normally rather than bypassing the canonical concurrency check.
    """
    with closing(sqlite3.connect(source)) as conn:
        journal = conn.execute('PRAGMA main.journal_mode').fetchone()[0]
        try:
            if journal == 'wal' and conn.execute('PRAGMA main.journal_mode=DELETE').fetchone()[0] != 'delete':
                raise GateError('Cannot prepare rollback journal for activation')
            yield conn
        finally:
            if conn.in_transaction:
                conn.rollback()
            if journal == 'wal' and conn.execute('PRAGMA main.journal_mode=WAL').fetchone()[0] != 'wal':
                raise GateError('Cannot restore WAL preference after activation')


def activate(source, candidate, expected, backup):
    """Copy the validated candidate in one SQLite transaction, never replace live files."""
    validate_candidate(candidate)
    if backup.exists():
        with closing(readonly(backup)) as conn:
            if fingerprint(conn) != expected: raise GateError('Preserved activation backup does not match the baseline')
    else:
        clone(source, backup)
    with activation_connection(source) as conn:
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


def validate_research_protocol_origin(previous, old, proof, *, audit_repair=False):
    """Resume a frozen research cohort after transport-only repair; never refreeze."""
    research_state = 'completed' if audit_repair else 'checkpointed'
    inherited = (not audit_repair and 'research' not in old['stages']
                 and old.get('research_protocol_origin')
                 and old['stages'].get('assessment', {}).get('state') == 'checkpointed')
    if not inherited and (old['stages'].get('assessment', {}).get('state') != 'completed' or old['stages'].get('research', {}).get('state') != research_state):
        raise GateError('Research protocol continuation requires completed assessment and drained checkpointed research')
    if any(key in old['stages'] for key in ('activation','export')) or (not audit_repair and 'positive_audit' in old['stages']):
        raise GateError('Research protocol continuation cannot reuse a consumed positive audit or activation')
    if audit_repair:
        score = read(Path(previous)/'positive-score.json')
        if old['stages'].get('positive_audit', {}).get('state') != 'checkpointed' or score.get('state') != 'failed':
            raise GateError('Audit repair requires a retained failed positive audit')
    origin = Path(old.get('immutable_manufacturer_root', Path(previous)/'manufacturer')).resolve()
    snapshot = hashlib.sha256((origin/'snapshot.json').read_bytes()).hexdigest()
    expected_snapshot = (old['research_protocol_origin']['snapshot_sha256'] if inherited
                         else old['stages']['assessment']['artifacts'].get('manufacturer/snapshot.json'))
    if snapshot != expected_snapshot:
        raise GateError('Frozen initially unknown cohort changed')
    bundle = research.read_bundle(origin)
    if not research.gathering_complete(research.bundle_status(bundle)):
        raise GateError('Frozen research acquisition is incomplete or changed')
    key = classifier.context_hash({'outcomes':bundle['outcomes'], 'sources':bundle['sources'],
                                   'shared_associations':bundle.get('shared_associations',{})})
    expected = {'root':str(origin),'snapshot_sha256':snapshot,'bundle_hash':key}
    if inherited and old['research_protocol_origin'] != expected:
        raise GateError('Inherited frozen research provenance changed')
    if proof.get('research_continuation') != expected:
        raise GateError('Research protocol proof does not bind the exact frozen cohort and captures')
    return expected


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
        snapshots = self.manifest.get('immutable_snapshots', {})
        self.manufacturer = Path(self.manifest.get('immutable_manufacturer_root', self.root/'manufacturer'))
        self.original = Path(snapshots.get('original', self.root / 'original.sqlite'))
        self.working = Path(snapshots.get('refreshed', self.root / 'refreshed.sqlite'))
        self.candidate = self.root / 'candidate.sqlite'

    def save(self):
        self.manifest['remaining'] = [stage for stage in STAGES if not self.satisfied(stage)]
        benchmark.save(self.path, self.manifest)

    def satisfied(self, stage):
        return self.manifest['stages'].get(stage, {}).get('state') == 'completed'

    def artifacts(self, stage):
        paths = {'checks':['checks/checks.json'],
                 'preflight':['preflight-controls/report.json','preflight-pilot/report.json'],
                 'assessment':['manufacturer/snapshot.json'],
                 'research':['manufacturer-coverage.json','manufacturer-affected.json'],
                 'positive_audit':['positive-audit.json','positive-audit.predictions.json','positive-readings/annotated-candidates.json','positive-score.json']}
        if getattr(self, 'manifest', {}).get('attribution_repair'):
            paths['positive_audit'] += ['positive-score.attribution-corrected.json',
                'positive-audit.attribution-corrected.predictions.json', 'attribution-repair.json']
        def path_for(name):
            if name.startswith('manufacturer/'):
                return getattr(self, 'manufacturer', self.root/'manufacturer')/name.split('/',1)[1]
            return self.root/name
        return {name: hashlib.sha256(path_for(name).read_bytes()).hexdigest() for name in paths.get(stage,[])}

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
            if not benchmark.successful(report) or report['fixture_sha256'] != old.get('benchmark_reuse',{}).get('benchmarked_fixture_sha256',old_config['fixture_sha256']):
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

    def continue_assessment(self, previous, authorization):
        """Reuse a completed scrape and unchanged classifier evidence by explicit user decision.

        Historical reports remain untouched. Production assessment has no
        category/reason sample admission gates; research/audit/export remain guarded.
        """
        previous = Path(previous).resolve(); old = read(previous/'manifest.json')
        if not authorization or not authorization.strip():
            raise GateError('Benchmark reuse requires the explicit user instruction')
        if self.manifest['stages'] or self.working.exists() or previous == self.root:
            raise GateError('Assessment continuation requires a fresh root')
        if old['stages'].get('scrape', {}).get('state') != 'completed' or old.get('state') == 'running':
            raise GateError('Assessment continuation requires a completed scrape before reassessment')
        old_config = old['configuration']; new_config = self.manifest['configuration']
        if {k:v for k,v in old_config.items() if k not in {'code_hashes','fixture_sha256'}} != {k:v for k,v in new_config.items() if k not in {'code_hashes','fixture_sha256'}}:
            raise GateError('Benchmark reuse cannot change models, policy, fixture, scope or source')
        if old_config['fixture_sha256'] != new_config['fixture_sha256']:
            # A metadata cleanup is not a new experiment. Require the exact
            # previously bound file and unchanged cases/references/splits/pilot.
            frozen = previous/'frozen-fixture.json'
            if not frozen.exists(): raise GateError('Changed fixture requires its bound frozen predecessor')
            old_fixture = read(frozen); new_fixture = read(self.fixture)
            if classifier.context_hash(old_fixture) != old_config['fixture_sha256']:
                raise GateError('Frozen predecessor does not match the previous fixture hash')
            def evaluation_projection(value):
                return {**{key: item for key,item in value.items() if key!='selection'},
                        'pilot_case_ids':value.get('selection',{}).get('pilot_case_ids',[])}
            if evaluation_projection(old_fixture) != evaluation_projection(new_fixture):
                raise GateError('Benchmark reuse cannot change fixture inputs, references, splits or pilot selection')
        changed = {key for key in old_config['code_hashes'].keys() | new_config['code_hashes'].keys()
                   if old_config['code_hashes'].get(key) != new_config['code_hashes'].get(key)}
        cleanup_paths = {
            'tools/refresh_whole_product.py', 'tests/test_refresh_whole_product.py',
            'tools/research_ocado_manufacturers.py', 'tools/cursory_research_accounting.py',
            'tools/build_bounded_manufacturer_bundle.py',
            'tests/test_cursory_research_accounting.py', 'tests/test_bounded_manufacturer_bundle.py',
            'tests/test_build_bounded_manufacturer_bundle.py',
            'tools/assemble_whole_product_fixture.py', 'tools/prepare_whole_product_corpus.py',
            'tools/compare_whole_product_models.py', 'tools/refresh_whole_product_references.py',
            'tests/test_benchmark_whole_product.py', 'tests/test_manufacturer_evidence.py',
            'tools/benchmark_ocado_vegan.py', 'tests/test_benchmark_ocado_vegan.py',
            'tools/scan_ocado_vegan_according_to_ingredients.py',
            'tools/scan_ocado_vegan_according_to_manufacturer.py',
            'tools/scan_ocado_vegan_according_to_manufacturer_sitemap.py',
            'tests/test_ocado_vegan_according_to_ingredients.py',
        }
        if not changed <= cleanup_paths:
            raise GateError('Benchmark reuse cannot cover classifier or other implementation changes')
        for stage in ('checks', 'preflight'):
            if old['stages'].get(stage, {}).get('state') != 'completed':
                raise GateError('Previous implementation checks and preflight must have completed')
            for name, digest in old['stages'][stage]['artifacts'].items():
                if hashlib.sha256((previous/name).read_bytes()).hexdigest() != digest:
                    raise GateError('Previous completed artifact changed: '+name)
        for name in ('preflight-controls', 'preflight-pilot'):
            report = read(previous/name/'report.json')
            if not benchmark.successful(report) or report['fixture_sha256'] != old.get('benchmark_reuse',{}).get('benchmarked_fixture_sha256',old_config['fixture_sha256']):
                raise GateError('Previous benchmark evidence is not applicable')
            for filename, digest in report['implementation_hashes'].items():
                if classifier.context_hash((ROOT/'tools'/filename).read_text()) != digest:
                    raise GateError('Benchmarked classification implementation changed: '+filename)
        with closing(readonly(self.source)) as conn:
            if fingerprint(conn) != old['canonical_baseline']:
                raise GateError('Canonical source changed before assessment continuation')
        with closing(readonly(previous/'refreshed.sqlite')) as conn:
            latest = dict(conn.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone())
            if latest['status'] != 'completed' or latest != old['sync_run']:
                raise GateError('Completed scrape provenance changed')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_context_baseline'").fetchone():
                raise GateError('An interrupted sync cannot be reused')
        clone(previous/'original.sqlite', self.root/'original.sqlite')
        clone(previous/'refreshed.sqlite', self.working)
        if (previous/'candidate.sqlite').exists():
            clone(previous/'candidate.sqlite', self.candidate)
            if (previous/'manufacturer').exists():
                shutil.copytree(previous/'manufacturer', self.root/'manufacturer')
        for name in ('preflight-controls', 'preflight-pilot'):
            (self.root/name).mkdir()
            (self.root/name/'report.json').write_bytes((previous/name/'report.json').read_bytes())
        for key in ('canonical_baseline', 'original_population', 'pre_sync_id', 'sync_run',
                    'sync_acquisition_run', 'historical_category_rows_restored'):
            if key in old: self.manifest[key] = old[key]
        reuse = {'authorization': authorization.strip(), 'at_utc': datetime.now(timezone.utc).isoformat(),
                 'previous_root': str(previous), 'previous_manifest_sha256': hashlib.sha256((previous/'manifest.json').read_bytes()).hexdigest(),
                 'changed_paths': sorted(changed), 'reused_candidate': self.candidate.exists(),
                 'previous_fixture_sha256':old_config['fixture_sha256'], 'current_fixture_sha256':new_config['fixture_sha256'],
                 'benchmarked_fixture_sha256':old.get('benchmark_reuse',{}).get('benchmarked_fixture_sha256',old_config['fixture_sha256']),
                 'note': 'User chose existing benchmark evidence and product-by-product all-known reassessment. Historical reports preserved; no category/reason sample minimum applies. Existing candidate decisions resume without reclassifying completed products. Research, positive audit, policy validation and activation/export gates remain required.'}
        (self.root/'frozen-fixture.json').write_bytes(self.fixture.read_bytes())
        benchmark.save(self.root/'benchmark-reuse.json', reuse); self.manifest['benchmark_reuse'] = reuse
        for stage in ('preflight', 'scrape'):
            self.manifest['stages'][stage] = {**old['stages'][stage], 'reused_from': str(previous)}
        self.save()

    def fork_policy_repair(self, previous, evidence_path, *, protocol_only=False, audit_repair=False, reference_only=False):
        """Start a new bound phase after an attributed, validated gate repair.

        Reuse immutable catalogue snapshots and accepted evidence, never old
        implementation hashes. Only the candidate is copied; no scraping or
        wholesale model replay is hidden inside this transition.
        """
        previous = Path(previous).resolve(); evidence_path = Path(evidence_path).resolve()
        old = read(previous/'manifest.json'); proof = read(evidence_path)
        if reference_only and (not audit_repair or protocol_only):
            raise GateError('Reference-only continuation requires its own failed-audit repair')
        if self.manifest['stages'] or previous == self.root:
            raise GateError('Policy repair requires a fresh root')
        if old['state'] == 'running' or any(stage['state'] == 'running' for stage in old['stages'].values()):
            raise GateError('Previous writers must drain before policy repair')
        metadata_only = proof.get('repair_scope') == 'unknown-decision-metadata-only'
        if metadata_only and (protocol_only or audit_repair or reference_only):
            raise GateError('Targeted metadata repair cannot change another continuation contract')
        research_resume = (protocol_only or audit_repair or metadata_only) and ('research' in old['stages']
            or ((protocol_only or metadata_only) and old.get('research_protocol_origin')))
        research_origin = validate_research_protocol_origin(previous, old, proof, audit_repair=audit_repair) if research_resume else None
        if old['stages'].get('scrape', {}).get('state') != 'completed' or (not research_resume and any(
                key in old['stages'] for key in ('research','positive_audit','activation','export'))):
            raise GateError('Policy repair requires completed scraping and unfinished assessment')
        expected = self.manifest['configuration']; previous_config = old['configuration']
        if {k:v for k,v in expected.items() if k!='code_hashes'} != {k:v for k,v in previous_config.items() if k!='code_hashes'}:
            raise GateError('Policy repair cannot change source, frozen fixture, models, scope or policy contract')
        if proof.get('state') != 'passed' or proof.get('candidate_hashes') != expected['code_hashes']:
            raise GateError('Repair evidence is failed or bound to different code')
        if proof.get('previous_manifest_sha256') != hashlib.sha256((previous/'manifest.json').read_bytes()).hexdigest():
            raise GateError('Previous phase changed after repair validation')
        if protocol_only:
            validate_protocol_continuation(previous_config['code_hashes'], expected['code_hashes'], proof['previous_protocol_sources'])
        if metadata_only:
            validate_metadata_continuation(previous_config['code_hashes'], expected['code_hashes'], proof['previous_metadata_sources'])
        if reference_only:
            validate_reference_audit_continuation(previous_config['code_hashes'], expected['code_hashes'], proof['previous_reference_sources'])
            reference = read(proof['reference_protocol_path'])
            if reference.get('state') != 'passed' or reference.get('candidate_hashes') != expected['code_hashes']:
                raise GateError('Reference instruction repair requires bound paired source diagnostics')
            from adjudicate_whole_product_corpus import ROW_SCHEMA, PROMPT_VERSION, validate_basis
            cases = {case['case_id']:case for case in reference.get('cases', [])}
            readings = reference.get('readings', [])
            if not cases or len(readings) != 2 * len(cases):
                raise GateError('Reference diagnostics require exactly two readings per frozen case')
            for case_id, case in cases.items():
                paired = [row for row in readings if row['annotation']['case_id'] == case_id]
                if (len(paired) != 2 or len({row.get('call_id') for row in paired}) != 2
                        or any(not row.get('call_id') or row.get('model') != MODELS['annotation']
                            or row.get('effort') != MODELS['annotation_effort']
                            or row.get('prompt_version') != PROMPT_VERSION
                            or row.get('input_sha256') != classifier.context_hash(case['classifier_input']) for row in paired)):
                    raise GateError('Reference diagnostic identity, independence or provenance differs')
                if len({(row['annotation']['expected_status'], row['annotation']['expected_reason']) for row in paired}) != 1:
                    raise GateError('Reference diagnostic readings remain disputed')
                if any(row['annotation']['expected_status'] != 'vegan' for row in paired):
                    raise GateError('Reference-only repair cannot retain a confirmed unsafe positive')
                with closing(readonly(previous/'candidate.sqlite')) as diagnostic_db:
                    product_id = case['classifier_input']['product']['id']
                    actual = classifier.product_context_for_llm(classifier.load_product_context(diagnostic_db, product_id))
                    stored = diagnostic_db.execute('SELECT vegan_status,vegan_reason FROM products WHERE id=?', (product_id,)).fetchone()
                    if actual != case['classifier_input'] or tuple(stored) != ('vegan', paired[0]['annotation']['expected_reason']):
                        raise GateError('Reference diagnostic differs from the retained candidate decision or evidence')
                for row in paired:
                    annotation = row['annotation']; policy.validate_schema(annotation, ROW_SCHEMA)
                    for ref in annotation['input_refs']:policy.resolve_reference(case['classifier_input'], ref)
                    validate_basis(annotation, case['classifier_input'])
        implementation = read(proof['checks_path']); checks.validate_record(implementation)
        reports = {name: read(proof[name+'_path']) for name in ('controls','pilot')}
        for report in reports.values():
            if (report.get('mode') != 'retained-call-policy-replay' or not benchmark.successful(report)
                    or report.get('candidate_hashes') != expected['code_hashes']
                    or report.get('fixture_sha256') != expected['fixture_sha256']):
                raise GateError('Frozen policy replay failed or its provenance changed')
        fixture = benchmark.load_fixture(self.fixture)
        controlled = {case['case_id'] for case in fixture['cases'] if case['case_kind']=='synthetic'}
        if set(reports['controls'].get('case_ids', [])) != controlled:
            raise GateError('Policy repair must replay every frozen controlled contract')
        if not reports['pilot'].get('case_ids') or not set(reports['pilot']['case_ids']) <= {
                case['case_id'] for case in fixture['cases'] if case['case_kind']=='natural'}:
            raise GateError('Policy repair needs retained frozen natural cases')
        protocol = read(proof['protocol_path'])
        if (protocol.get('state') != 'passed' or protocol.get('candidate_hashes') != expected['code_hashes']
                or not protocol.get('products') or protocol.get('validated_independent_passes') != 2):
            raise GateError('Repaired source protocol needs two validated fresh primary readings')
        with closing(readonly(self.source)) as conn:
            if fingerprint(conn) != old['canonical_baseline']:
                raise GateError('Canonical source changed before policy repair')
        clone_guard = clone_bound_candidate(previous/'candidate.sqlite', self.candidate,
                                            proof['previous_candidate_fingerprint'])
        before = clone_guard['logical_fingerprint']
        snapshots = old.get('immutable_snapshots', {})
        self.original = Path(snapshots.get('original', previous/'original.sqlite')).resolve()
        self.working = Path(snapshots.get('refreshed', previous/'refreshed.sqlite')).resolve()
        with closing(readonly(self.working)) as conn:
            latest = dict(conn.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone())
            if latest != old['sync_run'] or latest['status'] != 'completed':
                raise GateError('Immutable completed scrape changed')
        for key in ('canonical_baseline','original_population','pre_sync_id','sync_run',
                    'sync_acquisition_run','historical_category_rows_restored'):
            if key in old:self.manifest[key] = old[key]
        self.manifest['immutable_snapshots'] = {'original':str(self.original),'refreshed':str(self.working)}
        self.manifest['policy_repair_origin'] = {'root':str(previous),'manifest_sha256':proof['previous_manifest_sha256'],
            'candidate_fingerprint':before,'evidence_path':str(evidence_path),
            'evidence_sha256':hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            'clone_guard':clone_guard,
            'note':'Accepted evidence replay under a repaired gate; old prompt/call provenance retained. No new model reading is claimed for replayed decisions.'}
        if protocol_only or reference_only:
            result = {'mode':'reference-instruction-only-continuation' if reference_only else 'literal-quotation-protocol-continuation', 'new_model_calls':0,
                'decision_writes':0, 'previous_candidate_fingerprint':before,
                'note':'Accepted decisions and exact literal evidence remain unchanged. Reference-only repairs cannot rewrite product decisions or reuse the consumed audit; transport repairs retain their original contract.'}
            if reference_only:
                self.manifest['reference_repair_origin'] = dict(self.manifest['policy_repair_origin'])
            else:
                self.manifest['protocol_repair_origin'] = self.manifest.pop('policy_repair_origin')
        else:
            with closing(classifier.connect(self.candidate)) as conn:
                conn.execute('BEGIN IMMEDIATE')
                replay_function = policy_replay.replay_unknown_metadata if metadata_only else policy_replay.replay_candidate
                result = replay_function(conn, sync_run_id=self.manifest['sync_run']['id'])
                conn.commit()
        benchmark.save(self.root/'policy-replay.json', result)
        if audit_repair:
            repair_plan_path = Path(proof['native_text_plan_path'])
            if hashlib.sha256(repair_plan_path.read_bytes()).hexdigest() != proof['native_text_plan_sha256']:
                raise GateError('Native text recovery plan changed')
            with closing(readonly(self.candidate) if reference_only else classifier.connect(self.candidate)) as conn:
                # Replay changes audit/version metadata, not native fields.
                plan = text_recovery.preview(conn)
                original_plan = read(repair_plan_path)
                comparable = lambda value: [{k:v for k,v in row.items() if k not in
                    {'previous_decision','previous_audit_id','previous_status'}} for row in value['changes']]
                if comparable(plan) != comparable(original_plan) or plan['unmatched_projection']:
                    raise GateError('Native fields changed before retained text recovery')
                if reference_only and plan['changes']:
                    raise GateError('Reference-only repair cannot change catalogue evidence')
                recovery = apply_text_plan_if_needed(conn, plan)
            benchmark.save(self.root/'native-text-recovery.json', recovery)
            benchmark.save(self.root/'native-text-plan.json', plan)
            self.manifest['failed_audit_origin'] = {'root':str(previous),
                'score_sha256':hashlib.sha256((previous/'positive-score.json').read_bytes()).hexdigest(),
                'packet_sha256':hashlib.sha256((previous/'positive-audit.json').read_bytes()).hexdigest(),
                'note':'Failed audit remains unchanged and is diagnostic evidence. No completed reading or expectation is reused for the next fixed-candidate audit.'}
            self.manifest['positive_audit_seed'] = int.from_bytes(os.urandom(8),'big')
        (self.root/'checks').mkdir(); benchmark.save(self.root/'checks/checks.json', implementation)
        for name, report in reports.items():
            benchmark.save(self.root/('preflight-'+('controls' if name=='controls' else 'pilot'))/'report.json',report)
        (self.root/'frozen-fixture.json').write_bytes(self.fixture.read_bytes())
        for stage in ('checks','preflight'):
            self.manifest['stages'][stage] = {'state':'completed','artifacts':self.artifacts(stage),
                'finished_utc':datetime.now(timezone.utc).isoformat(),'verification_kind':'attributed-policy-repair'}
        self.manifest['stages']['scrape'] = {**old['stages']['scrape'],'reused_from':str(previous)}
        if research_origin:
            self.manufacturer = Path(research_origin['root'])
            self.manifest['immutable_manufacturer_root'] = str(self.manufacturer)
            self.manifest['research_protocol_origin'] = research_origin
            if not audit_repair or reference_only:
                self.manifest['stages']['assessment'] = {**old['stages']['assessment'],'reused_from':str(previous)}
            if reference_only:
                for name in ('manufacturer-coverage.json', 'manufacturer-affected.json'):
                    (self.root/name).write_bytes((previous/name).read_bytes())
                self.manifest['stages']['research'] = {**old['stages']['research'],'reused_from':str(previous)}
        self.manifest['state'] = 'checkpointed'; self.save()

    def step_scrape(self):
        if self.manifest.get('immutable_snapshots'):
            raise GateError('Retained immutable scrape snapshots cannot be modified')
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
        restored=restore_historical_categories(self.working, self.root/'original.sqlite')
        self.manifest['historical_category_rows_restored']=self.manifest.get('historical_category_rows_restored',0)+restored
        self.save()
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


    def annotate(self, name, packet, reuse=False):
        arguments = [ROOT/'tools/adjudicate_whole_product_corpus.py', '--packet', packet, '--output', self.root/name,
                     '--model', MODELS['annotation'], '--effort', MODELS['annotation_effort'], '--batch-size', '8', '--workers', '3']
        if reuse: arguments += ['--reuse-packet', self.root/'name-census.json', '--reuse-output', self.root/'name-readings']
        self.command(name, arguments)
        return read(self.root/name/'annotated-candidates.json')


    def classify(self, name):
        self.manifest['classification_execution'] = {'workers': 8, 'batch_size': 8, 'primary_passes': 2}
        self.save()
        self.command(name, [ROOT/'tools/classify_ocado_vegan.py', '--db', self.candidate, '--preserve-legacy',
                           'classify-all', '--codex', '--scope', 'all-known', '--sync-run-id', self.manifest['sync_run']['id'],
                           '--model', MODELS['primary'], '--reasoning-effort', MODELS['effort'], '--passes', '2',
                           '--workers', '8', '--batch-size', '8', '--retries', '1', '--usage-journal', self.root/(name+'.calls.jsonl')])
        validate_candidate(self.candidate)

    def step_assessment(self):
        if not self.candidate.exists(): shadow.snapshot(self.working, self.candidate, all_products=True)
        self.classify('all-known-assessment')
        root = getattr(self, 'manufacturer', self.root/'manufacturer')
        if not (root/'snapshot.json').exists(): research.create_snapshot(root, self.candidate, 6)

    def step_research(self):
        bundle = research.read_bundle(self.manufacturer); status = research.bundle_status(bundle)
        origin = self.manifest.get('research_protocol_origin')
        if origin and classifier.context_hash({'outcomes':bundle['outcomes'],'sources':bundle['sources'],
                'shared_associations':bundle.get('shared_associations',{})}) != origin['bundle_hash']:
            raise GateError('Frozen manufacturer acquisition changed after protocol continuation')
        benchmark.save(self.root/'manufacturer-coverage.json', status)
        if not research.gathering_complete(status):
            raise GateError('Manufacturer gathering remains incomplete; research every frozen product and retain primary captures before resuming')
        # Store all captures/outcomes, including contrary pages, as unverified
        # associations. The normal two-pass classifier, never the collector,
        # establishes suitability from those complete sources.
        affected = retain_refresh_sources(self.candidate, bundle, self.manufacturer)
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
            self.command('positive-sample', [ROOT/'tools/audit_whole_product_positives.py', '--db', self.candidate, '--output', packet,
                                          '--seed', self.manifest.get('positive_audit_seed',20261012)])
        self.annotate('positive-readings', packet)
        if not (self.root/'positive-score.json').exists():
            self.command('positive-score', [ROOT/'tools/audit_whole_product_positives.py', '--packet', packet,
                                          '--annotations', self.root/'positive-readings/annotated-candidates.json', '--output', self.root/'positive-score.json'])
        if read(self.root/'positive-score.json')['state'] != 'passed': raise GateError('Positive audit failed; no activation')

    def step_activation(self):
        # This method is also guarded when called directly rather than by run().
        if any(not self.satisfied(stage) for stage in STAGES[:STAGES.index('activation')]):
            raise GateError('All validation/research/audit stages must complete before activation')
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
        with closing(readonly(getattr(self, 'original', self.root/'original.sqlite'))) as original, closing(classifier.connect(self.candidate)) as conn:
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
            if self.satisfied(stage) and record.get('artifacts',{}) != self.artifacts(stage):
                raise GateError('Completed stage artifacts changed: '+stage)
        for stage in STAGES:
            if self.satisfied(stage):
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
    if not research.gathering_complete(status):
        raise GateError('Incomplete manufacturer coverage cannot be imported')
    with closing(classifier.connect(db)) as conn:
        manufacturer.ensure_schema(conn)
        key = classifier.context_hash({'outcomes': bundle['outcomes'], 'sources': bundle['sources'],
                                       'shared_associations': bundle.get('shared_associations', {})})
        prior = conn.execute('SELECT id FROM manufacturer_research_runs WHERE bundle_hash=?', (key,)).fetchone()
        if prior: return {'run_id': prior[0], 'resumed': True}
        affected = set(bundle['products']); changed = set()
        fresh_sources = {key: {**value, 'id': classifier.context_hash([value['url'],value['content_sha256'],value['retrieved_at']])}
                         for key,value in bundle['sources'].items()}
        # A checked page with no applicable evidence is an audit receipt, not
        # a replacement assertion or a model input for this or other products.
        applicable_ids = {source_id for outcome in bundle['outcomes'].values() for source_id in outcome['source_ids']}
        applicable_ids.update(source_id for association in bundle.get('shared_associations', {}).values() for source_id in association['source_ids'])
        refreshed = {}
        for source_id in sorted(applicable_ids):
            source = fresh_sources[source_id]
            refreshed.setdefault(source['url'], []).append(source)
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
                associations[product_id] = {**outcome,
                    'source_ids': [fresh_sources[key]['id'] for key in outcome['source_ids']],
                    'checked_source_ids': [fresh_sources[key]['id'] for key in outcome.get('checked_source_ids', [])]}
            # A newly encountered contrary page can affect an exact existing
            # positive even if it was never previously bound to this URL.
            # Collection specifies native IDs and hashes; normal assessment
            # must still establish applicability and the final status.
            for product_id, association in bundle.get('shared_associations', {}).items():
                context = classifier.load_product_context(conn, product_id)
                if classifier.context_hash(classifier.product_context_for_llm(context)) != association['catalogue_context_hash']:
                    raise GateError('Catalogue changed for shared-source association: ' + product_id)
                if association.get('positive_claim_approved') is not False or not association.get('scope_match'):
                    raise GateError('Shared capture cannot approve a product claim')
                affected.add(product_id)
                associations[product_id] = {**association, 'outcome': 'source_collected',
                    'source_ids': [fresh_sources[key]['id'] for key in association['source_ids']],
                    'classification_review_required': True}
            # Changed pages also need assessment for previously associated
            # products outside the unknown cohort; no brand-wide extrapolation.
            for product_id, url, old_hash in conn.execute('SELECT DISTINCT e.product_id,s.url,s.content_sha256 FROM manufacturer_website_evidence e JOIN manufacturer_website_sources s ON s.id=e.source_id'):
                replacements = [source for source in refreshed.get(url, []) if source['content_sha256'] != old_hash]
                if replacements:
                    affected.add(product_id)
                    associations.setdefault(product_id, {'outcome':'unverified','source_ids':[]})['source_ids'].extend(source['id'] for source in replacements)
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
    parser.add_argument('command', choices=['run','status','fork-scrape-repair','continue-assessment','fork-policy-repair','fork-protocol-repair','fork-audit-repair','fork-reference-audit-repair'])
    parser.add_argument('--from-root', type=Path)
    parser.add_argument('--reuse-benchmark-reason')
    parser.add_argument('--repair-evidence', type=Path)
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
        if args.command == 'continue-assessment':
            if not args.from_root or not args.reuse_benchmark_reason:
                parser.error('continue-assessment requires --from-root and --reuse-benchmark-reason with the explicit user instruction')
            runner.continue_assessment(args.from_root, args.reuse_benchmark_reason)
        if args.command in {'fork-policy-repair','fork-protocol-repair','fork-audit-repair','fork-reference-audit-repair'}:
            if not args.from_root or not args.repair_evidence:
                parser.error('fork-policy-repair requires --from-root and --repair-evidence')
            runner.fork_policy_repair(args.from_root, args.repair_evidence, protocol_only=args.command=='fork-protocol-repair', audit_repair=args.command in {'fork-audit-repair','fork-reference-audit-repair'}, reference_only=args.command=='fork-reference-audit-repair')
        if args.command == 'run': runner.run(args.until)
        print(json.dumps({'state': runner.manifest['state'], 'remaining': runner.manifest['remaining'], 'manifest': str(runner.path)}))
    except (GateError, ValueError) as exc:
        print(str(exc), file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
