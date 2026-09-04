# Monthly Ocado Catalogue And Userscript Maintenance

This runbook refreshes the SQLite source of truth, reclassifies only new or changed products, validates the result, and prepares a userscript update. Run it manually about once a month.

The live scrape itself does not use an LLM. Luna/high is used only after the scrape, for products that remain unresolved after deterministic rules.

## 1. Prepare And Benchmark

Start from the repository root with a clean tracked working tree. The local SQLite database is intentionally untracked.

```bash
cd /home/ivy/repos/personal/ocado_report
git status --short
codex --version
codex login status
python3 -m unittest discover -s tests
```

Run the fixed safety benchmark before contacting Ocado. It is read-only and must finish with `48/48 exact`, no false-vegan result, and no execution errors.

The standard fixture now contains frozen SQLite evidence in `benchmarks/vegan-classifier-v1-contexts.json`, with hashes checked by the runner. It does not reread changing catalogue rows. Keep these inputs and their expected statuses together when adding regression cases.

```bash
RUN_DATE="$(date +%F)"

python3 tools/benchmark_ocado_vegan.py \
  --model gpt-5.6-luna \
  --reasoning-effort high \
  --passes 2 \
  --batch-size 10 \
  --json-output "audit/benchmarks/${RUN_DATE}-luna-high.json"
```

Stop if the command exits non-zero. A pass disagreement is always merged to `unknown` and reported; investigate any new disagreement before continuing even when it does not create an exact-status mismatch. The runner retries transient schema/output failures, but any observed retry should still be noted in the monthly review.

## 2. Refresh The SQLite Catalogue

Run the DB-first sync. It makes a rolling SQLite backup before changing the database and prints the backup path and sync-run ID.

```bash
DB_PATH="$PWD/ocado_products.sqlite"
FIREFOX_PYTHON="/home/ivy/.venvs/codex-firefox/bin/python"
FIREFOX_SCRIPTS="/home/ivy/.codex/skills/browse-with-firefox/scripts"

test -x "$FIREFOX_PYTHON"
test -f "$FIREFOX_SCRIPTS/firefox_session.py"

BROWSE_WITH_FIREFOX_SCRIPTS="$FIREFOX_SCRIPTS" \
  "$FIREFOX_PYTHON" tools/sync_ocado_database.py --db "$DB_PATH"
```

The sync needs the dedicated Firefox environment because it imports Selenium's `firefox_session.py` helper and launches a headless snapshot of the current Firefox profile. It does not close, restart, or modify the real Firefox session.

Do not use the legacy `scan_ocado_vegan_according_to_ingredients.py` JSONL pipeline for monthly maintenance.

After a successful sync, capture the latest completed run ID:

```bash
SYNC_RUN_ID="$(sqlite3 "$DB_PATH" \
  "SELECT id FROM sync_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1;")"

test -n "$SYNC_RUN_ID"
echo "Using sync run ${SYNC_RUN_ID}"
```

Review what changed before classification:

```bash
sqlite3 -readonly "$DB_PATH" <<SQL
.headers on
.mode column
SELECT id, status, error, category_count,
       category_pages_attempted, category_pages_succeeded, category_pages_failed,
       category_products_discovered, sitemap_products_discovered,
       detail_fetch_attempted, detail_fetch_succeeded, detail_fetch_failed,
       products_new, products_changed, products_removed,
       classifications_invalidated
FROM sync_runs
WHERE id = ${SYNC_RUN_ID};

SELECT COUNT(*) AS current_products
FROM products
WHERE current_on_ocado = 1;

SELECT change_kind, COUNT(*) AS products
FROM sync_product_context_changes
WHERE run_id = ${SYNC_RUN_ID}
GROUP BY change_kind
ORDER BY change_kind;
SQL
```

Stop if the sync is incomplete, reports errors, or the change counts look implausibly large. Investigate before classifying or exporting anything.

## 3. Classify New And Changed Products

Run deterministic rules first and Luna/high only for the remaining unresolved products. Keep the model ID and effort as separate arguments.

```bash
python3 tools/classify_ocado_vegan.py --db "$DB_PATH" classify-all \
  --codex \
  --sync-run-id "$SYNC_RUN_ID" \
  --model gpt-5.6-luna \
  --reasoning-effort high \
  --passes 2 \
  --retries 2 \
  --batch-size 10 \
  --workers 8
```

The command is resumable: successfully classified products are no longer selected if the command must be rerun. Any disagreement between the two passes becomes `unknown`. Eight workers completed the 8,242-product August 2026 queue without a final error; lower the worker count and rerun only if the service starts returning persistent rate or execution errors.

## 4. Validate The Database

Run the following checks before generating the userscript:

