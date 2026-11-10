# 3. Permissions live in the business layer

**Context.** The same functions are reached from the agent, the MCP servers and the HTTP API.
**Decision.** Every business function receives the authenticated actor and decides what that person may see or change. The model never passes an identity; no tool has such a parameter (test T02).
**Alternatives.** Rules in the prompt (bypassable), or per-interface checks (three places to get wrong).
**Consequences.** A new interface inherits the rules. Canary tests check the tool results themselves, not only the replies.
