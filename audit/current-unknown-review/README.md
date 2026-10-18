# Complete current-Unknown review — 18 October 2026

**Finished, applied and exported locally on `dev`.** Every one of the 20,931 products that was current and Unknown at the start received fresh readings. The update adds 3,593 vegan products and leaves 17,338 Unknown. It preserves every published 1.7.2 classification, its evidence-group membership, and the earlier 40 reviewed produce additions. No tag, push, publication, main advancement or installed FireMonkey update was performed.

## Results

| Measure | Count |
| --- | ---: |
| Frozen current-Unknown products | 20,931 |
| Products fully reviewed | 20,931 |
| Unfinished products or calls | 0 |
| Fresh independent readings with validated provenance | 44,870 |
| Products receiving disagreement arbitration | 1,504 |
| Model-positive candidates | 3,719 |
| Source-bound or unsupported-reason promotion holds | 126 |
| Vegan additions applied | 3,593 |
| Reviewed products kept canonically Unknown | 17,338 |

The additions comprise 141 `tagged`, 752 `manufacturer`, three `name` and 2,697 `ingredients` decisions. The latter include unambiguous ordinary foods and materials; absence of an animal word alone is not the positive basis. Manufacturer precedence over name is retained.

| Canonical status | All known before | All known after | Current before | Current after |
| --- | ---: | ---: | ---: | ---: |
| Vegan | 20,551 | 24,144 | 18,071 | 21,664 |
| Unknown | 25,040 | 21,447 | 20,931 | 17,338 |
| Nonvegan | 13,933 | 13,933 | 12,589 | 12,589 |
| Total | 59,524 | 59,524 | 51,591 | 51,591 |

This is an additions-only operation. Raw model outcomes were 3,719 vegan, 15,639 unknown and 1,573 nonvegan. The latter remain canonical Unknown in this scoped update, with their findings retained in the review ledger. Model conclusions are not independent ground truth. The protected published population was not reclassified or independently revalidated.

## What “fresh review” means

Every frozen product was assessed by two fresh, independent GPT-6-Luna/high readings using its stored SQLite identity, product text, ingredients, flags, categories and relevant retained declarations. Previous classifications and expected answers were excluded. Raw status/reason disagreement received exactly two fresh blind GPT-6.1-Sol/medium readings of the same source evidence. Agreed Unknowns did not escalate. There was no category or reason quota and no candidate-only admission filter.

The classifier used isolated, tool-free included-plan Codex calls. Every attempt was retained and metered; final reconciliation found no unaccounted calls or missing usage. Bounded recovery retained valid readings and retried only unfinished protocol/source-reference cases. Narrow reference repairs preserve raw responses; they do not change suitability conclusions or quoted words. All completed readings remained unchanged after the final repairs.

The preceding membership refresh discovered new and removed products and fetched new product details. This review read every current Unknown afresh from the retained database; it did not refetch every existing product page or perform exhaustive manufacturer-site research. Neither model agreement nor quoted-source validation establishes otherwise unobservable manufacturing facts.

## Conservative holds and tag conflicts

[Promotion holds](promotion-exclusions.json) retain product-specific source quotations, hashes and explanations. They include unresolved sugar or additive/vitamin sourcing, a lager without process assurance, a component-scoped candle claim and unsupported positive attribution. The original model readings remain unchanged. No hold demotes a published positive.

[The separately inspected tag cases](tag-conflict-review.json) distinguish two different findings in the stored evidence:

- [M&S Percy Pig Advent Calendar Sweets](https://www.ocado.com/products/527870011): ingredients, description and box contents consistently include milk chocolate, including dried whole milk, alongside a vegan tag.
- [Skittles and Friends Christmas Selection Box](https://www.ocado.com/products/563256011): the description and allergen fields mention chocolate products and milk/eggs, while the ingredients and box contents describe a different sweets assortment. This requires identity/composition checking; it is not a confirmed nonvegan product from these conflicting records alone.

Both were excluded from additions. No report was sent to Ocado, and these findings were not freshly verified on live product pages. **The retained published runtime can still accept a live Ocado vegan tag independently of the embedded lists.** This existing behaviour was preserved as requested; omitting these IDs from additions does not repair that runtime limitation. The raw `ocado-tag-contradictions.csv` is a model-finding list; use the separately inspected JSON for the confirmed-declaration versus conflicting-record distinction.

## Activation and verification

Classification run **313** applied only the 3,593 approved Unknown-to-vegan additions in one transaction. The writer checked the complete cohort, every frozen source, protected classifications and raw-call accounting before writing. A verified compressed logical backup preserves every replaced classification field. Existing classification audits are retained, new positive audits use source `codex`, and `current_unknown_reviews` records all 20,931 outcomes with their cohort and source hashes.

The exporter passed its dry run before generation. Every existing ID remains in the same set and on its retained line; the nonvegan set is byte-for-byte unchanged. The script runtime is unchanged, version remains **1.7.2**, and only accepted IDs and generated counts changed. A subsequent dry run reports zero additions/removals in every set. Canonical baseline validation and `node --check` pass.

The final generated-script suite ran **152 tests: 151 passed, one optional live Ocado check skipped**. Temporary headless Firefox fixtures passed. This is not installed FireMonkey verification; the installed copy was not changed. See [verification.json](verification.json) for counts, hashes and preservation checks.

## Evidence map and recovery

- [added-vegan-products.csv](added-vegan-products.csv): the 3,593 actual additions, with names, reasons, URLs, rationales and source hashes.
- [review-outcomes.csv](review-outcomes.csv): all 20,931 model/final outcomes, arbitration flags and holds.
- [reviews.json](reviews.json): complete selected source contexts and supporting quotations for accepted additions.
- [outcomes.json.gz](outcomes.json.gz): all primary/arbitration readings, final outcomes, call references and holds.
- [accounting.json](accounting.json): exact coverage, reading provenance, attempts and token usage by model. `vegan-candidates.csv` contains pre-hold candidates, not the applied list.
- [application.json](application.json): transaction result, source/code/model bindings, operational repairs and verified pre-activation backup receipt. `preview.json` records the validated proposal.
- [raw-evidence-receipt.json](raw-evidence-receipt.json): verified local archive of frozen inputs, complete raw attempt journals and operational logs. The archive stays on the main NVMe and is not committed.

The local work root is `.rollback-work/current-unknown-review-2026-10-18/`; its `completion.json` and final `progress.json` confirm completion. Historical entries in `batch-errors.json` are resolved, not a queue to rerun. Do not restart classification or repeat this review when resuming the conversation. The source-bound application is already complete and refuses reapplication; future refresh work should preserve unchanged decisions and evidence.