```bash
sqlite3 -readonly "$DB_PATH" <<SQL
.headers on
.mode column
SELECT id, mode, status, model, reasoning_effort, sync_run_id,
       total_products, rule_classified, llm_submitted,
       llm_classified, errors, error
FROM vegan_classification_runs
WHERE sync_run_id = ${SYNC_RUN_ID}
ORDER BY id;

SELECT COUNT(*) AS unclassified_changed_products
FROM products
WHERE vegan_status IS NULL
  AND id IN (
    SELECT product_id
    FROM sync_product_context_changes
    WHERE run_id = ${SYNC_RUN_ID}
      AND change_kind IN ('new', 'changed')
  );

SELECT COUNT(*) AS unclassified_current_products
FROM products
WHERE current_on_ocado = 1
  AND vegan_status IS NULL;

SELECT COALESCE(vegan_status, 'unclassified') AS status,
       COALESCE(vegan_reason, '') AS reason,
       COUNT(*) AS all_known,
       SUM(CASE WHEN current_on_ocado = 1 THEN 1 ELSE 0 END) AS current_on_ocado
FROM products
GROUP BY vegan_status, vegan_reason
ORDER BY status, reason;

SELECT COUNT(*) AS official_tag_conflicts
FROM products AS p
WHERE (
    p.official_vegan = 1
    OR EXISTS (
      SELECT 1 FROM product_flags AS f
      WHERE f.product_id = p.id AND f.flag = 'vegan'
    )
  )
  AND (p.vegan_status IS NOT 'vegan' OR p.vegan_reason IS NOT 'tagged');

SELECT COUNT(*) AS disagreement_products
FROM product_vegan_classification_audit AS a
JOIN vegan_classification_runs AS r ON r.id = a.run_id
WHERE r.sync_run_id = ${SYNC_RUN_ID}
  AND r.mode = 'codex'
  AND a.evidence_json LIKE '%independent_codex_disagreement%';

SELECT COUNT(*) AS validation_error_products
FROM product_vegan_classification_audit AS a
JOIN vegan_classification_runs AS r ON r.id = a.run_id
WHERE r.sync_run_id = ${SYNC_RUN_ID}
  AND r.mode = 'codex'
  AND a.validation_error IS NOT NULL;

SELECT COALESCE(c.previous_vegan_status, 'unclassified') AS previous_status,
       COALESCE(c.previous_vegan_reason, '') AS previous_reason,
       p.vegan_status AS current_status,
       COALESCE(p.vegan_reason, '') AS current_reason,
       COUNT(*) AS products
FROM sync_product_context_changes AS c
JOIN products AS p ON p.id = c.product_id
WHERE c.run_id = ${SYNC_RUN_ID}
  AND c.change_kind = 'changed'
GROUP BY c.previous_vegan_status, c.previous_vegan_reason,
         p.vegan_status, p.vegan_reason
ORDER BY previous_status, previous_reason, current_status, current_reason;
SQL
```

Required gates:

- Both classification runs are `completed` with zero errors.
- `unclassified_changed_products` is zero.
- `unclassified_current_products` is zero.
- `official_tag_conflicts` is zero.
- `validation_error_products` is zero.
- Every reported pass disagreement is stored as `unknown` and reviewed as a group before export.
- Counts distinguish all known products from current Ocado products.
- Any large movement into or out of `vegan`, particularly `vegan/ingredients`, is manually reviewed.

`unknown` is a valid completed classification. `NULL` is unfinished work and blocks export.

## 5. Preview The Userscript Data Change

Preview allowlist additions and removals without changing the tracked userscript:

```bash
python3 tools/update_userscript_allowlists.py \
  --db "$DB_PATH" \
  --dry-run
```

The generator exports canonical classifications for all known product IDs, not just products currently listed by Ocado. This preserves evidence for products that temporarily disappear and later return. The monthly validation report still distinguishes current from historical products.

If there are no allowlist changes, do not bump the userscript version or publish a release. The refreshed untracked SQLite database can remain the local source of truth.

## 6. Regenerate And Test A Changed Userscript

If the allowlists changed and the validation gates passed, choose the next patch version. Replace the example version below with the intended value:

```bash
NEXT_VERSION="1.6.2"

python3 tools/update_userscript_allowlists.py \
  --db "$DB_PATH" \
  --version "$NEXT_VERSION"

node --check ocado-vegan-filter.user.js
python3 -m unittest discover -s tests

git diff --stat
git diff -- ocado-vegan-filter.user.js
```

The generator preserves retained ID order, removes obsolete IDs in place, appends genuinely new IDs, updates the counts in the source comment, and changes the metadata version.

Test the repo copy in Firefox with FireMonkey. Confirm at least:

- an officially tagged vegan product;
- a manufacturer/name allowlisted vegan product;
- an ingredients allowlisted vegan product;
- a known non-vegan product;
- an unknown product;
- product links and the real Add buttons remain clickable.

## 7. Commit And Release

Commit the tested userscript and retained benchmark/report artifacts directly to `main` as one scoped maintenance change.

Publishing remains a deliberate manual boundary:

1. Publish the exact tested version to Greasy Fork.
2. Confirm the live Greasy Fork version and source.
3. Only then create the signed annotated Git tag using the exact version string, without a `v` prefix.
4. Push the commit and tag; the tag workflow creates the GitHub release assets.

Do not tag an unpublished version, and do not update the installed FireMonkey copy directly unless that separate workflow was explicitly requested.

## Monthly Stop Conditions

Do not regenerate or publish the userscript if any of the following occurs:

- benchmark mismatch, false-vegan result, missing product, or model error;
- failed or incomplete Ocado sync;
- classification run error;
- unresolved `NULL` status for any current or sync-changed product;
- official vegan tag conflict;
- unexplained large classification-count movement;
- syntax, unit-test, or Firefox regression.
