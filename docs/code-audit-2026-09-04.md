# Code audit and Firefox measurements — 2026-09-04

Version 1.7.0 removes the largest measured sources of userscript work, fixes stale state when Ocado reuses product cards, and protects catalogue exports after interrupted syncs.
All 74 tests passed, including the opt-in live Firefox checks; the frozen classifier safety benchmark passed 48/48 with no false-vegan results.

## Browser performance

Measurements used real Firefox 155.0 in clean headless profiles on an AMD Ryzen 9 3900X, with a 1440 × 1000 viewport.
The baseline is commit `1aa4d49`, containing the August catalogue refresh and the pre-audit browser implementation.
The final measured userscript has SHA-256 `dd43faeedf2f275c402484e182382a4914a3dda0d860a046e716655aa82eaa2f`.

Each number below measures instrumented userscript JavaScript time in milliseconds.
Initial work is the median total time across the initial scheduled calls; longest task is the maximum individual call across all repetitions.

| Scenario | Cards | Repetitions | Initial work, before → after | Longest initial task, before → after |
| --- | ---: | ---: | ---: | ---: |
| Retained offers page | 74 | 5 | 282 → 10 ms | 298 → 9 ms |
| Retained milk search | 75 | 5 | 292 → 9 ms | 301 → 9 ms |
| Offers stress replay | 592 | 3 | 662 → 58 ms | 699 → 12 ms |
| Milk stress replay | 600 | 3 | 572 → 50 ms | 588 → 12 ms |
| Live offers page | 50 | 3 | 121 → 12 ms | 124 → 9 ms |
| Live milk search | 51 | 3 | 87 → 11 ms | 88 → 9 ms |

Twenty single-card mutations now process 20 cards in total, instead of repeatedly processing the whole grid.
Their median total work fell from 73 to 2 ms on the retained offers page and from 63 to 4 ms on milk search; the corresponding stress cases fell from 592 and 475 ms to 5 ms each.
Twenty unrelated header mutations trigger no card processing or scheduled filtering calls.
Across eight live scroll steps, median userscript work fell from 440 to 50 ms on offers and from 682 to 50 ms on milk search.
The longest observed final filtering call during those scroll steps was 6 ms.

### What changed and why

- **Use CSS to desaturate images.** The old implementation synchronously drew images into a canvas, edited every pixel, encoded PNG data URLs, and replaced responsive image sources while also applying CSS filters. Diagnostic replays attributed roughly 228–244 ms per ordinary grid to the canvas path. Firefox screenshots and pixel samples confirmed that CSS alone retains the intended appearance. Removing that path also removes its image cache and lets Ocado retain control of image loading and resolution.
- **Process affected cards and yield between batches.** A dirty-card queue replaces repeated whole-document processing. The observer ignores irrelevant changes, handles text-node and namespaced SVG updates, and indexes new hydration roots when needed. Card processing yields after an 8 ms budget, allowing large grids to complete over several frames. A single card or initial discovery can still exceed that budget; it is not a hard maximum on slower devices.
- **Narrow native button-style discovery.** Startup profiling found a further 253 ms spent scanning every element three times on a 592-card grid. A native class-substring selector now narrows candidates before applying the existing class-name pattern. Negative lookup results are cached. This removed the remaining large initial pause in the measured cases.

The embedded sets were not the dominant measured cost: script evaluation and initialisation were around 9 ms in the original replays.
The release retains the self-contained ID sets and makes no new requests while shopping.

### Measurement limits

These are script-work measurements, not whole-page load time, INP, paint cost, memory measurements, or a guaranteed frame rate.
Instrumentation adds some overhead, and the before/after live pages were measured at different times; live Ocado content and background activity vary.
The retained fixture supplies public Ocado card markup, CSS, product images, and reduced hydration data from two pages, so it makes the comparison repeatable without replaying the complete Ocado application.
Stress cases duplicate those cards eight times; they test grid scaling rather than a real 600-distinct-product page.
Runs were sequential, with diagnostic variants rotated between repetitions; there was no CPU throttling or low-end/mobile hardware coverage.

The benchmark injects the userscript into a clean Firefox page and does not exercise FireMonkey's installation, registration, or update lifecycle.
The user's installed script and real browser profile were not changed.

## Correctness and operational fixes

