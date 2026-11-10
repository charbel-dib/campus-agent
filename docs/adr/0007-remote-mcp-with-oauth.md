# 7. Remote MCP: act for the bearer of the token

**Context.** The stdio server acts for whoever starts it. A hosted server must know who is calling.
**Decision.** `/mcp` accepts only bearer tokens signed by the school's identity provider and issued for this resource (audience check). It publishes OAuth protected-resource metadata so clients find the authorization server. Stateless JSON-RPC with JSON responses.
**Alternatives.** An API key per user (no expiry, no consent, shared secrets), or sessions on the server (state to store and expire).
**Consequences.** Needs an OpenID Connect provider. Writes run when called, as with stdio: the MCP client is expected to ask its user, and write tools are annotated destructive. No server-initiated messages.
