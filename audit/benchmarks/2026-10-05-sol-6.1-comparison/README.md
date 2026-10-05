# GPT-6.1 Sol comparison — 2026-10-05

## Decision and scope

Select `gpt-6.1-sol` at `medium` reasoning for the disagreement-arbitration role previously performed by `gpt-6-astra`/`medium`. Sol medium and high both matched all 48 frozen reference statuses, and medium also matched all 15 expectations in the separate production-arbitration replay. High provided no observed benefit. This meets the existing benchmark bar for this role; it does not establish equal general capability or validate the planned whole-product “wine problem” policy.

The primary classifier remains `gpt-5.6-luna`/`high`, with two independent passes. The arbitration prompt, two fresh passes, disagreement-only routing, confirmation gate, failure handling and audit retention are unchanged. Classifier version advances to `db-vegan-codex-v11` to distinguish future runs. No catalogue classification, embedded allowlist, userscript or installed script was changed.

The original benchmark used **GPT-6 Astra**, not GPT-5.6 Astra. Historical Astra results and fixtures are preserved unchanged.

## Results

| Metric | Astra medium, historical | Sol medium | Sol high |
| --- | ---: | ---: | ---: |
| Frozen safety exact statuses | 48/48 | 48/48 | 48/48 |
| Safety false-vegan results | 0 | 0 | 0 |
| Safety pass disagreements | 0 | 0 | 0 |
| Safety execution errors | 0 | 0 | 0 |
| Safety elapsed seconds | 208.5 | 218.5 | 253.5 |
| Pilot agreed vegan / nonvegan / unknown | 5 / 1 / 9 | 4 / 1 / 10 | 4 / 1 / 10 |
| Pilot pass disagreements | 0 | 0 | 0 |
| Pilot summed call seconds | 80.5 | 88.1 | 128.3 |

All calls used two independent passes, batches of ten, and the unchanged primary classification prompt. The safety cohort retains its original challenge/holdout grouping and all expected statuses. Both safety fixture hashes match the Astra artifacts. All eight Sol pilot calls returned complete schema-valid output with exit status zero; neither safety run needed retries. The separate Sol arbitration replay passed 15/15 in 80.1 seconds, also without retries or arbitration disagreements; see [its report](../2026-10-05-sol-6.1-arbitration/README.md).

Sol medium and high agreed on every pilot status/reason. Both differed from Astra only on Estrella Galicia Premium Spanish Lager, product `640656011`: Sol returned unknown because the supplied evidence did not resolve fining or other brewing-process inputs. Astra had returned vegan/ingredients. This is a more conservative response to a processing-evidence gap, not a verified claim that the lager is nonvegan.

The remaining positive pilot decisions concern olives containing red wine vinegar, rum with no explicit ingredients field, vegetable dog food and plain coffee. In particular, the olive and rum decisions still need assessment against the revised whole-product policy. Agreement on the old prompt must not be presented as solving hidden processing risks. No new manufacturer research or independent gold annotation was performed.

## Method and limits

`cohort.json`, both `prompt-batch-*.txt` files and `output-schema.json` are byte-for-byte copies of the Astra experiment. The runner validates context hashes, prompt equality and schema equality before inference. The fifteen products were originally selected reproducibly from 156 historical Luna disagreements; their historical unknown labels are not accuracy targets. Pilot agreement and resolution counts are distinct from accuracy against the 48 retained reference labels.

Each configuration ran in its own worker, with sequential batches/passes within that worker. Medium and high ran concurrently. The later arbitration and production checks overlapped part of the experiment, so timings are descriptive, not a controlled speed comparison. Sol was not faster than Astra in these observations. No actual token usage or task cost was measured.

The explicit prompt/schema and evidence match Astra, but global/repository instructions and service conditions can differ between historical runs. Both experiments use the existing Codex CLI caller and its repository working directory; this comparison does not isolate inherited instructions. The original Astra and Sol runs were not randomized simultaneous controls. The small, repeatedly exposed cohorts do not establish catalogue error rates or constitute a new hidden test set.

## General price comparison

At equal token counts and the same Standard tier, Sol costs 80% less than Astra for uncached input and output, and 90% less for cached input. Relative to **GPT-5.6 Luna**, Sol costs 10× for uncached input, 8.33× for output and 5× for cached input. These ratios agree between the published API rates and Codex purchased-credit rates. They do not estimate this benchmark's actual bill or included subscription allowance.

| Model | Standard Codex credits / 1M input | Cached input | Output |
| --- | ---: | ---: | ---: |
| GPT-6 Astra | 250 | 25 | 1,250 |
| GPT-6.1 Sol | 50 | 2.5 | 250 |
| GPT-5.6 Luna | 5 | 0.5 | 30 |

Medium and high are reasoning-effort settings, not separate per-token price bands. Total task cost depends on actual reasoning/output usage, input size, caching, retries and execution/speed settings. Purchased-credit Fast rates and included subscription usage have different rules; do not infer an exact subscription-quota saving from this table. Codex credits have no separate cache-write charge. API prices and illustrative GBP equivalents before VAT are retained in `pricing-comparison.json`; the GBP conversion is explicitly an assumed exchange rate, not a current quote.

Sources checked on 2026-10-05: https://developers.openai.com/api/docs/pricing#text-tokens and https://learn.chatgpt.com/docs/pricing#token-rates . Model capabilities/settings: https://developers.openai.com/api/docs/models/gpt-6.1-sol . The successful calls establish availability for this account.

## Reproduction and retained evidence

Run `python3 audit/benchmarks/2026-10-05-sol-6.1-comparison/run.py` from the repository root. Existing matching pilot calls and safety results are reused. For a genuinely new experiment, copy the runner and frozen cohort/prompt/schema files into a new sibling directory without old outputs. The runner depends on the recorded classifier implementation; review changes and retain new provenance when rerunning later.

`environment.json` records the original source commit, source hashes, versions and inherited service tier. `*-pass-*-batch-*.json` retain pilot raw/parsed answers, prompt hashes, timings and exit status. `*-summary.json`, `safety-*.json`, `comparison.json` and `execution.log` retain outcomes. The 48-case runner retains final decisions rather than full per-pass responses; this pre-existing limitation is unchanged. The final production-pipeline check and repository-test summary are recorded in the arbitration report. No inference opened the catalogue database.
