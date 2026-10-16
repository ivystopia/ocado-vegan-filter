# Completed whole-product catalogue refresh

The canonical database and repo userscript are ready for local testing on `dev`, at unchanged development version `1.7.2`. No tag, push, main advancement, publication or installed FireMonkey update was made.

## Catalogue decisions

| Status | All known | Currently listed |
| --- | ---: | ---: |
| vegan | 12,400 | 11,275 |
| nonvegan | 14,379 | 13,201 |
| unknown | 31,543 | 26,249 |

All 58,322 known products were reassessed, including 7,597 historical products. No products remain unclassified. Unknown is a completed conservative assessment.

| Positive reason | All known | Currently listed |
| --- | ---: | ---: |
| ingredients | 167 | 149 |
| manufacturer | 6,026 | 5,278 |
| name | 22 | 19 |
| tagged | 6,185 | 5,829 |

The final comparison with the preserved pre-refresh catalogue records 687 vegan promotions and 8,798 withdrawals of old vegan classifications. Product-level explanations and original audit links remain local, bound by `product-explanation-provenance.json`.

The policy assesses the complete sold product, relevant processing and supplied components. Applicable official tags, manufacturer confirmation, affirmative whole-product names and unambiguous ordinary vegan identity/composition remain permitted positive routes. Manufacturer confirmation takes precedence over names. Correctly matched definite animal content overrides a positive claim. Only discrepancies materially affecting vegan suitability or claim applicability block a positive; ordinary recipe/style/count differences and generic packaging details alone do not invent a concern.

## Scraping and manufacturer checks

The scrape completed 4,981 category leaves, 6,353 pages and 50,725 current detail refreshes, with zero fetch failures. A post-acquisition SQL finalization issue was recovered from retained data without refetching. Historical category evidence was restored before assessment.

All 31,624 initially unknown products are individually accounted for: 3,572 had source captures collected, 27,267 completed bounded checks without applicable confirmation, and 785 had explicitly closed access-blocked checks. There are zero pending checks. The 1,672 complete source captures and scope restrictions remain retained locally. Source collection does not itself approve a claim, and access failure does not prove that no confirmation exists. These were cursory official-source checks, not exhaustive supply-chain research.

## Validation actually performed

- Production uses two independent GPT-6-Luna/high primary readings. Raw status/reason disagreements receive exactly two fresh blind GPT-6.1-Sol/medium readings; agreed unknowns do not escalate. Current accepted decisions exclude GPT-5.6-Luna.
- Three retained 497-case controlled repetitions pass the final gate: 494 exact matches, 193/196 positive recall (98.47%), zero unsafe positives or operational errors in each repetition. Three retained 96-case natural repetitions remain exact. Original contexts, expectations, scores and calls are unchanged; these are offline gate replays, not new model benchmark runs.
- Materiality diagnostics include eight products with fresh paired primary checks and 24 source-backed cases with two fresh blind reference readings. The subsequent gate replay recovered seven copy-only withheld positives and sent 66 old rule-only shortcuts for normal fresh classification (18 primary calls, zero operational errors). Other valid completed model evidence was preserved.
- All 600 uniformly sampled original positives passed both blind source-cited suitability readings. The strict score initially failed solely on Pulsin's name-versus-manufacturer attribution. With explicit user approval, that reason was separately corrected; the original failed score and corrected 600 score remain preserved.
- Post-export fixtures exposed a real false negative: a salmon-flavour seasoning heading and a truncated qualifier were treated as definite animal content in one historical Squeaky Bean product. The bounded source-aware repair preserves nested/adjacent real animal ingredients. Full-catalogue impact checks found exactly one decision affected. Two fresh primary readings and two fresh blind source-cited references support `vegan/manufacturer` for it; the original nonvegan audit remains preserved. Other labels and evidence were unchanged.
- The user approved retaining the original 600 readings and auditing every added positive. The final audit therefore covers 601 products: a uniform 600 sample from the original 12,399 positives plus a census of the one added positive. It is not a new uniform sample of 601. No unsupported positive, wrong final reason or reference dispute remains in the accepted audit.
- All 609 unittest checks pass, including isolated headless Firefox fixtures, plus JavaScript syntax and diff checks. One opt-in live Ocado test was skipped. Browser fixtures now inject explicit test-only sets; production sets independently pass disjointness, generated counts and exporter policy checks. This is not installed FireMonkey verification.
- Transactional activation preserved prior decisions and audit/source linkage. The approved one-product correction used a guarded delta transaction rather than copying the database again. Exporter dry runs preceded regeneration, and retained ID placement was preserved. Recorded nonvegan and blocked unknown IDs take precedence over live tags.

The conditional reference-policy error upper bound is approximately 0.498% at 95% confidence, weighting the original-stratum bound and the fully audited added stratum. It is not the binomial interval for 601 independent random products. Shared reference/model errors and undisclosed manufacturing facts are outside that calculation; this is not an unconditional physical guarantee or independent human certification.

## Ocado reporting and retained evidence

The separate JSON/CSV contains 28 confirmed tag/animal-content contradictions. The 31 identity-review cases and seven component-review cases are separate; they are not confirmed animal-content findings. No report was sent to Ocado.

The manifest, audit scores, source-qualification proof, model/reference readings, replay reports and executable check log bind the completed result. Complete database audits, source captures, call journals, snapshots and original failed diagnostics stay local on the main NVMe. Closed database archives have verified byte hashes and restore instructions. The userscript remains self-contained while shopping.
