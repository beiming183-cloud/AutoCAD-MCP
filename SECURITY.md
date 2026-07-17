# Security Policy

## Supported Version

Security fixes are applied to the latest release on `main`.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability that could execute arbitrary
code, expose local files, escape the managed output directory, or take control of an
AutoCAD session.

Use GitHub private vulnerability reporting when it is enabled for this repository.
If it is unavailable, contact the maintainer through the email address on the GitHub
profile and include:

- affected version and backend;
- minimal reproduction steps;
- expected and actual behavior;
- impact and suggested mitigation, if known.

## Security Boundaries

- Arbitrary AutoLISP is disabled by default.
- File outputs are contained under `AUTOCAD_MCP_OUTPUT_ROOT` unless explicitly allowed.
- The MCP server uses standard local stdio transport.
- Remote FreeCAD or other CAD bridges are outside this repository's trust boundary.
- CAD output still requires engineering review before manufacturing or safety use.
