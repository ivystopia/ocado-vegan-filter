# Astra escalation pilot — 2026-10-05

## Result

Recommend GPT-6 Astra at medium reasoning for the next escalation trial. Medium and high returned identical status/reason pairs on all 15 sampled Luna disagreements, with no within-setting disagreements. Both passed the existing frozen 48-product safety benchmark. This is evidence for a limited escalation trial, not proof of catalogue-wide accuracy or authorization to promote database classifications.

| Metric | Astra medium | Astra high |
| --- | ---: | ---: |
| Pilot products | 15 | 15 |
| Independent passes per product | 2 | 2 |
| Pass disagreements | 0 | 0 |
| Agreed vegan/ingredients | 5 | 5 |
| Agreed nonvegan | 1 | 1 |
| Agreed unknown | 9 | 9 |
| Pilot summed call time | 80.5s | 89.9s |
| Frozen safety benchmark exact statuses | 48/48 | 48/48 |
| Frozen safety false-vegan results | 0 | 0 |
| Frozen safety disagreements | 0 | 0 |
| Frozen safety execution errors | 0 | 0 |
| Frozen safety elapsed time | 208.5s | 237.5s |

High took 11.6% longer on the pilot and 13.9% longer on the safety benchmark, with no observed classification benefit. These are single-run timings under concurrent workloads; token usage and monetary cost were not measured. Neither safety run needed retries.

## Selection and method

The eligible population was 156 products currently unknown whose latest classification audit records a gpt-5.6-luna/high disagreement; all 156 were current_on_ocado = 1. Select the first 15 after sorting by SHA256("astra-benchmark-2026-10-05:" + product_id). This reproducible sample was not selected for easy resolutions or particular outcomes. All 15 selected products are currently on Ocado.

SQLite was opened read-only to freeze current classifier-visible evidence. The historical audit decision and its original context hash are retained for provenance; the newly frozen current-context hashes are separate. This is not a claim that we reconstructed the original historical prompt or its batch composition. No fresh Luna control was run, so the pilot does not isolate model improvement from repeat-run variability.

Each configuration used model ID gpt-6-astra and reasoning effort medium or high separately, with two independent ephemeral Codex sessions per batch and batches of 10 then 5. The unchanged production prompt, output schema, validation and conservative merge function were used. Astra received only the product evidence, not the Luna answers, historical unknown label, supervising review notes or the other Astra pass. Browsing was disabled and the prompt forbade file reads and external research. Medium and high ran concurrently; batches and passes within each configuration ran sequentially. All eight pilot calls returned schema-valid complete output and exit status zero, with no retries.

Official model documentation confirms the requested model and reasoning settings: https://developers.openai.com/api/docs/models/gpt-6-astra . The actual successful Codex executions establish availability for this account.

The existing frozen safety benchmark ran unchanged for each setting using two passes and batch size 10. Its 48 reference labels and contexts were not changed. Pilot historical unknown outcomes were not treated as gold labels: agreement and resolution counts are reported separately from accuracy on the established safety benchmark. Supervising pre-result notes and post-result evidence review are retained, but are not independent ground truth.

## Per-product pilot outcomes

Both configurations and all four individual decisions agreed on these status/reason pairs.

| Product | ID | Status | Vegan reason |
| --- | --- | --- | --- |
| England Football Size 5 | 694001011 | unknown | — |
| Cypressa Hand-Picked Whole Kalamata Olives | 633002011 | vegan | ingredients |
| M&S Boys Cotton Rich Animal Socks, 12-24 Months, Multi | 691539011 | unknown | — |
| Wray & Nephew 43 | 698034011 | vegan | ingredients |
| M&S Recycled Sports Swimsuit, 7-8 Years, Black | 599102011 | unknown | — |
| Cincoro Anejo Tequila | 662752011 | unknown | — |
| England FA 750ml Premium Stainless Steel Bottle | 693999011 | unknown | — |
| Scrumbles Dog Food Grain Free Veggie | 606252011 | vegan | ingredients |
| M&S Womens 60 Denier Body Sensor Tights, M, Navy | 683274011 | unknown | — |
| Big K Barbecue Coconut Shell Briquettes | 468269011 | unknown | — |
| Lavazza A Modo Mio Espresso Passionale Coffee Capsules | 589716011 | vegan | ingredients |
| 123 Baby Sipper Cup with Handle and Dust Cover 260ml/8oz Blue | 700310011 | unknown | — |
| Mozart Chocolate Cream Liqueur | 452849011 | nonvegan | — |
| Estrella Galicia Premium Spanish Lager | 640656011 | vegan | ingredients |
| Adios Compostable & Biodegradable Dog Poo Bags - Grey | 524647011 | unknown | — |

## Evidence review and limits

The six resolved model outcomes comprise five ingredient/identity-based vegan decisions and one nonvegan decision. Coffee is explicitly 100% Arabica roasted ground coffee; serving suggestions involving milk and capsule packaging do not establish animal ingredients in the coffee. The cream liqueur has a product-specific milk allergen statement beyond the may-contain prefix; its marketing vegan claim applies to another variant. The other vegan outcomes follow the current listed-ingredient and unambiguous-identity rules for olives, plain rum, plant-based dog food and lager. These are not new manufacturer confirmations.

The nine remaining unknown decisions appropriately preserve uncertainty about manufactured goods and briquette binders, and the conflicting Anejo/Reposado tequila identity. Full product-level review is retained in evidence-review.json. The rum, lager and wine-vinegar decisions particularly depend on how the existing evidence policy treats identity and unlisted processing inputs. If that policy is tightened, assess it as a separate rule change rather than claiming this benchmark externally verifies their recipes or processing.

A 15-product sample with no observed false-vegan concern does not establish an error rate or guarantee safety. Two agreeing passes can share an unsupported inference. The measured 6/15 resolution rate must not be extrapolated to all 156 disagreements without a larger reviewed evaluation. Before adopting an automatic arbitration policy, explicitly define when Astra may supersede a Luna disagreement, preserve the original audit trail, and keep unknown on unresolved evidence or Astra disagreement.

## Artifacts and reproduction

cohort.json contains frozen classifier-visible inputs, SHA256 hashes, selection method and historical audit provenance. prompt-batch-*.txt and output-schema.json preserve the actual prompt/schema; environment.json records the source revision and versions. *-pass-*-batch-*.json retain raw responses, parsed decisions, exit status and timings; *-summary.json retain conservative merged results. safety-*.json and safety-*.log retain the frozen benchmark results and execution logs.

Run the pilot from the repository root with `python3 audit/benchmarks/2026-10-05-astra-disagreements/run.py`. Existing matching call files are reused without model calls. To make a new independent run, copy the runner and cohort into a new sibling directory and preserve the original results. The runner depends on the recorded production classifier implementation; changing it requires reviewing prompt hashes and recording a separate experiment.

Safety reproduction (write to new output paths to preserve these results):

```sh
python3 tools/benchmark_ocado_vegan.py --model gpt-6-astra --reasoning-effort medium --json-output /tmp/astra-medium-safety-new.json
python3 tools/benchmark_ocado_vegan.py --model gpt-6-astra --reasoning-effort high --json-output /tmp/astra-high-safety-new.json
```

No database classifications, production classifier defaults, userscript, release tags or installed scripts were changed. Validation comprised real model runs, frozen input/response checks, cached reproduction and runner compilation. The full application/browser suite was not run because runtime code was unchanged.
