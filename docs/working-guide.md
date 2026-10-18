# Working in this repository

Read [AGENTS.md](../AGENTS.md) first. The active task starts from published 1.7.2 and makes small reviewed additions from unknown products. The [classifier design](vegan-classifier-design.md) describes the evidence policy; [maintenance](monthly-maintenance.md) describes future refreshes. Older reports and restored release-era tools are not current execution instructions.

## Code map

- `ocado-vegan-filter.user.js`: published shopping runtime and embedded ID sets. Preserve runtime behaviour during this additive effort.
- `tools/published_baseline.py`: protected restoration, source-bound reviews and audit writes; previews by default.
- `tools/update_userscript_allowlists.py`: validates canonical decisions and regenerates sets while preserving retained ID placement.
- `tools/refresh_catalogue_membership.py`: isolated complete discovery and new-product detail capture, followed by guarded membership import; existing classifications and detailed evidence remain intact.
- `audit/published-baseline-restart/`: verified release manifest, reviewed source snapshots and completion report.
- `tools/sync_ocado_database.py`: release-era scraper; use only an isolated database for future refresh work.
- `tools/classify_ocado_vegan.py`, `tools/research_ocado_manufacturers.py`: release-era bulk tools; not the active canonical writers. The classifier CLI rejects the protected database. Do not use the restored historical model default.
- `tools/build_ocado_database.py`: historical importer, destructive to its target. Never use against the source-of-truth database for setup or inspection.
- `tests/`: discoverable unittest tests, including real headless Firefox fixtures. The optional live test is separately gated.

## Inspect without changing data

Use `ocado_products.sqlite` read-only, not the historical JSONL files or the legacy classifier status command:

```sh
sqlite3 -readonly ocado_products.sqlite "SELECT vegan_status, vegan_reason, COUNT(*) AS all_known, SUM(current_on_ocado = 1) AS current FROM products GROUP BY 1,2;"
git status --short --branch
git log -5 --oneline
```

Keep all-known/current counts separate. Existing detailed records and raw SQLite evidence are retained. Current decisions live in products; previous audit rows are history. A restored decision does not mean that its modern source fields were freshly reclassified. A membership refresh discovers new and removed products while retaining existing detailed records; it does not claim to detect recipe changes on every existing product.

## Review and apply clear unknowns

1. Select unknown candidates and inspect full relevant SQLite fields, flags and categories. Select by name/category only to find candidates, never as automatic proof. Retain exact source snapshots, quotations, scope and rationale in a JSON review file using the existing produce review as its schema example.
2. Confirm that each accepted item is outside every published set, has unambiguous product identity/composition and no evidenced material concern. Prefer a small useful batch over speculative automatic rules. Record manufacturer precedence if applicable.
3. Preview and apply the review, then export:

```sh
.venv/bin/python tools/published_baseline.py --reviews audit/published-baseline-restart/produce-reviews.json
.venv/bin/python tools/published_baseline.py --reviews audit/published-baseline-restart/produce-reviews.json --apply
.venv/bin/python tools/update_userscript_allowlists.py --dry-run
.venv/bin/python tools/update_userscript_allowlists.py
```

An already applied review is not a reusable queue: application refuses it. Create a new review file for each subsequent batch. Existing reviews are validated during every export. A changed source requires an attributed review, not editing its stored hash to bypass the check. Baseline removals/demotions are outside this additive command.

The initial restoration is one-time and refuses to overwrite subsequent reviews. Its verified pre-change classification snapshot and existing complete recovery archives remain on local NVMe. `.whole-product-work/` and `.rollback-work/` are ignored recovery data, not instructions to resume the cancelled migration. Never use mounted drives.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests
node --check ocado-vegan-filter.user.js
.venv/bin/python tools/update_userscript_allowlists.py --dry-run
```

The suite uses Selenium with temporary headless Firefox; `FIREFOX_BINARY` chooses the browser. `OCADO_LIVE_TESTS=1` enables live Ocado checks. Report skipped live checks. Injecting a script in temporary Firefox does not verify installed FireMonkey.

Browser behaviour tests must preserve clickable native Add buttons and links, responsive images and host DOM changes. Reproduce a runtime bug before editing; measure performance changes with `tools/benchmark_ocado_userscript.py`. The published runtime is intentionally unchanged here.

## Recovery

- Preserve audit history and compressed decision backups. Apply repairs transactionally; do not regenerate the database with the historical importer.
- An unfinished latest sync or retained `sync_context_baseline` blocks export. Investigate and complete the interrupted sync in its isolated work area; never delete the baseline or select an older successful run to evade the check.
- Inspect actual processes before assuming a job survived a restart. No previous whole-product coordinator should be resumed after this user-requested rollback.
- Preserve Git history with rollback commits. Do not reset shared branches or force push. Use unsigned local dev commits if GPG is unavailable; release tags still require signatures.
- Keep this development work local. No release tag, main advancement, push, publication or installed FireMonkey update until requested.