- **Recycled images:** the old restoration path could put a previous product's image back after Ocado reused the element. The script now preserves `src`, `srcset`, and `sizes`, and restores only inline style values it still owns. Legacy generated sources are restored only while still pointing at a data URL.
- **Recycled basket controls:** a reused Add button could become an increase-quantity control while retaining the script's muted class; the script then replaced its label with `Unknown vegan`. Add-button detection now checks the current accessible label, and restoration preserves host-owned class and text changes. Tests verify the original clickable control and handler survive.
- **Changed names and vegan icons:** text-node updates previously left a product looking vegan after the word “vegan” was removed. Mutation routing now includes text nodes and namespaced SVG references, with regression coverage for classification changes and new hydration roots.
- **Malformed hydration:** a non-array `attributes` value previously threw an exception and abandoned processing of subsequent cards. Invalid entries are ignored conservatively, while valid official vegan metadata retains precedence.
- **Interrupted catalogue syncs:** the pre-sync classification context lived in a temporary SQLite table. Reopening the database after failure lost that baseline, allowing a changed product to keep stale vegan evidence even after a successful retry. The baseline now survives connection/process failure and is removed within successful finalisation. Userscript export rejects incomplete syncs, an outstanding baseline, or unclassified current products. A temporary-database reproduction demonstrated the failure with a previously vegan sauce whose ingredients changed to include milk; the regression now verifies invalidation after restart and retry.
- **Classifier benchmark drift:** expected labels were fixed but inputs were read from the changing live catalogue. The first audit run scored 47/48 because a product formerly lacking vegan evidence had since gained explicit manufacturer vegan text. The retained SQLite history supplied the original contexts; benchmark inputs are now frozen, hashed, and validated independently of the live database. Expected labels and classifier rules were not changed to obtain a pass.
- **Release checks:** browser tests were ordinary module functions and were silently skipped by unittest discovery. They now run through `TestCase`; live tests remain opt-in. CI runs the actual Firefox fixture suite, and the GitHub release job waits for it and checks exact equality between the tag and metadata version. Actions were updated to their current stable major versions during the audit.

## Validation and catalogue state

The final complete test run used `OCADO_LIVE_TESTS=1` and passed 74 tests in 25.962 seconds with no skips.
`node --check ocado-vegan-filter.user.js` passed, the benchmark tool passed Ruff checks, and both workflow files parsed successfully as YAML.
CI configuration was checked locally; no remote workflow was triggered during this audit.
All replayed card classifications, button labels, and disabled states matched the baseline, including every stress repetition.
Firefox regression tests additionally cover image/style ownership, recycled counters, malformed hydration, incremental updates, newly inserted cards, links, and button clicks.

The rerun of the frozen safety benchmark used `gpt-5.6-luna`, reasoning effort `high`, and two independent passes: 48/48 exact, zero false-vegan results, zero disagreements, zero execution errors, in 187.2 seconds.
Both the initial drifting-input result and the corrected frozen-input run are retained so that the reason for the rerun remains visible.
No product classifications or classifier rules were changed by this audit.

SQLite contains 54,418 known products, of which 49,951 are current on Ocado.
There are 19,113 known vegan products, including 17,449 current products; all current products are classified.
The embedded sets exactly match SQLite: 4,669 officially tagged vegan IDs, 8,291 manufacturer/name IDs, 6,153 ingredients IDs, and 13,933 non-vegan IDs.
The export dry run found zero additions and zero removals.
The August refresh is described in the [monthly refresh report](../audit/monthly/2026-08-21.md).

## Reproduction and retained evidence

Install `requirements-dev.txt` in a virtual environment with Firefox available, then run:

```sh
python tools/benchmark_ocado_userscript.py --output /tmp/ocado-replay.json
python tools/benchmark_ocado_userscript.py --stress --repeats 3 --output /tmp/ocado-stress.json
python tools/benchmark_ocado_userscript.py --live --repeats 3 --output /tmp/ocado-live.json
```

Set `FIREFOX_BINARY` if needed.
Replay pages are served only on localhost and block external resources with a Content Security Policy.
The live command reads public offers/search pages and scrolls them; it does not click shopping controls.
Selenium may download its driver when one is not already installed.

To reproduce the original implementation, export it without changing the checkout:

```sh
git show 1aa4d49:ocado-vegan-filter.user.js > /tmp/ocado-before.user.js
python tools/benchmark_ocado_userscript.py --userscript /tmp/ocado-before.user.js --output /tmp/ocado-before.json
```

The optional `--variants` diagnostic ablations require that original source and are not release implementations.

- [Frozen Firefox fixture](../benchmarks/firefox-2026-09-04.zip), SHA-256 `8774c01eb51db64c168c236fe81652140293362c714329559acde22c0067e94b`.
- [Baseline timings and bug reproductions](../audit/firefox/2026-09-04/baseline.json).
- [Final replay results](../audit/firefox/2026-09-04/after-replay.json), [stress results](../audit/firefox/2026-09-04/after-stress.json), and [live results](../audit/firefox/2026-09-04/after-live.json).
- [CSS-only appearance check](../audit/firefox/2026-09-04/css-only-replay.png).
- [Final validation summary](../audit/firefox/2026-09-04/validation.json) and [complete test output](../audit/firefox/2026-09-04/tests.log).
- [Initial classifier result with drifting inputs](../audit/benchmarks/2026-09-04-luna-high-unfrozen.json) and [frozen-input rerun](../audit/benchmarks/2026-09-04-luna-high-frozen.json).

## Further improvements worth considering

1. Repeat the Firefox measurements on a slower laptop and through the user's installed FireMonkey before drawing conclusions about low-end responsiveness or manager-specific behaviour. Additional captured pages would broaden coverage of Ocado layout changes.
2. Normalise storage of classifier batch responses in a separately planned database migration. The local database is about 2.5 GB, and the per-product audit rows contain about 1.38 billion characters of raw and parsed responses, often repeating the same batch response. Storing each batch response once could reduce maintenance I/O and backup size while preserving every decision's evidence. This needs migration and recovery testing and offers no direct shopping-browser benefit.

Neither remaining opportunity requires adding runtime dependencies, external requests, or broader classification rules to the userscript.
