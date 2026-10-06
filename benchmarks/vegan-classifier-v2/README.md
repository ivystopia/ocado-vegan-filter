# Whole-product benchmark

This is a frozen development corpus for `whole-product-v1`, not a release approval or an independent physical guarantee. The canonical catalogue has not been migrated. Historical fixture files in `benchmarks/vegan-classifier-v1*.json` remain unchanged.

## References and separation

`cases.json` retains complete classifier inputs, their hashes, source metadata and the two automated source readings supporting each natural reference. Sampling reads SQLite in read-only mode, excludes old classifications and model decisions, and uses retained manufacturer captures rather than new live retrieval. Annotation uses two fresh GPT-6.1-Sol/high sessions with locally validated input pointers, exact visible source quotes, explicit applicability and rationale. Agreement without this evidence is not the reference method. No human annotation review is assumed. The method still shares model-family and policy-interpretation risks with the evaluated models; accepted annotations may be wrong.

Reference A asks what the frozen classifier evidence establishes under the policy. Reference B separately records what the full associated primary captures establish for the exact product. Hidden source findings cannot upgrade A. Source dates are historical; neither stored `current_on_ocado` flags nor these captures prove current live availability/formulation. Annotation disagreements remain explicit and are excluded from scored cases, rather than silently converted to unknown.

The pool contains 1,000 natural candidates: 990 accepted references and 10 disputes. Canonical reference counts are 99 tagged, 288 manufacturer, 17 name, 125 ingredients, 293 unknown and 168 nonvegan. Applicable manufacturer confirmation takes precedence over an affirmative name; multiple positive bases therefore do not produce extra independent name-only observations. Family/shared-source union groups cannot cross development and locked splits. The largest group contains 213 M&S/shared-evidence cases; this coarse grouping affects representativeness and must be disclosed.

The original targets remain 240 development and 360 locked natural cases. Scarce routes are allocated proportionately for diagnostics: actual development is 224 (name short 13, unknown short 3); actual locked is 336 (name short 20, unknown short 4). Both include the other planned positive/negative classes. The pilot contains 96 development cases: six each positive reason, 48 unknown and 24 nonvegan. Shortfalls remain in `selection`; `target_complete` is false. Controlled cases do not fill natural quotas. Full comparison refuses these shortfalls; `--development-only` permits useful diagnostic work without locked evaluation or a release recommendation.

There are also 249 synthetic/metamorphic contracts and 200 reproducibly uniform unlabelled workload cases. Synthetic siblings and repeat runs are not independent catalogue observations. Workload cases have no expected label and measure operational usage/outcome mix only. They are not reference evidence and may overlap catalogue identities in challenge sets; no workload results may drive subsequent holdout tuning.

## Controlled-case history

`synthetic.json` preserves the initial 240 contracts. `synthetic-v2.json` adds five E1105 variants: its food-additive specification defines lysozyme from hens' egg whites (https://www.legislation.gov.uk/eur/2012/231/pdfs/eur_20120231_adopted_en.pdf, printed L83/276). `synthetic-v3.json` corrects two provisional `Not Vegan Wine` tag/manufacturer variants to unknown: the negative whole-product wording is an unresolved material contradiction. This is an explicit policy clarification, not an independent natural label or a silent replacement of historical results. `synthetic-v4.json` adds four brand/topic name contrasts. Each earlier fixture remains available, and reports identify the exact fixture/implementation hashes they actually tested.

`synthetic-v5.json` adds five liquid-versus-capsule formulation conflicts (254 controlled cases total). `cases.json` retains its originally frozen 249 controlled cases alongside the natural corpus; use v5 explicitly for current synthetic validation. Changing synthetic coverage does not refresh natural inputs or turn missing natural observations into controls.

`legacy-policy-preview.json` compares deterministic current-policy outcomes with the original 48 frozen legacy contexts. It is a rules-only crosswalk preview, not new gold: one conclusive status differs and 27 cases require semantic assessment. Older expectations must not constrain superseded policy or be relabelled just to obtain a pass.

## Reproduction

Use the development virtual environment. Preparation and annotation produce local ignored working artifacts; retain their hashes and reviewable results before a release claim. Preparation never opens SQLite for writing. Benchmark execution uses isolated in-memory fixture databases and never opens the live catalogue.

```sh
.venv/bin/python tools/prepare_whole_product_corpus.py --output .whole-product-work/new-candidates.json
.venv/bin/python tools/adjudicate_whole_product_corpus.py --packet .whole-product-work/new-candidates.json --output .whole-product-work/new-annotation
.venv/bin/python tools/assemble_whole_product_fixture.py --annotations .whole-product-work/new-annotation/annotated-candidates.json --output .whole-product-work/new-fixture.json
.venv/bin/python tools/compare_whole_product_models.py --fixture benchmarks/vegan-classifier-v2/cases.json --development-only --output audit/benchmarks/2026-10-06-whole-product/model-comparison
.venv/bin/python tools/benchmark_whole_product.py --fixture benchmarks/vegan-classifier-v2/synthetic-v4.json --split synthetic --model gpt-6-luna --effort high --output audit/benchmarks/2026-10-06-whole-product/synthetic-current-luna6
```

The frozen corpus was prepared with an earlier 1,000-candidate sampling target, then explicitly augmented with complete associated retained captures. The current preparer requests 1,040 candidates (a larger name signal stratum). A fresh preparation is a new corpus, not a way to overwrite these inputs. Saved annotation readouts are reused only for identical evidence/source/group/model configurations and currently valid cited bases; their original prompt versions are retained.

Model comparisons use 5.6-Luna/high and 6-Luna/high first; 6.1-Sol/medium is conditional primary fallback only if both fail. Arbitration candidates are 6.1-Sol/medium, 6.1-Sol/high and 6-Astra/medium on balanced development contrasts plus actual primary disagreements. Each call is fresh and blind to earlier conclusions. Both primary and arbitration paths use exactly two passes. Models receive no reference labels, earlier classifier outputs, local rules, repository access or live research tools. Included-plan exhaustion stops and checkpoints; no paid/API fallback or default change is authorised.

Results contain immutable configuration/code/fixture manifests, checkpoints, raw pass decisions, exact deduplicated call usage, failures/retries, latency, stage disagreements and policy/export results. Operationally failed positives remain in recall denominators. Raw unsafe model proposals are reported separately from final gated unsafe positives. Reasoning tokens are already included in output tokens. Credit equivalents at https://learn.chatgpt.com/docs/pricing are a comparative proxy, not included-plan quota rates, actual charges or a GBP price. Resume identical commands only; changes require new output directories.

Full locked gates require the original natural counts, three repetitions, zero final unsafe positives, at least 98% positive recall, at least 29/30 per positive reason, all mandatory controls and no operational/protocol/evidence/reporting failures. A development pass, synthetic pass or low-cost model preference does not satisfy these gates. A fixed shadow candidate must subsequently receive a uniform 600-positive audit, with automated-reference/correlation/dispute limitations disclosed. Only then may an explicitly chosen model pair and separately authorised catalogue/export migration proceed.
