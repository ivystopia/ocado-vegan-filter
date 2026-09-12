# Userscript refactor and Firefox audit — 2026-09-12

This audit revisits the runtime introduced in 1.7.0, using the published source as the baseline. Version 1.7.1 simplifies cosmetic state management, preserves Ocado's changing controls and image styles, and reduces measured card-processing work. The classification policy and all embedded product IDs are unchanged.

## Architecture and correctness

The main remaining cost was repeated DOM inspection and mutation around appearance. The embedded sets, bounded hydration index, incremental card queue, and 8 ms scheduling budget remain appropriate; replacing them or adding dependencies would not address that cost.

- Apply the verified secondary-button palette through one script-owned CSS class. Remove generated class-name discovery, template copying, cache invalidation, and class replacement. Ocado keeps its native layout classes, original buttons, and handlers, including subsequent changes while a card is muted.
- Store button labels in a `WeakMap` instead of several DOM attributes. Hover and keyboard focus show “Add anyway”. Restoring a card also handles a cloned muted button without a map entry, and preserves a button that Ocado has turned into a quantity control.
- Let the stylesheet handle ordinary images without writing inline styles or metadata. Only conflicting inline `!important` properties need saved overrides. Capture later host changes and restore a property only while its value is still owned by the script. Keep responsive `src`, `srcset`, and `sizes` intact.
- Share image selectors between CSS and DOM inspection. Move legacy image cleanup to startup. Share card discovery between initial and inserted content, and remove the redundant document-wide Add-button pass.
- Check embedded positive evidence before more expensive DOM evidence. Live and embedded official vegan evidence still override the known non-vegan fallback.

