#!/usr/bin/env python3
"""Reapply a repaired policy to accepted decisions without repeating model calls.

Use only an isolated candidate. Previous audit rows and model-call linkage stay
intact; a replay is explicitly labelled as such, never as a fresh model reading.
"""
from dataclasses import replace
from collections import Counter
import json

import classify_ocado_vegan as classifier


def unreviewed_native_manufacturer_claim(context, result, audit):
    """A changed policy cannot invent an uncited higher-priority interpretation."""
    policy = classifier.policy
    if (audit['knowledge_version'] == policy.KNOWLEDGE_VERSION
            or result.vegan_status != 'vegan' or result.vegan_reason not in {'name', 'ingredients'}):
        return False
    safe = classifier.product_context_for_llm(context)
    fields = ('dietary_information', 'features', 'detailed_description', 'further_description',
              'manufacturer_marketing', 'brand_marketing', 'other_information', 'specification',
              'vinification_details')
    candidates = [(f'/product/{field}', safe['product'].get(field)) for field in fields]
    candidates.extend((f'/manufacturer_vegan_evidence/{i}/content', row.get('content'))
        for i, row in enumerate(safe.get('manufacturer_vegan_evidence', []))
        if row.get('source_kind') != 'manufacturer_website')
    for path, value in candidates:
        if value and 'vegan' in str(value).casefold():
            ref = policy.reference(path, str(value))
            if policy.manufacturer_reference_claim(safe, ref):
                return True
    return False


def replay(context, audit, model_calls=()):
    """Reject stale/failed evidence and preserve the agreed-unknown contract."""
    product = context['product']
    pid = str(product['id'])
    if audit is None or str(audit['product_id']) != pid or audit['validation_error']:
        raise ValueError('Replay requires an accepted matching audit')
    if (audit['vegan_status'], audit['vegan_reason']) != (product['vegan_status'], product['vegan_reason']):
        raise ValueError('Canonical decision differs from its audit')
    current_hash = classifier.context_hash(classifier.product_context_for_llm(context))
    if product['vegan_evidence_hash'] != current_hash:
        raise ValueError('Catalogue evidence changed since the accepted decision')
    result = classifier.ClassificationResult(
        product_id=pid, vegan_status=audit['vegan_status'], vegan_reason=audit['vegan_reason'],
        confidence=audit['confidence'], summary=audit['summary'],
        evidence=json.loads(audit['evidence_json']), source=audit['source'],
        model=audit['model'], reasoning_effort=audit['reasoning_effort'],
        prompt_version=audit['prompt_version'], context_hash=audit['context_hash'],
        raw_response=audit['raw_response'], parsed_response_json=audit['parsed_response_json'],
        validation_error=None, exit_status=audit['exit_status'], model_calls=tuple(model_calls))
    # Recover only previously agreed positive readings withheld by mechanical
    # copy checks. The current gate must still validate every material issue.
    readings = result.evidence.get('independent_passes', [])
    if (result.vegan_status == 'unknown' and len(readings) == 2
            and all(row.get('vegan_status') == 'vegan' and row.get('confidence') == 'certain' for row in readings)
            and readings[0].get('vegan_reason') == readings[1].get('vegan_reason')
            and any(issue.get('rule_id') in classifier.policy.COPY_OBSERVATION_RULES
                    for issue in result.evidence.get('assessment', {}).get('material_issues', []))):
        safe = classifier.product_context_for_llm(context)
        for row in readings:
            classifier.validate_codex_payload({'decisions':[row]}, {pid})
            classifier.policy.validate_assessment(row['assessment'], safe)
        merged = classifier.merge_pass_decisions([{pid:row} for row in readings], pid)
        result = replace(result, vegan_status='vegan', vegan_reason=merged['vegan_reason'], confidence='certain',
            evidence={**result.evidence, 'assessment':merged['assessment'], 'retained_gate_assessment':result.evidence.get('assessment'),
                      'replayed_agreed_positive_materiality':True})
    # The earlier gate may have stored unknown after two validated negative
    # readings. Re-evaluate their original assessment, not the gate-generated
    # conflicts. This cannot promote an agreed unknown or introduce a positive.
    readings = result.evidence.get('independent_passes', [])
    if (result.vegan_status == 'unknown' and len(readings) == 2
            and all(row.get('vegan_status') == 'nonvegan' and row.get('vegan_reason') is None
                    and row.get('confidence') == 'certain' for row in readings)):
        safe_context = classifier.product_context_for_llm(context)
        for row in readings:
            classifier.validate_codex_payload({'decisions': [row]}, {pid})
            classifier.policy.validate_assessment(row['assessment'], safe_context)
        merged = classifier.merge_pass_decisions([{pid: row} for row in readings], pid)
        result = replace(result, vegan_status='nonvegan', vegan_reason=None,
            confidence=merged['confidence'], summary=merged['summary'],
            evidence={**result.evidence, 'assessment': merged['assessment'],
                'retained_gate_assessment': result.evidence.get('assessment'),
                'replayed_agreed_negative': True})
    rules = classifier.classify_by_rules(context)
    if audit['source'] == 'rule' and rules is None:
        # A withdrawn shortcut has no independent model interpretation to
        # reuse. Its generated assessment cannot justify its own conclusion.
        # Leave this product as work for the normal two-pass classifier.
        return None
    # Conclusive animal proof wins. A previously agreed unknown cannot be
    # promoted merely by replaying the same evidence through a positive rule.
    if rules and (rules.vegan_status == 'nonvegan' or product['vegan_status'] != 'unknown'):
        result = rules
    else:
        result = classifier.apply_policy_gate(context, result)
    if unreviewed_native_manufacturer_claim(context, result, audit):
        return None  # Fresh normal passes must interpret the previously uncited claim.
    return replace(result, evidence={**result.evidence, 'policy_replay': {
        'previous_audit_id': audit['id'], 'previous_classifier_version': audit['classifier_version'],
        'previous_knowledge_version': audit['knowledge_version'],
        'previous_prompt_version': audit['prompt_version'],
        'native_context_hash': current_hash, 'new_model_calls': 0}})


