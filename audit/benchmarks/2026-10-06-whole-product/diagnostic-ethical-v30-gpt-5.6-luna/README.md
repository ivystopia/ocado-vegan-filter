# V30 producer-assertion diagnostic

This primary-only diagnostic used 18 frozen assertion/name/topic contrasts, classifier v30, primary prompt v23, arbitration prompt v14, fixture v6 and preflight v24. Exactly two independent primary assessments were requested; primary disagreements remain unknown here because this diagnostic mode does not run arbitration. These outcomes cannot be treated as final production-pipeline failures or qualification.

The run is checkpointed; 10 cases were scored, 0 had an operational failure and 8 remain unrun. There were no final unsafe positives among the scored cases. `summary.json` reports disagreements, mismatches and complete attempt usage from the journal, including attempts for unscored cases. Run-level scored metrics alone do not account for every interrupted attempt.

A constructed local assessment independently reproduced a brand-only manufacturer false positive in the shared cited-claim validator. A stop file prevented new calls; admitted calls finished and their usage was preserved. The Luna 6 run completed before that stop prevented further work, while Luna 5.6 checkpointed. V31 repairs this shared defect; old results remain unchanged and cannot be reused after the source change. The next diagnostic uses the full production pipeline rather than primary-only mode. No production database, embedded IDs or defaults changed.
