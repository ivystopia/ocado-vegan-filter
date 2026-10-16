#!/usr/bin/env python3
"""Explicitly authorised reason-only audit continuation; never changes suitability.

Preserve the failed score and blind references. This is not a general exemption
from fresh positive audits: changed evidence, eligibility or export membership
must fail, as must any finding other than name-to-manufacturer precedence.
"""
import argparse
import ast
from contextlib import closing
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import shutil

import audit_whole_product_positives as audit
import benchmark_whole_product as benchmark
import classify_ocado_vegan as classifier
import refresh_whole_product as refresh
import validate_whole_product_candidate as checks


MODE = 'retained-suitability-audit-with-separately-validated-attribution'
FILES = ('positive-audit.json', 'positive-audit.predictions.json',
         'positive-readings/annotated-candidates.json', 'positive-score.json')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def attribution_cases(score):
    issues = score.get('issues', [])
    if (score.get('state') != 'failed' or score.get('reference_counts') != {'vegan': 600}
            or not issues or score.get('sampling', {}).get('size') != 600):
        raise ValueError('Only a completed 600-vegan suitability audit can be retained')
    if any((i.get('finding'), i.get('candidate_reason'), i.get('reference_reason')) !=
           ('unsupported_reason', 'name', 'manufacturer') for i in issues):
        raise ValueError('A suitability or different reason finding requires a fresh audit')
    ids = [i['case_id'] for i in issues]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate attribution findings')
    return ids


def validate_code(previous, current, sources):
    """The only production-policy delta is complete dietary-list recognition."""
    allowed = {'tools/classify_ocado_vegan.py', 'tools/vegan_policy.py',
               'tools/refresh_whole_product.py', 'tools/repair_vegan_attribution.py',
               'tests/test_slash_dietary_attributes.py', 'tests/test_attribution_repair.py'}
    changed = {k for k in previous.keys() | current.keys() if previous.get(k) != current.get(k)}
    if not changed <= allowed:
        raise ValueError('Unrelated code changed; cannot retain a suitability audit')
    def projection(text, name):
        tree = ast.parse(text)
        if name == 'tools/classify_ocado_vegan.py':
            tree.body = [n for n in tree.body if not (isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'CLASSIFIER_VERSION' for t in n.targets))]
        elif name == 'tools/vegan_policy.py':
            tree.body = [n for n in tree.body if not (isinstance(n, ast.FunctionDef)
                         and n.name in {'dietary_attribute_claim', 'direct_claim'})]
        elif name == 'tools/refresh_whole_product.py':
            for n in tree.body:
                if isinstance(n, ast.ClassDef) and n.name == 'Refresh':
                    n.body = [f for f in n.body if not (isinstance(f, ast.FunctionDef) and f.name == 'artifacts')]
        return ast.dump(tree, include_attributes=False)
    for name in ('tools/classify_ocado_vegan.py', 'tools/vegan_policy.py', 'tools/refresh_whole_product.py'):
        original = Path(sources[name])
        if sha(original) != previous[name]:
            raise ValueError('Previous source does not match the audited implementation')
        if projection(original.read_text(), name) != projection((benchmark.ROOT/name).read_text(), name):
            raise ValueError('Rules, prompts, models, schema or activation changed outside the attribution repair')
    return sorted(changed)


def product_signature(conn, *, target_ids=()):
    """All source/product fields and export membership, except repair metadata."""
    excluded = {'vegan_classifier_version', 'vegan_classified_at_epoch', 'vegan_audit_id'}
    digest = hashlib.sha256()
    for row in conn.execute('SELECT * FROM products ORDER BY id'):
        value = dict(row)
        if str(value['id']) in target_ids:
            for field in excluded:
                value.pop(field, None)
            # Name and manufacturer occupy the same browser allowlist.
            if value['vegan_status'] != 'vegan' or value['vegan_reason'] not in {'name', 'manufacturer'}:
                raise ValueError('Attribution repair cannot change suitability or export membership')
            value['vegan_reason'] = 'manufacturer-or-name'
        digest.update(classifier.json_dumps(value).encode())
    conflicts = [list(row) for row in conn.execute('SELECT * FROM product_vegan_conflicts ORDER BY product_id,issue_hash')]
    digest.update(classifier.json_dumps(conflicts).encode())
    return digest.hexdigest()


def validate_frozen_contexts(conn, packet):
    for case in packet['candidates']:
        context = classifier.product_context_for_llm(classifier.load_product_context(conn, case['classifier_input']['product']['id']))
        if context != case['classifier_input'] or classifier.context_hash(context) != case['input_sha256']:
            raise ValueError('A sampled product has changed evidence')