def replay_unknown_metadata(conn, *, sync_run_id):
    """Repair only affected unknowns; preserve every other completed decision."""
    if not conn.in_transaction:
        raise ValueError('Metadata repair requires a caller-owned write transaction')
    ids = [str(row[0]) for row in conn.execute("""
        SELECT p.id FROM products p
        JOIN product_vegan_classification_audit a ON a.id=p.vegan_audit_id
        WHERE p.vegan_status='unknown' AND coalesce(p.vegan_positive_block,0)=0
          AND a.source IN ('codex','codex_arbitration')
          AND (p.official_vegan=1 OR instr(a.evidence_json,'independent_codex_disagreement')>0)
        ORDER BY p.id
    """)]
    run_id = classifier.start_run(conn, mode='targeted_unknown_metadata_repair',
        model=None, reasoning_effort=None, sync_run_id=sync_run_id, selection_scope='all-known')
    changed = []
    for offset in range(0, len(ids), 250):
        for context in classifier.load_product_contexts(conn, ids[offset:offset + 250]):
            product = context['product']
            audit = conn.execute('SELECT * FROM product_vegan_classification_audit WHERE id=?',
                                 (product['vegan_audit_id'],)).fetchone()
            result = replay(context, audit)
            if result is None or result.vegan_status == 'vegan':
                raise ValueError('Metadata repair cannot requeue or promote a product')
            if result.vegan_status == 'unknown' and not result.evidence.get('positive_block'):
                continue
            classifier.write_classification(conn, run_id, result)
            new_id = conn.execute('SELECT vegan_audit_id FROM products WHERE id=?',
                                  (product['id'],)).fetchone()[0]
            conn.execute('INSERT OR IGNORE INTO vegan_audit_model_calls '
                         'SELECT ?,call_id FROM vegan_audit_model_calls WHERE audit_id=?',
                         (new_id, audit['id']))
            changed.append({'product_id':product['id'], 'previous_audit_id':audit['id'],
                'new_audit_id':new_id, 'before_status':'unknown', 'after_status':result.vegan_status,
                'before_block':False, 'after_block':bool(result.evidence.get('positive_block'))})
    classifier.increment_run(conn, run_id, 'total_products', len(ids))
    classifier.finish_run(conn, run_id, 'completed')
    return {'mode':'targeted-unknown-metadata-repair', 'run_id':run_id, 'checked':len(ids),
            'changed_products':changed, 'decision_writes':len(changed), 'new_model_calls':0,
            'requeued_products':[], 'unchanged_decisions_preserved':True}


