#!/usr/bin/env python3
"""Freeze source/family-disjoint reference cases plus explicit synthetic contrasts."""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import random
import re

from benchmark_whole_product import FIXTURE, load_fixture, save
import classify_ocado_vegan as classifier
import vegan_policy as policy


def synthetic_cases():
    """Use the current frozen controlled contracts, with independent copies."""
    return copy.deepcopy([case for case in load_fixture(FIXTURE)['cases'] if case['split']=='synthetic'])


def liquid_processing_cases():
    """Source-backed processing contrasts; these are policy contracts, not SKUs."""
    cases=[]
    products=[('Cider','Apples, Water'),('Cider Vinegar','Apples, Water'),
              ('Beer','Water, Malted barley, Hops'),('Apple Juice','Apples, Water'),
              ('Maple Syrup','Maple sap')]
    for group,(name,ingredients) in enumerate(products):
        for variant in range(7):
            product={'id':str(907000000+group*7+variant),'name':'Example '+name,'ingredients':ingredients}
            if variant==1:product['official_vegan']=1
            elif variant==2:product['dietary_information']='Suitable for vegans.'
            elif variant==3:product['name']='Vegan Example '+name
            elif variant==4:
                product.update(official_vegan=1,features='Gelatine is used during manufacture.')
            elif variant==5:
                product['features']='All production stages including ingredient and precursor production are animal-free.'
            elif variant==6:
                product['features']='Another variant is suitable for vegans.'
                if group!=4:product['features']='Unfiltered final product. '+product['features']
            status='unknown' if variant in (0,6) else 'nonvegan' if variant==4 else 'vegan'
            reason={1:'tagged',2:'manufacturer',3:'name',5:'ingredients'}.get(variant)
            value=classifier.product_context_for_llm({'product':product,'flags':[],
                'categories':[],'manufacturer_vegan_evidence':[]})
            cases.append({'case_id':f'synthetic.liquid-processing.{group}.{variant}',
                'case_kind':'synthetic','split':'synthetic','leakage_group':f'synthetic.liquid-processing.{group}',
                'classifier_input':value,'input_sha256':classifier.context_hash(value),
                'expected':{'vegan_status':status,'vegan_reason':reason,
                    'required_issue_codes':['processing_unresolved'] if status=='unknown' else []},
                'adjudication':{'state':'accepted','policy_version':policy.POLICY_VERSION,
                    'reference_type':'explicit-policy-contract',
                    'mechanism_source_url':'https://vegan.org/vegan-faq/clarifying-agents',
                    'rationale':'Animal clarification/defoaming inputs can be absent from ingredients. A complete applicable claim or whole-chain animal-free facts resolve the generic risk; wrong-variant claims and final filtering descriptions do not. Definite animal use overrides a tag.'}})
    for variant in range(3):
        product={'id':str(907000100+variant),'name':'Example Dressing','ingredients':'Water, Cider vinegar'}
        if variant==1:product['name']='Vegan Example Dressing'
        elif variant==2:product['features']='All production stages are animal-free.'
        value=classifier.product_context_for_llm({'product':product,'flags':[],
            'categories':[],'manufacturer_vegan_evidence':[]})
        cases.append({'case_id':f'synthetic.liquid-constituent.{variant}',
            'case_kind':'synthetic','split':'synthetic','leakage_group':'synthetic.liquid-constituent',
            'classifier_input':value,'input_sha256':classifier.context_hash(value),
            'expected':{'vegan_status':'vegan' if variant==1 else 'unknown',
                'vegan_reason':'name' if variant==1 else None,
                'required_issue_codes':[] if variant==1 else ['processing_unresolved']},
            'adjudication':{'state':'accepted','policy_version':policy.POLICY_VERSION,
                'reference_type':'explicit-policy-contract',
                'mechanism_source_url':'https://vegan.org/vegan-faq/clarifying-agents',
                'rationale':'Constituent vinegar carries the same mechanism. A final-product factory assurance does not establish precursor processing; a whole-product vegan assertion may resolve the generic uncertainty.'}})
    return cases


