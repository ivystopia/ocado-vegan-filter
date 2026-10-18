# Ocado Report Instructions

## Purpose and current work

- Serve the vegan customer shopping on Ocado: trustworthy, useful labels and working Add buttons and product links.
- Read `docs/working-guide.md`, inspect `git status --short --branch` and recent commits before editing, and preserve unrelated work.
- The user has cancelled the broad whole-product reclassification. The current foundation is the published **1.7.2** script and its classifications, with small, explicitly reviewed additions from products that were unknown in that baseline. Do not resume old coordinators, reclassify the published vegan population, or impose a new catalogue-wide benchmark/audit programme.
- Keep the fresh scraped product facts, previous database audit history and local recovery archives. Superseded implementation lives in Git history; it is not the active workflow.
- Work on `dev`, keep version 1.7.2 for testing, and do not tag, advance `main`, push, publish or update installed FireMonkey without a new request.

## Source of truth and safety

- Use `ocado_products.sqlite` for product analysis. Distinguish all known from `current_on_ocado = 1` products. Historical JSONL files are not the analysis source.
- Inspect SQLite read-only. The legacy classifier's `status` command migrates schema; use SQL instead. Never run `build_ocado_database.py` or its destructive `create_schema()` against the canonical database.
- Use only the main NVMe filesystem, never mounted drives such as `/mnt/e`, including for recovery or reads.
- Preserve prior decisions and provenance before replacing them. Use transactions, reject stale evidence, and preserve concurrent changes. Do not copy a live SQLite main file without its WAL; use SQLite backup or a consistent logical backup for the exact fields changed.
- Preserve interrupted `sync_context_baseline` records. A failed latest sync cannot be bypassed by selecting an earlier successful run.

## Classification policy for new reviews

- Valid statuses are `vegan`, `nonvegan`, `unknown`; valid vegan reasons are `tagged`, `manufacturer`, `name`, `ingredients`.
- Reason precedence is `tagged`, `manufacturer`, `name`, `ingredients`. Manufacturer confirmation takes precedence over a vegan name.
- Plain fruit and vegetables, including an unambiguously simple mix, can qualify from identity/composition without a vegan claim. Kissabel British Apples (647140011) is a positive example. A missing ingredients field does not establish simplicity for an arbitrary manufactured product.
- Assess the complete sold product, including relevant processing and supplied components. Ordinary bags, trays, wrapping, or missing packaging details are not themselves evidence of an animal component. Do not invent speculative packaging or supply-chain risks.
- Material evidence of animal ingredients, animal processing, source-dependent additives, a supplied component, or a genuinely inapplicable claim needs resolution. A product name containing a fruit word or membership of a produce category does not establish the sold product's composition.
- Separate actual ingredients from serving suggestions, recipes, precautionary may-contain warnings and descriptive words such as buttery potato flesh or custard apple. Do not infer animal content from those words alone.
- Correctly matched definite animal ingredients override a vegan tag in any new assessment; collect such contradictions for possible reporting, but never contact Ocado without explicit instructions. Preserve the published baseline in this additive task; newly discovered contrary evidence on a baseline product should be reported for a separately scoped correction.
- Keep `Unknown vegan` for unresolved cases. Do not use `Almost vegan` or `Probably vegan`. Missing/NULL status is unfinished work.
- No automatic blanket produce rule: explicitly review and retain each accepted record with its source context, quotations and rationale. Quantify additions and explain their evidence before applying them.

## Current workflow

