"""Campus agent: an agentic assistant for a fictional engineering school.

The package is organised in layers, from the data up to the interfaces:

- ``db``        SQLite schema and connection helpers
- ``seed``      deterministic generator of the fictional school
- ``domain``    business rules: permissions, calendar, academics, documents, messaging, audit
- ``tools``     the tool registry shared by the agent, the MCP server and the evaluation
- ``llm``       a small client for the Claude Messages API
- ``agent``     the tool-use loop with human confirmation of every write
- ``web``       the HTTP service (Starlette) and the demo interface
- ``mcp_server``the same tools over the Model Context Protocol (local stdio); ``mcp_http`` remote, with OAuth
- ``telemetry`` traces named after the OpenTelemetry GenAI conventions; ``ops`` the office's metrics
- ``privacy``   export, erasure and retention of personal data
- ``integrations`` iCalendar, SMTP, CalDAV, GLPI, Moodle, OpenID Connect
"""

__version__ = "2.0.0"
