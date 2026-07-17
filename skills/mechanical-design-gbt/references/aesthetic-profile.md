# User Aesthetic Profile

Status: `reference-corpus-indexed; user-preference-unconfirmed`

Use this file for durable, evidence-backed mechanical-product aesthetic preferences. A local CAD/JPG corpus has been supplied as learning material, but the user has not yet confirmed specific aesthetic likes or dislikes. Do not convert corpus content into confirmed preference without feedback.

## Rules

- Record transferable design rules, not unsupported words such as premium, minimal, or futuristic.
- Give every entry an ID, status, scope, rule, evidence, confidence, counterexample, and date.
- Prefer `user-confirmed` over `inferred`. Prefer recent, specific, same-category evidence over older generalizations.
- Preserve conflicts as `needs-resolution`; never overwrite them silently.
- When the user changes preference, mark the old entry `deprecated` and retain history.

## Confirmed Preferences

None yet.

## Confirmed Dislikes

None yet.

## Inferences to Validate

- `INF-001` (`experimental`, medium confidence): value same-scale multi-view coverage before detail, especially plan, front, side, top, installation, and state views. Evidence: repeated organization across the ten indexed JPG boards.
- `INF-002` (`experimental`, medium confidence): value configurable and modular product families rather than a single frozen depiction. Evidence: dynamic-block stretch, mirror, rotation, visibility, alignment, and distribution behaviors across the corpus.
- `INF-003` (`experimental`, medium confidence): value human, room, wall, counter, cable, and mating-product context for scale and action review. Evidence: people and installation context across furniture, appliance, custom-cabinet, and layout boards.
- `INF-004` (`experimental`, low confidence): value clean low-detail silhouette for early review. Limit: this may reflect CAD space-planning efficiency rather than the user's desired finished-product aesthetic.

## Form Grammar

### Proportion and Posture

Awaiting references and feedback.

### Massing and Silhouette

Awaiting references and feedback.

### Geometry, Lines, Surfaces, and Transitions

Awaiting references and feedback.

### Splits, Interfaces, and Detail Density

Awaiting references and feedback.

### CMF

Awaiting references and feedback.

### Engineering Expression

Awaiting references and feedback.

## Reference Image Index

| Image ID | Source | User response | Extracted rule IDs | Notes |
|---|---|---|---|---|
| CADLIB-JPG-01 | Local corpus broad catalog board | Supplied for learning; no explicit like/dislike | INF-001, INF-003, INF-004 | Broad category and human-scale reference |
| CADLIB-JPG-02 | Local corpus dynamic catalog board | Supplied for learning; no explicit like/dislike | INF-001, INF-002, INF-003 | Product, furniture, vehicle, and people states |
| CADLIB-JPG-03 | Local corpus kitchen/bath board | Supplied for learning; no explicit like/dislike | INF-001, INF-002 | Fixture families and multi-view coverage |
| CADLIB-JPG-04 | Local corpus appliance board | Supplied for learning; no explicit like/dislike | INF-001, INF-002 | Appliance and interface categories |
| CADLIB-JPG-05 | Local corpus custom-cabinet board | Supplied for learning; no explicit like/dislike | INF-001, INF-002, INF-003 | Modular envelopes, openings, and room context |
| CADLIB-JPG-06 | Local corpus branded kitchen/bath board | Supplied for learning; no explicit like/dislike | INF-001 | Supplier search lead only; dimensions unverified |
| CADLIB-JPG-07 | Local corpus sofa board | Supplied for learning; no explicit like/dislike | INF-002, INF-003, INF-004 | Modular plan compositions |
| CADLIB-JPG-08 | Local corpus table/chair/bed board | Supplied for learning; no explicit like/dislike | INF-001, INF-002, INF-004 | Family comparison and three-view chair references |
| CADLIB-JPG-09 | Local corpus kitchen/bath/component board | Supplied for learning; no explicit like/dislike | INF-001, INF-002 | Openings, component families, and wall envelopes |
| CADLIB-JPG-10 | Local corpus layout template board | Supplied for learning; no explicit like/dislike | INF-003 | Interior layout evidence, not a GB/T template |

## Preference Entry Template

```text
preference_id:
status: user-confirmed | inferred | experimental | needs-resolution | deprecated
scope:
rule:
evidence:
confidence:
counterexamples_or_limits:
date:
```

## Revision Log

- 2026-07-17: Created the empty profile and evidence rules; awaiting user references and feedback.
- 2026-07-17: Indexed ten user-supplied CAD-library JPG boards as reference evidence. Added four experimental inferences; confirmed-preference sections remain empty.
