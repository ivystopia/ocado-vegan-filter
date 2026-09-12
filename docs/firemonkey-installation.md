# Install a local release in FireMonkey

Use this when creating or remaking a requested local release tag, or when the user separately asks for a direct installed-script update. Installing the tagged source is already authorized as part of local release preparation. Ordinary development edits remain in the repo copy until that point.

The user tests this installed release before Greasy Fork publication. If a bug is found, keep the agreed version, fix and verify the source, remake the signed local tag, and repeat this installation. See [the release workflow](monthly-maintenance.md#7-commit-and-release) for shared-tag and publication boundaries.

## Source and current installation

- Use the exact `ocado-vegan-filter.user.js` content from the signed tag being prepared. Record its commit and source hash; the working tree may contain later edits.
- Rediscover the current Firefox profile, FireMonkey extension UUID, XPI, and storage path. Do not reuse old profile paths or extension UUIDs from conversation history.
- FireMonkey stores scripts in `browser.storage.local` under keys of the form `_<script name>`, here `_Ocado Vegan Filter`.
- The value is a parsed object containing metadata and the complete `js` source, not a raw source string. FireMonkey's parser is in its extension bundle at `content/meta.js`; use `Meta.get(source, pref)` rather than hand-building the object.
- Inspect the existing target and preserve other scripts and preferences. Before writing back, recheck that the real target has not changed concurrently; preserve any user edits instead of overwriting them.
- Never close or restart the user's real Firefox unless explicitly asked. Use the `browse-with-firefox` skill for browser automation where needed; a screenshot is not a browser-control connection.

## Backup, update, and verify storage

1. Back up FireMonkey's storage directory first under a timestamped name and retain the backup path for the handoff.
2. Use a temporary headless Firefox profile with the current FireMonkey XPI and a copy of its storage directory.
3. In the temporary extension page, call `browser.storage.local.get()`, dynamically import `content/meta.js`, parse the source with `Meta.get(source, pref)`, and write it with `browser.storage.local.set({['_Ocado Vegan Filter']: parsed})`.
4. Copy the serialized IndexedDB `object_data.data` blob for the `_Ocado Vegan Filter` key from the temporary profile back into the real profile. Update only the `data` column for the existing row; avoid trigger-sensitive columns such as `file_ids` and preserve unrelated rows.
5. Verify with a fresh temporary Firefox profile that FireMonkey reads the expected version, metadata, and exact tagged source. Compare the full source or its hash; version equality alone cannot distinguish a remade local release from the previous buggy copy.

Temporary profiles contain private browser data. Do not put profile snapshots, storage dumps, cookies, or other account/session data in the repo or tool output. Retain only the source verification and non-sensitive result details needed to explain the installation.

## Reload and verify the active extension

A running Firefox/FireMonkey process can retain an old in-memory userscript registration after storage changes. A disk update alone does not establish that the installed script is active.

Automate this sequence where safe targeted control is available:

1. Disable FireMonkey.
2. Load or reload the relevant Ocado page while FireMonkey is disabled.
3. Re-enable FireMonkey.
4. Hard-refresh the Ocado tab.
5. Verify the active script and representative product/link/Add-button behaviour without changing the user's basket as a test side effect.

Never send blind GUI keystrokes. First target and verify the specific Firefox window/tab; a previous attempt submitted a browser command into the user's terminal. Do not close or restart Firefox to complete this sequence without an explicit request.

If safe targeted automation is unavailable, give the user the exact four reload steps above and report active verification as pending. Keep completed storage verification distinct from an active installation check and from the user's acceptance testing.

Report the backup path, installed version, tagged source hash/match, and whether the running extension was successfully reloaded and checked. Do not describe a storage-only update as a verified active installation.
