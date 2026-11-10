# 4. Starlette rather than FastAPI

**Context.** A dozen JSON endpoints, one streaming endpoint, static files.
**Decision.** Starlette directly. Requests are small dicts validated by hand; tool arguments have their own JSON-schema validation shared with MCP.
**Alternatives.** FastAPI: automatic OpenAPI and Pydantic models, at the price of a second schema system next to the tool schemas.
**Consequences.** No generated API documentation. The dependency set stays small.
