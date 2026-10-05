#!/usr/bin/env python3
"""Reproduce the read-only 15-product Sol comparison on the frozen Astra cohort; preserve each call."""
import concurrent.futures
import json
from pathlib import Path
import shutil
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[2] / 'tools'))
import classify_ocado_vegan as classifier


def run(effort):
    cohort = json.loads((ROOT / 'cohort.json').read_text())
    contexts = [row['context'] for row in cohort['products']]
    for offset in range(0, len(contexts), 10):
        expected_prompt = (ROOT / f'prompt-batch-{offset // 10 + 1}.txt').read_text()
        assert classifier.build_codex_prompt(contexts[offset:offset + 10]) == expected_prompt
    assert classifier.CODEX_OUTPUT_SCHEMA == json.loads((ROOT / 'output-schema.json').read_text())
    for row in cohort['products']:
        assert classifier.context_hash(row['context']) == row['sha256']
    passes = []
    durations = []
    for pass_number in (1, 2):
        decisions = {}
        for offset in range(0, len(contexts), 10):
            path = ROOT / f'{effort}-pass-{pass_number}-batch-{offset // 10 + 1}.json'
            batch = contexts[offset:offset + 10]
            prompt_hash = classifier.context_hash(classifier.build_codex_prompt(batch))
            if path.exists():
                saved = json.loads(path.read_text())
                assert saved['prompt_sha256'] == prompt_hash
            else:
                started = time.perf_counter()
                print(f'{effort}: pass {pass_number}, batch {offset // 10 + 1}', flush=True)
                result, raw, parsed, code = classifier.call_codex_batch(
                    batch, model='gpt-6.1-sol', reasoning_effort=effort,
                    codex_bin=shutil.which('codex') or 'codex')
                saved = {'model': 'gpt-6.1-sol', 'reasoning_effort': effort,
                         'run_at': datetime.now(timezone.utc).isoformat(),
                         'prompt_sha256': prompt_hash,
                         'duration_seconds': round(time.perf_counter() - started, 3),
                         'exit_status': code, 'decisions': result, 'raw_response': raw}
                path.write_text(json.dumps(saved, indent=2) + '\n')
            if saved['exit_status']:
                raise RuntimeError(f"Nonzero exit status: {saved['exit_status']}")
            assert saved['model'] == 'gpt-6.1-sol'
            assert saved['reasoning_effort'] == effort
            validated = classifier.validate_codex_payload(
                classifier.extract_json_object(saved['raw_response']),
                {row['product']['id'] for row in batch})
            assert validated == saved['decisions']
            decisions.update(saved['decisions'])
            durations.append(saved['duration_seconds'])
        passes.append(decisions)
    merged = {row['product_id']: classifier.merge_pass_decisions(passes, row['product_id'])
              for row in cohort['products']}
    summary = {'model': 'gpt-6.1-sol', 'reasoning_effort': effort, 'passes': 2,
               'batch_size': 10, 'call_duration_seconds': sum(durations), 'decisions': merged}
    (ROOT / f'{effort}-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'{effort}: complete, {sum(durations):.1f}s summed call time', flush=True)


def run_all(effort):
    run(effort)
    import benchmark_ocado_vegan as benchmark
    output = ROOT / f'safety-{effort}.json'
    if output.exists():
        result = json.loads(output.read_text())
        assert result['model'] == 'gpt-6.1-sol'
        assert result['reasoning_effort'] == effort
    else:
        args = benchmark.build_parser().parse_args([
            '--model', 'gpt-6.1-sol', '--reasoning-effort', effort,
            '--passes', '2', '--batch-size', '10'])
        result = benchmark.run_benchmark(args)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(f"{effort}: safety {result['exact_matches']}/{result['product_count']}, false-vegan={result['false_vegan_ids']}, disagreements={result['disagreement_ids']}, errors={result['error_ids']}", flush=True)
    return not any(result[key] for key in ['false_vegan_ids', 'mismatch_ids', 'missing_ids', 'error_ids'])


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run_all, ['medium', 'high']))
    raise SystemExit(0 if all(outcomes) else 1)
