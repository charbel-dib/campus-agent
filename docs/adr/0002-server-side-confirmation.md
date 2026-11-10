# 2. Confirmation is enforced by the server, not asked of the model

**Context.** A model can be persuaded, by a user or by text planted in data, to act.
**Decision.** A write tool never writes when the model calls it. It validates in dry-run and stores a pending action; only the user's own request to the API executes it, after every rule is checked again.
**Alternatives.** Telling the model to "ask before acting": depends on the model obeying, and fails exactly when it is attacked.
**Consequences.** One more click per change. An injected instruction can at worst produce a slip the user sees and refuses; the evaluation counts even that as a successful attack.
