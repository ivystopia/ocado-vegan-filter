# Nutrition-serving parser repair: read-only impact screening

SQLite was opened read-only. Native product-information fields contain 82 all-known products, 79 stored-current, with both vegan wording and the narrowly matched quantified nutrient-per-serving form; all 82 already have historical vegan status. These are signal counts, not new promotions or a complete external-capture impact estimate. Exact IDs/fields and the screening pattern are retained in report.json.

The repair changes interpretation of a cited manufacturer assertion within a complete semantic assessment. It does not add a deterministic positive shortcut, infer ingredient/component sources or relax whole-product scope. Quantified nutrition alone supplies no claim; serving suggestions, component/formula-only wording, negation, exceptions and definite animal content retain their existing constraints. No canonical database or embedded-set changes occurred.
