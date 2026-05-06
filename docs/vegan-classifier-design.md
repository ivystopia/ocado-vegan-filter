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
- Plain dried pasta with ingredients `Durum wheat semolina` can be `vegan/ingredients`.
- Udon noodles containing fortified wheat/flour should be `unknown` unless the DB text explicitly says vegan.
- Products containing milk, egg, honey, gelatine, meat, fish, shellfish, beeswax, shellac, carmine, lanolin, or similar animal-derived ingredients should be `nonvegan`.
- `May contain milk` warnings do not make a product `nonvegan`.
- Vague or ambiguous ingredients such as natural flavourings, enzymes, vitamins, vitamin D3, glycerine, mono/diglycerides, shellac/glaze, colours, or fortified flour should be `unknown` unless explicit vegan evidence exists.

## Decision Order

Apply deterministic rules first.
Stop at the first conclusive rule.

1. Official vegan tag:
   classify `vegan/tagged` if `products.official_vegan = 1` or `product_flags.flag = 'vegan'`.

2. Explicit manufacturer text:
   classify `vegan/manufacturer` when product text explicitly says suitable for vegans, certified vegan, vegan friendly, or registered with the Vegan Society.
   classify `nonvegan` when product text explicitly says not vegan or not suitable for vegans.
   classify `unknown` if explicit positive and negative vegan statements conflict.

3. Product name:
   classify `vegan/name` when the product name contains the standalone word `vegan` and no explicit negative vegan statement was found.

4. Ingredients:
   classify `nonvegan` when ingredients contain obvious animal-derived terms.
   classify `unknown` when ingredients contain ambiguous terms.
   classify `vegan/ingredients` only when all parsed ingredient terms are definitely vegan.
   classify `vegan/ingredients` for no-ingredients products only when product identity is unambiguously a single vegan ingredient.

5. No conclusive rule:
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

Recommended defaults:

- bulk unresolved classification: `gpt-5.4-mini`, `medium`
- escalation/review: `gpt-5.4`, `high`
- prompt/schema review: `gpt-5.5`, `high` or `xhigh`

Do not use `low` reasoning for product decisions because false certainty is more harmful than extra `unknown` classifications.

For the first bulk run, run two independent Codex passes for any LLM-classified product.
If the passes disagree on `vegan_status` or `vegan_reason`, classify the product as `unknown`.

## Subagent Strategy

Use subagents for review and audit, not as DB writers.

Effective roles:

- Prompt red-team: reviews ambiguity rules and false-positive risks.
- Schema-risk inspector: reviews migration/upsert/audit/versioning risks.
- Audit sampler: reviews stratified samples after classification.

A persistent `ocado-vegan-classifier` subagent may be added after the prompt/schema is stable.
It should be stateless, read-only, and receive product JSON only.

Keep `agents.max_depth = 1`.
Keep classifier concurrency low initially: 1-2 workers, then 2-4 after sample audits pass.

## CLI

The implementation entrypoint is:

```bash
python3 classify_ocado_vegan.py migrate-schema
python3 classify_ocado_vegan.py classify-rules
python3 classify_ocado_vegan.py classify-codex --limit 100 --batch-size 10
python3 classify_ocado_vegan.py classify-all --codex
python3 classify_ocado_vegan.py status
```

`migrate-schema` creates a rolling SQLite backup before changing the DB.
`classify-rules` is safe to run before Codex.
`classify-codex` is resumable because it selects only rows where `vegan_status IS NULL`.

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
