# Working in this repository

Read [AGENTS.md](../AGENTS.md) first. This guide maps the current implementation, safe inspection commands and operational recovery.

## Code map

| Area | Entry point and responsibility |
| --- | --- |
| Shopping behaviour | `ocado-vegan-filter.user.js`: self-contained embedded ID sets, page evidence, cosmetic card/button styling, and incremental DOM updates. |
| Catalogue refresh | `tools/sync_ocado_database.py`: category and sitemap discovery, product detail refresh, retained raw/context evidence, and invalidation of changed classifications. |
| Whole-product policy/benchmarks | `tools/vegan_policy.py`, `tools/codex_evidence.py`, `tools/benchmark_whole_product.py`: shared policy, evidence-only metered calls, frozen end-to-end evaluation. |
| Classification | `tools/classify_ocado_vegan.py`: deterministic rules, JSON-only Luna fallback, bounded two-pass GPT-6.1-Sol/medium arbitration, canonical fields, and audit writes. |
| Manufacturer research | `tools/research_ocado_manufacturers.py`: unknown-product cohort, research coverage, independent source/scope review and audited imports; [manufacturer research](manufacturer-research.md) describes the separate website-gathering workflow. |
| Export | `tools/update_userscript_allowlists.py`: read-only SQLite validation, all-known-product ID sets, header counts, and optional userscript version update. `--dry-run` also avoids writing the userscript. |
| Firefox benchmark | `tools/benchmark_ocado_userscript.py` with the retained Firefox ZIP fixture: repeatable page replay, stress replay, and optional live measurements. |
| Live layouts | `tools/check_ocado_userscript_scenarios.py`: temporary Firefox at verified viewport widths, initial/scrolled product states, hit targets, and screenshots. |
| Database helpers | `tools/build_ocado_database.py`: parsing/schema helpers required by sync and tests. Its isolated reconstruction CLI is destructive to the target. |
| Verification | `tests/` uses `unittest.TestCase`; `.github/workflows/test.yml` runs the suite, and `release.yml` requires it before publishing tag assets. |

In the userscript, start at `veganStatusForCard()` for evidence precedence, `processCard()` for appearance, and `queueMutations()` / `run()` for scheduling. Recorded nonvegan IDs and blocked unknown IDs take precedence over embedded positives and live official icons/hydration. Names and URL slugs cannot promote at runtime. The offline classifier has its own decision order in [the design](vegan-classifier-design.md); keep that distinction explicit when investigating a classification mismatch.

## Environment and first checks

