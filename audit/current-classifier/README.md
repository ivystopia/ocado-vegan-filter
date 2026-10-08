# Current classifier validation

The applicable frozen control and natural-pilot reports are retained byte-for-byte in `controls.json` and `pilot.json`. Their manifests bind the assessed model, prompts, policy, schema, fixture and classification implementation. Reuse them for the unchanged classifier; do not claim a fresh run.

Controls: 496/497 exact, 195/196 positive recall, one accepted conservative unknown, zero final unsafe positives or operational failures. Pilot: 96/96 exact, 24/24 positives, zero final unsafe positives or operational failures. Raw proposals rejected by the shared gate remain visible in the reports.

These reports do not establish exhaustive catalogue accuracy or an unconditional manufacturing guarantee. The catalogue migration still needs complete product assessment, manufacturer research, final positive audit and persistence/export/browser validation.
