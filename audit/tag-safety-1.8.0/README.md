# Vegan-tag safety precedence in development 1.8.0

The user requested that an Ocado vegan tag cannot override confirmed nonvegan evidence or an unresolved conflict, with version 1.8.0 kept locally on `dev` without a tag or push.

## Behaviour

The userscript now checks known nonvegan IDs first, reviewed blocked Unknown IDs second, then its existing positive routes. These safety states take precedence over live icons, hydration attributes, names/URLs and stale embedded positive membership. Generic Unknown products without a reviewed conflict retain the existing positive routes. Native Add buttons and product links remain usable; no product-page fetching or external service is introduced.

The initial headless Firefox regression reproduced the old defect for a known nonvegan product (17959011) and both source-reviewed cases below: each incorrectly retained the normal Add appearance under a live vegan tag. The final regression exercises these cases, deliberately stale positive membership, late hydration, reuse of a card between blocked and vegan IDs, and native button/link actions.

## Source-reviewed decisions

The two records were previously canonical Unknown. Their retained SQLite evidence exactly matches the source snapshots inspected during the complete current-Unknown review. No fresh live-page verification or new model calls were performed for this runtime fix.

- [M&S Percy Pig Advent Calendar Sweets](https://www.ocado.com/products/527870011) becomes `nonvegan`. Ingredients explicitly include dried whole milk; the description and box contents consistently identify milk chocolate. The vegan statement for the accompanying sweets does not cover the complete calendar.
- [Skittles and Friends Sweets Medium Christmas Selection Box](https://www.ocado.com/products/563256011) stays `unknown` with a promotion block. Description/allergens refer to chocolate products and milk/eggs, while ingredients and box contents refer to a different sweets assortment. This is an unresolved identity/composition conflict, not a confirmed nonvegan finding.

[reviews.json](reviews.json) contains source snapshots, hashes, exact quotations, findings and rationales. The complete previous review and its outcomes remain unchanged. No report was sent to Ocado.

## Persistence and export

`tools/reviewed_safety.py` previews by default. Application requires an immediate transaction, unchanged sources and a verified logical backup. Classification run **314** retains previous decisions and audit references, writes the new canonical decisions, and records source-bound safety rows. The blocked Unknown has `vegan_positive_block=1` and is exported into `BLOCKED_UNKNOWN_PRODUCT_IDS`. Historical conflict records from the cancelled migration are not activated.

The protected baseline validator now includes these explicit safety records. It rejects stale evidence, missing/mismatched audit links, cleared/unreviewed promotion blocks and unapproved classification changes. Ordinary unknown-to-vegan additions cannot clear a reviewed conflict. The safety writer itself only accepts previously unblocked Unknown products and cannot demote protected published/reviewed positives.

All **24,144 vegan IDs are unchanged**, including the complete review's 3,593 additions. The original 13,933 nonvegan IDs remain; the calendar makes **13,934**. Exactly **one** blocked Unknown ID is exported. A comparison of all 59,524 canonical records found only the two intended decision/audit changes. Retained ID placement is preserved, and exporter dry runs bracket generation. See [application.json](application.json) for the backup receipt and [verification.json](verification.json) for final verification.

## Verification and scope

Tests cover precedence, late hydration, stale positive sets, reused cards, native Add actions and real link clicks; persistence tests cover source changes, fabricated quotes, audit linkage, block removal, reapplication, attempted promotion and atomic rollback. The first full-suite attempt exposed an offscreen image hit-test assertion in an existing fixture; the card already had the correct Not vegan appearance. The assertion was corrected consistently with other offscreen fixture cards, and actual link-click coverage was added. Both test logs are retained locally under `.rollback-work/tag-safety-1.8.0/`.

The final suite ran **159 tests: 158 passed, one optional live Ocado check skipped**. Syntax checks and final export verification passed. The script checksum is in [verification.json](verification.json). Temporary Firefox fixtures are distinct from installed FireMonkey verification. Version metadata is 1.8.0; no Git tag, push, main advancement, publication or installed FireMonkey change is part of this work.
