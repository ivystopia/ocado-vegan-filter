# Whole-product vegan classification

The classifier assesses stored evidence for the complete sold article, including relevant manufacturing and supplied packaging, pods, filters and other non-edible components. Development is on `dev`. Canonical catalogue reassessment is in progress; activation and local userscript regeneration require the policy, research, audit and export checks below. Keep the development version until release preparation is requested.

## Evidence policy

Statuses are `vegan`, `nonvegan` and `unknown`. `unknown` is an assessed insufficiency of evidence; NULL is unfinished work. A failed execution or invalid response stays operational work, never an implicit unknown.

Permitted positive reasons, in canonical order, are an applicable Ocado tag (`tagged`), scoped manufacturer confirmation (`manufacturer`), an affirmative whole-product vegan name (`name`), and unambiguous vegan identity/composition with relevant sourcing and processing resolved (`ingredients`). Manufacturer confirmation takes precedence over a name. Never guess from probability, reputation, a category or absence of an animal word.

Correctly matched definite animal ingredients or processing settle `nonvegan`, even with a positive claim or official tag. Unrelated generic uncertainties cannot downgrade this proof to unknown. Retain official-tag/animal contradictions for reporting; do not contact Ocado without instructions. Suspected wrong-product joins, unresolved claim applicability and other material conflicts remain blocked `unknown`.

Applicable whole-product claims can resolve generic composition, sourcing, processing and component uncertainty. They cannot override definite animal evidence or establish their own product/variant/market/period binding. Without such assurance, unresolved source-dependent additives, incomplete composition or relevant process risks mean unknown. General knowledge identifies mechanisms and ordinary identities; it cannot invent product-specific assurances.

Ordinary foods such as carrots and a simple leaf salad can qualify by common sense. Missing ingredients do not prove simplicity. Missing packaging details alone do not block an unambiguous ordinary food when no material component concern is evidenced; do not invent packaging or claim it is absent. Explicitly described manufactured components require assessment. Protective-atmosphere wording alone establishes neither a component concern nor packaging suitability.

“May contain milk” is precautionary information rather than intentional milk. Keep mixed allergy/factory fields intact for interpretation. Standard wheat/flour fortification with calcium, iron, niacin, thiamin or folic acid is permitted; other vitamin sources need applicable evidence. Resolved source wording such as soya lecithin resolves that ingredient alone. Known animal additives include E120, E901, E904 and E1105; the source-backed registry in `tools/vegan_policy.py` is bounded, not proof that unlisted additives are safe.

The registry covers relevant wine fining and other wine processing, source-dependent additives, sugar refining, clarification/defoaming and planting-seed treatments. “Unfined” or animal-free fining agents alone do not establish the wine's complete production chain. Plant origin, filtering or distillation alone does not prove animal-free precursor processing. Apply mechanisms case by case, including relevant constituents, without speculative universal risks.

## Claims and scope

Name matching identifies candidates. Name-only candidates enter the independent model path for polarity, whole-article scope and higher-precedence evidence. A normal affirmative vegan SKU name covers the named article; a food noun or ordinary pack suffix alone does not restrict it to edible contents. Negation, probabilistic names, brand wording, component/formula claims, other variants and serving suggestions cannot establish whole-product suitability. “Not certified vegan” is absence of certification, not animal proof.

An affirmative producer self-description or a typed whole-product Vegan feature can support manufacturer evidence without the exact phrase “suitable for vegans”. A brand's name containing Vegan cannot supply this attribute. A quoted title or a name mentioned without an assertion remains name evidence only. Recipe/formula statements do not restrict an independent complete-product assurance unless they actually qualify that assurance. Nutrition figures per serving do not themselves restrict a separate whole-product claim.

Read every cited assertion in its full retained sentence/capture. Reject clipped negations, hedges, component subjects and exceptions. A FAQ answer inherits its own question's subject; a packaging answer or a later question's answer cannot assure the food. Metadata and URL titles cannot supply assertions. Both model stages inspect and cite applicable positive routes before choosing a reason. Validate alternative supplied proof in canonical order; rejecting one basis cannot erase independently sufficient lower-priority proof. Agreed unknowns never promote.

An unqualified assurance for the matched named product need not enumerate ordinary pack sizes. Different formulations, flavours, markets, vintages, explicit material restrictions or selector mismatches remain consequential. Supplied Ocado/UK provenance establishes the sale market, not manufacturer origin or equivalence of international formulations.

## Pipeline and models

Conclusive rules run first. Interpretively difficult scope, identity, composition and potentially applicable product-information claims enter the model queue. A rule's failure to recognize a claim is not proof of its absence. Genuinely unresolved source facts may finish unknown without a model call. The shared final policy gate applies to every decision-writing path, including manufacturer imports.

Use exactly two fresh independent `gpt-6-luna` primary passes at reasoning effort `high`. Only raw status/reason disagreement triggers exactly two fresh blind `gpt-6.1-sol` passes at `medium` on the same evidence, without primary conclusions. Arbitration supports all four positive routes, has no recursion, and leaves unresolved disagreement unknown. Agreed primary unknowns do not escalate. Substantive issues from either agreeing pass survive merging. Exclude GPT-5.6-Luna. The supervising coding model does not change these defaults.

