# Operations runbook

For whoever runs the assistant: how it is deployed, what to watch, and what to do when something goes wrong.

## Deployment

- **Demo**: Render web service from `render.yaml` (Docker image, `CAMPUS_SANDBOX=1`), redeployed on every
  push to `main`. `keep-alive.yml` calls it every ten minutes.
- **Elsewhere**: `docker run -p 8000:8000 -e ANTHROPIC_API_KEY=… -e CAMPUS_SECRET_KEY=… campus-agent`.
  All settings are environment variables, listed in [`.env.example`](../.env.example).
- **Checks before a release**: `tests.yml` (tests, lint, image probe), `security.yml` (dependencies, static
  analysis, image scan), `eval.yml` (the gate against the real model).

## What to watch

`GET /api/health` (liveness, today's spend), `GET /api/status` (version, prompt version, features), and the
**Pilotage** tab of the office (`GET /api/admin/metrics`).

| Objective | Target | Where it is measured |
|---|---|---|
| Availability | 99.5 % outside the free tier's cold starts | external probe on `/api/health` |
| Latency of a turn, p95 | under 8 s | spans `invoke_agent`, Pilotage |
| Mean cost of a turn | under 0.2 cent | spans, Pilotage |
| Tool errors (internal, not refusals) | under 1 % | audit log outcome `error`, Pilotage |

The evaluation measured p50 4.7 s and p95 14.8 s **per task** (several turns with confirmations), and 0.07 cent per task.

Traces go to any OTLP collector with `OTEL_EXPORTER_OTLP_ENDPOINT` (Phoenix in the compose file). Follow
the convention's attributes: `gen_ai.usage.input_tokens`, `campus.cost_usd`, `campus.tool.outcome`, `error.type`.

## Weekly review (30 minutes)

1. Pilotage: confirmation rate (a drop means the agent proposes the wrong things), refusals, tool errors.
2. Read the thumbs-down and their traces; read the "unmet requests" (hand-offs and tickets about the assistant).
3. For each real defect, add a task to `eval/suites/` that fails, fix, and let the gate confirm.
   This is how the six fixes of version 1.1 were made (see [evaluation.md](evaluation.md)).

## Incidents

| Symptom | What happens by itself | What to do |
|---|---|---|
| Claude API down or slow | Three retries with back-off, then a clear message; the calendar, messages and hand-off still work | Check status.anthropic.com; nothing to restart |
| Daily budget reached | The agent stops answering until midnight, says so | Raise `CAMPUS_DAILY_BUDGET_USD` if the usage is legitimate; look at Pilotage for abuse |
| A tool misbehaves | — | `CAMPUS_DISABLED_TOOLS=name` and restart: the tool disappears for the model and is refused if called |
| Suspected injection (a user reports an odd slip) | The slip was not executed unless they clicked | Find the trace and the audit lines; add the text to `eval/suites/injection.json` |
| Bad release | — | Render: redeploy the previous commit ("Rollback"); the database is not migrated destructively |
| Mail or helpdesk down | Outbox keeps the items and retries (five attempts) | `python -m campus_agent.cli sync` once the system is back; failed rows are in table `outbox` |
| API key leaked | — | Revoke it in the Anthropic console, set the new one in Render, redeploy. Rotate `CAMPUS_SECRET_KEY` too: it signs users out |

## Routine commands

```bash
python -m campus_agent.cli purge                 # apply retention (schedule it daily)
python -m campus_agent.cli export stu-0001       # a person's data, as JSON
python -m campus_agent.cli erase stu-0001        # erase it
python -m campus_agent.cli sync                  # deliver pending mail, calendar entries, tickets
python -m campus_agent.cli import-ics edt.ics --organizer tch-001 --group 3A-IA
```

## Backups

Outside the demo, the state is one SQLite file (`CAMPUS_DB_PATH`) and the generated PDFs
(`CAMPUS_RUNTIME_DIR`). Back up with `sqlite3 campus.db ".backup backup.db"` (safe while running);
restore by replacing the file while the service is stopped. The demo keeps nothing by design.

## Changing the prompt or a tool description

The instructions are in `campus_agent/prompts/system.md`. A change is a pull request; `eval.yml` runs the
gate; the new prompt version (a hash) then appears in `/api/status`, in every trace and in the evaluation results.
