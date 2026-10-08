# Current classifier fixture

`ethical-cases-v29.json` is the frozen evidence and reference fixture for the current whole-product policy. It contains 497 synthetic contracts plus natural cases and workload evidence. Keep classifier inputs, hashes and expected outcomes together; do not silently refresh or relabel them. Synthetic cases are controlled policy tests, not independent natural products.

Run `tools/benchmark_whole_product.py` only when appropriate for an actual classifier change. The completed unchanged-classifier benchmark is reused for the current catalogue refresh. Assess catalogue products individually; fixture selection metadata is descriptive and never a category-count admission gate.

Applicable control/pilot reports are in `audit/current-classifier/`. Source-applicability contrasts additionally use `benchmarks/manufacturer-source-scope-v2.json`. The Firefox ZIP remains a working replay fixture, regardless of its capture date.
