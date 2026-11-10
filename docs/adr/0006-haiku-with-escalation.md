# 6. Claude Haiku by default, a stronger model on demand

**Context.** The tools carry the difficulty (permissions, conflicts, free slots). On the held-out tasks Haiku 5.5 passes 99 % and Sonnet 5.5 100 %, at twenty times the cost.
**Decision.** Haiku 5.5 by default. `CAMPUS_ESCALATION_MODEL` lets a stronger model take over a turn after six steps or two tool errors.
**Alternatives.** Sonnet everywhere (cost), or a classifier choosing the model up front (one more model call per turn, nothing to measure it against yet).
**Consequences.** Escalation is implemented and tested but off: its benefit on this bench is not measured, because Haiku rarely fails here.
