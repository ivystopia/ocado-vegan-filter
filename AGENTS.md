# Ocado Report Instructions

These instructions apply to this repository.

## Project Purpose

This repository supports an Ocado vegan-labelling audit and the `Ocado Vegan Filter` userscript.

The project has two related goals:

- Maintain a local product database for analysis of Ocado catalogue data.
- Maintain a self-contained userscript that helps vegan shoppers by treating products as vegan only when the local evidence supports that decision.

## Start Here

- Inspect `git status --short --branch` and recent commits before editing; preserve any existing user changes.
- Read [the working guide](docs/working-guide.md) for the code map, environment, safe inspection commands, and recurring failure modes.
- For catalogue refreshes, use [monthly maintenance](docs/monthly-maintenance.md); for classifier changes, use [the classifier design](docs/vegan-classifier-design.md).
- For browser or performance changes, read the Firefox guidance in `docs/working-guide.md` and relevant tests in `tests/test_ocado_vegan_filter.py` before changing the script.
- Keep current guidance and applicable verification material in the working tree; use Git history for superseded designs, fixture versions and reports. Preserve operational database audits, backups and active resumable checkpoints.

## Source Of Truth

- Use `ocado_products.sqlite` as the source of truth for product analysis.
- Do not use the JSONL scrape files for new analysis unless the user explicitly asks for raw scrape archaeology.
- Treat JSONL files as historical import/raw data only.
- If new live scraping is performed, import the result into SQLite and keep enough raw/pre-check data in SQLite or clearly named raw files so future analysis does not require re-hitting Ocado unnecessarily.
- When reporting product counts, distinguish all known products from `current_on_ocado = 1` products.
- Open SQLite read-only for inspection. The classifier CLI's `status` command calls schema migration code, so use the working guide's SQL for read-only status checks.
- `tools/build_ocado_database.py` is a historical importer whose CLI deletes its output database and sidecars before rebuilding. Its `create_schema()` helper drops tables. Use an isolated target for reconstruction/tests; never run either against the source-of-truth database as a setup or inspection step.

## Vegan Classification Rules

- Retain positive classifications from official Ocado tags, explicit manufacturer statements, clearly affirmative vegan product names, and unambiguous vegan product identity/composition. Ordinary foods such as a basic bag of carrots or a bagged salad containing only unambiguously vegan ingredients can qualify without an explicit vegan claim. Retain all four vegan reasons.
- Assess suitability for the complete product, including relevant manufacturing processes, not just the absence of animal ingredients. If product-specific doubt remains about processing (for example wine fining or sugar refining), ingredient sourcing, an unspecified source-dependent E number, composition completeness, or conflicting evidence, classify unknown unless the supplied evidence resolves it. General knowledge may identify such risks and ordinary vegan food identities; it must not invent product-specific sourcing or manufacturing assurances.
- Assess the complete sold product, including packaging, coffee pods, brewing filters and other supplied non-edible components. This script serves ethical vegans, not only a vegan diet. A food-only assurance does not resolve a material question about another component. Keep ordinary-food/common-sense positives where the complete product is unambiguous; do not invent unsupported component or supply-chain risks.
- Plain produce with no packaging details may qualify from unambiguous identity when no material component concern is evidenced. Missing packaging details alone are not a blocker; packaging remains within scope. Do not claim unknown packaging is absent or invent its materials.
- Use `gpt-6-luna` at reasoning effort `high` for primary classification and `gpt-6.1-sol` at `medium` for disagreement arbitration. Exclude GPT-5.6-Luna. Never guess or alter the bounded independent-pass contract.
- Only block discrepancies that materially affect vegan suitability or whether the relied-on vegan claim applies. Plant-only recipe differences, ingredient counts, beer styles, generic pack formats and unrelated cosmetic claims do not alone defeat an independently applicable whole-product vegan assurance. Preserve the discrepancy for audit; never invent a reconciliation. Concrete animal use, unresolved sourcing/processing/components and genuine wrong-product, variant, market or time restrictions remain strict.
- Vegan classification must prefer false negatives over false positives.
- Do not tag a product as vegan from probability, brand reputation, category assumptions, or "usually vegan" reasoning.
- Valid product statuses are `vegan`, `nonvegan`, and `unknown`.
- Valid vegan reasons are `tagged`, `manufacturer`, `ingredients`, and `name`.
- When several positive routes apply, keep canonical reason precedence `tagged`, `manufacturer`, `name`, `ingredients`; manufacturer confirmation takes precedence over a vegan product name.
- `unknown` means assessed, but not enough database evidence exists to classify safely. Keep the shopper-facing `Unknown vegan` label; the user declined `Probably vegan` for now, and `Almost vegan` is not an acceptable output.
- `NULL` or missing status means unclassified and should generally be treated as work remaining.
- May-contain allergen warnings do not make a product non-vegan.
- A missing ingredients field does not itself prove a single-ingredient product. Classify from stored identity/category text only when it establishes an unambiguous ordinary vegan food and leaves no unresolved composition or relevant processing uncertainty.
- Ocado's official vegan tag supplies explicit positive evidence, but correctly matched definite animal ingredients override it to `nonvegan`; collect tag/animal contradictions for reporting to Ocado. Suspected wrong-product identity and other unresolved material conflicts remain `unknown` with a promotion block. Do not contact Ocado without explicit instructions.
- Regardless of an official tag or positive claim, correctly matched explicit animal-derived ingredients such as milk, egg, honey, gelatine, meat, fish, shellfish, beeswax, shellac, carmine, lanolin, or similar should classify as non-vegan.
- Ingredients with ambiguous sourcing should classify as unknown unless the product text explicitly resolves the source.
- Fortified wheat/flour should be treated as vegan when the fortification is limited to standard flour additions such as calcium, iron, niacin, thiamin, or folic acid.
- Vitamins outside standard flour fortification should remain unknown unless the evidence explicitly proves the sources are vegan.
- Safe parser improvements should be source-aware, for example allowing `soya lecithin` while keeping bare `lecithin` unknown.

