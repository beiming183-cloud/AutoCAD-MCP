# Failure Learning and Gate Repair

Read this file whenever the user reports that a result is unattractive, impractical, misaligned, unequal, hard to understand, unsafe-looking, or otherwise inconsistent with the brief; also use it after a prototype failure, escaped gate, repeated tool limitation, or stale cross-view result.

## Required Record

Create one revision-bound record for each distinct problem:

```text
failure_id:
product_document_revision:
user_report_or_observed_problem:
evidence_and_location:
impact_and_affected_states:
escaped_gate_and_why_it_did_not_detect:
root_cause_in_design_tool_data_or_process:
contributing_conditions:
temporary_containment:
permanent_source_correction:
permanent_process_or_skill_change:
next_revision_verification_method:
acceptance_criteria:
owner_or_decision_authority:
affected_artifacts_and_revisions:
recheck_result:
```

Separate the symptom from the root cause. “USB openings are unequal” is a symptom; independent parameters, stale projections, or missing repeated-feature checks may be root causes. “The product looks like boxes” is a symptom; skipped massing review, primitive-only backend, or undefined form grammar may be root causes.

## Closure Rules

- Repair the smallest owning source: brief, design DNA, parameter, component hierarchy, native feature, camera, rule deck, or workflow gate.
- Do not close a failure by editing only a screenshot, render, dimension, or explanatory note when the authoritative source remains wrong.
- Add or strengthen a permanent gate, checklist item, schema rule, deterministic validator, fixture, or reviewer responsibility when the failure could recur.
- Recreate every affected view, configuration, table, drawing, and manifest from the corrected source.
- Verify the next revision with a method that would have caught the original defect before delivery.
- Preserve the failed artifact and disposition when it provides useful regression evidence.

`FAILURE_LEARNING_GATE` passes only when root cause is evidence-backed, containment is bounded, the source correction is complete, recurrence prevention changed the owning process, and the next-version verification has a result. A promise to “pay more attention” does not pass.
