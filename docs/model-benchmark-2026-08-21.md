# Vegan Classifier Model Benchmark — 2026-08-21

## Decision

Use `gpt-5.6-luna` with reasoning effort `high` and two independent passes for bulk unresolved product classification.

Keep `gpt-5.6-terra`/`high` as the fallback and occasional shadow comparison. Reserve `gpt-5.6-sol` for prompt/schema review rather than routine catalogue classification.

## Method

The benchmark used 48 products from `ocado_products.sqlite`:

- 24 deliberately difficult products, including manufactured non-food goods, conflicting product fields, incomplete composition, and ambiguous ingredients.
- A separate 24-product holdout containing eight vegan, eight non-vegan, and eight unknown products.
- Two independent passes per configuration.
- The production merge rule: any pass disagreement on status or vegan reason becomes `unknown`.
- Stored SQLite evidence only, with model web search disabled.
- Fast service tier inherited from the local Codex configuration.

The fixed cohort and gold statuses are retained in `benchmarks/vegan-classifier-v1.json`. The read-only runner is `tools/benchmark_ocado_vegan.py`.

The original cross-model comparison submitted each 24-product cohort as one batch. The retained monthly runner defaults to the production batch size of 10 for better response completeness.

## Results

```text
Configuration          Products  Exact  False-vegan  Disagreements  Runtime
Luna / low                   48   47/48       1              0        119.4s
Luna / medium                48   46/48       1              1        126.2s
Luna / high                  48   48/48       0              0        167.2s
Luna / xhigh                 48   48/48       0              0        251.4s
Terra / medium               48   48/48       0              2        107.9s
Terra / high                 48   48/48       0              2        112.3s
Terra / xhigh                48   48/48       0              1        148.0s
Sol / medium                 24   23/24       1              1        102.5s
Sol / high                   48   48/48       0              1        201.4s
Sol / xhigh                  48   48/48       0              0        259.8s
GPT-5.5 / medium             24   23/24       1              0         80.1s
```

Luna/high processed approximately 112,000 input tokens and produced 11,727 output tokens across its four calls. Luna/xhigh produced 19,204 output tokens and took about 50% longer without improving the classifications. One preliminary xhigh response also omitted a required product before a clean rerun.

Luna/high was about 49% slower than Terra/high in this benchmark, but the current official Luna token prices are one-tenth the corresponding Terra prices. This monthly offline workflow values safety and economic efficiency over interactive latency.

An end-to-end validation using the original 24-product batches also returned 48/48 exact with zero false-vegan results and zero disagreements. One holdout response initially omitted a required ID and recovered on retry.

A final validation using the retained production batch size of 10 returned 48/48 exact with zero false-vegan results, zero execution errors, and no retries in 284.5 seconds. The two passes disagreed on product `468140011` and safely merged it to the expected `unknown`. This is why the gate reports disagreements even when the final status remains correct.

## Live Catalogue Evidence

The 2026-08-21 monthly refresh provided a much larger operational validation. Deterministic rules resolved 4,453 of 12,695 new or changed products, then Luna/high classified the remaining 8,242 products with two passes, batches of 10, and eight concurrent workers.

The Luna run completed in 9,240 seconds with all 8,242 products written and zero final execution or validation errors. There were 166 pass disagreements (2.0%); every one was conservatively merged to `unknown`. A small number of responses omitted an ID or returned an invalid field combination, but the existing retry and split-batch recovery paths produced complete validated results before anything was written.

This confirms that Luna/high is suitable for the full monthly catalogue workload, not only the fixed benchmark. Eight workers are the current operational default on this machine; the classification remains transactionally resumable if a future run needs to restart with lower concurrency.

## Rejected Configurations

Luna/low and Luna/medium both classified product `697048011`, a dog dental powder with insufficient vegan evidence, as vegan in both passes. The two-pass merge cannot protect against identical unsupported decisions, so both efforts are prohibited for production classification.

Luna/medium also disagreed on vegan product `374339011`, causing a conservative false negative after merging.

Luna/xhigh passed the gold set but provided no quality improvement over high, took materially longer, emitted many more output tokens, and showed one preliminary response-completeness failure.

## Corrected Availability Finding

An initial Luna attempt incorrectly passed the reasoning effort as part of the model ID, for example `gpt-5.6-luna/medium`. The server correctly rejected that nonexistent model.

The valid configuration is:

```text
model: gpt-5.6-luna
reasoning effort: high
```

The corrected model worked through the existing ChatGPT-authenticated Codex CLI and with the Fast service tier.

## Rerun Policy

Run the retained benchmark before every monthly live sync and after any change to:

- model or reasoning effort;
- model alias behaviour;
- classifier prompt or output schema;
- merge logic;
- deterministic vegan-classification rules.

Stop before classification or userscript regeneration if the benchmark reports any mismatch, false-vegan decision, missing product, or model execution error.

Official model references:

- <https://developers.openai.com/api/docs/models/gpt-5.6-luna>
- <https://developers.openai.com/api/docs/guides/latest-model>
