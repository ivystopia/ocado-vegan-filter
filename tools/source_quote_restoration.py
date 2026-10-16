"""Restore unique source literals after reversible response-transport changes.

No fuzzy matching, substantive punctuation/word/number changes, different fields
or source selection. The original strict resolver must validate every restored reference.
"""
from copy import deepcopy
import json
import vegan_policy as policy

RESTORATION_VERSION = "literal-transport-v2"

ASCII_FOLD = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')


def restore_ascii(context, ref):
    try:
        policy.resolve_reference(context, ref)
        return ref, None
    except ValueError as original:
        value = context
        try:
            for part in ref['path'][1:].split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise original
        if not isinstance(value, str) or not isinstance(ref.get('quote'), str):
            raise original
        quote = policy.normalized(ref['quote'])
        if not quote:
            raise original
        views = [policy.normalized(value)]
        if ref['path'].endswith('/source_text'):
            views.append(policy.visible_source_text(value))
        matches = set()
        for view in views:
            folded, needle = view.translate(ASCII_FOLD), quote.translate(ASCII_FOLD)
            offset = 0
            while (at := folded.find(needle, offset)) >= 0:
                matches.add(view[at:at+len(quote)])
                offset = at+1
        if len(matches) != 1:
            raise original
        actual = next(iter(matches))
        corrected = {**ref, 'quote':actual}
        policy.resolve_reference(context, corrected)
        return corrected, {'path':ref['path'], 'returned_quote':ref['quote'],
            'source_quote':actual, 'source_value_hash':policy.digest(value),
            'operation':'restore_unique_literal_ASCII_case_only'}



def restore(context, ref):
    try:
        return restore_ascii(context, ref)
    except ValueError as original:
        failure = original
    quote = ref.get('quote')
    if not isinstance(quote, str):
        raise failure
    variants = []
    decoded = quote
    for depth in range(1, 3):
        try:
            decoded = json.loads('"' + decoded + '"')
        except json.JSONDecodeError:
            break
        if not isinstance(decoded, str):
            break
        if decoded != quote:
            variants.append((decoded, 'decode_JSON_string_escaping', depth))
    originals = [(quote, 'literal', 0)] + list(variants)
    for value, operation, depth in originals:
        for encoding in ('latin1', 'cp1252'):
            try:
                transported = value.encode('utf-8').decode(encoding)
            except UnicodeError:
                continue
            if transported != value and transported.encode(encoding).decode('utf-8') == value:
                variants.append((transported, 'restore_reversible_UTF8_' + encoding, depth))
    matches = {}
    for value, operation, depth in variants:
        try:
            fixed, case_record = restore_ascii(context, {**ref, 'quote': value})
        except ValueError:
            continue
        matches.setdefault(fixed['quote'], (fixed, case_record, operation, depth))
    if len(matches) != 1:
        raise failure
    fixed, case_record, operation, depth = next(iter(matches.values()))
    policy.resolve_reference(context, fixed)
    value = context
    for part in ref['path'][1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return fixed, {'path': ref['path'], 'returned_quote': quote,
        'source_quote': fixed['quote'], 'operation': operation,
        'JSON_depth': depth, 'ASCII_case_record': case_record,
        'restoration_version': RESTORATION_VERSION,
        'source_value_hash': policy.digest(value)}


def canonicalize(payload, contexts):
    result = deepcopy(payload); repairs = []
    by_id = {str(c['product']['id']):c for c in contexts}
    def walk(value, context, pid):
        if isinstance(value, dict):
            if set(value) == {'path','quote'}:
                corrected, repair = restore(context, value)
                if repair:
                    value['quote'] = corrected['quote']
                    repairs.append({'product_id':pid, **repair})
            else:
                for child in value.values():walk(child, context, pid)
        elif isinstance(value,list):
            for child in value:walk(child, context, pid)
    for decision in result.get('decisions', []):
        pid = str(decision.get('product_id'))
        if pid in by_id:walk(decision.get('assessment',{}), by_id[pid], pid)
    return result, repairs
