# Ethical model comparison v15: completed pilot, not qualified

This development-only comparison used ethical fixture v6, classifier v29, primary prompt v22, arbitration prompt v13 and passing preflight v23. Exact configurations and source hashes are retained in the manifests. Both candidates reused their complete, compatible v29 controlled-stage reports; these reused observations are not additional fresh calls.

Both fresh 96-case pilots matched every expected status, recovered all 24 eligible positives and produced no final unsafe positive or unresolved operational failure. Neither pilot triggered arbitration. Both nevertheless failed the mandatory reason gate for `natural.634499011`: their two primary passes cited the affirmative name and returned `vegan/name`, omitting the supplied producer assertion that supports the higher-priority manufacturer reason. Separate fresh one-case diagnostics repeated that omission. The result is `primary_failure_review_required`, not qualification.

| Fresh stage | Calls | Rejected attempts | Input tokens | Cached input | Output tokens | Reasoning within output | Standard-credit proxy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Luna 5.6/high pilot | 58 | 17 | 1,905,129 | 17,920 | 507,560 | 352,811 | 24.671805 |
| Luna 5.6/high diagnostic | 2 | 0 | 29,260 | 0 | 5,949 | 4,427 | 0.324770 |
| Luna 6/high pilot | 30 | 3 | 1,118,786 | 19,968 | 114,158 | 42,035 | 4.179012 |
| Luna 6/high diagnostic | 2 | 0 | 30,188 | 0 | 2,522 | 1,032 | 0.106995 |

Every fresh attempt has retained usage; there were no guard rejections or missing usage records. Local citation/ID validation rejected the failed attempts before accepting decisions. The proxy uses the standard-credit rates checked on 2026-10-06 at https://learn.chatgpt.com/docs/pricing, accounts for cache separately and does not add reasoning twice. It is neither an included-plan allowance estimate nor a charge. Luna 6's pilot proxy was approximately 83% lower on these matched inputs; this does not establish eligibility or catalogue-wide savings.

Failure attribution found a shared prompt/reference definition gap: annotation v11 explicitly describes declarative producer assertions whose subject repeats a vegan SKU, while production v22 only described producer self-descriptions more generally. Subsequent classifier v30 embeds the exact shared definition in both classification prompts. The annotation policy string and frozen v6 references remain unchanged. These v29 pilot failures remain retained and cannot qualify or be reused after that classification-prompt change. An expensive primary upgrade was not authorised by this shared defect.

`completed-stage-summary.json`, each stage's `report.json`, manifests, call records and compressed cited decisions retain the observations and failed attempts. No expanded development, arbitration comparison, locked evaluation, workload run, default-model change, canonical migration or allowlist activation followed from this comparison. Independent natural name-reference quotas remain incomplete; model agreement is not ground truth.