## Classifier Workflow

- Prioritise the vegan shopper: trustworthy labels and usable Ocado controls are the outcome. Preserve completed, valid model decisions and research. Apply attributed repairs to affected evidence and reuse unchanged model calls; do not restart broad model classification or benchmarking for transport, metadata or unrelated changes. Genuine fundamental policy migrations still reassess all known products, and final safety/export gates remain mandatory. Use eight bounded classification workers with the same independent-pass and included-plan admission guards; annotation/audit concurrency remains separately bounded.

- The classifier must use only data already stored in SQLite unless the task is explicitly to scrape/import new data.
- Manufacturer-site reassessment is a separate evidence-gathering workflow: research every initially unknown product, retain the manufacturer's published statement, URL, retrieval date, market/range restrictions, and exact product match, then import accepted evidence into SQLite as `vegan/manufacturer`.
- Keep manufacturer research resumable and account for every initially unknown product, including historical products. Use a cursory check of readily available official product pages or directly applicable FAQs/range statements, with a straightforward lookup when needed; do not deep-crawl sites or investigate difficult supply chains to rescue unknowns. The user accepts conservative misses from this bounded search. An unsuccessful completed check leaves the product unknown. Retain checked-without-confirmation, access-blocked and not-yet-attempted outcomes separately; a closed access-blocked check is accounted for, not a claim that the manufacturer publishes no confirmation. Shared evidence may cover correctly matched families, but a few illustrative brands cannot substitute for individual accounting. Positive evidence, contrary evidence and scope validation remain strict.
- For manufacturer-only userscript updates, change the embedded vegan IDs and generated counts only; preserve runtime behaviour, existing classifications outside the reviewed unknown cohort, and the non-vegan set.
- Follow `docs/vegan-classifier-design.md` for end-to-end classifier behaviour.
- Use the user-selected `gpt-6-luna/high` primary with two independent passes and `gpt-6.1-sol/medium` disagreement arbitration. The user accepts a few conservative misses: reference-vegan products returned as `unknown` count against the existing 98% positive-recall target and per-route recall gates, including synthetic positive controls. Unsafe vegan promotions, incorrect nonvegan/animal-content decisions, unsupported positive reasons, invalid evidence, operational failures and audit/export/browser/reporting contracts remain strict. Keep every mismatch visible and version the acceptance rubric; do not rewrite old reports or reference labels. The full scrape, all-known reassessment, manufacturer refresh and positive audit are authorised. Activation of validated database decisions and regeneration of the local userscript are authorised once every gate passes; no further model-choice or activation question is needed.
- The supervising Codex model and bulk classifier model are separate choices. A change of supervising model does not change the benchmarked classifier default.
- Treat positive official tags, manufacturer statements and vegan names as provisional bases until the selected two independent primary readings check complete-product applicability and consistency. Do not let a regex, empty conflict list or read-only rule candidate bypass those readings. Keep deterministic conclusive animal evidence, known blockers and unambiguous ordinary composition. Retain the four reasons and manufacturer precedence; raw disagreement still receives exactly two fresh blind arbitration passes, and agreed unknowns never escalate.
- Pass the model and reasoning effort separately; `gpt-6-luna/high` is not a valid model ID.
- Do not use Luna `low` or `medium` for product decisions: the selected effort is high.
- Validate genuine model, prompt, schema or rule changes using the current whole-product benchmark. Reuse completed evidence for an unchanged classifier after scraping; a scrape alone does not require another model benchmark.
- Keep the benchmark's frozen contexts, hashes, and expected labels together. Investigate mismatches; do not refresh inputs from today's catalogue or change expected labels merely to obtain a pass.
- For monthly maintenance, follow `docs/monthly-maintenance.md` and use the DB-first sync/classifier workflow.
- Routine maintenance classifies new/changed products. A fundamental policy migration reclassifies every known product after scraping, including historical products and existing vegan/nonvegan/unknown decisions. Use an isolated working database, preserve previous decisions and bind sync-run attribution independently of selection (`--scope all-known`). Preserve historical products' last-known fields, flags and category evidence; replace categories only for the complete newly discovered current cohort. Recover accidentally erased historical categories from the immutable pre-scrape snapshot before freezing benchmark or assessment inputs.
- Assess products individually without category or positive-reason sample admission gates. Refresh manufacturer sources for every initially unknown product, including historical products; retain negative/withdrawn statements and complete coverage. Validated database activation and local userscript regeneration are authorised after policy, operational, research, uniform 600-positive audit and export checks pass. Keep `dev` and the current development version for testing; no tag, main advancement, push, publication or installed FireMonkey update.
- The current completed scrape must proceed directly to all-known reassessment using the completed unchanged-classifier benchmarks. Do not re-benchmark, delay assessment for category counts or change reasons to populate categories. Bind continued work to source/model/policy versions and retained applicable verification.
- When the user asks to continue until completion, supervise the authorised pipeline in the current turn until it finishes or an actual included-plan limit or external blocker prevents progress. Do not end the turn by leaving a background process or substituting a scheduled follow-up unless the user explicitly requests that arrangement.
- Keep audit trails for classification decisions in the database tables, including evidence, summary, classifier version, and run metadata.
- If the primary LLM passes disagree on status or vegan reason, run exactly two fresh, independent `gpt-6.1-sol` passes at reasoning effort `medium` on the same stored evidence, without exposing the primary conclusions. Accept their agreed status/reason only subject to the shared whole-product policy, including all four positive routes; otherwise keep `unknown`. Retain both stages and errors in the audit trail. Agreed primary `unknown` results do not trigger arbitration.
- The user explicitly authorised retaining the completed 600-product suitability audit on 2026-10-16 while separately validating its sole name-to-manufacturer attribution correction. Use `tools/repair_vegan_attribution.py` with bound proof, preserve the original failed score, and prove unchanged suitability, frozen evidence, positive population and browser membership. This specific authorisation does not permit reuse after a suitability defect or changed evidence.
- The user additionally approved retaining those 600 unchanged positive readings and auditing every added positive after the separately validated source-qualification repair. Report the mixed design accurately: uniform original-positive sample plus a census of all added positives, not a new uniform 601-product sample. Preserve original scores and require exact population/evidence accounting and two fresh source-cited readings for each added positive.
- Keep the selected model pair unless actual new evidence warrants a proposed change. Use the included ChatGPT plan only; stop admissions and checkpoint at limits, never use paid/API fallback, reset allowance or upgrade the primary automatically. Automated reference/audit readings must be source-backed and citation-validated; do not require human review or treat model agreement as independent ground truth.
- For any rule change that increases vegan classifications, first quantify likely impact with SQLite queries and explain the safety argument.
- Use only the main NVMe filesystem for catalogue data, working databases, backups and archives. Do not use mounted drives, including the flaky USB disk at `/mnt/e`, for reads, recovery, writes or offloading. Preserve sufficient activation headroom using verified lossless compression of closed local artifacts; use the intact local SSD snapshots for recovery.
- After an interrupted sync, preserve `sync_context_baseline` and inspect the latest run before resuming. Never delete the baseline or edit run status to bypass export checks; follow the recovery guidance in the working guide.

