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

## Model use and future changes

No model calls are needed for this first conclusive ordinary-food batch. Do not use restored legacy model settings on the canonical database. A later model-backed workflow must implement the selected two Luna 6/high readings and two blind Sol 6.1/medium readings on disagreement, within the included plan. It is a separately bounded change, not a reason to restart catalogue-wide classification now.

A future refresh should gather new facts into an isolated database and present affected products for review, preserving the protected baseline until changes are explicitly scoped. Genuine newly discovered evidence that a baseline item is nonvegan must be reported; baseline preservation is not a claim that known contrary evidence can be ignored forever.
