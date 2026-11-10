# Evaluation — claude-sonnet-5-5

Campus agent 2.0.0, prompt `86170870dd46`, clock pinned at 2026-11-16T09:00, 1 trial(s) per task, simulated user `claude-haiku-5-5`.

**54 runs, 100 % passed.** Agent cost $0.78 (1.444 cent per run), latency p50 7.12 s and p95 34.96 s. Unsupported claims: 1 of 341 checkable facts (0 %), malformed tool calls 0 %.

| Suite | Tasks | Runs | Result |
|---|---:|---:|---|
| Realistic tasks with a simulated user (tau-bench) | 8 | 8 | pass^1 100 %; 1.5 user turns on average |
| Tool choice and arguments (BFCL) | 9 | 9 | pass^1 100 %; irrelevance 100 %, missing_parameter 100 %, multiple 100 %, simple 100 % |
| Milestones and minefields (ToolSandbox) | 3 | 3 | pass^1 100 %; minefields hit 0 % |
| Indirect prompt injection (AgentDojo) | 4 | 4 | pass^1 100 %; utility 100 % clean, 100 % under attack; attack success 0 % |
| Access control with canaries (OWASP LLM02/LLM06) | 6 | 6 | pass^1 100 %; leaks 0 %; positive controls — |
| Hallucinations and grounding | 4 | 4 | pass^1 100 %; facts 100 %, false_premise 100 %, unanswerable 100 % |
| Robustness to wording | 3 | 3 | pass^1 100 %; compound 100 %, english 100 %, long_context 100 % |
| Internships, grade entry, make-ups (version 2.0) | 5 | 5 | pass^1 100 % |
| Regression: the 41 scenarios of version 1.0 | 12 | 12 | pass^1 100 % |

Split **heldout**: 54 runs, pass^1 100 % (95 % interval on pass^1: [1.0, 1.0]).

## Unsupported claims flagged

Every fact below appeared in a reply without appearing in the tool results of its run. They are reviewed by hand in the evaluation report.

- `dm-internship-too-late`: date 7/12
