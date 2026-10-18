# Catalogue maintenance

Keep the published classification baseline and refresh catalogue membership without bulk reclassification. The [working guide](working-guide.md) and [classifier design](vegan-classifier-design.md) describe reviewed unknown additions.

## Discover new and removed products

Read the `browse-with-firefox` skill for the current snapshot environment. Use its interpreter and helper directory with a fresh local NVMe work root:

```sh
BROWSE_WITH_FIREFOX_SCRIPTS=/home/ivy/.codex/skills/browse-with-firefox/scripts \
  /home/ivy/.venvs/codex-firefox/bin/python tools/refresh_catalogue_membership.py \
  --root .rollback-work/membership-YYYY-MM-DD
```

This captures every advertised category/product sitemap part, visits the category pages in a disposable Firefox profile, and fetches detail pages for genuinely new IDs. The canonical database is read-only during discovery. Existing product details are retained: this command does not detect changed recipes on existing IDs. It creates a small staging database instead of duplicating the large historical audit database.

Inspect the completed manifest and discovery/detail failures. Investigate implausible membership changes before importing. Removed products remain historical, not deleted. A failed/incomplete capture cannot activate. Do not restart a large scan blindly after interruption: inspect its retained stage and actual process first, repair the specific failure and preserve the captures.

```sh
.venv/bin/python tools/refresh_catalogue_membership.py \
  --root .rollback-work/membership-YYYY-MM-DD --import-completed
.venv/bin/python tools/refresh_catalogue_membership.py \
  --root .rollback-work/membership-YYYY-MM-DD --import-completed --apply
```

Import rejects concurrent membership changes, preserves existing decisions and source fields apart from membership metadata, retains discovery/fetch attribution and new raw captures, and records new products as unknown pending review. The exact prior membership fields are backed up locally before the transaction. No model calls occur.

## Review and export

Review clear unknown candidates individually, including newly discovered products. Apply source-bound additions using `tools/published_baseline.py`. Keep published positives/nonvegan IDs unchanged unless a separate correction is explicitly scoped. No quotas, blanket rules, broad reclassification, repeat model benchmark or exhaustive manufacturer research belongs in this workflow.

Run the full tests, exporter dry run, then regenerate the local userscript and verify that only reviewed IDs and counts changed. Commit on dev without a new tag, push or FireMonkey installation. The user will test this development script before deciding on a release.

For a later authorized release, follow AGENTS.md: prepare the agreed version on dev, fast-forward main, create a signed annotated local tag, and verify the exact source in FireMonkey using the [installation runbook](firemonkey-installation.md). Publication and pushing require explicit authorization.
