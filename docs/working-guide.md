# Working in this repository

Read [AGENTS.md](../AGENTS.md) first. This guide maps the current implementation and captures recurring problems from the April–September 2026 work. Dated reports are retained evidence, not current release instructions or a reason to reuse old model settings.

## Code map

| Area | Entry point and responsibility |
| --- | --- |
| Shopping behaviour | `ocado-vegan-filter.user.js`: self-contained embedded ID sets, page evidence, cosmetic card/button styling, and incremental DOM updates. |
| Catalogue refresh | `tools/sync_ocado_database.py`: category and sitemap discovery, product detail refresh, retained raw/context evidence, and invalidation of changed classifications. |
| Classification | `tools/classify_ocado_vegan.py`: deterministic rules, JSON-only Codex fallback, conservative pass merging, canonical fields, and audit writes. |
| Export | `tools/update_userscript_allowlists.py`: read-only SQLite validation, all-known-product ID sets, header counts, and optional userscript version update. `--dry-run` also avoids writing the userscript. |
| Classifier benchmark | `tools/benchmark_ocado_vegan.py` with `benchmarks/vegan-classifier-v1*.json`: frozen, hashed evidence and expected decisions; calls the real Codex CLI. |
| Firefox benchmark | `tools/benchmark_ocado_userscript.py` with the retained Firefox ZIP fixture: repeatable page replay, stress replay, and optional live measurements. |
| Historical import | `tools/build_ocado_database.py` and the `tools/scan_ocado_vegan_according_to_*` entry points: historical import/scraping workflows. The DB-first sync still imports parsing helpers from the builder. |
| Verification | `tests/` uses `unittest.TestCase`; `.github/workflows/test.yml` runs the suite, and `release.yml` requires it before publishing tag assets. |

In the userscript, start at `veganStatusForCard()` for evidence precedence, `processCard()` for appearance, and `queueMutations()` / `run()` for scheduling. Page icons and hydration, standalone vegan names/slugs, and embedded sets provide positive evidence before the non-vegan fallback. The offline classifier has its own decision order in [the design](vegan-classifier-design.md); keep that distinction explicit when investigating a classification mismatch.

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

The live page test is skipped unless `OCADO_LIVE_TESTS=1`. The real 48-product classifier safety benchmark is separate from these unit tests: it calls authenticated Codex and is required before monthly syncs or classifier model/prompt/schema/rule changes. A documentation-only change does not require model calls.

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

The export deliberately includes all known classified IDs, including products temporarily absent from Ocado. `unknown` is a completed conservative decision; `NULL` is unfinished. A canonical conflict with an official vegan tag blocks export, while an apparent animal-ingredient conflict on a correctly classified `vegan/tagged` product is retained for manual review without downgrading the official tag.

## Recovery and classifier pitfalls

- **Interrupted sync:** inspect the latest run, including failed/running rows, and check whether the process is still active before starting another writer. Preserve `sync_context_baseline`: it holds the original evidence across restarts. Within an authorized refresh, fix the cause and rerun the sync; successful finalisation removes the baseline. Do not select an older completed run to bypass the latest failure.
- **Rolling backups:** sync and schema migration both replace `ocado_products.sqlite.bak`. Before retrying, preserve any known-good backup needed for recovery under a separate name. Use SQLite's backup API for a live database; copying only its main file can omit WAL changes.
- **Interrupted classification:** after the sync is complete, rerun the same scoped classifier command. It selects only remaining `NULL` statuses and keeps successful batch writes. Preserve and account for earlier failed/interrupted run records; do not rewrite them to make validation look clean.
- **Partial sync flags:** `--category-limit`, `--product-limit`, and `--detail-limit` are for isolated smoke-test databases. They do not provide a representative full catalogue refresh. `--missing-details-only` intentionally skips previously fetched details and therefore cannot check existing products for recipe changes.
- **Changed rules:** `classify-rules --force` reruns deterministic rules; it is not a complete reclassification or a reset of old LLM decisions. Plan affected-row invalidation, preserve before/after evidence, and quantify any increase in vegan classifications before changing the working database.
- **Model availability:** keep the model ID and reasoning effort separate. The earlier unsupported-Luna report came from sending `gpt-5.6-luna/medium` as the model ID. Bulk classification uses Luna/high, two passes, and the monthly runbook's explicit worker count; the supervising session's model is independent.
- **Benchmark mismatch:** first check the frozen inputs, hashes, prompt/model settings, and returned product IDs. The September drift incident came from reading updated catalogue evidence against old expected labels. Do not relabel gold cases or suppress disagreements to produce a passing result.

The [monthly runbook](monthly-maintenance.md) owns execution commands and export gates. Do not start a live scrape or full reclassification as a side effect of ordinary documentation or userscript work.

## Firefox pitfalls and useful reproductions

- **Badge absent from a product card:** Ocado can omit a visible vegan icon while retaining official vegan metadata in hydration. Reproduce with the card and its page data; a missing icon alone is not non-vegan evidence.
- **Image links stop working:** CSS filters create stacking contexts. Muted decorative images use `pointer-events: none` so clicks reach Ocado's link overlay. Verify the actual click target, not just whether an anchor exists.
- **Stale image or quantity label:** Ocado reuses DOM elements. Preserve `src`, `srcset`, `sizes`, native event handlers, and host-owned text/classes. Check a reused Add button's current accessible label before treating it as Add rather than a quantity control.
- **Missed updates or repeated work:** exercise text-node changes, namespaced SVG references, malformed hydration attributes, new hydration roots, inserted cards, and unrelated mutations. Keep writes idempotent and the observer from reacting endlessly to the script's own changes.
- **Slow grids:** compare retained replay and stress results with the previous implementation before optimising. Earlier measurements found canvas image conversion and broad DOM class scans expensive; the embedded ID sets were not the main cost. Use the reproduction commands and measurement limits in [the Firefox audit](code-audit-2026-09-04.md).
- **Green test command that ran no browser tests:** new regression tests must be `unittest.TestCase` methods. Check the discovered test names and skips. Ordinary top-level helper functions were silently skipped before the September audit.
- **Installed source looks correct but behaviour is old:** FireMonkey can retain an old in-memory registration after a disk update. Follow [installation and reload verification](firemonkey-installation.md). Injecting the repo source into a clean Firefox fixture does not verify FireMonkey's registration lifecycle.

## Release notes and historical context

The [release steps](monthly-maintenance.md#7-commit-and-release) create a signed local tag, install its exact source into FireMonkey, and allow the user to test before publication. Bugs during this local testing keep the agreed version and require both remaking the local tag and reinstalling its source. Check hashes as well as version numbers.

Greasy Fork release notes should use brief imperative bullets about shopping behaviour and evidence changes. Count additions/removals from the last published version, explain whether changes came from new catalogue evidence or a classification-rule change, and include skipped local versions in that interval. Keep release notes in the signed tag annotation and text prepared for Greasy Fork; a standalone changelog was deliberately removed during the September release.

For unresolved historical intent, use the `codex-history-search` skill to locate the relevant session and read the specific user requests. Older conversations contain authentication material: avoid dumping transcripts or copying their private contents into the repository. The main lessons are already reflected here and in the current instructions; old one-off permissions to restart browsers, rewrite history, or publish are not standing instructions for a new task.