def planting_seed_cases():
    """Planting treatment contrasts, separate from ordinary edible seed food."""
    cases=[]
    for variant in range(9):
        product={'id':str(908000000+variant),'name':'Example Wildflower Seeds',
                 'preparation_and_usage':'Sow outdoors in prepared soil.'}
        if variant==1:product['official_vegan']=1
        elif variant==2:product['dietary_information']='Suitable for vegans.'
        elif variant==3:product['name']='Example Vegan Wildflower Seeds'
        elif variant==4:product.update(ingredients='Seeds, Gelatine',official_vegan=1)
        elif variant==5:product['features']='Untreated and uncoated seeds.'
        elif variant==6:product['features']='Native-grown organic seeds.'
        elif variant==7:product.update(name='Example Edible Pumpkin Seeds',ingredients='Pumpkin Seeds',
                                     preparation_and_usage='Sprinkle over salad.')
        elif variant==8:product['features']='Not all seed treatments are animal-free.'
        status='unknown' if variant in (0,6,8) else 'nonvegan' if variant==4 else 'vegan'
        reason={1:'tagged',2:'manufacturer',3:'name',5:'ingredients',7:'ingredients'}.get(variant)
        value=classifier.product_context_for_llm({'product':product,'flags':[],
            'categories':[],'manufacturer_vegan_evidence':[]})
        cases.append({'case_id':f'synthetic.planting-seeds.{variant}','case_kind':'synthetic','split':'synthetic',
            'leakage_group':'synthetic.planting-seeds','classifier_input':value,'input_sha256':classifier.context_hash(value),
            'expected':{'vegan_status':status,'vegan_reason':reason,
                        'required_issue_codes':['processing_unresolved'] if status=='unknown' else []},
            'adjudication':{'state':'accepted','policy_version':policy.POLICY_VERSION,
                'reference_type':'explicit-policy-contract',
                'mechanism_source_url':'https://patents.google.com/patent/US20180303023A1/en',
                'rationale':'Planting seed identity does not resolve treatment/coating/binder uncertainty. Untreated/uncoated facts or an applicable whole-product claim can resolve it; native/organic origin and qualified absence do not. Actual animal content overrides claims; edible food seeds do not inherit planting mechanisms.'}})
    return cases


def groups(cases):
    parent={case['case_id']:case['case_id'] for case in cases}
    def root(key):
        while parent[key]!=key:parent[key]=parent[parent[key]];key=parent[key]
        return key
    seen={}
    for case in cases:
        keys=['family:'+case['family_key']]+['source:'+source['url'] for source in case.get('external_sources',[])]
        for key in keys:
            if key in seen:parent[root(case['case_id'])]=root(seen[key])
            else:seen[key]=case['case_id']
    result={}
    for case in cases:result.setdefault(root(case['case_id']),[]).append(case)
    return result




def assemble(annotation_path,output):
    packet=json.loads(annotation_path.read_text())
    accepted=[case for case in packet['candidates'] if case['adjudication']['state']=='accepted']
    clustered=groups(accepted)
    keys=sorted(clustered);random.Random(20261006).shuffle(keys)
    # Split whole families/shared sources. Every accepted product is retained;
    # its evidence reason has no effect on admission or split allocation.
    boundary=max(1,round(len(keys)*.4)) if keys else 0
    development_groups=set(keys[:boundary])
    cases=[]
    for key in keys:
        split='development' if key in development_groups else 'locked'
        for case in clustered[key]:
            source_metadata=[{field:value for field,value in source.items() if field!='source_text'} for source in case['external_sources']]
            cases.append({**{field:value for field,value in case.items() if field not in {'external_sources','family_key','source_group_keys'}},
                'split':split,'leakage_group':key,'source_metadata':source_metadata})
    pilot=[case['case_id'] for case in cases if case['split']=='development'][:96]
    available=Counter(case['expected']['vegan_reason'] or case['expected']['vegan_status'] for case in accepted)
    cases+=synthetic_cases()+packet.get('workload',[])
    report={'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':cases,
        'selection':{'annotation_hash':classifier.context_hash(packet),'accepted_candidates':len(accepted),
            'disputed_cases':packet.get('disputed_cases',[]),'available_reference_classes':dict(available),
            'counts':dict(Counter(case['split'] for case in cases)),'pilot_case_ids':pilot,
            'note':'Every accepted product retained. Family/source-disjoint splits without category admission targets. Automated evidence readings are not independent physical truth.'}}
    save(output,report)
    return report['selection']


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--annotations',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--synthetic-only',action='store_true')
    args=p.parse_args()
    if args.synthetic_only:save(args.output,{'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':synthetic_cases()})
    else:print(json.dumps(assemble(args.annotations,args.output),indent=2))
