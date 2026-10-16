"""Lossless wire references to prevalidated excerpts of complete native fields.

The full original context is still supplied. IDs name literal text, never labels,
source applicability, missing facts or expected decisions. Every hydrated reference
must pass the ordinary exact resolver and assessment validators afterward.
"""
from copy import deepcopy
import hashlib
import json
import re

import vegan_policy as policy

VERSION = 'literal-reference-catalogue-v5'


def chunks(value):
    # Partition the original text once; preserve every character and boundary.
    offset = 0
    while offset < len(value):
        end = min(len(value), offset + 240)
        if end < len(value):
            whitespace = max(value.rfind(' ', offset, end), value.rfind('\n', offset, end))
            if whitespace > offset:
                end = whitespace + 1
        yield value[offset:end]
        offset = end


def fragments(value):
    if not isinstance(value, str):
        yield json.dumps(value, ensure_ascii=True)
        return
    for fragment in chunks(value):
        quote = policy.normalized(fragment)
        if quote:
            yield quote


def build(contexts):
    catalogues = {}
    sequence = 0
    for context in contexts:
        pid = str(context['product']['id'])
        if pid in catalogues:
            raise ValueError('Duplicate product in reference catalogue')
        refs = {}
        def visit(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    visit(child, path + '/' + str(key).replace('~', '~0').replace('/', '~1'))
            elif isinstance(value, list):
                if not value:
                    add(path, '[]')
                for index, child in enumerate(value):
                    visit(child, path + '/' + str(index))
            elif value is not None:
                for quote in fragments(value):
                    add(path, quote)
                if (isinstance(value, str) and policy.normalized(value)
                        and len(value) <= 3000 and not path.endswith('/source_text')
                        and path.startswith(('/manufacturer_vegan_evidence/', '/manufacturer_source_candidates/'))):
                    add(path, value)
        def add(path, quote):
            nonlocal sequence
            ref = {'path': path, 'quote': quote}
            policy.resolve_reference(context, ref)
            if ref in refs.values():
                return
            rid = sequence
            sequence += 1
            if rid in refs and refs[rid] != ref:
                raise ValueError('Reference catalogue ID collision')
            refs[rid] = ref
        for key in ('product','flags','categories','manufacturer_vegan_evidence',
                    'manufacturer_source_candidates','manufacturer_assertion_history'):
            if key in context:
                visit(context[key], '/' + key)
        catalogues[pid] = refs
    return catalogues


def wire_schema(schema, catalogues):
    """Preserve each native reference constraint and bind IDs to its product."""
    output = deepcopy(schema)
    cache = {}
    source_paths = {pid: {ref['path'].rsplit('/', 1)[0] for ref in refs.values() if ref['path'].endswith('/source_text')} for pid, refs in catalogues.items()}
    def intervals(ids):
        runs = []
        for rid in sorted(ids):
            if runs and rid == runs[-1][1] + 1:
                runs[-1][1] = rid
            else:
                runs.append([rid, rid])
        choices = [{"minimum": lo, "maximum": hi} for lo, hi in runs]
        return {"type": "integer", **(choices[0] if len(choices) == 1 else {"anyOf": choices})}
    def walk(node, pid):
        if isinstance(node, dict):
            scoped = node.get('properties', {}).get('source_path', {}).get('enum')
            if scoped is not None and not set(scoped) & source_paths[pid]:
                return None
            if node.get('required') == ['path', 'quote']:
                key = (pid, json.dumps(node, sort_keys=True))
                if key not in cache:
                    allowed = []
                    for rid, ref in catalogues[pid].items():
                        try:
                            policy.validate_schema(ref, node)
                        except ValueError:
                            continue
                        allowed.append(rid)
                    cache[key] = allowed
                if not cache[key]:
                    return None
                return {'type': 'object', 'additionalProperties': False,
                        'required': ['reference_id'],
                        'properties': {'reference_id': intervals(cache[key])}}
            out = {}
            for key, child in node.items():
                if key == 'anyOf':
                    choices = [walk(branch, pid) for branch in child]
                    choices = [branch for branch in choices if branch is not None]
                    if not choices:
                        return None
                    out[key] = choices
                elif key == 'items':
                    value = walk(child, pid)
                    if value is None:
                        out['maxItems'] = 0
                        out[key] = {'type': 'object', 'additionalProperties': False,
                                    'properties': {}, 'required': []}
                    else:
                        out[key] = value
                else:
                    value = walk(child, pid)
                    if value is None:
                        return None
                    out[key] = value
            if node.get('type') == 'array' and node.get('items', {}).get('required') == ['path', 'quote']:
                out['maxItems'] = min(out.get('maxItems', 4), 4)
            return out
        if isinstance(node, list):
            return [walk(child, pid) for child in node]
        return node
    decisions = output.get('properties', {}).get('decisions')
    if decisions:
        original = decisions['items']
        variants = original.get('anyOf', [original])
        alternatives = []
        for pid in catalogues:
            for variant in variants:
                branch = walk(variant, pid)
                if branch is None:
                    continue
                product_id = branch['properties'].setdefault('product_id', {'type': 'string'})
                product_id['enum'] = [pid]
                alternatives.append(branch)
        if not alternatives:
            raise ValueError('No native-constrained product reference schema')
        decisions['items'] = {'anyOf': alternatives}
    else:
        if len(catalogues) != 1:
            raise ValueError('Product-scoped reference transport requires decision items')
        output = walk(output, next(iter(catalogues)))
    return output


def validate_wire_payload(payload, schema):
    """Check numeric wire constraints as well as the native local validator."""
    policy.validate_schema(payload, schema)
    def walk(value, node):
        if node.get('type') == 'integer' and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError('Literal reference ID must be an integer')
        if isinstance(value, (int, float)):
            if 'minimum' in node and value < node['minimum']:
                raise ValueError('Literal reference ID below its permitted range')
            if 'maximum' in node and value > node['maximum']:
                raise ValueError('Literal reference ID above its permitted range')
        if 'anyOf' in node:
            for branch in node['anyOf']:
                try:
                    policy.validate_schema(value, branch)
                    walk(value, branch)
                except ValueError:
                    continue
                return
            raise ValueError('No product-scoped native-permitted wire alternative')
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, node.get('properties', {}).get(key, {}))
        elif isinstance(value, list):
            for child in value:
                walk(child, node.get('items', {}))
    walk(payload, schema)


