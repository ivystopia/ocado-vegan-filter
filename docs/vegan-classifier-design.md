# Published baseline and reviewed unknown products

The active workflow retains the published 1.7.2 classifications and adds individually reviewed unknown products. It does not repeat the broad reclassification. The purpose is to help vegan shoppers find clear choices without removing useful, established labels because of speculative concerns.

## Baseline

`audit/published-baseline-restart/published-baseline.json` records the exact four ID sets extracted from the publicly served 1.7.2 source, its SHA-256 and release commit. Manufacturer versus name attribution was recovered from the pre-refresh SQLite snapshot, whose four sets exactly match the release. The exporter validates this immutable manifest.

Canonical product facts remain fresh. Restoring a published classification is an explicitly requested historical restoration, **not a fresh validation of its suitability under a new policy**. Other known IDs become unknown. Source flags, ingredients, current/historical membership and previous audit records are retained. A consistent compressed snapshot preserves all replaced classification columns; the restoration appends audit records linked to previous audits.

## Additive assessment

Select unknown candidates from SQLite and review the actual product, rather than automatically approving a category or name token. Plain apples, carrots, sprouts and simple all-vegetable mixes can qualify without a vegan statement. Missing ingredients or an ordinary bag/tray alone do not create doubt about such a product. Food processing and supplied components remain in scope where there is a specific material concern.

Distinguish butter actually included in prepared vegetables from instructions to add butter. Likewise, fruit names, natural nutrients, sensory descriptions, serving suggestions and may-contain warnings are not animal ingredients. Manufactured products, coatings, unclear additives and material contrary evidence need their own review; they are outside the first plain-produce batch.

Each accepted record retains the complete selected SQLite source fields, flags, categories, context hash, exact supporting quotations and an explicit rationale. This is direct source review by the supervising agent, not an independent model benchmark. Mechanical checks verify unchanged evidence and valid attribution; they do not independently prove the semantic conclusion. Canonical reason order remains tagged, manufacturer, name, ingredients.

## Application and export

`tools/published_baseline.py` previews by default and requires `--apply` to write. A transaction locks out concurrent writers, checks every proposed addition before writing, and permits only unknown-to-vegan changes. Application writes the ordinary classification audit, links the new decision and records the reviewed addition. Duplicate/repeated IDs, changed evidence or missing quotations fail rather than silently changing scope.

`tools/update_userscript_allowlists.py` recognizes the protected baseline and requires exact agreement with its published decisions plus recorded additions. It rejects unreviewed promotions, demotions, removed baseline IDs, stale reviews and missing review audits. The old unconditional official-tag invariant is inapplicable to this deliberate restoration: newer scraped flags remain evidence, not permission to rewrite the historical decision set.

The userscript's published runtime is unchanged. Export preserves retained ID positions and changes only additions and counts. No blocked-unknown set or whole-product coordinator is part of the active implementation.

## Complete current-Unknown review

The current task reviews every current Unknown product afresh. Freeze all `current_on_ocado = 1 AND vegan_status = 'unknown'` records with complete relevant source fields, flags, categories and source hashes. Do not filter admission by category or apparent likelihood. Keep previous decisions out of model inputs. Two independent GPT-6-Luna/high readings assess each product; raw status/reason disagreement receives exactly two fresh blind GPT-6.1-Sol/medium readings. Agreed unknowns do not escalate. Every product gets a retained outcome, including non-promotions; an operational failure is unfinished work.

Use `tools/review_current_unknowns.py`. The queue and call journals live on the main NVMe under `.rollback-work/`. Every attempt checks included-plan eligibility and retains usage, input bindings and raw responses. No tools or prior answers are available to the classifier. Validate exact IDs, source quotations, canonical reason, complete-product dimensions and unresolved concerns locally. Resume completed passes without repeating them. Schema/transport failures have bounded retries; valid conservative decisions are never retried to obtain a positive.

Before application, require complete cohort accounting, unchanged source hashes and unchanged protected classifications. Apply only clearly supported unknown-to-vegan additions through the protected baseline writer. Retain all other review outcomes in a separate audit ledger, leaving their canonical Unknown labels intact. Preserve all published and already reviewed classifications. This is a complete review of the frozen Unknown cohort, not a revalidation of published positives, a new scrape, or an exhaustive external manufacturer search.

Preview first, then apply, export with a dry run, verify unchanged runtime and existing ID placement, run tests and commit on dev. No tag, push, publication or installed FireMonkey update. Continue supervising until finished or an actual included-plan/external blocker prevents progress; never call a partial batch complete.
