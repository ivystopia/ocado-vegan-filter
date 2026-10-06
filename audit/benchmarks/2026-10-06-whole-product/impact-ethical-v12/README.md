# Read-only v25 rule impact

The source-of-truth SQLite database was opened mode=ro under a consistent read snapshot: 54,418 all-known products, 49,951 stored-current. No canonical write or allowlist activation occurred. Classifier v25 / registry v7 / whole-product-v2.

23 products newly qualify conclusively by stored manufacturer claims, 22 current; all citations are retained in new-rule-positives.json. This promotion cohort remains unchanged from the v23 preview. The semantic queue is 19,349, including 3618 previous positives, down from 19,548 / 3,793. Changes from v23 are retained separately; removal of false animal declarations is not evidence of hidden manufacturing facts.

19 definite animal/tag contradictions remain collected (16 stored-current), with one separate tagged identity mismatch. No message to Ocado was sent. Compressed transitions retain every reviewed ID and its per-product input hash; full source contexts remain local ignored working evidence. Stored current flags do not prove current live listing. This is a deterministic impact preview, not independent catalogue ground truth or final migration.