## Userscript Source And Release Rules

- The distributable userscript is `Ocado Vegan Filter`.
- The repo copy is `ocado-vegan-filter.user.js`.
- Ordinary development edits go to the repo copy. Whenever a requested local release tag is created or remade, also update the user's installed FireMonkey copy to the exact tagged source, reload it safely, and verify it for the user's pre-publication testing. This installation is part of release preparation and does not need a separate permission question.
- Preserve the current userscript formatting style; edits should already match VS Code autoformat-on-save output and should not introduce formatting-only churn.
- If the user says they updated a separate local copy of the userscript, treat that named file as the current source of truth and sync the repo copy from it after validating.
- Keep the script self-contained; it should not fetch Ocado product detail pages or call third-party services while shopping.
- The script should treat products as vegan from an accepted official tag, a clearly affirmative whole-product vegan name, or a validated embedded vegan allowlist entry, subject to unresolved-conflict checks. A name/URL keyword alone must not bypass negation, claim scope, or contrary product evidence.
- Browser precedence is recorded nonvegan, blocked unknown, validated positive IDs/applicable live official tags, then unknown. Runtime name or URL keywords cannot promote; assess names offline and export supported IDs.
- Keep separate allowlists for manufacturer/name evidence and ingredients evidence.
- Non-vegan or not-known-vegan products should be visually de-emphasised only; the real Ocado Add button must remain present and clickable.
- Use `Not vegan` only for products affirmatively classified `nonvegan`; use `Unknown vegan` for products without enough evidence to establish either vegan or non-vegan status.
- Product links must remain clickable.
- Preserve Ocado's responsive image sources and existing button elements/handlers. Recycled cards and quantity controls must retain host-owned changes; restore only styles, classes, and labels still owned by this script.
- Keep userscript metadata free of personal identifiers.
- Never use a personal domain in userscript metadata.
- Greasy Fork may force or preserve `@namespace`; if a namespace is required, use a non-personal value.
- Use `@license Unlicense` and preserve the Unlicense text when preparing release files.
- Bump the userscript version when preparing a new release. Fixes found while testing an unpublished local release keep that release's agreed version number.
- Keep the product counts at the top of the userscript comment block up to date whenever a userscript change is finalised.
- When regenerating embedded product ID allowlists, keep the diff minimal: preserve the relative order and line placement of retained IDs, remove obsolete IDs in place, append only genuinely new IDs, and avoid unrelated userscript changes.
- Use `tools/update_userscript_allowlists.py`, starting with `--dry-run`. It exports all known classified IDs, including historical products; do not silently narrow exports to current products or hand-edit sets around a failed safety gate.
- Commit userscript development, tooling, and testing changes to `dev`. Documentation-only commits may go directly to `main` between releases, provided `ocado-vegan-filter.user.js` remains byte-for-byte identical to the latest release tag. Release versions describe the userscript; Git tags still snapshot the whole repository.
- Once the user approves the locally tested `dev` changes for release, fast-forward merge `dev` into `main` and create and verify a signed annotated tag at that exact `main` commit. Create the release tag on `main`, then return to `dev` for further work. Complete the FireMonkey tagged-source verification before publication.
- Use exact Greasy Fork version strings for tags, for example `1.4.4`, not `v1.4.4`.
- If the user finds a bug while testing an unpublished local release, fix it on `dev` at the same version and rerun the relevant checks. After the user approves the correction, fast-forward `main` to the corrected commit and remake the signed annotated local tag there.
- Check local/remote tag state and publication state before moving an existing tag. Replacing an unpublished, unpushed local tag is part of this testing workflow. Never force-push or rewrite shared history; a published or pushed release requires a new version and tag.
- Local release preparation does not authorize pushing or publishing. A tag push creates a GitHub release through the workflow; a local tag or metadata version alone does not prove publication on Greasy Fork.
- Documentation-only changes may advance `main` without a userscript version bump, release tag, or FireMonkey installation. Keep documentation about unreleased development with that work on `dev`. Tooling changes remain on `dev` until an approved release and do not themselves require a userscript version bump or FireMonkey installation.
- Write short, imperative Greasy Fork release notes describing user-visible results, product additions/removals, and why classifications changed. Cover the whole interval since the last published version, including skipped local versions. Use tag annotations and Greasy Fork text; do not add a standalone `CHANGELOG.md` unless requested.

