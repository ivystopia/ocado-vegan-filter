# Manufacturer confirmations for Ocado Vegan Filter 1.7.2

The release adds 1,398 manufacturer-confirmed product IDs across 186 catalogue brand groups, including the group with no stored brand. The additions include 1,324 products currently listed on Ocado and 74 historical products. It recognises 20,511 vegan IDs in total. The userscript changes are appended manufacturer/name IDs, three generated counts and the version number; executable behaviour and the other three embedded sets are unchanged.

Research ended at the user's diminishing-returns checkpoint. This is a partial research result, not an exhaustive reassessment of the initially unknown catalogue. All remaining work is retained explicitly, and insufficient or conflicting evidence leaves products unknown.

## Classification impact

| Measure | All known before | All known after | Current before | Current after |
| --- | ---: | ---: | ---: | ---: |
| Unknown vegan | 21,372 | 19,974 | 19,371 | 18,047 |
| Vegan: manufacturer | 8,025 | 9,423 | 7,449 | 8,773 |
| Vegan: official tag | 4,669 | 4,669 | 4,414 | 4,414 |
| Vegan: ingredients | 6,153 | 6,153 | 5,366 | 5,366 |
| Vegan: name | 266 | 266 | 220 | 220 |
| Non-vegan | 13,933 | 13,933 | 13,131 | 13,131 |

The catalogue still contains 54,418 known products, of which 49,951 are current. No products were removed, and no classification outside the accepted unknown cohort changed. The current unknown count fell by 6.8%.

## Coverage and evidence

The immutable starting cohort contains 21,372 unknown products from 2,366 catalogue brands, including 19,371 current products. At the checkpoint, 457 brand groups covering 12,624 products had completed research records. Additional individual conclusions exist within partially researched groups; these do not make those groups complete.

| Final recorded research outcome | Products |
| --- | ---: |
| Manufacturer candidates remaining after closing audit | 1,563 |
| No applicable confirmation, including five closing-audit exclusions | 11,678 |
| Pending research or unfinished source/product matching | 8,075 |
| Blocked by unresolved source access | 56 |

The 5,820 retained source records include successful publications, discovery material and unsuccessful retrieval responses; this is not a count of useful confirmations. Manufacturer FAQs, product pages, specifications, published vegan lists and archived manufacturer publications were retained with identity, dates and scope. Retailer descriptions and search snippets were discovery leads. Brand reputation, vegetarian/cruelty-free wording and plant materials alone were insufficient. There are no brand-specific classifier branches.

