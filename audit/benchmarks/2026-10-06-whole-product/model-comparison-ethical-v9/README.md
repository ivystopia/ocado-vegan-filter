# Ethical v9 development checkpoint

Policy whole-product-v2, registry v5, classifier v22, primary prompt v19, arbitration prompt v10, schema 4, strict citation validation and grounded-unknown-blocker-v2 scoring. This comparison used the passing v13 checks. Frozen inputs and expected statuses/reasons were not changed.

Luna 5.6/high completed the 327 controlled cases with 327 matches, zero final unsafe positives and full positive recall. Its fixed 96-case natural pipeline pilot completed with 91 matches, one unsupported vegan positive (M&S Cider Vinegar) and four conservative demotions: three affirmative Skittles names plus plain M&S coffee beans. Positive recall was 20/24 (83.3%); name recall was 3/6. Sol 6.1/medium arbitration actually ran for two pilot disagreements. The candidate failed safety and usefulness gates and is not qualified.

The original full-pilot model-only diagnostic was checkpointed to avoid repeating passing cases. All active calls finished and were accounted for. It wrote no completed case decisions; 22 attempt records include four pre-invocation STOP guard rejections. The 18 invoked attempts used 464,680 input tokens (26,880 cached) and 175,215 output tokens (122,232 reasoning, already included in output). Six validated call responses do not constitute completed two-pass product assessments. The checkpoint report is retained, not a passing diagnostic.

Complete reports, original manifests, compact decisions and citation errors are retained. Raw call journals/checkpoints remain ignored local data. `pilot-failure-review.json` is preliminary attribution with primary mechanism sources; it is not permission for an expensive-primary fallback. The next runner uses targeted failure diagnostics in a fresh comparison, reusing the completed controls AND pilot only after validating identical classification/persistence/export code, fixture, selection, prompts, schema and call settings. The same observations must not be counted twice in total experimental usage.

No Luna 6 calls were started in v9. No default, canonical SQLite decision, embedded product ID set, installed script, release or remote was changed by this benchmark.