## Git Workflow

- Local `dev` commits may be unsigned when GPG signing is unavailable. Use a per-command `git -c commit.gpgsign=false` override and preserve the configured signing preferences; release tags must still be signed.
- Keep development work local unless pushing it is explicitly requested. For an authorized release push, push only `main` and the named release tag atomically; do not use `--all`, `--tags`, or push `dev` implicitly. Verify the current remote state and that `origin/main` is an ancestor of the release commit before pushing.
- Keep local `main` aligned with authorized remote updates using fast-forward pulls. Before releasing, merge any documentation commits from `main` into `dev` so the approved release can fast-forward `main`. Verify release alignment by comparing `ocado-vegan-filter.user.js` with the latest tag, rather than requiring the branch and tag to point to the same commit between releases.
- Commit each finalised logical change individually: one change per commit and one commit per change.
- Account for new files by tracking intended repository artifacts or explicitly ignoring local working data. Leave finalised changes committed and report anything intentionally left uncommitted.
- Keep formatting-only changes in a separate commit from feature, behaviour, documentation, or data changes.
- Keep repo-specific signing configuration in local `.git/config`, not in tracked repo files.
- Keep GitHub Actions workflows on the latest stable major versions of actions and avoid deprecated JavaScript runtimes.
- Keep the GitHub remote on SSH, not HTTPS, and use SSH for Git pushes/fetches.
- For GitHub CLI API commands, do not let stale environment tokens override the authenticated `gh` keyring login; run commands as `env -u GH_TOKEN -u GITHUB_TOKEN gh ...` unless the user explicitly asks to test an environment token.
- If GitHub CLI returns `HTTP 401: Bad credentials`, first check `gh auth status` with those token variables unset before asking the user to re-authenticate.
- When the user makes a firm statement about how this repository should be handled in future, update this `AGENTS.md` directly as part of the current task.

