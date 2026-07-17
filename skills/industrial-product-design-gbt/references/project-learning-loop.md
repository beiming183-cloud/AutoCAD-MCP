# Cross-Window Project Learning Loop

Read this file when another task/window produces a design, review, correction, user preference, backend failure, prototype result, or final choice that should inform later projects.

## Minimum Useful Handoff

Ask the other window to return one structured record containing:

- `project identity`: project ID, exact revision/configuration, iteration index, branch, parent record, maturity, date, and Skill or workflow version.
- `intent`: task summary, user scenario, immutable design DNA, intended product character, and critical constraints.
- `artifacts`: authoritative CAD path or ID, stable review views, relevant hashes, and evidence locations. Prefer references over copying large or private files.
- `feedback`: the user's exact words, category, target view/feature/state, expected result, observed result, and severity.
- `decision`: selected direction, rejected alternatives, rationale, what changed, and whether the user accepted the revision.
- `verification`: before/after evidence, checks rerun, measurements or review disposition, unresolved items, and next test.
- `learning proposal`: project-only fact, tentative reusable pattern, explicit cross-project preference, backend limitation, or proposed permanent gate change.
- `privacy`: whether the record may be stored locally and whether any generalized part may be published.

The most valuable feedback identifies a location and comparison. “不好看” is useful but incomplete; “正面 USB 组比 AC 模块偏右，左右边距不等，第二版居中后接受” can change a gate and create a regression check.

## Recommended Record Shape

```json
{
  "schema_version": 2,
  "record_id": "project-revision-event",
  "project_id": "project-name",
  "project_revision": "B",
  "iteration": {
    "index": 3,
    "branch": "main",
    "parent_record_id": "project-B-iteration-2",
    "status": "accepted",
    "reason": "Correct the rejected interface layout",
    "baseline_revision": "B2",
    "candidate_revision": "B3",
    "change_summary": ["Moved USB group to the shared front datum"],
    "design_dna_disposition": "preserved"
  },
  "timestamp": "2026-07-17T12:00:00+08:00",
  "task_summary": "What this window attempted",
  "maturity": "concept | design-development | engineering-candidate",
  "skill_version": "commit or local version",
  "design_dna": ["immutable feature"],
  "feedback": [
    {
      "verbatim": "Exact user feedback",
      "category": "aesthetics | usability | alignment | proportion | interface | motion | engineering | tool",
      "target": "view, feature, state, or artifact",
      "expected": "expected condition",
      "observed": "observed condition",
      "severity": "blocker | major | minor | preference",
      "user_disposition": "rejected | accepted-after-change | accepted | unresolved"
    }
  ],
  "outcome": {
    "selected_direction": "identifier",
    "what_changed": ["source-level change"],
    "verification": ["before/after or test evidence"],
    "accepted": true,
    "unresolved": []
  },
  "evidence": [
    {"type": "render | CAD | report | measurement", "identity": "path, ID, or hash", "notes": "bounded relevance"}
  ],
  "learning": {
    "reuse_scope": "project-only | tentative | cross-project | backend-specific | gate-change",
    "proposed_rule": "bounded reusable lesson",
    "counterexamples_or_limits": "where it may not apply",
    "confidence": "low | medium | high"
  },
  "privacy": {"may_store_locally": true, "may_publish": false, "contains_private_assets": false}
}
```

## Persistence and Promotion Rules

Store raw records in ignored `local-learning/project-feedback.jsonl`. Keep personal aesthetic rules in `local-learning/aesthetic-profile.local.md` and reusable workflow rules in `local-learning/reusable-rules.local.md`.

- Treat one event as project evidence, not a universal rule.
- Promote one explicit statement such as “以后所有桌面产品都不要做成塔式” to a user-confirmed cross-project preference when its scope is clear.
- Otherwise require the same pattern in at least two independent `project_id` values before proposing promotion; ask the user to confirm the generalized wording. Five iterations of one project still count as one project.
- Preserve counterexamples and category limits. A preference for a desktop charger does not automatically govern a workshop fixture.
- Never learn a safety threshold, regulatory dimension, tolerance, material property, or certification claim from aesthetic feedback or an unverified artifact.
- Bind backend failures to application, bridge, kernel, and version. Do not generalize one tool failure to all CAD systems.
- Update a public Skill rule only when it is nonprivate, transferable, evidence-backed, and explicitly authorized for publication.

## Iteration and Branch Lineage

Create a record for every meaningful design attempt that changes the direction, source geometry, parameters, interface layout, motion state, review verdict, or user disposition. Do not create noise for autosaves or changes that do not affect a decision.

- Keep one stable `project_id` across all revisions of the same product.
- Increase `iteration.index` within a branch and link every iteration after the first with `parent_record_id`.
- Use a new branch name when exploring alternatives in parallel; never overwrite one branch with another.
- Mark each attempt `proposed`, `rejected`, `accepted`, `superseded`, or `rolled-back`.
- Preserve failed and rejected attempts. A later accepted version supersedes them but does not erase their evidence.
- Record the reason, baseline, candidate revision, source-level change summary, and whether immutable design DNA was preserved, explicitly changed, or violated.
- Compare iterations with stable cameras, common scale, shared datums, and the same acceptance criteria whenever possible.
- Treat a defect repeated across several iterations of one project as strong evidence of an escaped gate or unstable parameter source, not as independent cross-project preference evidence.
- Promote only the final accepted disposition as the current project outcome; retain earlier dispositions for failure learning and regression tests.

## Privacy and Storage Boundary

Do not store tokens, passwords, credentials, private keys, customer names, confidential project text, proprietary CAD geometry, or unlicensed source images in the learning log. Store an artifact identity, controlled path, hash, cropped evidence, or bounded description instead. Keep `may_publish: false` by default.

Normal operation is automatic when ignored `local-learning/settings.json` contains `"auto_capture": true`. At a capture trigger, construct the record from the active project context and run `scripts/record_project_feedback.py <record.json> --auto-lineage`; the recorder assigns the next branch iteration, parent record, and record ID. Do not ask the user to copy a template when the required context is already known.

Use `assets/project-feedback-template.json` only as a manual fallback or inter-task interchange file. The default store is local to the Skill and ignored by Git. A script pass proves schema and storage policy only; it does not prove the lesson is correct or generalizable.

Automatic capture triggers are: explicit user like/dislike feedback, concept selection or rejection, accepted/rejected revision, meaningful source-level redesign, gate escape, backend failure affecting evidence, prototype/test disposition, milestone handoff, and project closure. Skip autosaves, formatting-only edits, and repeated messages that add no new decision evidence.
