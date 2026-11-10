# 8. OpenTelemetry names and wire format, without the SDK

**Context.** The GenAI semantic conventions are still marked "Development"; the core avoids dependencies.
**Decision.** `telemetry.py` creates spans with the convention's names and attributes, stores them in SQLite for the operations page, and can post them as OTLP/HTTP JSON to any collector.
**Alternatives.** The OpenTelemetry SDK with an instrumentation package: batching, sampling and context propagation for free, with three more dependencies and names that change between releases.
**Consequences.** No sampling and no propagation to other services. Prompts and replies are never recorded, so a trace viewer shows structure, timing, tokens and cost, not content.
