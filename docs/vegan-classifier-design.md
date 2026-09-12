# DB-Only Vegan Classification System

## Purpose

Classify every product in `ocado_products.sqlite` as one of:

- `vegan`
- `nonvegan`
- `unknown`

The classifier must use only data already stored in SQLite.
It must not call Ocado live pages, Ocado APIs, product pages, search pages, category pages, or any other Ocado source.
If the database does not contain enough information to decide, the classifier returns `unknown`.

This replaces the old phase split:

- `vegan-according-to-manufacturer`
- `vegan-according-to-ingredients`

The database should expose one canonical product classification, not separate phase-specific vegan booleans.

## Canonical Product Fields

The `products` table stores the latest classification:

- `vegan_status`: `vegan`, `nonvegan`, or `unknown`
- `vegan_reason`: nullable; when `vegan_status = 'vegan'`, one of `tagged`, `manufacturer`, `ingredients`, `name`
- `vegan_classifier_version`
- `vegan_classified_at_epoch`

The old product columns `vegan_according_to_manufacturer` and `vegan_according_to_ingredients` must not exist after migration.
Source-signal fields such as `official_vegan` and `name_contains_vegan` may remain because they are evidence inputs, not final classification fields.

## Audit Tables

Classification must be auditable without re-scraping Ocado.

`vegan_classification_runs` records each rules or Codex run:

- run start/end/status
- mode
- classifier version
- prompt version
- model and reasoning effort when applicable
- originating sync run ID when classification is limited to a monthly sync
- counters and errors

`product_vegan_classification_audit` records each product decision:

- product ID
- source: `rule` or `codex`
- status/reason/confidence/summary
- evidence JSON
- prompt version
- model/reasoning effort when applicable
- product context hash
- raw Codex final response
- parsed Codex JSON
- validation errors and exit status

The `products` table is the current source of truth.
The audit tables explain how each current decision was reached.

## Classification Semantics

The classifier answers one question:

> Based only on the data in the database, can this product be treated as vegan?

Do not infer from brand reputation, product category, marketing assumptions, or common sense except for a true single-ingredient item with no ingredients field and an unambiguous vegan product identity.

Examples:

- Fresh green beans with no ingredients field can be `vegan/ingredients`.
- A product with no ingredients field should be treated as a single-ingredient product; use stored product identity and category text to decide whether that single ingredient is unambiguously vegan.
- Plain dried pasta with ingredients `Durum wheat semolina` can be `vegan/ingredients`.
- Udon noodles containing fortified wheat/flour can be `vegan/ingredients` when the fortification is limited to standard flour additions such as calcium, iron, niacin, thiamin, or folic acid.
- Products without an official Ocado vegan tag that contain milk, egg, honey, gelatine, meat, fish, shellfish, beeswax, shellac, carmine, lanolin, or similar animal-derived ingredients should be `nonvegan`.
- `May contain milk` warnings do not make a product `nonvegan`.
- Material conflicts between supplied product fields should be `unknown`, even when each possible product or ingredient identity would individually be vegan.
- Vague or ambiguous ingredients such as natural flavourings, enzymes, vitamins outside standard flour fortification, vitamin D3, glycerine, mono/diglycerides, glaze of unspecified composition, or colours should be `unknown` unless explicit vegan evidence exists. Explicit shellac is animal-derived and follows the non-vegan rule above.

## Decision Order

Apply deterministic rules first.
Stop at the first conclusive rule.

1. Official vegan tag:
   classify `vegan/tagged` if `products.official_vegan = 1` or `product_flags.flag = 'vegan'`.
   Ocado's official tag is authoritative for this extension; store any apparent ingredient conflict in the tagged decision's audit evidence for manual review rather than downgrading the product.

2. Conclusive animal-derived ingredients:
   for products without an official Ocado vegan tag, classify `nonvegan` when explicit ingredients such as whey, egg, honey, beeswax, gelatine, shellac, carmine, or lanolin conflict with a vegan claim.
   This override intentionally excludes lexically ambiguous terms such as cocoa butter, coconut cream, vegan cheese, vegan collagen, and oyster mushroom.

3. Explicit manufacturer text:
   classify `vegan/manufacturer` when product text explicitly says suitable for vegans, certified vegan, vegan friendly, or registered with the Vegan Society.
   classify `nonvegan` when product text explicitly says not vegan or not suitable for vegans.
   classify `unknown` if explicit positive and negative vegan statements conflict.

4. Product name:
   classify `vegan/name` when the product name contains the standalone word `vegan` and no explicit negative vegan statement was found.

5. Ingredients:
   classify `nonvegan` when ingredients contain obvious animal-derived terms.
   classify `unknown` when ingredients contain ambiguous terms.
   classify `vegan/ingredients` only when all parsed ingredient terms are definitely vegan.
   classify `vegan/ingredients` for no-ingredients products only when product identity is unambiguously a single vegan ingredient.

