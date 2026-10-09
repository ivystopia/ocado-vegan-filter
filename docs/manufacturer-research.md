# Manufacturer website reassessment

This workflow reduces unknown classifications using explicit statements published by manufacturers. It is separate from Ocado catalogue scraping and the offline classifier. It covers every product that was unknown at the start, including historical products; report all-known and currently listed counts separately. Use `vegan/manufacturer` for accepted confirmations. There is no additional status, brand exception, shopping-time network call, or runtime behaviour change.

## Snapshot and research

Create a fresh, immutable cohort from read-only SQLite. The tool partitions whole brands by product count; it does not contain brand-specific rules. Local working captures are ignored by Git.

```sh
python3 tools/research_ocado_manufacturers.py --root .manufacturer-research/YYYY-MM-DD snapshot
```

Perform a cursory check for every assigned product or correctly matched product family using readily available official product pages or directly applicable FAQs/range statements, with a straightforward lookup when needed. Stored manufacturer addresses and URLs can help identify the right publisher. Do not deep-crawl websites, exhaust their catalogues or prolong difficult sourcing investigations: the user accepts vegan products remaining unknown when confirmation is not readily established. Search results are discovery leads, not positive evidence. Keep actual source text with page identity and all relevant scope qualifications and exclusions. Use direct retrieval for static pages and the browser skill when rendering is needed. Do not contact manufacturers or other people unless explicitly requested.

For Ocado own-label products, Ocado manufacturer policy, technical and household publications can be primary evidence. This does not require refreshing the live Ocado catalogue or rescraping its product pages.

A confirmation must cover the complete product and applicable market, flavour, format, range, date/vintage or recipe. Do not extrapolate from a vegan item to a brand, from current reformulations to unmatched older variants, or from cruelty-free/vegetarian claims and plant materials to vegan suitability. A catalogue brand field can itself be wrong: check the actual product identity. No confirmation leaves the product unknown. Retain explicit contrary statements for review without silently changing the non-vegan export in a vegan-additions-only task.

The product brand's verified official website is a primary manufacturer source. The catalogue may name a parent company, licensee, distributor, legal entity or contract factory, or omit that field; that naming difference alone does not invalidate the brand's own statement. Verify that the publication represents the actual product brand, rather than an unrelated brand accidentally assigned in the catalogue.

Each partition has `catalogue.json`, `sources/` and `results/`. Save per-brand JSON results with the exact `brand`, `checked_at`, `research_complete`, actual `queries`, `sources`, `products` and `unresolved_work`. Split large brands into disjoint result files if useful; each product ID must occur exactly once. A source records `url`, `title`, `retrieved_at`, `official_identity`, `market_scope`, `source_text_path` and scope/access `notes`. `source_text_path` points to a UTF-8 plain-text capture, with enough context to assess the claim and exceptions; retain larger raw files separately. Each product records `product_id`, `outcome`, `source_urls`, a short exact `quote`, `scope_match` and `notes`. Valid research outcomes are `confirmed_vegan` (a candidate), `no_confirmation`, `blocked` and `pending`. Missing work is automatically pending. A completed cursory check without usable confirmation leaves the product unknown. A bounded check closed because its source is inaccessible remains explicitly `blocked` with the attempted access and closure reason; it is accounted for but does not establish absence of confirmation. Unattempted work and unreviewed search results remain pending. Never manufacture a quotation for a negative or blocked outcome.

```sh
python3 tools/research_ocado_manufacturers.py --root .manufacturer-research/YYYY-MM-DD status
```

## Independent review and import

Run the frozen classifier benchmark after classifier/schema changes, as required by the monthly runbook. The manufacturer reviewer additionally checks publisher identity, UK applicability and exact claim-to-product scope against the retained text. The full-refresh coordinator imports complete raw captures and applies the normal two-pass Luna 6/high classifier with disagreement-only Sol 6.1/medium arbitration. Browsing is confined to source gathering. For a separately requested positive-only import, pass `--model gpt-6-luna --reasoning-effort high` explicitly to the reviewer. Every positive review must cite a supplied source and an exact quote found in that capture. Disagreement leaves the product unknown. Candidate notes are untrusted hypotheses, not proof. Reviews are hashed against the prompt, product and captured sources; changed inputs must be reviewed again. Review can run during research and resumes using matching saved reviews.

