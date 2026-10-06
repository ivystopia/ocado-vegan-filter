# Ethical candidate preflight v17

235 discoverable unittest tests passed; one live Ocado test was explicitly skipped because OCADO_LIVE_TESTS was not enabled. The normal temporary headless Firefox fixtures ran. Node syntax and Git diff checks passed. Source hashes are bound in checks.json.

Candidate: classifier v25, policy whole-product-v2, registry v7, schema 4, primary prompt v21, arbitration prompt v12, annotation v8, ethical-cases-v4. Python 3.13 reports ResourceWarnings about unclosed isolated test SQLite connections; these are retained in the complete log, not suppressed or counted as test failures. No model calls, live catalogue writes, installed FireMonkey changes or publication were part of these checks.
