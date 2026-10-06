#!/usr/bin/env python3
"""Retain actual local checks bound to a candidate before model comparison."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import benchmark_whole_product as benchmark


def validate_record(record):
    if record.get('state')!='passed' or record.get('candidate_hashes')!=benchmark.candidate_hashes():
        raise ValueError('Implementation checks are failed or stale; run validate_whole_product_candidate.py')
    required={'unittest','userscript-syntax','diff-check'}
    checks=record.get('checks',[])
    if {check.get('name') for check in checks}!=required or any(check.get('exit_code')!=0 for check in checks):
        raise ValueError('Implementation check record does not contain all successful checks')


def run(output):
    output.mkdir(parents=True,exist_ok=True)
    before=benchmark.candidate_hashes()
    checks=[]
    commands=[('unittest',[sys.executable,'-m','unittest','discover','-s','tests']),
              ('userscript-syntax',['node','--check','ocado-vegan-filter.user.js']),
              ('diff-check',['git','diff','--check'])]
    for name,command in commands:
        with (output/(name+'.log')).open('w') as log:
            result=subprocess.run(command,cwd=benchmark.ROOT,stdout=log,stderr=subprocess.STDOUT)
        checks.append({'name':name,'command':command,'exit_code':result.returncode,'log':str(output/(name+'.log'))})
    after=benchmark.candidate_hashes()
    record={'state':'passed' if before==after and all(check['exit_code']==0 for check in checks) else 'failed',
            'at_utc':datetime.now(timezone.utc).isoformat(),'candidate_hashes':after,
            'code_changed_during_checks':before!=after,'checks':checks,
            'note':'Unit checks use isolated fixtures; inspect unittest.log for the explicit live-test skip. No model calls or live catalogue writes.'}
    benchmark.save(output/'checks.json',record)
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.output)
    print(result['state']+': '+str(args.output/'checks.json'))
    raise SystemExit(0 if result['state']=='passed' else 1)
