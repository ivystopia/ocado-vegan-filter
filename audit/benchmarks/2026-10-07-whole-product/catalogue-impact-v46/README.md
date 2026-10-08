# Read-only v46 catalogue impact

One consistent SQLite read snapshot, exact fixed classifier/policy hashes and all-known/current transitions are retained. All 54,418 known products (49,951 stored-current) are inspected. No schema migration, snapshot activation, classifier model call or canonical write occurs. Stored current flags do not establish current live availability.

Rules leave 23,321 cases for semantic assessment: 15,552 historically unknown, 7,473 historically vegan and 296 historically nonvegan. The stored-current subset has 20,750 pending semantic cases, including 6,505 historically vegan. Unassessed semantic cases are not gold unknowns. This supports the plan to use conclusive rules first and revalidate existing positives before any broader reassessment; model-pair selection and migration remain gated, and no demotions are activated.

Nineteen stored official-vegan/definite-animal contradictions (sixteen stored-current) are retained separately in CSV and Markdown with product URLs. One tagged identity conflict stays separate. No message has been sent to Ocado. The rule preview is evidence-based preparation; verify current listing and identity before sending a report.

Full decision-level rule findings are compressed here. The larger frozen semantic input queue remains ignored local working data and is hash-bound in summary.json. The status/reason transition counts equal the prior v39 preview; the v46 feature grammar repair only affects semantic proof acceptance, not a deterministic promotion shortcut.