Quote validation removes retrieval line markers and link/formatting decoration while preserving the source's words and qualifications. It never accepts a paraphrase or drops a negation. Incomplete or invalid model output is retained for inspection; individually valid decisions are kept and missing/invalid products receive up to two further attempts within that pass. Every accepted product still needs its own valid decision from each of the two independent passes. Unresolved execution or validation failures remain errors, never implicit unknown decisions or vegan additions.

The retained `benchmarks/manufacturer-source-scope-v2.json` additionally exercises fictional range exclusions, wrong brands/markets/vintages, reformulations, retailer claims, misleading serving suggestions, animal-ingredient conflicts, untrusted page instructions and official-brand versus legal-company identity. Preserve frozen inputs, hashes and expected labels together. Run it before applying a changed source-review prompt or schema, and investigate mismatches or disagreements.

```sh
python3 tools/research_ocado_manufacturers.py --root .manufacturer-research/YYYY-MM-DD benchmark --output audit/benchmarks/YYYY-MM-DD-manufacturer-source-scope-luna-high.json
```

```sh
python3 tools/research_ocado_manufacturers.py --root .manufacturer-research/YYYY-MM-DD review --model gpt-6-luna --reasoning-effort high --workers 4 --batch-size 8
python3 tools/research_ocado_manufacturers.py --root .manufacturer-research/YYYY-MM-DD apply --dry-run
```

The import requires complete product accounting and valid independent reviews for every positive candidate. The authorised cursory search may close an attempted access-blocked check while retaining its blocked outcome and unknown classification; it must not conceal unattempted products or classifier execution errors. Tooling that still requires exhaustive source resolution needs an explicit recorded bounded-research completion policy, not relabelling blocked outcomes as no-confirmation. For separately requested partial imports, pass `--partial-reason` only when the user explicitly ends the remaining product checks, recording that instruction in both the dry run and application. Source validation, independent review and classification checks remain strict.

The dry run quantifies the all-known/current additions and reports candidates rejected by canonical rules. Explain that impact and the evidence supporting the increase before applying. The shared whole-product policy applies: correctly matched animal ingredients override an official tag or positive claim to nonvegan; identity and other material conflicts block promotion. Imports also require the current policy migration/export gate. The importer rechecks the current product classification and catalogue context, including pack size, so concurrent changes cannot be overwritten silently.

```sh
python3 tools/research_ocado_manufacturers.py --root .manufacturer-research/YYYY-MM-DD apply
```

The import makes a timestamped SQLite backup using SQLite's backup API, retains all research outcomes and captured sources in `manufacturer_research_*` / `manufacturer_website_*` tables, and writes canonical decisions and classification audit rows transactionally. Only independently accepted unknown products become `vegan/manufacturer`. The website evidence is stored separately from the Ocado manufacturer-text table, so a subsequent Ocado detail refresh cannot delete the research. Each accepted statement is bound to the reviewed catalogue evidence, pack format and publisher identity fields; a change makes the old claim unavailable for classification while preserving its history. Revisit source statements during subsequent manufacturer audits, especially when formulations or publisher policies change.

## Userscript deliverable

Use `tools/update_userscript_allowlists.py --dry-run`, then export using the same tool. In a manufacturer-only reassessment, verify that only the manufacturer/name set and generated counts change. The official, ingredients and non-vegan sets, metadata version and executable code must be identical. Existing retained IDs keep their relative order and placement; only new confirmed IDs are appended. Run the repository suite and `node --check`; record skips and the fact that fixture injection is separate from installed FireMonkey verification. A data update alone does not authorize a tag, push, publication or installed-script update.

Retain a dated report under `audit/manufacturer-research/` with total cohort accounting, all-known/current additions, remaining unknown counts, source URLs, qualifications and excluded/conflicting examples. Describe completion as bounded cursory checks, reporting closed access-blocked products separately; do not claim exhaustive discovery of manufacturer statements. Keep promoted product IDs, source references and decision explanations reviewable. Every product must have a recorded completed check or explicit closed access-blocked outcome; pending work remains unfinished. Commit finalized tooling/evidence and the userscript data change as separate logical commits.
