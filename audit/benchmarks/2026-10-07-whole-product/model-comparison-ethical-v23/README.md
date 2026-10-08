# Fixed whole-product candidate v48: development model comparison

This retained comparison uses ethical-cases-v21, policy whole-product-v2, schema 5, knowledge registry v7, classifier v48, primary prompt v29, arbitration prompt v20 and passing preflight v49. Included-plan calls only; no API, paid-credit or model fallback. Development-only results cannot qualify a release or authorise activation. The conditional Sol primary run followed the hash-bound failure review after both Luna candidates failed. Completed cheap stages were reused only under the identical comparison manifest, without new cheap-stage calls.

## Completed stages

| Primary | Stage | Valid / expected | Status mismatches | Reason errors | Operational errors | Final unsafe positives | Calls (rejected) | Standard-credit proxy |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Luna 5.6/high | [Controls](controls-gpt-5.6-luna-high/README.md) | 432 / 435 | 4 | 0 | 3 | 0 | 137 (17) | 33.8173850 |
| Luna 5.6/high | [Natural pilot](pilot-gpt-5.6-luna-high/README.md) | 95 / 96 | 0 | 0 | 1 | 0 | 55 (16) | 25.1891900 |
| Luna 6/high | [Controls](controls-gpt-6-luna-high/README.md) | 435 / 435 | 1 | 0 | 0 | 0 | 113 (5) | 9.2795340 |
| Luna 6/high | [Natural pilot](pilot-gpt-6-luna-high/README.md) | 96 / 96 | 0 | 0 | 0 | 0 | 38 (4) | 7.2682495 |
| Sol 6.1/medium | [Controls](controls-sol-primary/README.md) | 435 / 435 | 0 | 0 | 0 | 0 | 104 (0) | 125.1067000 |
| Sol 6.1/medium | [Natural pilot](pilot-sol-primary/README.md) | 96 / 96 | 0 | 0 | 0 | 0 | 25 (1) | 95.4408000 |
| Sol 6.1/medium | [Development (stopped)](development-sol-primary/README.md) | 104 / 413 | 0 | 1 | 0 | 0 | 26 (0) | 88.9364500 |

Luna 5.6 misses four mandatory positives and exhausts protocol retries on three controls plus one natural pilot case. Luna 6 passes its natural pilot but misses the mandatory affirmative whole-product Vegan seed-name control. Both are disqualified. Their matched controls/pilot proxies are 59.006575 and 16.5477835 respectively (approximately 72.0% lower for Luna 6); the price advantage cannot erase the mandatory-control failure. The separate Luna 5.6 one-case diagnostic is accounted for in experimental usage, but excluded from this matched comparison and cannot repair the failed pilot.

Sol passes all 435 controls. One raw disagreement receives exactly two fresh blind arbitration readings, and three raw agreed unsupported positives are blocked by the shared gate. It also passes all 96 natural pilot cases. The development stage stops after 104/413 cases with one manufacturer-to-name reason failure; 309 cases are explicitly unrun. Exact replay attributes this to a shared parser veto of quantified nutrition per serving. Both raw model readings correctly choose manufacturer. No primary qualifies, so arbitration comparisons and paired repetitions are unrun. The terminal comparison state is no_eligible_primary; no model recommendation is produced.

## Terminal outcome

The reproduced shared-code defect is retained in nutrition-serving-code-attribution.json. Correcting it requires a new fixed candidate and fresh model validation; neither the previously passed stages nor constructed proof replay can qualify the repaired code. All 500 actual calls across completed/stopped stages and the separate diagnostic have complete reported usage. The experimental Standard-credit proxy total is 385.6443085. No later model stages were admitted after disqualification.

## Evidence and limitations

Each completed stage retains a summary, immutable manifest, exact frozen input copy/binding and a compressed cited-decision projection. Raw local model-call events, invocation and quota account details are omitted from the reviewable projections. Failed attempts, citations, actual usage and stage outcomes remain available. Reasoning tokens are included in output, not added twice. Standard-credit proxies are workload comparisons under the documented public rate snapshot, not charges, GBP amounts or included-plan quota measurements.

The fixed references assess sufficiency of supplied evidence under the accepted policy; model agreement and historical catalogue labels are not ground truth or physical manufacturing guarantees. Constructed proof replays demonstrate unchanged validator/policy behaviour and never qualify the actual model outputs. The natural name-only quota remains short by 13; locked current-policy references, locked repetitions, workload evaluation, model-pair selection, shadow migration and the random-positive audit remain required before activation.

The live database, embedded product ID sets, installed FireMonkey script and historical defaults remain unchanged. No publication, push or contact with Ocado has occurred. No checkpoint commit is made.
