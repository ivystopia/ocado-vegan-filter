# Monthly Ocado Catalogue And Userscript Maintenance

This runbook refreshes the SQLite source of truth, reclassifies only new or changed products, validates the result, and prepares a userscript update. Run it manually about once a month.

The live scrape itself does not use an LLM. Luna/high is used only after the scrape, for products that remain unresolved after deterministic rules.

The Astra arbitration change does not authorize reclassification. The broader confirmation-only migration is pending; reconcile that policy before executing the legacy catalogue classification/export steps below.

## 1. Prepare And Benchmark

Start from the repository root on `dev` with a clean tracked working tree and the [development environment](../README.md#development) active. The local SQLite database is intentionally untracked. If resuming an interrupted refresh, inspect the latest run and follow [the recovery guidance](working-guide.md#recovery-and-classifier-pitfalls) before starting another writer.

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
RUN_STAMP="$(date +%F-%H%M%S)"

python3 tools/benchmark_ocado_vegan.py \
  --arbitrate-disagreements \
  --model gpt-5.6-luna \
  --reasoning-effort high \
  --passes 2 \
  --batch-size 10 \
  --json-output "audit/benchmarks/${RUN_STAMP}-luna-high.json"
```

Stop if the command exits non-zero. Primary disagreements receive two independent Astra/medium assessments; unresolved Astra disagreements remain `unknown`. Review `arbitrated_ids` and the retained audit evidence; investigate any new disagreement before continuing even when it does not create an exact-status mismatch. The runner retries transient schema/output failures, but any observed retry should still be noted in the monthly review. Use a new output filename for each attempt so failed results remain available alongside successful reruns.

## 2. Refresh The SQLite Catalogue

Run the DB-first sync. It makes a rolling SQLite backup before changing the database and prints the backup path and sync-run ID. This replaces `ocado_products.sqlite.bak`, so preserve any known-good backup needed for recovery before retrying a failed run.

```bash
DB_PATH="$PWD/ocado_products.sqlite"
FIREFOX_PYTHON="/home/ivy/.venvs/codex-firefox/bin/python"
FIREFOX_SCRIPTS="/home/ivy/.codex/skills/browse-with-firefox/scripts"

test -x "$FIREFOX_PYTHON"
test -f "$FIREFOX_SCRIPTS/firefox_session.py"

BROWSE_WITH_FIREFOX_SCRIPTS="$FIREFOX_SCRIPTS" \
  "$FIREFOX_PYTHON" tools/sync_ocado_database.py --db "$DB_PATH"
```

Read the `browse-with-firefox` skill for the current environment/helper paths if the checks above fail. The sync needs this Firefox environment because it imports Selenium's `firefox_session.py` helper and launches a headless snapshot of the current Firefox profile. It does not close, restart, or modify the real Firefox session.

Do not use the legacy `scan_ocado_vegan_according_to_ingredients.py` JSONL pipeline for monthly maintenance.

Use the full default detail refresh for monthly maintenance. Limit flags are for isolated smoke-test databases; `--missing-details-only` cannot detect changed ingredients on already fetched products.

After a successful sync, capture the latest run ID. Do not skip failed/running rows to select an older successful run:

```bash
SYNC_RUN_ID="$(sqlite3 -readonly "$DB_PATH" \
  "SELECT id FROM sync_runs ORDER BY id DESC LIMIT 1;")"

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

Stop if the selected run is not `completed`, reports errors, or the change counts look implausibly large. Investigate before classifying or exporting anything. An unfinished sync's `sync_context_baseline` must survive until a successful rerun; never drop it or edit run status to bypass the export gate.

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

The command is resumable: successfully classified products are no longer selected if the command must be rerun. Primary status/reason disagreements trigger two fresh Astra/medium passes on the stored evidence. Only an agreed, policy-supported result is accepted; unresolved disagreements stay `unknown`, and exhausted arbitration failures are recorded as errors. Agreed primary unknowns are not escalated. Eight workers completed the 8,242-product August 2026 queue without a final error; lower the worker count and rerun only if the service starts returning persistent rate or execution errors.

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
  AND EXISTS (SELECT 1 FROM json_each(a.evidence_json, '$.ambiguity_notes')
              WHERE value = 'independent_codex_disagreement');

SELECT json_extract(a.evidence_json, '$.arbitration.outcome') AS arbitration_outcome,
       COUNT(*) AS products
FROM product_vegan_classification_audit AS a
JOIN vegan_classification_runs AS r ON r.id = a.run_id
WHERE r.sync_run_id = ${SYNC_RUN_ID}
  AND json_extract(a.evidence_json, '$.arbitration') IS NOT NULL
GROUP BY arbitration_outcome;

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

For resumed classification, retain earlier failed/interrupted run records and account for their errors; require successful completion of the remaining queue and all product-level gates. Do not rewrite historical run statuses. The `official_tag_conflicts` query checks canonical classifications against official tags; apparent ingredient conflicts on correctly classified `vegan/tagged` products belong in a separate manual-review report and do not override the official tag.

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

If the allowlists changed and the validation gates passed, agree the release version using the current source, local tags, and last published Greasy Fork version. A routine catalogue update normally uses the next patch version; fixes during testing of an unpublished local release keep its agreed version. Set `NEXT_VERSION` to that value before running:

```bash
: "${NEXT_VERSION:?Set NEXT_VERSION to the agreed release version}"

python3 tools/update_userscript_allowlists.py \
  --db "$DB_PATH" \
  --version "$NEXT_VERSION"

node --check ocado-vegan-filter.user.js
python3 -m unittest discover -s tests

git diff --stat
git diff -- ocado-vegan-filter.user.js
```

The generator preserves retained ID order, removes obsolete IDs in place, appends genuinely new IDs, updates the counts in the source comment, and changes the metadata version.

Confirm these states in the automated Firefox tests, then repeat the acceptance checks in the installed FireMonkey copy after step 7 installs the tagged release:

- an officially tagged vegan product;
- a manufacturer/name allowlisted vegan product;
- an ingredients allowlisted vegan product;
- a known non-vegan product;
- an unknown product;
- product links and the real Add buttons remain clickable.

## 7. Commit And Release

Commit the tested userscript and retained benchmark/report artifacts on `dev` as one scoped maintenance change. Complete local development and testing there, including any requested FireMonkey testing using the [installation runbook](firemonkey-installation.md). Wait for the user's approval of the tested changes before advancing `main` for release. Tooling and documentation about unreleased changes stay on `dev`; other documentation-only commits may go directly to `main` between releases while its userscript remains identical to the latest tag.

Prepare the release locally before publication:

1. Inspect the current remote branches and tags, fetch `origin`, and fast-forward local `main` to `origin/main` if needed. Merge any new documentation commits from `main` into `dev`. Ensure both `main` and `origin/main` are ancestors of the approved `dev` commit; integrate other remote changes and rerun relevant checks before obtaining release approval if they change the tested implementation. With a clean working tree, run `git switch main` and `git merge --ff-only dev`. Create and verify a signed annotated local tag at that exact `main` commit, using the exact userscript version without a `v` prefix. Include the release notes in the tag annotation. Verify that `main` and the tag resolve to the same commit at release creation, then run `git switch dev`. Later documentation-only commits may advance `main` while preserving the tagged userscript. If fast-forwarding fails, investigate rather than resetting or force-pushing.
2. Back up FireMonkey storage, install the exact tagged source into the user's local FireMonkey, safely reload it, and verify the installed source using [the FireMonkey runbook](firemonkey-installation.md). This is part of preparing the local tag, so no separate installation permission is needed. The user then tests it before publishing to Greasy Fork.
3. If final verification finds a bug, fix and test it on `dev` at the same version. After the user approves the correction, fast-forward `main` to the corrected commit and remake and verify the signed annotated local tag there, then return to `dev`. Repeat the FireMonkey installation, reload, and source verification. Check first that the tag is still unpublished and unpushed; never replace a shared tag or force-push. A pushed or published release requires a new version and tag.
4. After the user's testing, publish the exact tested source to Greasy Fork when requested, or let the user publish it. Verify the live version and source before describing it as published.
5. When pushing is requested, recheck the remote state and push only `main` and the named release tag with `git push --atomic origin refs/heads/main "refs/tags/${NEXT_VERSION}"`. Do not push `dev`, all branches, or all tags implicitly. This sends the approved release while retaining later `dev` commits locally. The tag workflow runs the checks, verifies that the tag equals the userscript metadata version, and creates the GitHub release assets. Verify both remote refs and the completed workflow.

Creating a local tag includes updating the installed FireMonkey copy, but does not authorize pushing or publication. If safe reload automation is unavailable, provide the exact manual reload steps and report that active installation verification remains incomplete; do not close or restart the user's Firefox without an explicit request.

Release versions describe `ocado-vegan-filter.user.js`, although Git tags snapshot the whole repository. Between releases, verify the userscript against the latest tag with `git diff <latest-release-tag> main -- ocado-vegan-filter.user.js`; an empty diff confirms alignment even when `main` contains later documentation commits.

## Monthly Stop Conditions

Do not regenerate or publish the userscript if any of the following occurs:

- benchmark mismatch, false-vegan result, missing product, or model error;
- failed or incomplete Ocado sync;
- classification run error;
- unresolved `NULL` status for any current or sync-changed product;
- official vegan tag conflict;
- unexplained large classification-count movement;
- syntax, unit-test, or Firefox regression.