def hydrate(payload, catalogues, schema):
    validate_wire_payload(payload, schema)
    result = deepcopy(payload)
    records = []
    for decision in result.get('decisions', []):
        pid = str(decision.get('product_id'))
        if pid not in catalogues:
            raise ValueError('Unknown product in catalogue response')
        def walk(node):
            if isinstance(node, dict):
                if set(node) == {'reference_id'}:
                    rid = node['reference_id']
                    if rid not in catalogues[pid]:
                        raise ValueError('Reference belongs to another product or is absent')
                    ref = catalogues[pid][rid]
                    node.clear()
                    node.update(ref)
                    records.append({'product_id':pid,'reference_id':rid,
                                    **ref, 'operation':VERSION})
                else:
                    for child in node.values():
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(decision.get('assessment', {}))
    return result, records


def prompt_transport(contexts, catalogues):
    products = []
    short = {}
    for context in contexts:
        pid = str(context['product']['id'])
        refs = catalogues[pid]
        reverse = {(ref['path'], ref['quote']): rid for rid, ref in refs.items()}
        embedded = set()
        def visit(value, path):
            if isinstance(value, dict):
                return {key: visit(child, path + '/' + str(key).replace('~','~0').replace('/','~1')) for key,child in value.items()}
            if isinstance(value, list):
                return [visit(child, path + '/' + str(index)) for index,child in enumerate(value)]
            if isinstance(value, str) and len(value) > 240:
                segments = []
                for fragment in chunks(value):
                    quote = policy.normalized(fragment)
                    rid = reverse[(path,quote)] if quote else None
                    if rid is not None:
                        embedded.add(rid)
                    segments.append([rid,fragment])
                if ''.join(segment[1] for segment in segments) != value:
                    raise ValueError('Incomplete source transport')
                return {'literal_segments':segments}
            return value
        products.append(visit(context,''))
        short[pid] = {rid: ref for rid,ref in refs.items() if rid not in embedded}
    return {'catalogue_provenance':policy.CATALOGUE_PROVENANCE,'products':products,'short_field_reference_catalogue':short}