def repair(root, proof_path):
    root = Path(root).resolve(); proof_path = Path(proof_path).resolve(); proof = load(proof_path)
    if proof.get('authorization') != 'Retain the audit; validate the reason-only correction':
        raise ValueError('This exception requires explicit user authorisation')
    if proof.get('candidate_hashes') != benchmark.candidate_hashes():
        raise ValueError('Repair evidence is stale')
    for name, digest in proof['evidence_sha256'].items():
        if sha(name) != digest:
            raise ValueError('Repair validation artifact changed: '+name)
    manifest_path = root/'manifest.json'; old = load(manifest_path)
    if old.get('attribution_repair'):
        receipt = load(root/'attribution-repair.json')
        if receipt['proof_sha256'] != sha(proof_path):
            raise ValueError('A different repair already exists')
        return receipt
    if sha(manifest_path) != proof['previous_manifest_sha256']:
        raise ValueError('Concurrent manifest change')
    if old['stages'].get('positive_audit', {}).get('state') != 'checkpointed' or any(
            old['stages'].get(s, {}).get('state') != 'completed' for s in refresh.STAGES[:refresh.STAGES.index('positive_audit')]):
        raise ValueError('Repair requires completed pre-audit stages and a failed positive audit')
    if any(s in old['stages'] for s in ('activation', 'export')):
        raise ValueError('Never repair an activated candidate through this continuation')
    for name in FILES:
        if sha(root/name) != proof['retained_artifacts'][name]:
            raise ValueError('Retained audit artifact changed: '+name)
    score = load(root/'positive-score.json'); case_ids = attribution_cases(score)
    packet = load(root/'positive-audit.json'); original_predictions = load(root/'positive-audit.predictions.json')
    annotations = load(root/'positive-readings/annotated-candidates.json')
    if audit.score(packet, original_predictions, annotations) != score:
        raise ValueError('Original failed score does not match the immutable references')
    changes = validate_code(old['configuration']['code_hashes'], proof['candidate_hashes'], proof['previous_sources'])
    checks.validate_record(load(proof['checks_path']))
    for path in proof['replay_paths']:
        report = load(path)
        if report.get('candidate_hashes') != proof['candidate_hashes'] or not benchmark.successful(report):
            raise ValueError('Retained controlled/natural validation failed')
    protocol = load(proof['protocol_path'])
    core = {k:v for k,v in proof['candidate_hashes'].items() if k not in {'tools/refresh_whole_product.py','tools/repair_vegan_attribution.py'} and not k.startswith('tests/')}
    if (protocol.get('state') != 'passed' or protocol.get('model') != 'gpt-6-luna' or protocol.get('effort') != 'high'
            or {k:protocol['candidate_hashes'].get(k) for k in core} != core):
        raise ValueError('Fresh selected-model source protocol is missing or stale')
    preview = load(proof['preview_path'])
    if (preview.get('state') != 'passed' or preview.get('suitability_changes') != 0
            or preview.get('newly_vegan') != 0 or {'positive-audit.'+c['id'] for c in preview['changes']} != set(case_ids)):
        raise ValueError('Whole-catalogue impact differs from the attribution findings')
    if any(c['before'] != ['vegan','name'] or c['after'] != ['vegan','manufacturer'] for c in preview['changes']):
        raise ValueError('Impact is not solely manufacturer precedence')
    backup = load(proof['backup_receipt'])
    if not backup.get('verified_lossless') or sha(backup['archive']) != backup['archive_sha256']:
        raise ValueError('Verified pre-repair database backup is missing')
    history = root/'before-attribution'; history.mkdir(exist_ok=True)
    preserved = history/'manifest.json'
    if preserved.exists() and sha(preserved) != proof['previous_manifest_sha256']:
        raise ValueError('Preserved manifest differs')
    if not preserved.exists(): shutil.copyfile(manifest_path, preserved)
    for name in ('checks', 'preflight-controls', 'preflight-pilot'):
        source = root/name
        if not (history/name).exists(): shutil.copytree(source, history/name)
    db = root/'candidate.sqlite'; source = Path(old['configuration']['source'])
    with closing(refresh.readonly(source)) as canonical:
        if refresh.fingerprint(canonical) != old['canonical_baseline']:
            raise ValueError('Canonical database changed before repair')
    result_fields = {f.name for f in fields(classifier.ClassificationResult)}
    fresh = {v['product_id']:v for v in load(proof['results_path'])}
    targets = {key.removeprefix('positive-audit.') for key in case_ids}
    receipt_key = sha(proof_path)
    with closing(classifier.connect(db)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            prior = conn.execute("SELECT name FROM sqlite_master WHERE name='vegan_attribution_repair_receipts'").fetchone()
            saved = conn.execute('SELECT receipt_json FROM vegan_attribution_repair_receipts WHERE proof_sha256=?',(receipt_key,)).fetchone() if prior else None
            if saved:
                receipt = json.loads(saved[0])
                if product_signature(conn,target_ids=targets) != receipt['unchanged_product_signature']:
                    raise ValueError('Candidate changed after the reason repair')
            else:
                if refresh.fingerprint(conn) != old['candidate_audit_baseline']:
                    raise ValueError('Candidate changed since the completed suitability readings')
                rows = [dict(row) for row in conn.execute("SELECT id,vegan_reason,current_on_ocado,vegan_policy_version,vegan_evidence_hash,vegan_audit_id FROM products WHERE vegan_status='vegan' ORDER BY id")]
                if classifier.context_hash(rows) != packet['sampling']['population_sha256']:
                    raise ValueError('Positive sampling population changed')
                validate_frozen_contexts(conn, packet)
                before = product_signature(conn,target_ids=targets)
                run_id = classifier.start_run(conn,mode='user-authorised-attribution-only-repair',model='gpt-6-luna',reasoning_effort='high',selection_scope='all-known')
                applied = []
                for pid in sorted(targets):
                    product = conn.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
                    if (product['vegan_status'],product['vegan_reason']) != ('vegan','name'):
                        raise ValueError('Target changed before repair')
                    item = fresh[pid]; args = {k:v for k,v in item.items() if k in result_fields}
                    args['model_calls'] = tuple(args.get('model_calls',[]))
                    result = classifier.ClassificationResult(**args)
                    if result.validation_error or (result.vegan_status,result.vegan_reason) != ('vegan','manufacturer'):
                        raise ValueError('Fresh selected passes did not validate manufacturer precedence')
                    result.evidence['attribution_repair'] = {'previous_audit_id':product['vegan_audit_id'],'proof_sha256':receipt_key,'suitability_unchanged':True}
                    classifier.write_classification(conn,run_id,result)
                    after = conn.execute('SELECT vegan_status,vegan_reason,vegan_audit_id FROM products WHERE id=?',(pid,)).fetchone()
                    if tuple(after[:2]) != ('vegan','manufacturer'):
                        raise ValueError('Final policy gate rejected attribution correction')
                    applied.append({'product_id':pid,'previous_audit_id':product['vegan_audit_id'],'audit_id':after['vegan_audit_id'],'before':'name','after':'manufacturer'})
                if product_signature(conn,target_ids=targets) != before:
                    raise ValueError('Suitability, evidence, another decision or export membership changed')
                classifier.increment_run(conn,run_id,'total_products',len(targets))
                classifier.increment_run(conn,run_id,'llm_classified',len(targets))
                classifier.finish_run(conn,run_id,'completed')
                receipt = {'state':'passed','mode':MODE,'proof_path':str(proof_path),'proof_sha256':receipt_key,
                    'authorization':proof['authorization'],'changes':applied,'unchanged_product_signature':before,
                    'suitability_changes':0,'evidence_changes':0,'browser_membership_changes':0,
                    'original_failed_score_sha256':sha(root/'positive-score.json'),'retained_artifacts':proof['retained_artifacts'],
                    'code_changes':changes,'run_id':run_id,'note':'600 original blind suitability references retained by explicit user instruction; attribution was repaired separately. This is not a fresh unseen test of reason accuracy.'}
                conn.execute('CREATE TABLE IF NOT EXISTS vegan_attribution_repair_receipts(proof_sha256 TEXT PRIMARY KEY,receipt_json TEXT NOT NULL)')
                conn.execute('INSERT INTO vegan_attribution_repair_receipts VALUES (?,?)',(receipt_key,json.dumps(receipt)))
            validate_frozen_contexts(conn, packet)
            predictions = json.loads(json.dumps(original_predictions))
            for key in case_ids:
                row = conn.execute('SELECT vegan_status,vegan_reason,vegan_audit_id FROM products WHERE id=?',(key.removeprefix('positive-audit.'),)).fetchone()
                predictions['predictions'][key] = {'vegan_status':row[0],'vegan_reason':row[1],'audit_id':row[2]}
            corrected = audit.score(packet,predictions,annotations)
            if corrected['state'] != 'passed': raise ValueError('Strict retained-reference scoring still fails')
            conn.commit()
        except BaseException:
            conn.rollback(); raise
    corrected.update(mode=MODE,attribution_repair=receipt,note=corrected['note']+' Original suitability observations retained after explicitly authorised attribution-only correction; not a fresh unseen reason benchmark.')
    benchmark.save(root/'positive-score.attribution-corrected.json',corrected)
    benchmark.save(root/'positive-audit.attribution-corrected.predictions.json',predictions)
    benchmark.save(root/'attribution-repair.json',receipt)
    shutil.copyfile(proof['checks_path'], root/'checks/checks.json')
    for path, folder in zip(proof['replay_paths'], ('preflight-controls','preflight-pilot')):
        shutil.copyfile(path,root/folder/'report.json')
    with closing(refresh.readonly(db)) as conn: baseline = refresh.fingerprint(conn)
    old['configuration']['code_hashes'] = proof['candidate_hashes']
    old['candidate_audit_baseline'] = baseline
    old['attribution_repair'] = {'receipt':'attribution-repair.json','receipt_sha256':sha(root/'attribution-repair.json'),'previous_manifest_sha256':proof['previous_manifest_sha256']}
    old['accepted_positive_score'] = 'positive-score.attribution-corrected.json'
    # Existing normal artifact resolver now binds both original and corrected records.
    runner = object.__new__(refresh.Refresh); runner.root=root; runner.path=manifest_path; runner.manifest=old
    runner.manufacturer=Path(old.get('immutable_manufacturer_root',root/'manufacturer'))
    for stage in ('checks','preflight','positive_audit'):
        old['stages'][stage] = {'state':'completed','artifacts':runner.artifacts(stage),'mode':MODE}
    old['state']='checkpointed';runner.save()
    return receipt


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(repair(args.root,args.evidence),indent=2))