def replay_candidate(conn, *, sync_run_id):
    """Transactionally replay accepted rows in an isolated, quiescent copy.

    The caller owns snapshot/provenance validation and the transaction. This
    function never commits, admits model calls or changes an unclassified row.
    """
    if not conn.in_transaction:
        raise ValueError('Policy replay requires a caller-owned write transaction')
    ids = [str(row[0]) for row in conn.execute(
        'SELECT id FROM products WHERE vegan_status IS NOT NULL ORDER BY id')]
    run_id = classifier.start_run(conn, mode='accepted_evidence_policy_replay',
        model=None, reasoning_effort=None, sync_run_id=sync_run_id, selection_scope='all-known')
    transitions = Counter(); current_transitions = Counter(); changed = []; requeued = []; completed = 0
    decision_writes = preserved_nonpositive = preserved_positive = 0
    for offset in range(0, len(ids), 250):
        for context in classifier.load_product_contexts(conn, ids[offset:offset + 250]):
            product = context['product']
            audit = conn.execute('SELECT * FROM product_vegan_classification_audit WHERE id=?',
                (product['vegan_audit_id'],)).fetchone()
            result = replay(context, audit)
            if result is None:
                assignments = ','.join(name+'=NULL' for name in classifier.CLASSIFICATION_PRODUCT_COLUMNS
                                       if name != 'vegan_positive_block')
                conn.execute('UPDATE products SET '+assignments+',vegan_positive_block=0 WHERE id=?', (product['id'],))
                requeued.append({'product_id':product['id'],'previous_audit_id':audit['id'],
                    'native_context_hash':product['vegan_evidence_hash'],
                    'reason':'changed_policy_requires_independent_assessment',
                    'previous_source':audit['source'],'previous_reason':audit['vegan_reason']})
                status, reason, summary = None, None, 'Changed policy; normal independent assessment required'
                written = False
            else:
                status, reason, summary = result.vegan_status, result.vegan_reason, result.summary
                unchanged_decision = ((status, reason) == (product['vegan_status'], product['vegan_reason'])
                    and product['vegan_policy_version'] == classifier.policy.POLICY_VERSION
                    and audit['knowledge_version'] == classifier.policy.KNOWLEDGE_VERSION
                    and audit['schema_version'] == classifier.policy.SCHEMA_VERSION
                    and bool(product['vegan_positive_block']) == bool(result.evidence.get('positive_block')))
                written = not unchanged_decision
                if written:
                    classifier.write_classification(conn, run_id, result)
                    decision_writes += 1
                else:
                    if status == 'vegan':preserved_positive += 1
                    else:preserved_nonpositive += 1
            # The original successful model calls already exist in this copy.
            # Retain their IDs rather than duplicating responses or suggesting
            # a new reading happened. Rule-derived decisions use native proof.
            if written and result.source == audit['source'] and result.model:
                new_id = conn.execute('SELECT vegan_audit_id FROM products WHERE id=?',
                    (product['id'],)).fetchone()[0]
                conn.execute('INSERT OR IGNORE INTO vegan_audit_model_calls '
                    'SELECT ?,call_id FROM vegan_audit_model_calls WHERE audit_id=?',
                    (new_id, audit['id']))
            transition = f"{product['vegan_status']}/{product['vegan_reason']} -> {status}/{reason}"
            transitions[transition] += 1
            if product['current_on_ocado'] == 1:current_transitions[transition] += 1
            if (product['vegan_status'], product['vegan_reason']) != (status, reason):
                changed.append({'product_id': product['id'], 'name': product['name'],
                    'current_on_ocado': product['current_on_ocado'], 'previous_audit_id': audit['id'],
                    'before': [product['vegan_status'], product['vegan_reason']],
                    'after': [status, reason], 'summary': summary})
            completed += 1
        print(f'Accepted-evidence policy replay: {completed}/{len(ids)}', flush=True)
    classifier.increment_run(conn, run_id, 'total_products', completed)
    classifier.finish_run(conn, run_id, 'completed')
    return {'run_id': run_id, 'replayed': completed, 'new_model_calls': 0,
        'decision_writes':decision_writes, 'unchanged_nonpositive_decisions_preserved':preserved_nonpositive,
        'unchanged_positive_decisions_preserved':preserved_positive,
        'transitions_all_known': dict(transitions), 'transitions_current': dict(current_transitions),
        'changed_products': changed, 'requeued_products': requeued}