def restore_transport(value):
    if isinstance(value,dict):
        if set(value)=={'literal_segments'}:
            return ''.join(segment[1] for segment in value['literal_segments'])
        return {key:restore_transport(child) for key,child in value.items()}
    if isinstance(value,list):
        return [restore_transport(child) for child in value]
    return value


def compact_schema(schema):
    # Repeated identical wire-reference alternatives add no constraint.
    if isinstance(schema,list):
        return [compact_schema(child) for child in schema]
    if not isinstance(schema,dict):
        return schema
    out={key:compact_schema(child) for key,child in schema.items()}
    if 'anyOf' in out:
        unique=[];seen=set()
        for branch in out['anyOf']:
            key=json.dumps(branch,sort_keys=True)
            if key not in seen:
                seen.add(key);unique.append(branch)
        out['anyOf']=unique
        if len(unique)==1 and len(out)==1:
            return unique[0]
    return out


def make_prompt(base, contexts, catalogues):
    prefix = base.rsplit('Product JSON:\n',1)[0]
    instruction = '\nLiteral reference transport version 5: the complete product fields follow. Long text values use literal_segments, ordered [reference_id, unmodified_text] pairs. Concatenating the text reproduces that original field exactly, with every character retained. Read all segments, including qualifications, across boundaries. There is no separate shared_source_texts table in this transport. Short field references are supplied separately. Return {"reference_id":0} in every evidence_refs/resolution_refs item, selecting only an ID belonging to this product and the required native field. IDs represent literal excerpts, never claims, expected labels, missing facts or previous conclusions. Do not edit quotes or invent facts. Local validation restores the exact original path/excerpt and checks the full unmodified native evidence.\nProduct JSON:\n'
    return prefix + instruction + json.dumps(prompt_transport(contexts,catalogues),ensure_ascii=False,separators=(',',':'))


def schema_property_count(schema):
    """Count service schema parameters before attempting an included-plan call."""
    if isinstance(schema, list):
        return sum(schema_property_count(child) for child in schema)
    if not isinstance(schema, dict):
        return 0
    return len(schema.get('properties', {})) + sum(schema_property_count(child) for child in schema.values())


NATIVE_WIRE_VERSION = "native-reference-schema-v1"


def native_wire_schema(schema):
    """Factor reference path alternatives; keep all exact constraints local."""
    if isinstance(schema, list):
        return [native_wire_schema(child) for child in schema]
    if not isinstance(schema, dict):
        return schema
    options = schema.get('anyOf')
    if set(schema) == {'anyOf'} and options and all(
            isinstance(item, dict) and item.get('required') == ['path', 'quote']
            and set(item.get('properties', {})) == {'path', 'quote'}
            and item['properties']['quote'].get('type') == 'string'
            for item in options):
        return {'type':'object', 'additionalProperties':False,
                'required':['path','quote'], 'properties':{
                    'path':{'anyOf':[deepcopy(item['properties']['path']) for item in options]},
                    'quote':{'type':'string'}}}
    return {key:native_wire_schema(child) for key,child in schema.items()}


NATIVE_PATH_VERSION = 'native-product-fields-v1'

def constrain_product_fields(schema, contexts):
    """Mask fabricated product-field pointers without altering native validation."""
    names=sorted({str(key).replace('~','~0').replace('/','~1') for context in contexts for key in context['product']})
    if not names:raise ValueError('Product fields are absent')
    alternatives='(?:'+'|'.join(re.escape(key) for key in names)+')'
    result=deepcopy(schema)
    def visit(node):
        if isinstance(node,dict):
            if node.get('required')==['path','quote']:
                path=node['properties']['path']
                if 'pattern' in path:
                    path['pattern']=path['pattern'].replace('product(?:/[^/]+)?','product(?:/'+alternatives+')?').replace('/product/[^/]+','/product/'+alternatives)
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(result)
    return result