- Follow `docs/vegan-classifier-design.md` and use `tools/published_baseline.py` for the protected baseline and reviewed unknown additions. Preview first; its application uses a transaction and exact source checks.
- Preserve every published positive and nonvegan ID and its evidence-group membership. Only reviewed unknowns may be added; neither baseline demotions nor unrelated promotions belong in this effort.
- Refresh catalogue discovery for new and removed products when requested. Use `tools/refresh_catalogue_membership.py`: scan all advertised sitemap parts and category pages, fetch new IDs into an isolated database, then validate and import membership/new facts. Keep existing product details and classifications intact; removed products remain historical. A membership refresh is not a full detail refresh or permission for bulk reclassification.
- Clear ordinary-food reviews need no new model benchmark or expensive bulk calls. Do not require a sample quota, exhaustive manufacturer research or a catalogue-wide positive audit for this bounded additive workflow.
- The restored release-era classifier is not the active canonical writer. Its CLI refuses the protected database. Manufacturer imports and future syncs must use isolated databases until they can preserve this baseline and present product-level changes for review.
- If later unresolved classification is requested, retain GPT-6-Luna/high, two independent passes, and exactly two fresh blind GPT-6.1-Sol/medium passes only on status/reason disagreement. Agreed unknowns never escalate. Do not revive GPT-5.6-Luna or silently use the restored legacy classifier's old settings/semantics. Implement and validate this separately before using models for the protected workflow.
- Use the included ChatGPT plan only; no paid/API fallback, automatic model upgrade or allowance reset. The supervising model does not select the bulk classifier.
- Manufacturer research, when needed, is a brief check of applicable official product pages or FAQs. Retain exact source, scope, URL and date; an unsuccessful check leaves unknown. Do not deep-crawl or reopen the previous exhaustive research programme.
- Run `tools/update_userscript_allowlists.py --dry-run` before regeneration. It checks exact published classifications plus source-bound reviewed additions, including historical IDs. New scraped flags do not silently overwrite the restored baseline.

## Userscript and browser

- Edit the repo copy `ocado-vegan-filter.user.js`; keep it self-contained. Preserve the published runtime during this additive effort. Change only the relevant embedded IDs and generated counts, keeping retained ID order and line placement.
- Keep separate manufacturer/name and ingredients ID sets and preserve the nonvegan set. Product links and the real Ocado Add button must remain clickable; de-emphasis must not disable shopping.
- Preserve responsive image sources, host-owned DOM updates and native button handlers. Follow the Firefox guidance before any runtime change; reproduce behaviour defects in temporary Firefox before fixing them.
- Do not close or restart real Firefox without explicit authorization. No blind GUI keystrokes.
- The installed manager is FireMonkey. A later requested release installs the exact tagged source using `docs/firemonkey-installation.md`; local development does not.
- Keep metadata free of personal identifiers, use `@license Unlicense`, and preserve the license. Bump version only when release preparation is requested.

## Git and release

- Commit finalized logical changes separately on `dev`, inspect staged diffs, and account for new files. Local commits may be unsigned with `git -c commit.gpgsign=false`; preserve global signing preferences.
- No history rewriting or force pushing. A rollback commit preserves recovery through Git history.
- Keep SSH remote URLs. For GitHub CLI use `env -u GH_TOKEN -u GITHUB_TOKEN gh ...` to avoid stale environment tokens.
- After explicit release approval, merge any main documentation changes into dev, fast-forward main to approved dev, create a signed annotated tag with the exact version (no `v` prefix), verify FireMonkey, then return to dev. A release tag remains local unless push is explicitly requested.
- An authorized release push sends only main and the named tag atomically, never dev or all tags. Check remote state first. Never move a published tag; unpublished local testing fixes retain the agreed version and can remake its local tag.
- Use short imperative user-visible release notes in tag annotations and Greasy Fork text, not a standalone changelog.
- Firm user workflow decisions should update these instructions directly.

## Verification and output

- Use `.venv/bin/python -m unittest discover -s tests` and `node --check ocado-vegan-filter.user.js`. Tests must be discoverable `unittest.TestCase` methods.
- Test exact baseline retention, unknown-only admission, source changes, audit linkage, atomic rollback and export consistency for review tooling. Use clean temporary Firefox fixtures; `OCADO_LIVE_TESTS=1` enables optional live checks. Report skips and distinguish injected-script tests from FireMonkey verification.
- For runtime performance changes, measure before/after using `tools/benchmark_ocado_userscript.py`; source size is not performance evidence.
- Keep reviewable evidence under `audit/`, tools under `tools/`, and tests under `tests/`. Ignore large local databases, raw captures and recovery work.
- Use `https://www.ocado.com/products/<id>` for product reports. Do not expose credentials, cookies or private browser/session details.
