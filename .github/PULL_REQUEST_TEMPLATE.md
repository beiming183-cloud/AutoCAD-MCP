## What Changed

Describe the user-visible behavior and why it is needed.

## Verification

- [ ] `uv run pytest tests -q` passes
- [ ] Mutable CAD calls include postcondition readback
- [ ] Error cases return structured error codes
- [ ] AutoCAD does not steal focus or open output viewers
- [ ] Temporary CAD/image artifacts are cleaned up

## Capability Boundaries

List any unsupported, approximate, or review-required behavior introduced by this PR.
