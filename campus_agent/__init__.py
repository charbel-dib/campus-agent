"""Campus agent: an agentic assistant for a fictional engineering school.

The package is organised in layers, from the data up to the interfaces:

- ``db``        SQLite schema and connection helpers
- ``seed``      deterministic generator of the fictional school
- ``domain``    business rules: permissions, calendar, academics, documents, messaging, audit
- ``tools``     the tool registry shared by the agent, the MCP server and the evaluation
- ``llm``       a small client for the Claude Messages API
- ``agent``     the tool-use loop with human confirmation of every write
- ``web``       the FastAPI service and the demo interface
- ``mcp_server``the same tools exposed over the Model Context Protocol
"""

__version__ = "1.0.0"
