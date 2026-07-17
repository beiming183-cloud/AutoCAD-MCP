# Reference Image Analysis and Aesthetic Learning

Read this file when the user supplies reference images, competitor products, sketches, design examples, or like and dislike feedback. Extract transferable design logic instead of copying an image.

## Intake and Identity

Assign every image a stable ID. Record source, author or brand, product category, date, view, crop, photographic or rendered nature, and usage rights when known. Mark unknown provenance `unknown`; do not guess.

Store images under `assets/reference-images/` only when later tasks need durable access. Preserve original filename mapping and source notes. Do not republish unlicensed source images.

## Observe Before Interpreting

Record four layers:

1. `observed`: directly visible proportion, silhouette, lines, surfaces, boundaries, color, material appearance, interfaces, and detail.
2. `likely_intent`: a possible design intention with confidence.
3. `engineering_unknown`: dimensions, internal construction, material grade, process, tolerance, and performance that the image cannot prove.
4. `user_response`: explicit likes, dislikes, uncertainty, and requested transfer.

Never present `likely_intent` as fact or infer manufacturability and safety from a render.

## Visual DNA Decomposition

Analyze only at the resolution supported by the image:

- posture and visual center of gravity;
- overall and primary-mass proportions;
- positive and negative silhouette;
- primary, secondary, and tertiary mass hierarchy;
- geometry vocabulary, sections, and control lines;
- soft and hard transitions, radius or chamfer families, and highlight behavior;
- split, seam, opening, grille, texture, and fastening strategy;
- integration of controls, ports, handles, feet, and cables;
- CMF zoning, contrast, gloss, and implied tactility;
- detail density, rhythm, and brand-specific identity;
- likely weaknesses exposed by other views.

Mark unreadable details unknown.

## Synthesize Across Images

Build a comparison matrix for an image set and identify:

- repeated shared rules;
- category-specific or context-specific rules;
- common traits behind the user's likes;
- common traits behind the user's dislikes;
- contradictory preferences;
- photography, rendering, color, or brand-halo bias.

Require two independent pieces of evidence or one explicit user confirmation before promoting an inference into a stable preference. Keep single-image findings experimental.

## Transfer into a New Design

Transfer in this order:

1. principles: proportion tension, mass hierarchy, line and surface logic, detail rhythm, and material contrast;
2. parameterizable rules: ratio ranges, control lines, radius families, split strategy, pattern pitch, and CMF zoning;
3. local elements only after checking function, manufacturing, and brand context.

Offer at least two transfer strengths when useful:

- `subtle transfer`: preserve the target product's identity and borrow underlying grammar;
- `strong transfer`: make the reference direction obvious while redesigning interfaces, structure, and process.

Do not copy brand marks, distinctive graphics, or source-confusing product identity unless the user explicitly requests it and has the right to use it.

## Update the Aesthetic Profile

When editing `aesthetic-profile.md`:

- assign a stable preference ID;
- mark it `user-confirmed`, `inferred`, `experimental`, `needs-resolution`, or `deprecated`;
- cite image IDs, user wording, or review evidence;
- record scope, counterexamples, and confidence;
- preserve conflicting evidence until the user resolves it;
- log every addition, change, and deprecation.

Keep per-image analysis in task evidence. Add only reusable rules to the long-term profile.

## High-Information Feedback

Ask questions that separate possible causes, for example:

- Do you prefer concept A's overall proportion or only concept B's local detail?
- Is the preference the floating, lightweight posture or the silver material?
- Does exposed structure feel professional or visually noisy here?
- Which region most breaks the overall coherence, and why?

Focus each feedback round on a few high-information decisions. Preserve the user's wording before translating it into rules.
