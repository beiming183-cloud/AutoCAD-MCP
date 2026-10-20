# Ngoc Viet CNC - cloud connection design

## Verified client facts (2026-10-20)

- OpenAI documents custom MCP servers for ChatGPT on the web, using a public HTTPS `/mcp` endpoint or Secure MCP Tunnel. The documented transports are SSE and Streamable HTTP. Workspace/account policy applies, and OpenAI recommends approvals and logging for MCP calls. See [OpenAI custom MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server) and [connection testing](https://developers.openai.com/plugins/deploy/connect-chatgpt).
- OpenAI documents Secure MCP Tunnel for a private/on-prem server without public Internet exposure. See [OpenAI MCP server guide](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).
- The inspected OpenAI documentation describes ChatGPT web access. This project has not verified a custom MCP workflow in ChatGPT for iPhone; status is **BLOCKED / not confirmed**, and ChatGPT Plus alone must not be treated as sufficient entitlement.
- Anthropic documents remote MCP on Claude/Claude Desktop for Pro, Max, Team and Enterprise, and says iOS/Android can use remote servers already added through Claude.ai. See [Anthropic remote MCP connector guidance](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp-servers).

## Proposed architecture

```text
ChatGPT web / Claude clients
            |
   authenticated HTTPS or Secure MCP Tunnel
            |
 Fedora MCP gateway (policy, OAuth, rate limit, audit)
            |
       private authenticated channel
            |
 Windows staging agent (non-admin, DXF workspace only)
            |
       Ngoc Viet safe DXF MCP
```

For headless DXF, run the safe server in an isolated Fedora container with only a dedicated output volume. AutoCAD is unnecessary for this path. A future Windows AutoCAD agent must be a separate service and approval domain; do not expose COM, named pipes or AutoCAD directly to the Internet.

## Gateway requirements

- Prefer Secure MCP Tunnel for ChatGPT private-network access; otherwise use Streamable HTTP behind TLS and OAuth 2.1.
- Bind the Windows agent only to a private network/Tailscale or an outbound mutually authenticated channel.
- Per-tool allowlist: only the five safe tools. Reject dynamic tool changes until reviewed.
- Require per-call approval for create/resize/export actions; read/validate may be read-only.
- Enforce JWT audience/issuer, short token TTL, rotation, rate/body-size limits, host/origin validation and replay protection.
- Store request ID, user, tool, validated arguments, result hash and approval record. Never log OAuth tokens.
- Fedora has no filesystem mapping to production drawings or laser controller folders.

## Not implemented in this pull request

The remote HTTP gateway, OAuth issuer, Secure MCP Tunnel enrollment, Fedora deployment and iPhone end-to-end test are intentionally not implemented. Publishing an unauthenticated staging service would violate the project security rules.