6. No conclusive rule:
   leave the product unresolved for Codex classification.

## Codex LLM Fallback

Use Codex only for unresolved products.
The local machine is authenticated through ChatGPT/Codex, not `OPENAI_API_KEY`.

The Python orchestrator is the only SQLite writer.
Codex receives product-context JSON only and returns schema-validated JSON.
It must not read files, query the DB, browse the web, or access Ocado.

Use:

```bash
codex exec --ephemeral --output-schema schema.json --output-last-message result.json
```

Bulk classification defaults:

- model: `gpt-5.6-luna`
- reasoning effort: `high`
- independent passes: two, including resumed runs

The supervising interactive Codex model is a separate choice and can review code, prompts, and schemas without replacing the bulk classifier. Alternative classifier models/settings require a successful retained benchmark before writing decisions; historical fallback comparisons are recorded in the benchmark report rather than silently selected on errors.

The bulk default was selected with a two-pass, evidence-grounded benchmark covering known vegan, non-vegan, unknown, and newly discovered products. Luna/high matched all 48 retained gold decisions with zero pass disagreements. Luna low and medium both repeated a false-vegan decision, while xhigh added no classification benefit and used materially more time and output tokens. See `docs/model-benchmark-2026-08-21.md`.

The model ID and reasoning effort are separate settings. Use model `gpt-5.6-luna` with reasoning effort `high`; never construct a slash-suffixed model ID such as `gpt-5.6-luna/high`.

False-vegan errors are the primary rejection criterion. Pass agreement and latency are secondary because identical passes can still repeat the same unsupported inference.

For manufactured non-food goods, material descriptions such as cotton, plastic, melamine, metal, or glass do not prove that the complete product is vegan. Unlisted dyes, adhesives, coatings, trims, and processing inputs require an `unknown` result unless explicit vegan evidence or a complete composition resolves them.

Do not use Luna `low` or `medium` reasoning for product decisions because both repeated a false-vegan result in the retained benchmark. Use `high` unless a later benchmark justifies another setting.

For every bulk run, run two independent Codex passes for any LLM-classified product.
If the passes disagree on `vegan_status` or `vegan_reason`, classify the product as `unknown`.

## Concurrency and optional review

The Python orchestrator owns the SQLite connection and writes batch results transactionally. Its `--workers` setting controls concurrent Codex batches; it is separate from interactive review subagents. The CLI defaults to one worker, while [monthly maintenance](monthly-maintenance.md) explicitly uses eight based on the completed August run. Start small when evaluating a changed model or prompt, and lower concurrency if persistent service errors require it.

When review is delegated, keep reviewers read-only and give them a bounded task:

- Prompt red-team: reviews ambiguity rules and false-positive risks.
- Schema-risk inspector: reviews migration/upsert/audit/versioning risks.
- Audit sampler: reviews stratified samples after classification.

Review subagents are not required to run the classifier. Do not introduce a persistent custom agent or change global Codex settings just to execute the existing workflow.

## CLI

The implementation entrypoint is below. These are database-writing operations for an authorized classification task; place `--db PATH` before the subcommand when selecting a different database:

```bash
python3 tools/classify_ocado_vegan.py migrate-schema
python3 tools/classify_ocado_vegan.py classify-rules
python3 tools/classify_ocado_vegan.py classify-codex --limit 100 --batch-size 10
python3 tools/classify_ocado_vegan.py classify-all --codex
```

`migrate-schema` creates a rolling SQLite backup before changing the DB.
`classify-rules` must run before Codex for the selected queue.
`classify-codex` is resumable because it selects only rows where `vegan_status IS NULL`.
It refuses to submit an officially tagged product, so deterministic rules cannot be bypassed accidentally.

The `status` CLI also calls schema migration code, so use the read-only SQL in [the working guide](working-guide.md#inspecting-the-local-database-safely) for inspection. The guide also explains rolling-backup replacement, interrupted-sync baselines, and why `classify-rules --force` is not a complete reclassification.

## Verification

Unit tests should cover:

- schema migration
- deterministic official-tag rule
- explicit manufacturer vegan/nonvegan text
- conflicting manufacturer text
- standalone `vegan` in product name
- obvious animal-derived ingredients
- may-contain allergen warnings
- ambiguous ingredients
- definitely vegan ingredient lists
- single-ingredient no-ingredients products
- Codex output parsing
- independent Codex disagreement handling
- DB write invariants

After a full run:

```sql
select count(*) from products where vegan_status is null;
select vegan_status, vegan_reason, count(*) from products group by vegan_status, vegan_reason;
pragma table_info(products);
```

The first query must return `0`.
The schema must not contain `vegan_according_to_manufacturer` or `vegan_according_to_ingredients`.
