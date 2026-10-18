# Published baseline and clear produce additions

The user requested cancellation of the broad reclassification, restoration of the currently published script and classifications, and a fresh start focused on clear unknown-to-vegan additions. The user subsequently requested a new/removed catalogue scan. All work stays local on dev, at version 1.7.2, for testing.

## Published baseline

The publicly served Greasy Fork source was fetched on 2026-10-18 and matched local tag **1.7.2** byte-for-byte. Release commit: `905b54348fd39d67d4fe42eb24654a8cbf7409e6`. Source SHA-256: `5276d765f27fc870df5ec319fdfc63bcaa5dc88dc6ea87970c42b55e9f47a039`.

Source: https://update.greasyfork.org/scripts/576838/Ocado%20Vegan%20Filter.user.js

The pre-refresh SQLite snapshot exactly matches all four published sets. Its attribution distinguishes manufacturer from name without changing the combined published set. The immutable [baseline manifest](published-baseline.json) retains the IDs, release identity and source hash.

| Published group | IDs |
| --- | ---: |
| Official vegan | 4,669 |
| Manufacturer/name vegan | 9,689 |
| Ingredients vegan | 6,153 |
| All vegan | 20,511 |
| Nonvegan | 13,933 |

The tracked tree was restored with commit `e895cf0`, preserving the discarded work in Git history. SQLite run 309 restored the published classifications across 58,322 known products, leaving IDs outside the published sets unknown. All source fields were verified unchanged by an exact before/after hash. Historical audits were retained; every replacement has a new audit record linked to the former audit. The verified compressed backup of the replaced classification fields is `.rollback-work/published-baseline-2026-10-18/before-restoration.classifications.json.gz`.

This restoration is the user's requested return to the published baseline, not a claim that its entire population has passed a fresh suitability assessment.

## First reviewed additions

Run 310 added **32** previously unknown ordinary produce products as vegan/ingredients. See [the product list](added-products.csv) and [the full source-bound reviews](produce-reviews.json). A further eight plain-produce records from the new scrape were reviewed and added in run 312; see [their retained evidence](new-produce-reviews.json). The two applicable official tags retain tagged precedence. The script now contains **20,551 vegan IDs**, including **6,191 ingredients IDs** and **4,671 tagged IDs**. Every published vegan and nonvegan ID remains in its original set.

Kissabel British Apples, 647140011, is included. Its identity and description establish plain apples. Ordinary wrapping and an absent ingredients list are not by themselves material animal-use concerns. The same reasoning supports the other reviewed plain fruit/vegetables and the mix containing only carrots, broccoli and sprouts. Customer recipes/serving suggestions mentioning butter, cheese or bacon were distinguished from the product actually sold.

These 40 additions form a deliberately bounded first pass, not an exhaustive review of every unknown. No category-wide automatic rule, probability-based promotion or new model calls were used. Each addition was directly reviewed by the supervising agent using stored source text, with exact quotations and context hashes retained. There were no manufacturer-evidence records or website evidence records for these 32 products. The mechanical validators check unchanged sources and audit linkage; they are not independent semantic adjudicators.

## Development checks

The userscript difference from the published source is restricted to 38 appended ingredients IDs, two appended official IDs and four generated counts. Runtime and version are unchanged. Export validates the exact baseline plus recorded reviews and rejects unreviewed changes or stale sources. Legacy bulk writers are guarded from the protected database.

The development suite includes a real temporary Firefox fixture verifying that the Kissabel apples remain unmuted and their real Add button still works. **128 tests ran: 127 passed and one optional live-page test was skipped.** JavaScript/Python syntax and the exporter dry run passed. Replacing the exported sets with the baseline sets reproduces the published source SHA-256 exactly, proving runtime and retained ID layout are unchanged. Nothing was installed in FireMonkey, tagged, pushed or published.

The new membership scan completed with **6,411 category pages and 1,202 new-product detail responses**, with zero failures. It added 1,202 products new to this database, marked 380 formerly current products historical, and restored current membership for 44 historical products. There are now **59,524 known products, 51,591 currently listed**. The 1,194 new products not included in the eight additional reviews remain unknown pending individual review. See [membership changes](catalogue-membership-changes.csv) and [completion data](completion.json).

The transactional import retained every existing product field and classification except its intended membership metadata, verified by a before/after hash. It preserved raw new-product captures and sync attribution. Existing details were retained: this was a membership/new-product refresh, not a complete recipe refresh.

One added berry product says strawberries in its name/description and blueberries in ingredients. Both describe plain berries; the discrepancy is retained explicitly in its review and does not materially affect vegan suitability. No reconciliation or product-specific manufacturing fact was invented.
