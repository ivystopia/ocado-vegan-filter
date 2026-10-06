#!/usr/bin/env python3
"""Freeze source/family-disjoint reference cases plus explicit synthetic contrasts."""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import random
import re

from benchmark_whole_product import save
import classify_ocado_vegan as classifier
import vegan_policy as policy


def synthetic_cases():
    # Expectations state the independent policy contract, not rule outputs.
    patterns=[
        ('Wine',None,'unknown',False),('White Wine','Grapes, Water','unknown',False),
        ('Champagne',None,'unknown',False),('Refined Sugar','Sugar','unknown',False),
        ('Example Bread','Wheat flour, E471','unknown',False),('Example Snack','Water, E 422','unknown',False),
        ('Example Snack','Water, Lecithin','unknown',False),('Example Snack','Water, E322','unknown',False),
        ('Vitamin Capsules','Vitamin D3','unknown',False),('Vitamin Capsules','Vitamins','unknown',False),
        ('Cake Mix','Wheat flour, Raising agent','unknown',False),('Fruit Product','Fruit, Firming agent','unknown',False),
        ('Paper plates','Paper','unknown',False),('Cotton pads','Cotton','unknown',False),
        ('Simple Leaf Salad','Lettuce, Spinach','vegan',False),('Carrots',None,'vegan',False),
        ('Plain Rice','Rice','vegan',False),('Plain Pasta','Durum wheat semolina','vegan',False),
        ('Example Capsules','Gelatine','nonvegan',True),('Example Food','Milk','nonvegan',True),
        ('Example Food','Egg','nonvegan',True),('Example Food','Honey','nonvegan',True),
        ('Example Food','E120','nonvegan',True),('Example Food','E 901','nonvegan',True),
        ('Example Food','E904','nonvegan',True),('Example Supplement','Marine Collagen (Fish)','nonvegan',True),
        ('Ground Cumin','Pork fat, pork meat','unknown',True),('Carrots','Beef, water','unknown',True),
        ('Example Cosmetics','Lanolin','nonvegan',True),('Example Food','Beeswax','nonvegan',True),
        ('Not Vegan Wine',None,'unknown',True),('Chicken with vegan dressing',None,'unknown',True),
        ('Vegan leather boots',None,'unknown',True),('Recipe book for vegans',None,'unknown',True),
        ('Unwaxed Lemons',None,'vegan',False),('Lemons',None,'unknown',False),
        ('Butter Gold Bakers',None,'unknown',False),('Plain beans','Beans. May contain milk.','vegan',False),
        ('Example Food','Water, Soy Lecithin','vegan',False),('Fortified flour','Wheat Flour (Calcium Carbonate, Iron, Niacin, Thiamin)','vegan',False),
        ('Body Cream','Butyrospermum Parkii (Shea) Butter','unknown',False),
        ('Soya dessert','Ice-Cream (55%): Soy Extract, Water, Sugar','unknown',False),
        ('Example Cultured Food','Yogurt Cultures (S. Thermophilus)','unknown',False),
        ('Example Food','Mixed Mushrooms (Oyster, Champignon), Water','vegan',False),
        ('Example Food','Free from milk but contains egg','nonvegan',True),
        ('Example Food','Water; May contain milk; Milk powder','nonvegan',True),
        ('Example Food','Cheese Flavoured Seasoning (Rice Flour, Natural Flavouring)','unknown',False),
        ('Example Food','Vegetable suet, Water','unknown',False),
        # EU/GB E1105 specification obtains lysozyme from hens' egg whites;
        # this code is animal proof, rather than unspecified enzyme sourcing.
        ('Example Food','E 1105','nonvegan',True),
    ]
    cases=[]
    for group,(name,ingredients,baseline,hard) in enumerate(patterns):
        for variant in range(5):
            product={'id':str(901000000+group*5+variant),'name':name}
            if ingredients is not None:product['ingredients']=ingredients
            if variant==1:product['official_vegan']=1
            elif variant==2:product['dietary_information']='Suitable for vegans.'
            elif variant==3:product['name']='Vegan '+name
            elif variant==4:product['dietary_information']='Another variant is suitable for vegans.'
            status=baseline if variant in (0,4) or hard else 'vegan'
            # An explicit whole-product statement resolves a component-only name
            # or missing identity detail, but never an actual identity misjoin.
            identity_conflict=group in (26,27)
            scope_only=group in (31,32,33)
            if scope_only and variant in (1,2):status='vegan'
            if identity_conflict:status='unknown'
            if group==30:status='unknown'  # Negative whole-product wording is a material conflict.
            reason=None if status!='vegan' else 'tagged' if variant==1 else 'manufacturer' if variant==2 else 'name' if variant==3 else 'ingredients'
            context={'product':product,'flags':[],'categories':[],'manufacturer_vegan_evidence':[]}
            context=classifier.product_context_for_llm(context)
            cases.append({'case_id':f'synthetic.{group:02}.{variant}','case_kind':'synthetic','split':'synthetic',
                'leakage_group':f'synthetic.{group:02}','classifier_input':context,'input_sha256':classifier.context_hash(context),
                'expected':{'vegan_status':status,'vegan_reason':reason,'required_issue_codes':['identity_conflict'] if identity_conflict else []},
                'adjudication':{'state':'accepted','reference_type':'explicit-policy-contract','rationale':'Controlled claim/scope/source/ingredient contrast. Not an independent natural product.'}})
    # Additional name contrasts are controlled evidence, never extra natural
    # observations used to conceal a catalogue/reference shortfall.
    names=[('The Vegan Company Classic Product','The Vegan Company','unknown',None),
           ('The Vegan Company Vegan Beans','The Vegan Company','vegan','name'),
           ('A Vegan\u2019s Guide to Gardening','','unknown',None),
           ('Vegan Cookbook','','unknown',None)]
    for index,(name,brand,status,reason) in enumerate(names):
        context=classifier.product_context_for_llm({'product':{'id':str(902000000+index),'name':name,'brand':brand},
            'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})
        cases.append({'case_id':f'synthetic.name-scope.{index}','case_kind':'synthetic','split':'synthetic',
            'leakage_group':'synthetic.name-scope','classifier_input':context,'input_sha256':classifier.context_hash(context),
            'expected':{'vegan_status':status,'vegan_reason':reason},
            'adjudication':{'state':'accepted','reference_type':'explicit-policy-contract',
                'rationale':'Brand/topic wording is not a whole-product suitability claim; a separate affirmative assertion can qualify.'}})
    for variant in range(5):
        product={'id':str(903000000+variant),'name':'Example Oil Blend','pack_size':'500ml',
            'ingredients':'Flax seed oil, Gelatin Capsule (North American Source).'}
        if variant==1:product['official_vegan']=1
        elif variant==2:product['dietary_information']='Suitable for vegans.'
        elif variant==3:product['name']='Vegan Example Oil Blend'
        elif variant==4:product['dietary_information']='Another variant is suitable for vegans.'
        context=classifier.product_context_for_llm({'product':product,'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})
        cases.append({'case_id':f'synthetic.form-scope.{variant}','case_kind':'synthetic','split':'synthetic',
            'leakage_group':'synthetic.form-scope','classifier_input':context,'input_sha256':classifier.context_hash(context),
            'expected':{'vegan_status':'unknown','vegan_reason':None,'required_issue_codes':['identity_conflict']},
            'adjudication':{'state':'accepted','reference_type':'explicit-policy-contract',
                'rationale':'Liquid volume form conflicts with a capsule ingredient declaration; a positive claim cannot resolve which formulation applies.'}})
    components=[
        ('Example Cup Filters Coffee','Coffee',{'features':'Filters made from sugar cane fibres.'},'unknown',None),
        ('Example Coffee Pods','Coffee',{'dietary_information':'Suitable for vegans; packaging not assessed.'},'unknown',None),
        ('Example Coffee Pods','Coffee',{'features':'Pod material: aluminium.'},'unknown',None),
        ('Example Coffee Pods','Coffee',{'official_vegan':1},'vegan','tagged'),
        ('Example Coffee Pods','Coffee',{'dietary_information':'Suitable for vegans.'},'vegan','manufacturer'),
        ('Vegan Coffee Pods','Coffee',{},'vegan','name'),
        ('Example Coffee Pods','Coffee',{'specification':'Wrapper: Gelatine.','official_vegan':1},'nonvegan',None),
        ('Example Tea Bags','Tea leaves',{},'unknown',None),
        ('Vegan Tea Bags','Tea leaves',{},'vegan','name'),
        ('Example Coffee Pods','Coffee',{'features':'Complete component composition: aluminium pod and lid, plant-derived adhesive. All components manufactured without animal inputs.'},'vegan','ingredients'),
        ('Carrots',None,{'package_type':'Plastic Bag'},'unknown',None),
        ('Carrots',None,{'package_type':'Plastic Bag','official_vegan':1},'vegan','tagged'),
        ('Carrots',None,{'package_type':'Loose'},'vegan','ingredients'),
        ('Plain Rice','Rice',{'package_type':'Carton'},'unknown',None),
    ]
    for index,(name,ingredients,fields,status,reason) in enumerate(components):
        context=classifier.product_context_for_llm({'product':{'id':str(904000000+index),'name':name,'ingredients':ingredients,**fields},
            'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})
        cases.append({'case_id':f'synthetic.ethical-components.{index}','case_kind':'synthetic','split':'synthetic',
            'leakage_group':'synthetic.ethical-components','classifier_input':context,'input_sha256':classifier.context_hash(context),
            'expected':{'vegan_status':status,'vegan_reason':reason,
                'required_issue_codes':['composition_incomplete'] if status=='unknown' else []},
            'adjudication':{'state':'accepted','policy_version':policy.POLICY_VERSION,'reference_type':'explicit-policy-contract',
                'rationale':'User ethical-product scope includes supplied non-edible components; a food-only assurance or headline material is insufficient.'}})
    identities=[
        ('Example Pineapple & Ginger',{'brand':'Example','other_information':'Full Product Name: Pineapple & Ginger Gin Liqueur.'},'unknown',None),
        ('Example Mango',{'brand':'Example','features':'Sparkling Water with Alcohol and a Hint of Natural Flavours.'},'unknown',None),
        ('Example Mango',{'brand':'Example','other_information':'Full Product Name: Fresh Mango.'},'vegan','ingredients'),
    ]
    for index,(name,fields,status,reason) in enumerate(identities):
        context=classifier.product_context_for_llm({'product':{'id':str(905000000+index),'name':name,**fields},
            'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})
        cases.append({'case_id':f'synthetic.fruit-identity.{index}','case_kind':'synthetic','split':'synthetic',
            'leakage_group':'synthetic.fruit-identity','classifier_input':context,'input_sha256':classifier.context_hash(context),
            'expected':{'vegan_status':status,'vegan_reason':reason},
            'adjudication':{'state':'accepted','policy_version':policy.POLICY_VERSION,'reference_type':'explicit-policy-contract',
                'rationale':'Stored manufactured-drink identity defeats a fruit-only name shortcut; unambiguous fresh fruit remains eligible.'}})
    # Append newly demonstrated bypasses without changing prior frozen cases.
    contrasts=[]
    for name in ['Not-Vegan Wine','Not: Vegan Wine','Not (Vegan) Wine','Non: Vegan Wine',
                 'Vegan Formula Shampoo','Shampoo Formula Vegan']:
        for variant in range(3):
            fields={} if variant==0 else {'official_vegan':1} if variant==1 else {'dietary_information':'Suitable for vegans.'}
            formula='Formula' in name
            status='vegan' if formula and variant else 'unknown'
            reason=('tagged' if variant==1 else 'manufacturer') if status=='vegan' else None
            contrasts.append((name,fields,status,reason,['evidence_conflict'] if not formula else []))
    for variant in range(3):
        fields={'other_information':"Full Product Name: Cow's Milk."}
        if variant==1:fields['official_vegan']=1
        if variant==2:fields['dietary_information']='Suitable for vegans.'
        contrasts.append(("Vegan Cow's Milk",fields,'unknown',None,['identity_conflict']))
    for text in ['Derived from milk.','Obtained from gelatine.','Isinglass is used during manufacture.',
                 'Animal-derived milk is used during manufacture.','Bone char is used during refining.']:
        for variant in range(3):
            fields={'features':text}
            if variant==1:fields['official_vegan']=1
            if variant==2:fields['dietary_information']='Suitable for vegans.'
            contrasts.append(('Example Product',fields,'nonvegan',None,[]))
    for variant in range(3):
        fields={'features':'Not vegan.'}
        if variant==1:fields['official_vegan']=1
        if variant==2:fields['dietary_information']='Suitable for vegans.'
        contrasts.append(('Example Product',fields,'nonvegan' if not variant else 'unknown',None,[]))
    contrasts.extend([
        ('Example Product',{'features':'Derived from coconut milk.','official_vegan':1},'vegan','tagged',[]),
        ('Not Certified Vegan Wine',{'official_vegan':1},'vegan','tagged',[]),
    ])
    for index,(name,fields,status,reason,issues) in enumerate(contrasts):
        value=classifier.product_context_for_llm({'product':{'id':str(906000000+index),'name':name,**fields},
            'flags':[],'categories':[],'manufacturer_vegan_evidence':[]})
        cases.append({'case_id':f'synthetic.claim-bypass.{index}','case_kind':'synthetic','split':'synthetic',
            'leakage_group':'synthetic.claim-bypass','classifier_input':value,'input_sha256':classifier.context_hash(value),
            'expected':{'vegan_status':status,'vegan_reason':reason,'required_issue_codes':issues},
            'adjudication':{'state':'accepted','policy_version':policy.POLICY_VERSION,'reference_type':'explicit-policy-contract',
                'rationale':'Negative or formula-scoped names cannot assert whole-product suitability; contrary full identity remains unresolved; actual animal inputs override claims; plant sources and absent certification are distinct.'}})
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


def select(candidates,target,excluded,seed):
    available={key:values for key,values in candidates.items() if key not in excluded};selected=[];chosen=set();remaining=dict(target)
    rng=random.Random(seed);keys=list(available);rng.shuffle(keys)
    category=lambda case:case['expected']['vegan_reason'] if case['expected']['vegan_status']=='vegan' else case['expected']['vegan_status']
    while any(value>0 for value in remaining.values()):
        best=None;score=0
        for key in keys:
            if key in chosen:continue
            counts=Counter(category(case) for case in available[key])
            value=sum(min(counts[route],count)*({'name':8,'manufacturer':4,'ingredients':3,'tagged':2}.get(route,1)) for route,count in remaining.items())
            if value>score:best,score=key,value
        if best is None:break
        chosen.add(best)
        values=list(available[best]);rng.shuffle(values)
        for case in values:
            route=category(case)
            if remaining.get(route,0)>0:selected.append(case);remaining[route]-=1
    return selected,chosen,{key:value for key,value in remaining.items() if value>0}


def assemble(annotation_path,output):
    packet=json.loads(annotation_path.read_text())
    accepted=[case for case in packet['candidates'] if case['adjudication']['state']=='accepted']
    clustered=groups(accepted)
    targets={'development':{'tagged':20,'manufacturer':20,'name':20,'ingredients':20,'unknown':120,'nonvegan':40},
             'locked':{'tagged':30,'manufacturer':30,'name':30,'ingredients':30,'unknown':180,'nonvegan':60}}
    available=Counter(case['expected']['vegan_reason'] or case['expected']['vegan_status'] for case in accepted)
    # Allocate scarce reference classes proportionately for diagnostics. The
    # original targets and their shortfalls remain the release contract.
    allocated={split:dict(counts) for split,counts in targets.items()}
    for route in targets['development']:
        total=sum(counts[route] for counts in targets.values())
        if available[route]<total:
            dev=round(available[route]*targets['development'][route]/total)
            allocated['development'][route]=dev
            allocated['locked'][route]=available[route]-dev
    development,dev_groups,_=select(clustered,allocated['development'],set(),20261006)
    locked,locked_groups,_=select(clustered,allocated['locked'],dev_groups,20261007)
    def shortfalls(split,selected):
        counts=Counter(case['expected']['vegan_reason'] or case['expected']['vegan_status'] for case in selected)
        return {route:count-counts[route] for route,count in targets[split].items() if counts[route]<count}
    locked_short=shortfalls('locked',locked);dev_short=shortfalls('development',development)
    pilot=[]
    for route,count in {'tagged':6,'manufacturer':6,'name':6,'ingredients':6,'unknown':48,'nonvegan':24}.items():
        eligible=[case for case in development if (case['expected']['vegan_reason'] or case['expected']['vegan_status'])==route]
        pilot.extend(case['case_id'] for case in eligible[:count])
    cases=[]
    for split,selected in [('development',development),('locked',locked)]:
        for case in selected:
            group=next(key for key,values in clustered.items() if any(value['case_id']==case['case_id'] for value in values))
            # Preserve source metadata/short validated claims, not complete web captures.
            source_metadata=[{key:value for key,value in source.items() if key!='source_text'} for source in case['external_sources']]
            cases.append({**{key:value for key,value in case.items() if key not in {'external_sources','family_key','source_group_keys'}},
                'split':split,'leakage_group':group,'source_metadata':source_metadata})
        for reason in policy.REASONS:
            controls=[case for case in cases if case['split']==split and case['expected'].get('vegan_reason')==reason][:4]
            for case in controls:case['mandatory_control']=True
    cases+=synthetic_cases()+packet['workload']
    report={'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':cases,
        'selection':{'annotation_hash':classifier.context_hash(packet),'accepted_candidates':len(accepted),'disputed_cases':packet['disputed_cases'],
            'targets':targets,'available_reference_classes':dict(available),'diagnostic_allocations':allocated,
            'locked_shortfalls':locked_short,'development_shortfalls':dev_short,'target_complete':not locked_short and not dev_short,
            'counts':dict(Counter(case['split'] for case in cases)),
            'pilot_case_ids':pilot,
            'note':'Gold uses two automated source readings, not human-independent truth. Locked candidates have not been tested/tuned.'}}
    save(output,report)
    return report['selection']


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--annotations',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--synthetic-only',action='store_true')
    args=p.parse_args()
    if args.synthetic_only:save(args.output,{'schema_version':2,'policy_version':policy.POLICY_VERSION,'cases':synthetic_cases()})
    else:print(json.dumps(assemble(args.annotations,args.output),indent=2))
