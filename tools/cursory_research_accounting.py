"""Validate bounded manufacturer checks without approving suitability assertions."""
from pathlib import Path
from urllib.parse import urlsplit
import hashlib, json

MODE = 'cursory-official-sources-v1'

def retained_lookup(row, root):
    """A failed search-service call is not a completed manufacturer lookup."""
    path_value = row.get('retained_search_result')
    if not isinstance(path_value, str):return False
    path = (Path(root) / path_value).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():return False
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row.get('retained_search_sha256'):return False
    try:trace = json.loads(raw)
    except (TypeError, ValueError):return False
    queries = trace.get('queries', [])
    results = trace.get('results', [])
    requested = row.get('queries')
    if not isinstance(requested, list) or not requested:return False
    for q in requested:
        indexes = [i for i, value in enumerate(queries)
            if (value if isinstance(value, str) else value.get('q') if isinstance(value, dict) else None) == q]
        if not indexes:return False
        successful = False
        for index in indexes:
            # Older retained web calls contain one response to a declared
            # multi-query request. Preserve that actual joint-response format.
            if 'result' in trace and 'results' not in trace:
                result = trace['result']
            else:
                if index >= len(results):continue
                result = results[index]
            if isinstance(result, dict) and 'status' in result:
                if result.get('status') != 'fulfilled' or 'value' not in result:continue
                result = result['value']
            if isinstance(result, dict) and 'query' in result:
                declared = result['query']
                if isinstance(declared, dict) and declared.get('q') != q:continue
                if isinstance(declared, str) and declared != q:continue
            if isinstance(result, dict) and 'result' in result:result = result['result']
            if isinstance(result, dict) and (result.get('error') or result.get('isError')):continue
            if isinstance(result, str) and (not result.strip() or result.lstrip().startswith('Fatal error:')):continue
            if result is None or isinstance(result, bool):continue
            successful = True
        if not successful:return False
    return True

def retained_sources(ids, bundle):
    """Checked sources and applicable sources both require immutable provenance."""
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):return False
    for sid in ids:
        source = bundle.get('sources',{}).get(sid)
        if not isinstance(source,dict):return False
        text = source.get('source_text')
        if not isinstance(text,str) or not text.strip():return False
        if len(text)>120_000:return False
        if hashlib.sha256(text.encode()).hexdigest()!=source.get('content_sha256'):return False
        if not all(isinstance(source.get(key),str) and source[key].strip()
            for key in ('url','title','retrieved_at','official_identity','market_scope')):return False
        url=urlsplit(source['url'])
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password:return False
    return True

def collected_source_check(row, bundle):
    """Collection permits normal review, never a suitability assertion."""
    if row.get('outcome') != 'source_collected' or row.get('research_complete') is not True:return False
    if row.get('positive_claim_approved') is not False or row.get('classification_review_required') is not True:return False
    if not isinstance(row.get('notes'),str) or not row['notes'].strip():return False
    return retained_sources(row.get('source_ids'), bundle)

def closed_access_check(row, root):
    if row.get('outcome')!='blocked' or row.get('research_complete') is not True or row.get('check_closed') is not True:
        return False
    if not isinstance(row.get('closure_reason'),str) or not row['closure_reason'].strip():
        return False
    attempts=row.get('access_attempts')
    if not isinstance(attempts,list) or not attempts:
        return False
    for attempt in attempts:
        if not isinstance(attempt,dict):return False
        url=attempt.get('url');parsed=urlsplit(url) if isinstance(url,str) else None
        if parsed is None or parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
            return False
        rel=attempt.get('metadata_path')
        if not isinstance(rel,str) or not rel:return False
        path=(Path(root)/rel).resolve()
        if not path.is_relative_to(Path(root).resolve()) or not path.is_file():return False
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=attempt.get('metadata_sha256'):return False
        try:meta=json.loads(raw)
        except (TypeError,ValueError):return False
        if meta.get('url')!=url or not meta.get('retrieved_at'):return False
        # Only a retained unsuccessful attempt can close a blocked check.
        # Readable pages and snippets need a real scope review instead.
        if meta.get('state') not in {'fetch_failed','rendered_fetch_failed','browser_access_review_needed','rate_limit_deferred'}:
            return False
        if not (meta.get('error') or meta.get('http_status') in {401,403,404,410,429,500,502,503,504}):
            return False
    return True

def status(bundle, root, policy):
    authorized=(policy.get('mode')==MODE and isinstance(policy.get('authorization'),str)
        and bool(policy['authorization'].strip()) and policy.get('positive_validation_unchanged') is True)
    completed=[];closed=[];collected=[];pending=[]
    for pid in bundle['products']:
        row=bundle['outcomes'].get(pid,{})
        if authorized and row.get('outcome') == 'no_confirmation' and row.get('source_ids'):
            pending.append(pid)
            continue
        if row.get('research_complete') is True and row.get('outcome') in {'confirmed_vegan','no_confirmation'}:
            # Existing source/search-attempt validation remains required.
            if authorized:
                source_refs = row.get('source_ids') if row.get('outcome') == 'confirmed_vegan' else row.get('checked_source_ids')
                supported = retained_sources(source_refs,bundle) or retained_lookup(row,root)
            else:
                supported = bool(row.get('source_ids') or row.get('queries'))
            if supported:completed.append(pid)
            else:pending.append(pid)
        elif authorized and closed_access_check(row,root):closed.append(pid)
        elif authorized and collected_source_check(row,bundle):collected.append(pid)
        else:pending.append(pid)
    return {'mode':policy.get('mode'),'complete_products':len(completed),'closed_access_blocked_products':len(closed),
        'collected_source_products':len(collected),'classification_review_required_ids':collected,
        'accounted_products':len(completed)+len(closed)+len(collected),'pending_products':len(pending),
        'pending_product_ids':pending,'closed_access_blocked_ids':closed,
        'can_continue':authorized and not bundle.get('errors') and not pending}