## FireMonkey Workflow

- Do not assume any userscript manager other than FireMonkey is active.
- Do not close or restart the user's real Firefox unless the user explicitly asks.
- Prefer editing userscript files on disk for release work.
- When creating/remaking a requested local release tag, or separately asked to update the installed script, follow [the FireMonkey installation runbook](docs/firemonkey-installation.md). It covers backup, parsing, storage writes, reload, and exact-source verification.
- Never use blind GUI keystrokes. Target and verify the specific Firefox window/tab before any GUI interaction.

## Testing

- Run targeted tests after code changes when practical.
- Use `python3 -m unittest discover -s tests` in the development environment. New tests must be discoverable `unittest.TestCase` methods; bare helper functions were silently skipped in an earlier browser suite.
- For userscript/browser behaviour bugs, reproduce the issue in clean temporary Firefox before implementing a fix; use retained fixtures where possible and live pages when needed. These automated checks are part of development and do not require changing the installed FireMonkey copy.
- For performance changes, measure before and after with `tools/benchmark_ocado_userscript.py` and preserve relevant results. Protect incremental card processing, bounded hydration scans, and CSS-only image filtering; do not infer speed from source size alone.
- For userscript syntax, run `node --check` on the script file.
- Python tools live under `tools/`, tests live under `tests/`, and retained audit outputs live under `audit/`.
- The normal suite runs real headless Firefox fixtures; `OCADO_LIVE_TESTS=1` enables the live Ocado checks. Report skips explicitly and distinguish injected-script tests from installed FireMonkey verification.

## Output Conventions

- Use URLs in the form `https://www.ocado.com/products/<id>` when generating lists for customer-service or public reports.
- Do not expose credentials, cookies, account-specific Ocado session details, or browser profile secrets.
