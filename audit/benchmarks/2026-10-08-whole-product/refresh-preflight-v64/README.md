# Fresh v64 refresh preflight

Executed on 8 October 2026 after commit c52c9bd, before the authorised full catalogue scrape. These checks establish admission to scraping, not completion of the fresh natural qualification, all-known reassessment, manufacturer research or final positive audit.

The fixed pipeline used two independent GPT-6-Luna/high primary readings and, on raw status/reason disagreement only, two fresh blind GPT-6.1-Sol/medium readings. Policy whole-product-v2, registry v8, schema 5, primary prompt v30, arbitration prompt v21; acceptance conservative-unknown-recall-v2. Full candidate and fixture hashes are retained in the reports. No historical fixture/reference/report was overwritten.

- The 497 controlled cases had 496 exact final matches, zero unsafe positives, zero final operational/evidence/reason/reportable-negative failures and one conservative vegan-to-unknown miss, synthetic.ethical-components.9. Positive recall was 195/196 (99.49%). Per-route recovery was tagged 49/49, manufacturer 74/74, name 42/42 and ingredients 30/31; each route met its recorded gate.
- The frozen 96-case development pilot had 96 exact final matches, 24/24 recovered positives and zero final unsafe or operational failures. This is retained historical natural evidence, not the newly refreshed catalogue corpus.
- The controls included eight raw unsafe-positive case/stage entries, including five agreed raw positives, which the shared policy rejected before the final decision. The reports retain these observations; model agreement alone was not accepted as suitability evidence.
- Four controlled cases and one pilot case triggered arbitration. The controls retained 149 attempts (11 failed calls), and the pilot retained 39 attempts (six failed calls). All retries, failures and token totals count in usage; raw private journals remain in the ignored working directory.
- Some pilot unknowns omitted additional reference issue codes while identifying another grounded blocker. The reports preserve this diagnostic under the existing grounded-unknown-blocker-v2 rubric. No reference label or gate was changed for this run.

The `release_challenge_passed` fields are false because these are preflight synthetic/development checks, not the full locked release challenge. Both passed `benchmark_whole_product.successful` and allowed the coordinator to begin scraping an isolated database. Canonical classifications and the userscript were unchanged at this milestone.

Implementation checks are retained separately in refresh-coordinator-checks-v1, v2 and v3. The final v3 run passed all 320 discovered tests, with one explicit live-Ocado skip, plus userscript syntax and diff checks. The normal suite exercised temporary headless Firefox; it did not install or verify FireMonkey.