`tools/codex_evidence.py` uses fresh temporary evidence-only sessions, disabled web/tools/skills/config/rules and a read-only sandbox. API-key environment variables are removed; use the existing ChatGPT sign-in. Observed tool use invalidates a call. Validate isolation against the installed CLI: a prompt alone is not access control.

Validate exact IDs, schema, status/reason combinations, source applicability, pointers and quotations locally. Reject missing, duplicate and unexpected products, fabricated references and unsupported claims. Each product in a batch uses only its own evidence and linked captures; shared source keys or repeated names do not invent other variants or conflicts. Retain every failed/retried attempt and its usage. Reasoning tokens are already output tokens. Included-plan limits stop admissions, drain calls and checkpoint; no paid/API fallback, allowance reset or automatic expensive-primary upgrade.

## Provenance and source lifecycle

Canonical fields retain policy/classifier versions, per-product evidence hash, promotion block and audit ID. Runs/audits record knowledge/schema/prompt versions. `vegan_model_calls` retains each call once; `vegan_audit_model_calls` links product audits. Failed calls cannot replace accepted canonical decisions or clear pending work.

Manufacturer captures retain complete text/hash, URL, retrieval date, publisher/market identity and exact product association. Supply all associated captures, including contrary pages and unused positive quotations; exclude old research conclusions. Source assertions retain active/revoked/superseded state and audit history. Withdrawal invalidates dependent decisions. Newer retrieval alone does not override a more specific contrary statement. Catalogue identity/recipe/pack/publisher changes invalidate applicability.

Schema 5 assesses identity, composition, sourcing, processing and supplied components, plus website product/variant/market/period applicability. Every relied-on source needs its own source-backed dimensions and product/source identity binding in both passes. Rebind persisted pointers by URL and complete capture hash. Catalogue bindings exclude external captures to prevent circular invalidation. Exact visible quotations may remove retrieval/Markdown decoration, never words, negations or qualifications. Full-capture validation remains strict; structured references do not mathematically prove interpretation or undisclosed production facts.

## Assessment, validation and activation

Evaluate products individually. There are no category or positive-reason sample admission gates. Reuse completed benchmarks for the unchanged classifier after scraping; do not repeat model qualification for this catalogue refresh. Genuine model/prompt/schema/rule changes require appropriate new validation. The current frozen fixture is `benchmarks/vegan-classifier-v2/ethical-cases-v29.json`; it contains 497 controlled contracts. `audit/current-classifier/` retains applicable control/pilot evidence. Existing classifications or model agreement are not independent truth.

Future benchmark evaluation retains every mismatch, zero unsafe positives and strict evidence/reason/animal-content/operational/persistence/export/browser contracts. Conservative vegan-to-unknown misses consume the 98% positive-recall budget and proportional per-route recall checks. Report coverage and every unrun or disputed case; never change reasons or expectations to improve a score. Separate family/shared-source groups between development and holdout. Metamorphic siblings and repetitions are not independent catalogue observations; a consumed holdout cannot support an untouched-holdout claim.

For this fundamental migration, `tools/refresh_whole_product.py` works on WAL-consistent snapshots, preserves previous decisions and reassesses every known product, including historical nonvegan/unknown decisions. Sync-run attribution must not narrow all-known selection. Routine refreshes classify new/changed products. Freeze every initially unknown product for fresh exhaustive manufacturer research, including historical products, and reassess all products affected by changed shared sources. Preserve negative findings, withdrawals and explicit pending/blocked coverage.

Require no unclassified products, resolution of operational errors, complete research accounting, current provenance and valid export. Audit a uniform 600-positive sample of the final fixed candidate with two blind source-cited Sol 6.1/high readings, validated quotations/applicability and explicit dispute accounting. Do not describe a zero-error automated sample as an unconditional physical vegan guarantee. Produce all-known/current transitions, promotion/demotion explanations and separate confirmed tag/animal contradictions from identity-review cases.

Activation is authorised after these checks. Reject concurrent canonical/candidate changes; transactionally preserve prior decisions and source/audit linkage. Run exporter dry-run before regenerating the local userscript. Rollback must retain newly established safety exclusions. Commit finalized tooling, evidence and script changes separately on `dev`; no tag, main advancement, push, publication or installed FireMonkey update.

## Export and shopper behaviour

Exporter validation rejects stale/legacy positives, missing audit linkage, changed evidence and unfinished syncs. Export all known classified IDs, including historical products, into separate positive-reason sets, nonvegan IDs and blocked unknown IDs. Preserve retained ID order/placement and update counts.

Browser precedence is recorded nonvegan, blocked unknown, validated positive IDs/applicable live official tags, then unknown. Runtime names and URL slugs cannot promote. Evidence-absent ordinary unknowns may benefit from a new live tag; recorded material conflicts cannot. Keep `Unknown vegan` for uncertainty and `Not vegan` only for affirmative negatives. Preserve native Add buttons, links, responsive images, quantity controls, incremental processing and self-contained shopping behaviour.
