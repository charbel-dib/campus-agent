"""Evaluation bench of the campus agent.

The bench follows the methods of published agent benchmarks, adapted to the school:

- ``tasks``      realistic tasks played by a simulated user who only knows a goal; success is the exact
                 end state of the database, and reliability is reported as pass^k (tau-bench, Sierra)
- ``tool_calls`` choice of tool and arguments, irrelevance and missing parameters (BFCL, Berkeley)
- ``traps``      required steps ("milestones") and forbidden actions ("minefields"), insufficient
                 information (ToolSandbox, Apple)
- ``injection``  attacker text in every channel the agent reads; utility with and without attack and
                 attack success rate (AgentDojo, ETH Zurich)
- ``access``     canary values planted in data the user may not see, then asked for (OWASP LLM02/LLM06)
- ``grounding``  questions without an answer, false premises, and a check that every date, time, room,
                 grade and citation in a reply comes from a tool result
- ``robustness`` typos, English, informal wording, several requests in one, long conversations
- ``core``       the 41 scenarios of version 1.0 (29 tuning, 12 held out), kept as a regression suite

Checks are deterministic: they read the database, the tool calls and the tool results. No model grades
another model; the only model in the bench besides the agent plays the user in ``tasks``.

    python -m campus_eval run --suite all --k 4
    python -m campus_eval report
"""

__all__ = ["checks", "grounding", "metrics", "report", "runner", "simulator", "state", "suites"]
