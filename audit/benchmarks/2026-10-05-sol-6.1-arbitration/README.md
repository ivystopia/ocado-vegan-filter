# Sol arbitration validation — 2026-10-05

The candidate `gpt-6.1-sol`/`medium` arbitrator matched all **15/15** frozen confirmation-policy expectations: fourteen unknown and one nonvegan, with zero execution errors, pass disagreements or retries. It took 80.111 seconds. This supports replacing Astra/medium for the existing arbitration role alongside the [48-case comparison](../2026-10-05-sol-6.1-comparison/README.md).

The fixture is a byte-for-byte copy of `../2026-10-05-arbitration-confirmation-v1/fixture.json`, with the same hash and unchanged expected statuses. The primary stage replays retained historical Luna disagreement records; the actual production wrapper then makes four fresh Sol calls, two passes per product in batches of ten and five. Sol sees only the stored product evidence, not the replayed primary conclusions. The prompt, confirmation gate and audit construction use the production functions. The candidate model is overridden only in the replay process; no database is opened or written.

The cream liqueur's product-specific milk evidence yields nonvegan; the fourteen other cases lack applicable explicit positive confirmation under this deliberately narrower arbitration policy. This fixture has no positive-confirmation cases and does not validate the revised ordinary-food/name permissions or all whole-product risks. Positive confirmation and rejection/error paths are additionally covered by the existing repository tests. Historical expectations were not relabelled to obtain a pass.

The replay process started with classifier v10 before the production model default changed; its model override matches the selected v11 default. That version change identifies the new model for future audit records. `result.json` retains raw/parsed responses and both stages, and `environment.json` records this distinction. The classifier's primary model remains Luna/high. No catalogue reclassification, userscript export, release or installation was performed.

To make a fresh replay, copy `run.py` and `fixture.json` into a new sibling directory and run its `run.py` from the repository root. The runner refuses to overwrite an existing result. Keep its explicit Sol candidate selection when comparing later defaults; this script is an experiment, not the production configuration entry point.

## Final production validation

After selecting the v11 default, the unchanged frozen benchmark with `--arbitrate-disagreements --model gpt-5.6-luna --reasoning-effort high --passes 2 --batch-size 10` returned 48/48 exact in 268.503 seconds, with zero false-vegan results, pass disagreements, missing products, execution errors or retries. This run had 0 arbitration triggers; the separate 15-case replay above directly exercises Sol arbitration. `production-safety.json` and `production-safety.log` retain the result.

The full development suite (`.venv/bin/python -m unittest discover -s tests`) completed 117 tests in 31.711 seconds, with one skip for the opt-in live Ocado test. Temporary headless Firefox fixtures ran; installed FireMonkey was not changed or tested. Fixture/hash/response checks and runner syntax validation passed, as did `git diff --check`.
