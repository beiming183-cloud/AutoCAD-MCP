# Contributing to AutoCAD MCP

Thanks for helping make CAD automation more reliable and easier to use.

## Good First Contributions

- Reproduce an AutoCAD or AutoCAD LT issue with a minimal drawing.
- Add a deterministic test for a command, document, plot, or geometry contract.
- Improve setup instructions for another MCP client.
- Add a bounded CAD primitive without enabling arbitrary code execution.
- Improve English or Chinese documentation.

## Development Setup

```powershell
git clone https://github.com/beiming183-cloud/AutoCAD-MCP.git
cd AutoCAD-MCP
uv sync --dev
uv run pytest tests -q
```

The full test suite does not require AutoCAD. Real AutoCAD smoke tests live under
`tests/manual_*.py` and must keep AutoCAD minimized, avoid foreground activation,
delete temporary CAD/image artifacts, and retain a JSON evidence record.

## Pull Requests

1. Open an issue first for large protocol or architecture changes.
2. Keep MCP operations client-neutral; do not make them Codex- or Claude-specific.
3. Add structured errors and postcondition readback for every mutable CAD operation.
4. Do not advertise a capability until it has deterministic tests and honest limits.
5. Run `uv run pytest tests -q` and include the result in the pull request.

## Engineering Rules

- A successful command is not proof that the resulting geometry is correct.
- Never use volatile edge or face indices as stable feature identities.
- Geometry DRC, visual review, and product review are separate verdicts.
- Generated outputs should be atomic and should not open external viewers.
- AutoCAD may be visible in the taskbar, but automation must not steal focus.

By contributing, you agree that your contribution is licensed under the MIT License.
