# Native AutoCAD worker plugin

This is the preferred industrial path for full AutoCAD 2025 and 2026. It keeps
database mutations inside AutoCAD under `DocumentLock` and a native database
transaction. The existing COM/LISP backend remains the compatibility path for
AutoCAD LT and installations where the signed plugin is unavailable.

Build prerequisites:

```powershell
$env:AUTOCAD_MCP_AUTOCAD_DIR = 'D:\cad\AutoCAD 2025'
dotnet build .\native\AutoCADMcp.Plugin\AutoCADMcp.Plugin.csproj -c Release
```

The build targets `.NET 8.0-windows`, matching AutoCAD 2025/2026. A production
installation must be signed and verified; do not disable `SECURELOAD`:

```powershell
.\native\scripts\build-plugin.ps1 `
  -AutoCADDir 'D:\cad\AutoCAD 2025' `
  -DotNet 'D:\Codex\Tools\dotnet-sdk\dotnet.exe' `
  -CertificateThumbprint 'YOUR_CERTIFICATE_THUMBPRINT' `
  -Install
```

The named pipe is restricted to the current Windows user and can additionally
require `AUTOCAD_MCP_PLUGIN_TOKEN`. A descriptor publishes protocol version,
capability version, plugin version, PID, HWND, session ID, and supported
operations under `%LOCALAPPDATA%\AutoCAD-MCP\workers`.

Implemented protocol operations:

- `system.ping`
- `document.context`
- `document.create`
- `document.activate`
- `transaction.execute`

`document.activate` requires the target `docId` and its last observed
`expectedRevision`. The worker validates both before switching documents and
returns the actual active identity/revision as a postcondition. It does not
show, restore, minimize, or foreground the AutoCAD window.

`transaction.execute` currently supports line, circle, box, cylinder, and solid
boolean operations. Every transaction requires session identity, active document
identity, expected revision, and an idempotency key. Unknown fields and missing
layers fail before commit. Any failed item disposes the native transaction, so
earlier items are rolled back. Feature GUIDs are generated once, written to XData,
and returned unchanged. Only successfully committed idempotent responses are
cached; a failed request can be retried with the same key.

The Python client uses a four-byte little-endian length followed by UTF-8 JSON.
It has no pywin32 dependency, so document context and native transactions remain
available if the optional COM compatibility environment is unhealthy. This is a
standard MCP-server implementation detail and is not tied to Codex or Claude.