Examples of retained manufacturer publications include the [Fever-Tree FAQ](https://fever-tree.com/en-gb/faqs-contact-us), https://fever-tree.com/en-gb/faqs-contact-us; [Method UK FAQ](https://methodproducts.co.uk/about-us/faq/), https://methodproducts.co.uk/about-us/faq/; and [Ecover UK vegan policy](https://uk.ecover.com/faq/are-ecover-products-vegan/), https://uk.ecover.com/faq/are-ecover-products-vegan/. The product index records the actual source selected by each independent reviewer for every addition; a brand-level source was applied only where its scope covered the product.

Access failures and discontinued products remain visible. Historical gift contents, older formulations, wine vintages and poorly identified products often could not be established. No new Ocado product-detail scrape was used. The user's stopping decision, all product outcomes and retained sources are stored in SQLite research run 1; accepted classifications are in classification run 32.

## Independent review and closing audit

Each remaining candidate received two independent `gpt-5.6-luna` reviews at reasoning effort `high`, using prompt `manufacturer-source-scope-v2`, the complete stored product context and retained primary-source captures. Browsing was disabled during these reviews. Source URLs and exact quotations were validated, and review inputs were hashed. Reviews were reused only when their inputs matched; disagreement was not resampled to seek approval.

Both reviews accepted 1,398 products. Another 79 had disagreeing reviews and 86 were unknown in both reviews; all 165 remain unknown. The existing canonical classifier then checked every accepted product, preserving official-tag precedence and explicit animal-ingredient exclusions. No additional product was rejected at that stage.

A separate, bounded source audit checked 16 accepted cases involving formula, market or vintage scope. Five earlier candidates were conservatively withheld: Antipodes Kiwi Seed Oil Eye Cream (599866011), Antipodes Diem Water Cream (599867011), M&S Coteaux Varois rosé small bottle (512988011), Dr Organic Evoke Day Cream (645504011) and Neal's Yard Mint/Bergamot Hand Lotion (42739011). The current manufacturer formulations or grape blend did not establish the stored versions; the Dr Organic discrepancy was smaller but unresolved. These are unknown outcomes, not findings that the products are non-vegan. Their original independent reviews are preserved within the database outcome audit. The closing audit is a sample, not a claim that all source interpretation is infallible.

## Validation and release

- Repository suite: 101 tests run, 100 passed, one optional live Ocado check skipped because `OCADO_LIVE_TESTS=1` was not enabled. Headless Firefox fixture tests passed.
- Userscript syntax: `node --check ocado-vegan-filter.user.js` passed.
- Frozen classifier benchmark after the evidence/identity schema changes: 48/48 exact matches, two independent Luna/high passes. Manufacturer scope benchmark: 19/19, with zero mismatches or disagreements. Earlier frozen fixtures and results remain retained under `audit/benchmarks/`.
- Database comparison against the timestamped pre-application backup: exactly the accepted 1,398 products changed classification; all unrelated product fields, other product classifications and product counts are identical. All 21,372 cohort outcomes were retained.
- Export dry run and final comparison: 1,398 manufacturer/name IDs appended, zero removals; official, ingredient and non-vegan sets are byte-identical. Retained IDs keep their order and line placement. Only the generated counts and release version differ elsewhere.
- Published and local/remote prior release was 1.7.1 when checked. The new local release version is 1.7.2. Tagging does not publish the release. Fixture tests are distinct from the installed FireMonkey verification performed during local release preparation.

Source SHA-256: `5276d765f27fc870df5ec319fdfc63bcaa5dc88dc6ea87970c42b55e9f47a039`.

Database backup: `/home/ivy/repos/personal/ocado_report/ocado_products.sqlite.before-manufacturer-20260913T194132Z.bak`.

## Retained indexes

- [summary.json](summary.json): cohort, coverage, before/after counts, review statistics and database run identifiers.
- [promoted-products.csv](promoted-products.csv): every added ID, current status, Ocado URL, both reviewers' source URLs and input/source hashes.
- [reviewed-sources.csv](reviewed-sources.csv): the retained sources supplied for accepted products, with retrieval dates and content hashes.
- [coverage-by-brand.csv](coverage-by-brand.csv): all 2,366 brands, including pending and blocked work.
- [unaccepted-candidates.csv](unaccepted-candidates.csv): disagreeing/unknown reviews and closing-audit exclusions.
- [closing-evidence-audit.json](closing-evidence-audit.json): the bounded source audit and the parent's conservative exclusions.
- [validation.json](validation.json): test results and exact data/source comparison results.
- [release-verification.json](release-verification.json): verified signed tag `1.7.2` at `905b543`, exact installed-source match, and the running-extension verification outcome.

Full source captures, review records and unfinished discovery remain in the ignored `.manufacturer-research/2026-09-13/` workspace and the SQLite audit tables. The application used a frozen bundle; later research would require a new, explicitly resumed run.

## Installed release handoff

FireMonkey storage was backed up and updated to the exact signed 1.7.2 source. A fresh disposable Firefox profile verified the version, enabled state and full source hash; 36 unrelated storage rows were preserved. The original Firefox process remained running. This post-tag verification record does not change the tagged userscript.

The automated activation attempt could not confirm the complete inserted reload-bootstrap command, so it was not executed; the diagnostic console was cleared and closed. No extension toggle, Ocado reload or basket change was made during that attempt.

On 14 September 2026, a fresh read-only installation check confirmed that the enabled script still matched the exact tagged 1.7.2 source, with no duplicate enabled Ocado filter. Clean Firefox fixtures reproduced “Unknown vegan” for all six plain Pepsi Max pack IDs with 1.7.1 and normal Add buttons with 1.7.2. Injecting 1.7.2 on the live search page also left product 16309011 unmuted and preserved its original button, link and responsive image attributes; these were injected-script checks, not inspection of the user's active runtime.

The user then confirmed that Pepsi Max 2L showed its normal Add button after disabling FireMonkey, reloading the Ocado search page while it was disabled, re-enabling FireMonkey and hard-refreshing with `Ctrl+Shift+R`. This records a successful user check of activation and resolves the reported “Unknown vegan” label. The active runtime source was not inspected through automation. The userscript and signed tag remain unchanged.
