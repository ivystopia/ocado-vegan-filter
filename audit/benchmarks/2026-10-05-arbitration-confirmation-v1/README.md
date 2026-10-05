# Confirmation-only Astra arbitration validation — 2026-10-05

Implemented pipeline: two primary Luna/high passes, followed only on status/reason disagreement by two independent Astra/medium passes on the same stored context. Agreed primary unknowns and primary errors do not escalate. Astra cannot promote ingredient/name-only evidence under the confirmation-only requirement committed in 99c8b06. The broader deterministic/primary-classifier confirmation-only migration remains pending.

## Scope and impact

Read-only SQLite inspection found 156 latest Luna/high disagreements still unknown, all current on Ocado. Across all historical models there are 1,805 latest unknown disagreements (1,453 current). These counts describe potential future review cohorts, not products reclassified by this change. Production selection remains limited to NULL/unclassified statuses; no historical classifications were reset, reassessed, exported or changed.

The prior 15-product Astra pilot was conducted before the confirmation-only requirement. Its five ingredient-only vegan outcomes are not permitted under this stage's policy. The new stage uses a separate prompt and an additional deterministic confirmation check. This limits positive arbitration decisions to supplied official metadata or explicit manufacturer confirmation, with canonical conflicts blocking non-official promotions. Agreement alone is insufficient. All original primary and arbitration decisions are retained, including a positive assessment rejected by the confirmation gate.

## Live inference validation

The unchanged 48-product frozen safety benchmark passed with `--arbitrate-disagreements`: 48/48 exact statuses, zero false-vegan results, zero disagreements, zero errors, no retries. It took 263.0 seconds. This run had 0 arbitration triggers; it is a legacy-regression check, not proof that all existing positive-decision paths already implement the new policy. Its result is retained in `../2026-10-05-luna-astra-arbitration-v1.json`.

The separate live arbitration replay uses the exact hashed evidence from the 15-product pilot and recorded Luna disagreement results. Only the primary stage is replayed; the production wrapper then makes fresh Astra calls through the real Codex CLI. The fixture expectations were fixed before these calls: 14 unknown and one nonvegan, because none of these 15 has an applicable positive vegan confirmation and Mozart has explicit milk evidence. The old pilot and 48-product gold labels are unchanged. All 15 matched, with zero errors, zero Astra disagreements and no retries, in 77.749 seconds. Four calls used Astra medium, two passes per product in batches of 10 and 5. Full raw/parsed Astra responses and retained historical primary evidence are in `result.json`; the original primary raw batch responses were not duplicated in the replay fixture, but both product decisions remain in its evidence.

These inference runs used frozen inputs only and did not open the catalogue database for writes. No live catalogue classification was executed.

## Automated verification

`python3 -m unittest discover -s tests` in `.venv` passed 117 tests in 30.707 seconds, with one skip for the opt-in live Ocado check. The normal headless Firefox fixtures ran; installed FireMonkey verification was not performed because no userscript changed. New tests cover disagreement-only routing, fresh Astra inputs, reason-only disagreement, agreement on unknown, no recursive escalation, confirmation enforcement, canonical ingredient conflicts, failed/nonzero model calls, usage limits, split recovery, minimum passes and SQLite audit/error persistence. The benchmark routing flag is tested as well. Python compilation and `git diff --check` passed.

## Reproduction

From the repository root, run `python3 tools/benchmark_ocado_vegan.py --arbitrate-disagreements --json-output <new-output-path>` for the legacy regression check. For a new arbitration-only replay, copy this directory to a new sibling directory, omit the existing result.json, and run its run.py. The runner refuses to overwrite an existing result before making any calls. It verifies input hashes and replays primary records while calling Astra through the production wrapper. This is deliberately an evidence replay, not a rerun of Luna or a catalogue migration. The confirmation fixture is a new policy-specific evaluation and must not be substituted for the original frozen benchmark merely to obtain a pass.