From the repo root, inspect the working tree and recent commits, then follow the [README setup](../README.md#development). Reuse `.venv` if present; install `requirements-dev.txt` there rather than into Debian's system Python. The project uses Python, Node for syntax checks, and real Firefox through Selenium; it has no npm build step.

With the development environment active:

```sh
git status --short --branch
git log -8 --oneline
node --check ocado-vegan-filter.user.js
python3 -m unittest discover -s tests -v
```

The normal suite uses temporary/in-memory databases and mocks classifier model calls. It needs Firefox and the development dependencies, but neither `ocado_products.sqlite` nor a Codex login. `FIREFOX_BINARY` selects a non-default Firefox executable. Selenium may obtain a driver if one is not cached.

The live page test is skipped unless `OCADO_LIVE_TESTS=1`. Current classifier validation uses the frozen whole-product suite; see the classifier design before monthly maintenance or model/prompt/schema/rule changes. A documentation-only change does not require model calls.

For a targeted test file, use discovery rather than treating `tests` as an installed package:

```sh
python3 -m unittest discover -s tests -p test_sync_ocado_database.py -v
python3 -m unittest discover -s tests -p test_ocado_vegan_filter.py -v
```

The live sync has an additional dependency on the `browse-with-firefox` skill's `firefox_session.py`. Follow the environment setup in [monthly maintenance](monthly-maintenance.md), including `BROWSE_WITH_FIREFOX_SCRIPTS`; passing the ordinary test suite does not establish that this helper is configured.

## Inspecting the local database safely

The catalogue is local and ignored by Git. Its absence in a fresh checkout is expected: ordinary development and fixture tests still work. Locate the existing database or an agreed backup before starting database-dependent work; do not reconstruct it or rescrape Ocado merely to inspect the repository.

Use a read-only connection. In the current CLI, even `classify_ocado_vegan.py status` invokes `ensure_classification_schema()` and can alter an older database. `build_ocado_database.py` deletes its output database and WAL/SHM sidecars, while `create_schema()` drops tables; neither is a safe inspection/setup command against the working catalogue.

```sh
sqlite3 -readonly ocado_products.sqlite <<'SQL'
.bail on
.headers on
.mode column
SELECT COALESCE(vegan_status, 'unclassified') AS status,
       COALESCE(vegan_reason, '') AS reason,
       COUNT(*) AS all_known,
       SUM(CASE WHEN current_on_ocado = 1 THEN 1 ELSE 0 END) AS current
FROM products
GROUP BY vegan_status, vegan_reason
ORDER BY status, reason;

SELECT id, status, products_new, products_changed, products_removed
FROM sync_runs ORDER BY id DESC LIMIT 3;

SELECT EXISTS (
  SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_context_baseline'
) AS unfinished_sync_baseline;

SELECT id, mode, status, sync_run_id, total_products, errors
FROM vegan_classification_runs ORDER BY id DESC LIMIT 4;
SQL

python3 tools/update_userscript_allowlists.py --dry-run
```

Use `products.vegan_status` and `vegan_reason` for current decisions. `product_vegan_classification_audit` and `vegan_classification_runs` explain them; `sync_product_context_changes` retains before/after evidence. Legacy `classifier_decisions` and `precheck_results` are historical evidence, not alternative canonical classifications. Inspect `.schema` / `PRAGMA table_info(...)` before assuming a column exists on an older copy.

The export deliberately includes all known classified IDs, including products temporarily absent from Ocado. `unknown` is a completed conservative decision; `NULL` is unfinished. Under the whole-product policy, correctly matched definite animal evidence overrides an official vegan tag to `nonvegan` and is retained for reporting. Unresolved identity/material conflicts become blocked `unknown`. Export accepts these outcomes when their audit and evidence are valid, and refuses legacy/stale positives or missing provenance. The live catalogue remains on its historical classifications until the gated migration.

## Recovery and classifier pitfalls

- **Interrupted sync:** inspect the latest run, including failed/running rows, and check whether the process is still active before starting another writer. Preserve `sync_context_baseline`: it holds the original evidence across restarts. Within an authorized refresh, fix the cause and rerun the sync; successful finalisation removes the baseline. Do not select an older completed run to bypass the latest failure.
- **Discovery completeness:** read Ocado's advertised sitemap index and every category/product part, rather than assuming `part1` is complete. Retain the index/part captures in `sync_discovery_sources`. A failed part blocks sync. For a pre-qualification, sync-only repair, fork the checkpointed coordinator into a fresh root with `fork-scrape-repair --from-root <old-root>`, then run that new root. This preserves the original snapshot, interrupted baseline and exact old preflight reports, requires fresh implementation checks and full scraping, and refuses reuse if classifier, policy, fixture, models or other implementation changed. Never alter old report hashes or mark a failed sync completed.
- **Failure after complete fetching:** `sync_ocado_database.py --db <working-db> --finish-run-id <failed-id>` can finish from retained evidence only when guarded checks prove complete indexed sitemap coverage, every category pagination chain, matching current selection, successful per-product fetch records and intact raw captures. It creates a new finalization-only run linked by `resumed_from_run_id`, preserves the failed run unchanged and retains zero new HTTP attempts. The coordinator chooses this route after a verified full acquisition; otherwise it performs the full sync. Sync schema setup preserves prior decisions while adding required provenance columns, including on old databases. A full fetch followed by a failed SQL finalization is not a completed sync or permission to export.
- **Historical category evidence:** complete category and sitemap discovery before replacing categories for that current cohort. Keep last-known categories for products absent from the new catalogue. The coordinator restores historical-only missing rows from its immutable original snapshot before freezing inputs, without changing fresh current categories or canonical decisions.
- **Rolling backups:** sync and schema migration both replace `ocado_products.sqlite.bak`. Before retrying, preserve any known-good backup needed for recovery under a separate name. Use SQLite's backup API for a live database; copying only its main file can omit WAL changes.
- **Interrupted classification:** after the sync is complete, rerun the same scoped classifier command. It selects only remaining `NULL` statuses and keeps successful batch writes. Preserve and account for earlier failed/interrupted run records; do not rewrite them to make validation look clean. After a Codex/app restart, check the host process list before admitting another writer; a lost terminal handle alone does not prove that its child exited. If the coordinator manifest remains `running` but no writer survived, retain its exact bytes and a dated recovery record containing the process check and outstanding database run records, then checkpoint the interrupted stage. Leave database run/audit records untouched. Continue from a fresh root with `continue-assessment`; reuse the completed scrape and applicable unchanged-classifier benchmarks, and select only remaining unclassified products.
- **Partial sync flags:** `--category-limit`, `--product-limit`, and `--detail-limit` are for isolated smoke-test databases. They do not provide a representative full catalogue refresh. `--missing-details-only` intentionally skips previously fetched details and therefore cannot check existing products for recipe changes.
- **Changed rules:** `classify-rules --force` reruns deterministic rules; it is not a complete reclassification or a reset of old LLM decisions. Plan affected-row invalidation, preserve before/after evidence, and quantify any increase in vegan classifications before changing the working database.
- **Model availability:** keep the model ID and reasoning effort separate. Bulk classification defaults to GPT-6-Luna/high, two passes, and the monthly runbook's explicit worker count; the supervising session's model is independent.
- **Benchmark mismatch:** first check the frozen inputs, hashes, prompt/model settings, and returned product IDs. Do not relabel gold cases or suppress disagreements to produce a passing result.

The [monthly runbook](monthly-maintenance.md) owns execution commands and export gates. Do not start a live scrape or full reclassification as a side effect of ordinary documentation or userscript work.

## Firefox pitfalls and useful reproductions

- **Badge absent from a product card:** Ocado can omit a visible vegan icon while retaining official vegan metadata in hydration. Reproduce with the card and its page data; a missing icon alone is not non-vegan evidence.
- **Image links stop working:** CSS filters create stacking contexts. Muted decorative images use `pointer-events: none` so clicks reach Ocado's link overlay. Verify the actual click target, not just whether an anchor exists.
- **Stale image or quantity label:** Ocado reuses DOM elements. Preserve `src`, `srcset`, `sizes`, native event handlers, and host-owned text/classes. Check a reused Add button's current accessible label before treating it as Add rather than a quantity control.
- **Missed updates or repeated work:** exercise text-node changes, namespaced SVG references, malformed hydration attributes, new hydration roots, inserted cards, and unrelated mutations. Keep writes idempotent and the observer from reacting endlessly to the script's own changes.
- **Slow grids:** compare retained replay and stress results with the previous implementation before optimising. Use `tools/benchmark_ocado_userscript.py` with the retained Firefox replay fixture and temporary Firefox tests; retain before/after measurements.
- **Green test command that ran no browser tests:** new regression tests must be `unittest.TestCase` methods. Check the discovered test names and skips. Ordinary top-level helper functions are not discovered as unittest tests.
- **Installed source looks correct but behaviour is old:** FireMonkey can retain an old in-memory registration after a disk update. Follow [installation and reload verification](firemonkey-installation.md). Injecting the repo source into a clean Firefox fixture does not verify FireMonkey's registration lifecycle.

## Release notes and historical context

Userscript development, tooling changes, and local testing take place on `dev`. Documentation-only commits may advance `main` between releases while its userscript remains byte-for-byte identical to the latest tag; documentation about unreleased work stays on `dev`. Merge any new `main` documentation commits into `dev` before release. The [release steps](monthly-maintenance.md#7-commit-and-release) promote the approved development changes to `main`, create a signed local tag there, and install its exact source into FireMonkey for verification before publication. Corrections to an unpublished, unpushed release are made and tested on `dev`; after user approval, advance `main`, remake the local tag, and reinstall its source. Check userscript hashes as well as version numbers, and never force-push.

Greasy Fork release notes should use brief imperative bullets about shopping behaviour and evidence changes. Count additions/removals from the last published version, explain whether changes came from new catalogue evidence or a classification-rule change, and include skipped local versions in that interval. Keep release notes in the signed tag annotation and text prepared for Greasy Fork; do not add a standalone changelog unless requested.


## Whole-product transition

Follow the rewritten classifier design for current semantics. `tools/shadow_vegan_policy.py` previews all existing positives read-only and retains a reportable tag-conflict list; the live SQLite database and embedded ID sets remain on their historical policy until shadow validation, complete manufacturer-research accounting, the final positive audit and export checks pass. The user has selected Luna 6/high and Sol 6.1/medium and authorised activation after these checks; reuse the completed unchanged-classifier benchmarks for the current refresh, with no category admission gates. Export intentionally refuses legacy/stale positives. New research candidates/checkpoints/raw captures live in ignored `.whole-product-work/`; retain reviewable fixture metadata/results under `benchmarks/` and `audit/`.
