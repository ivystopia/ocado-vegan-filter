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
- For browser or performance changes, read [the Firefox audit](docs/code-audit-2026-09-04.md) and the relevant tests in `tests/test_ocado_vegan_filter.py` before changing the script.
- Dated audit reports describe the evidence and policy at the time of that run. Follow current instructions and runbooks for today's workflow; do not infer current release state or model defaults from an old report.

## Source Of Truth

- Use `ocado_products.sqlite` as the source of truth for product analysis.
- Do not use the JSONL scrape files for new analysis unless the user explicitly asks for raw scrape archaeology.
- Treat JSONL files as historical import/raw data only.
- If new live scraping is performed, import the result into SQLite and keep enough raw/pre-check data in SQLite or clearly named raw files so future analysis does not require re-hitting Ocado unnecessarily.
- When reporting product counts, distinguish all known products from `current_on_ocado = 1` products.
- Open SQLite read-only for inspection. The classifier CLI's `status` command calls schema migration code, so use the working guide's SQL for read-only status checks.
- `tools/build_ocado_database.py` is a historical importer whose CLI deletes its output database and sidecars before rebuilding. Its `create_schema()` helper drops tables. Use an isolated target for reconstruction/tests; never run either against the source-of-truth database as a setup or inspection step.

## Vegan Classification Rules

- Vegan classification must prefer false negatives over false positives.
- Do not tag a product as vegan from probability, brand reputation, category assumptions, or "usually vegan" reasoning.
- Valid product statuses are `vegan`, `nonvegan`, and `unknown`.
- Valid vegan reasons are `tagged`, `manufacturer`, `ingredients`, and `name`.
- `unknown` means assessed, but not enough database evidence exists to classify safely.
- `NULL` or missing status means unclassified and should generally be treated as work remaining.
- May-contain allergen warnings do not make a product non-vegan.
- A product with no explicit ingredients field should be treated as a single-ingredient product and classified from stored product identity/category text when that identity is unambiguous.
- Ocado's official vegan tag is authoritative: classify tagged products as `vegan/tagged` even when stored ingredient text appears to conflict, and report those conflicts separately for manual review.
- For products without an official Ocado vegan tag, explicit animal-derived ingredients such as milk, egg, honey, gelatine, meat, fish, shellfish, beeswax, shellac, carmine, lanolin, or similar should classify as non-vegan.
- Ingredients with ambiguous sourcing should classify as unknown unless the product text explicitly resolves the source.
- Fortified wheat/flour should be treated as vegan when the fortification is limited to standard flour additions such as calcium, iron, niacin, thiamin, or folic acid.
- Vitamins outside standard flour fortification should remain unknown unless the evidence explicitly proves the sources are vegan.
- Safe parser improvements should be source-aware, for example allowing `soya lecithin` while keeping bare `lecithin` unknown.

## Classifier Workflow

- The classifier must use only data already stored in SQLite unless the task is explicitly to scrape/import new data.
- Follow `docs/vegan-classifier-design.md` for end-to-end classifier behaviour.
- Use `gpt-5.6-luna` with reasoning effort `high` and two independent passes for bulk unresolved classification.
- The supervising Codex model and bulk classifier model are separate choices. A change of supervising model does not change the benchmarked classifier default.
- Pass the model and reasoning effort separately; `gpt-5.6-luna/high` is not a valid model ID.
- Do not use Luna `low` or `medium` for product decisions: both repeated a false-vegan result in the retained safety benchmark.
- Run `tools/benchmark_ocado_vegan.py` successfully before each monthly live sync or after any model, prompt, schema, or classification-rule change.
- Keep the benchmark's frozen contexts, hashes, and expected labels together. Investigate mismatches; do not refresh inputs from today's catalogue or change expected labels merely to obtain a pass.
- For monthly maintenance, follow `docs/monthly-maintenance.md` and use the DB-first sync/classifier workflow rather than the legacy JSONL scanners.
- Keep audit trails for classification decisions in the database tables, including evidence, summary, classifier version, and run metadata.
- If independent LLM/classifier passes disagree on status or vegan reason, classify the product as unknown unless the user explicitly authorizes a different arbitration workflow.
- For any rule change that increases vegan classifications, first quantify likely impact with SQLite queries and explain the safety argument.
- After an interrupted sync, preserve `sync_context_baseline` and inspect the latest run before resuming. Never delete the baseline or edit run status to bypass export checks; follow the recovery guidance in the working guide.

## Userscript Source And Release Rules

- The distributable userscript is `Ocado Vegan Filter`.
- The repo copy is `ocado-vegan-filter.user.js`.
- Ordinary development edits go to the repo copy. Whenever a requested local release tag is created or remade, also update the user's installed FireMonkey copy to the exact tagged source, reload it safely, and verify it for the user's pre-publication testing. This installation is part of release preparation and does not need a separate permission question.
- Preserve the current userscript formatting style; edits should already match VS Code autoformat-on-save output and should not introduce formatting-only churn.
- If the user says they updated a separate local copy of the userscript, treat that named file as the current source of truth and sync the repo copy from it after validating.
- Keep the script self-contained; it should not fetch Ocado product detail pages or call third-party services while shopping.
- The script should treat products as vegan when Ocado tags them vegan, when the name explicitly contains standalone `vegan`, or when the product ID is in an embedded vegan allowlist.
- Live or embedded official Ocado vegan metadata must take precedence over the embedded non-vegan set.
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
- Commit development changes directly to `main`.
- For requested release work, prepare a signed annotated local tag once the agreed version passes repository checks, before the user's FireMonkey testing and Greasy Fork publication.
- Use exact Greasy Fork version strings for tags, for example `1.4.4`, not `v1.4.4`.
- If the user finds a bug while testing an unpublished local release, fix it at the same version, rerun the relevant checks, and remake the signed annotated local tag at the corrected commit.
- Check local/remote tag state and publication state before moving an existing tag. Replacing an unpublished, unpushed local tag is part of this testing workflow; changing shared tags or rewriting pushed history needs explicit authorization for that concrete action.
- Local release preparation does not authorize pushing or publishing. A tag push creates a GitHub release through the workflow; a local tag or metadata version alone does not prove publication on Greasy Fork.
- Documentation/tooling-only changes that leave the userscript unchanged do not need a userscript version bump or release tag and do not trigger a FireMonkey installation.
- Write short, imperative Greasy Fork release notes describing user-visible results, product additions/removals, and why classifications changed. Cover the whole interval since the last published version, including skipped local versions. Use tag annotations and Greasy Fork text; do not add a standalone `CHANGELOG.md` unless requested.

## Git Workflow

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
