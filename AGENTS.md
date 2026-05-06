# Ocado Report Instructions

These instructions apply to `this repository`.

## Project Purpose

This repository supports an Ocado vegan-labelling audit and the `Ocado Vegan Filter` userscript.

The project has two related goals:

- Maintain a local product database for analysis of Ocado catalogue data.
- Maintain a self-contained userscript that helps vegan shoppers by treating products as vegan only when the local evidence supports that decision.

## Source Of Truth

- Use `ocado_products.sqlite` as the source of truth for product analysis.
- Do not use the JSONL scrape files for new analysis unless the user explicitly asks for raw scrape archaeology.
- Treat JSONL files as historical import/raw data only.
- If new live scraping is performed, import the result into SQLite and keep enough raw/pre-check data in SQLite or clearly named raw files so future analysis does not require re-hitting Ocado unnecessarily.
- When reporting product counts, distinguish all known products from `current_on_ocado = 1` products.

## Vegan Classification Rules

- Vegan classification must prefer false negatives over false positives.
- Do not tag a product as vegan from probability, brand reputation, category assumptions, or "usually vegan" reasoning.
- Valid product statuses are `vegan`, `nonvegan`, and `unknown`.
- Valid vegan reasons are `tagged`, `manufacturer`, `ingredients`, and `name`.
- `unknown` means assessed, but not enough database evidence exists to classify safely.
- `NULL` or missing status means unclassified and should generally be treated as work remaining.
- May-contain allergen warnings do not make a product non-vegan.
- Explicit animal-derived ingredients such as milk, egg, honey, gelatine, meat, fish, shellfish, beeswax, shellac, carmine, lanolin, or similar should classify as non-vegan.
- Ingredients with ambiguous sourcing should classify as unknown unless the product text explicitly resolves the source.
- Fortified flour/wheat should remain unknown unless the evidence explicitly proves the fortification sources are vegan.
- Safe parser improvements should be source-aware, for example allowing `soya lecithin` while keeping bare `lecithin` unknown.

## Classifier Workflow

- The classifier must use only data already stored in SQLite unless the task is explicitly to scrape/import new data.
- Follow `docs/vegan-classifier-design.md` for end-to-end classifier behaviour.
- Keep audit trails for classification decisions in the database tables, including evidence, summary, classifier version, and run metadata.
- If independent LLM/classifier passes disagree, classify the product as unknown unless the user explicitly authorizes a different arbitration workflow.
- For any rule change that increases vegan classifications, first quantify likely impact with SQLite queries and explain the safety argument.

## Userscript Source And Release Rules

- The distributable userscript is `Ocado Vegan Filter`.
- The repo copy is `ocado-vegan-filter.user.js`.
- If the user says they updated the separate local userscript copy, treat `a separate local userscript file` as the current source of truth and sync the repo copy from it after validating.
- Keep the script self-contained; it should not fetch Ocado product detail pages or call third-party services while shopping.
- The script should treat products as vegan when Ocado tags them vegan, when the name explicitly contains standalone `vegan`, or when the product ID is in an embedded vegan allowlist.
- Keep separate allowlists for manufacturer/name evidence and ingredients evidence.
- Non-vegan or not-known-vegan products should be visually de-emphasised only; the real Ocado Add button must remain present and clickable.
- Product links must remain clickable.
- Keep userscript metadata free of personal identifiers.
- Never use `personal.example` or another personal domain in userscript metadata.
- Greasy Fork may force or preserve `@namespace`; if a namespace is required, use a non-personal value.
- Use `@license Unlicense` and preserve the Unlicense text when preparing release files.
- Bump the userscript version for fixes and behavior changes before publishing.
- Commit development changes directly to `main`.
- Tag only versions that are live on Greasy Fork.
- Use exact Greasy Fork version strings for tags, for example `1.0.1`, not `v1.0.1`.
- Do not create a release tag until the user confirms the version is ready to publish to Greasy Fork.

## FireMonkey Workflow

- The user has switched from userscript manager to FireMonkey.
- Do not assume userscript manager is active.
- Do not close or restart the user's real Firefox unless the user explicitly asks.
- Prefer editing userscript files on disk for release work.
- FireMonkey stores scripts in `browser.storage.local` under keys of the form `_<script name>`, for example `_Ocado Vegan Filter`.
- The stored FireMonkey value is not just raw source; it is a parsed object containing metadata fields plus the full `js` source.
- FireMonkey's parser is available in its extension bundle as `content/meta.js`; use `Meta.get(source, pref)` to create the correct stored object instead of hand-building it.
- FireMonkey's current local extension ID is `<firemonkey-extension-id>`; rediscover its Firefox extension UUID from `extensions.webextensions.uuids` if needed.
- At the time this file was written, the local FireMonkey UUID was `<extension-uuid>`.
- The corresponding storage directory was `~/.mozilla/firefox/<firefox-profile>/storage/default/moz-extension+++<extension-uuid>^userContextId=4294967295`.

## Updating Installed FireMonkey Directly

Use this only when the user explicitly asks to update the installed userscript without using the browser UI.

- Back up FireMonkey's storage directory first.
- Use a temporary headless Firefox profile with the FireMonkey XPI and a copy of FireMonkey's storage directory.
- In the temporary extension page, call `browser.storage.local.get()`, dynamically import `content/meta.js`, parse the source with `Meta.get(source, pref)`, and write it with `browser.storage.local.set({['_Ocado Vegan Filter']: parsed})`.
- Copy the serialized IndexedDB `object_data.data` blob for the `_Ocado Vegan Filter` key from the temporary profile back into the real profile.
- Update only the `data` column for the existing row; avoid modifying IndexedDB trigger-sensitive columns such as `file_ids`.
- Verify with a fresh temporary Firefox profile that FireMonkey reads the expected version, metadata, and source.
- A running Firefox/FireMonkey process may have an in-memory registration of the old script. Updating IndexedDB on disk is not enough by itself; FireMonkey must be reloaded so it unregisters/re-registers the userscript from storage.
- If Firefox is running and the user has authorized updating their real install, automate the reload step where practical: disable FireMonkey, load/reload the relevant Ocado page, re-enable FireMonkey, then hard-refresh the Ocado tab.
- Never use blind GUI keystrokes for this reload. If GUI automation is required, first target and verify the specific Firefox window/tab; do not type into whichever window currently has focus.
- Do not close or restart Firefox for this workflow unless the user explicitly asks. If safe targeted automation is not possible, stop and give the user the exact manual reload steps instead.

## Testing

- Run targeted tests after code changes when practical.
- For userscript syntax, run `node --check` on the script file.
- For classifier/database changes, run the relevant `test_*` files with `pytest` or `python -m unittest` according to the existing test style.

## Output Conventions

- Use URLs in the form `https://www.ocado.com/products/<id>` when generating lists for customer-service or public reports.
- Do not expose credentials, cookies, account-specific Ocado session details, or browser profile secrets.