The CSS cascade explains the exceptional inline path: stylesheet `!important` overrides ordinary inline declarations, while inline `!important` needs explicit handling. Weak keys let detached elements and their state be collected together. These choices follow [MDN's cascade documentation](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/important) and [WeakMap documentation](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/WeakMap).

Clean Firefox reproductions demonstrated lost host button classes and lost subsequent image-opacity changes before the fix. Regression coverage also found a cloned-button restoration error during the refactor, which was fixed before final measurements. Five new discoverable tests cover those cases, native classes while muted, keyboard activation and changed counter attributes, and zero script writes to ordinary/lazy images. Hover checks now use real WebDriver pointer movement.

The section from `addStyles()` through startup shrank from 728 to 574 lines. This is a readability result, not the basis for the performance claims. The fixed palette now needs a visual check if Ocado changes its design; it deliberately leaves host layout and focus styling intact.

## Performance measurements

| Scenario | Cards | Repetitions | Initial work, before → after | Longest initial call, before → after |
| --- | ---: | ---: | ---: | ---: |
| Retained offers | 74 | 5 | 10 → 8 ms | 9 → 8 ms |
| Retained milk | 75 | 5 | 10 → 7 ms | 9 → 8 ms |
| Stress offers | 592 | 3 | 56 → 35 ms | 12 → 11 ms |
| Stress milk | 600 | 3 | 51 → 37 ms | 12 → 11 ms |
| Live offers | 51 | 3 | 11 → 9 ms | 9 → 9 ms |
| Live milk | 50 | 3 | 10 → 9 ms | 9 → 9 ms |

Initial work is the median sum of instrumented scheduled filtering calls, in milliseconds. The maximum is the longest individual call across the repetitions. Replays use the same retained public markup, images, CSS, and reduced hydration fixture on both sides. Stress cases repeat the cards eight times. The baseline is commit `15974b4`, whose userscript matches the published 1.7.0 source byte-for-byte. Runs used Firefox 155.0.1 on the same AMD Ryzen 9 3900X Linux desktop, sequentially without concurrent browser test jobs.

Ordinary replay filtering work fell by 20–30%; stress replay work fell by 27–38%. Twenty unrelated mutations still cause no filtering calls, and twenty single-card mutations still process exactly twenty cards. Their median totals were offers 4 → 4 ms, milk 4 → 3 ms, stress offers 3 → 6 ms, and stress milk 5 → 2 ms. Small mutation totals are noisy at the browser clock resolution; there is no uniform improvement claim for that path. Compilation/initialisation also did not uniformly improve: the stress offers median rose from 7 to 13 ms, while total initial work remained lower. Across eight live scroll steps, median filtering work fell from 47 to 36 ms on offers and 52 to 40 ms on milk; the maximum observed scroll call fell from 12 to 10 ms and 7 to 5 ms respectively.

Every retained replay and stress repetition produced the same card classifications, button labels, and disabled states before and after. The benchmark now retains browser and exact source-hash metadata in live output as well as replay output.

These are userscript JavaScript measurements, not page-load time, INP, paint cost, memory usage, or a guaranteed frame rate. Compilation/initialisation is recorded separately. Timing precision, instrumentation, background activity, and changing live content limit small comparisons. The 8 ms budget is cooperative: discovery or one card can exceed it. Mobile checks exercise responsive layouts on a desktop CPU, not slower physical phones. No classifier or database performance changes were made.

## Browser coverage

The final release passes all 79 repository tests with `OCADO_LIVE_TESTS=1`, with no skips. Syntax and targeted Ruff checks pass. The exporter dry run reports zero additions and removals; the four embedded sets still contain 4,669 official vegan, 8,291 manufacturer/name, 6,153 ingredients, and 13,933 known non-vegan IDs. The recognised vegan union remains 19,113. These are all-known exported IDs, not current-catalogue counts. Classifier rules and decisions were unchanged, so this task did not invoke the separate model safety benchmark.

The reusable [live scenario checker](../tools/check_ocado_userscript_scenarios.py) verifies 18 combinations: milk, vegan cheese, and offers; initial and scrolled content; and actual viewport widths of 390, 768, and 1440 CSS pixels. Firefox's outer-window sizing silently clamped the narrowest case to 500 pixels during initial tooling work; the checker now uses WebDriver BiDi viewport control and asserts the actual width. It dismisses the anonymous advertising-consent overlay through its visible control before checking hit targets.

Checks cover classification, labels, muted images, button text overflow, and visible image-link/Add-button hit targets. Virtual placeholder cards are counted separately from rendered products and are not treated as classified products. Representative screenshots are retained at [390 pixels](../audit/firefox/2026-09-12/milk-390.png), [768 pixels](../audit/firefox/2026-09-12/milk-768.png), and [1440 pixels](../audit/firefox/2026-09-12/milk-1440.png).

Actual installed FireMonkey testing is separate from injected-script tests. In the user's already-running Firefox, 17 grid scenarios covered milk with prolonged scrolling, same-document search to vegan cheese, offers, an organic category, manufacturer-evidence gherkins, bread, washing-up liquid, and carrots, including scrolling on each. Nine further checks covered price sorting and restoration of the original sort, clicking a product link, Back/Forward navigation, an empty search, and recovery through the search form. No basket changes were made. Real shopping controls were checked for hit targets; handler activation and quantity changes were exercised in isolated fixtures.

## Proving which source was active

The development build tested through FireMonkey has SHA-256 `e5ea9c7c80066c4d9a9ef9306344db430cb3438f9bac7defb71ae2d63a6c63e3`. Its source was verified exactly in extension storage, then the running extension's new stylesheet, preserved native classes, and lack of old image markers were checked. A temporary hidden cloned-card fixture exercised the active extension and passed the regression that had failed before the fix. The same active checks and the complete installed scenario matrix were repeated after tagging 1.7.1; the retained installed reports identify the release hash.

Testing also revealed an enabled duplicate Ocado script in Violentmonkey. That explained old styling appearing alongside FireMonkey. After backing up both stores, only the duplicate Ocado entry was disabled; its source and other scripts were preserved. FireMonkey's 36 unrelated storage entries were also verified unchanged. Version labels alone were not used as proof of active code.

The requested release is **1.7.1**, SHA-256 `5783204bb6911b38cbcf76908bd4e67086c813059ac0f3b822c8c2837b47b5a8`. It differs from the verified development source only in the `@version` line. The full suite, responsive matrix, and final benchmarks were rerun against these release bytes. A patch increment fits the compatible fixes to existing filtering behaviour under [SemVer](https://semver.org/).

The signed local tag `1.7.1` points to commit `0ff48c703bed598beb57eca65dd9c25b06a1c225`. Installation used that tagged source, backed up FireMonkey again, preserved unrelated rows, and verified exact bytes through a fresh temporary profile. FireMonkey was then disabled, the task Ocado tab reloaded with no filter present, FireMonkey re-enabled, and the tab hard-refreshed. The running extension reported the exact tagged source without requiring a cached-storage rewrite. Its stylesheet and active cloned-card regression passed again. The installed grid/navigation matrix was also repeated on the tagged build. The [installation evidence](../audit/firefox/2026-09-12/installation.json) records these separate checks. The tag is local; nothing was pushed or published. Audit documentation is committed after the tag without changing the tagged userscript. The task-created window, private debugging server, and control process were closed; the original Firefox process and user window were preserved.

## Reproduce and inspect

With the repository virtual environment active:

```sh
node --check ocado-vegan-filter.user.js
OCADO_LIVE_TESTS=1 python3 -m unittest discover -s tests -v
python3 tools/check_ocado_userscript_scenarios.py --output /tmp/ocado-responsive/checks.json
python3 tools/benchmark_ocado_userscript.py --repeats 5 --output /tmp/ocado-replay.json
python3 tools/benchmark_ocado_userscript.py --stress --repeats 3 --output /tmp/ocado-stress.json
python3 tools/benchmark_ocado_userscript.py --live --repeats 3 --output /tmp/ocado-live.json
```

To measure the baseline, export `git show 15974b4:ocado-vegan-filter.user.js` to a temporary file and pass it with `--userscript`. Do not replace the working source or installed script to run a replay. Installation and active-extension verification follow the [FireMonkey runbook](firemonkey-installation.md).

- Replay: [before](../audit/firefox/2026-09-12/before-replay.json), [after](../audit/firefox/2026-09-12/after-replay.json).
- Stress: [before](../audit/firefox/2026-09-12/before-stress.json), [after](../audit/firefox/2026-09-12/after-stress.json).
- Live timing: [before](../audit/firefox/2026-09-12/before-live.json), [after](../audit/firefox/2026-09-12/after-live.json). The older baseline live format has no header; its source provenance is recorded in the validation summary.
- Behaviour: [responsive release checks](../audit/firefox/2026-09-12/responsive-live.json), [installed release grids](../audit/firefox/2026-09-12/installed-live.json), [installed release navigation](../audit/firefox/2026-09-12/installed-navigation.json), and [complete release test output](../audit/firefox/2026-09-12/tests.log).
- [Validation and measurement summary](../audit/firefox/2026-09-12/validation.json).
