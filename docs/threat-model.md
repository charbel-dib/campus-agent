# Threat model

What can go wrong, what stops it, and the test that proves the control is in place. The tests are in
`tests/test_threats.py` (named after the identifiers below) and in the evaluation suites, which run
against the real model.

## What is protected

Students' grades, absences and their reasons, private calendar entries, e-mail addresses, tickets and
messages; the integrity of the timetable; the school's API budget.

## Trust boundaries

```
 user ─(1)─▶ web API ─▶ agent ─(2)─▶ Claude API
                          │  ▲
                          ▼  │(3) tool results: text written by other users, documents
                     business layer ─▶ database ─(4)─▶ SMTP, CalDAV, GLPI, Moodle
 MCP client ─(5)─▶ /mcp
```

(1) and (5) authenticate a person. (2) leaves the school: only what the user is entitled to see is sent.
(3) is where untrusted text reaches the model. (4) carries data to other systems.

## Threats and controls (STRIDE)

| Id | Threat | Control | Proof |
|---|---|---|---|
| T01 | **Spoofing**: a forged or altered session token | HMAC-signed, expiring tokens; OIDC tokens verified against the provider's keys | `test_threat_T01…`, `test_tokens_are_checked…` |
| T02 | **Elevation**: the model is talked into acting as someone else | No tool takes an identity; the actor comes from the token | `test_threat_T02…`; suite `access` |
| T03 | **Repudiation**: "the assistant did it, not me" | Audit line for every call, with actor, channel and outcome; writes carry the confirming user | `test_threat_T03…` |
| T04 | **Disclosure**: reading someone else's records through the assistant | Scoping in every business function; other people's calendars as busy/free | `test_threat_T04…`; suite `access` (0 leaks in 56 probes) |
| T05 | **Tampering by prompt injection**: instructions hidden in a ticket, an event note, an absence reason | User text returned in fields labelled as user data; instructions say it is never an order; and control T06 | `test_threat_T05…`; suite `injection` (0 of 32 attacks) |
| T06 | **Excessive agency**: the model changes data on its own | Writes held until the user's own confirmation, then re-validated; no "confirm" tool exists | `test_threat_T06…`; suite `traps` |
| T07 | **Disclosure of the system prompt** | Nothing secret is in it; it is public in the repository | `test_threat_T07…` |
| T08 | **Denial of wallet or of service** | Step cap, budgets per conversation, visitor and day, body and list size limits, rate limit | `test_threat_T08…` |
| T09 | **Path traversal** through a model-chosen name | Names validated; files resolved from identifiers stored in the database | `test_threat_T09…` |
| T10 | **Confused deputy**: a token meant for another service replayed on `/mcp` | Audience bound to this resource | `test_remote_mcp_acts_for_the_bearer` |
| T11 | **Disclosure through telemetry** | Spans carry identifiers, counts and outcomes, never text | `test_a_turn_is_traced…` |

## OWASP Top 10 for LLM Applications (2025)

| Risk | Status here |
|---|---|
| LLM01 Prompt injection | T05, T06; measured in every data channel the agent reads |
| LLM02 Sensitive information disclosure | T04, T11; canaries checked in tool results and in replies |
| LLM03 Supply chain | `security.yml`: pip-audit, Trivy on the image, CycloneDX bill of materials, Dependabot |
| LLM04 Data and model poisoning | No training. The rule documents are files in the repository, changed by reviewed commits |
| LLM05 Improper output handling | Replies are escaped before rendering; a strict Content-Security-Policy; tool arguments validated against schemas |
| LLM06 Excessive agency | T02, T06; tools filtered by role; any tool can be switched off (`CAMPUS_DISABLED_TOOLS`) |
| LLM07 System prompt leakage | T07 |
| LLM08 Vector and embedding weaknesses | BM25 by default; the corpus is not user-writable |
| LLM09 Misinformation | Answers cite passages; the grounding suite checks every date, time, room, grade and citation against tool results |
| LLM10 Unbounded consumption | T08 |

## Accepted risks

- A user can be shown a misleading slip if they phrase a request ambiguously; the slip states the exact
  action, and the user must still click.
- Over MCP, confirmation is delegated to the client. A client that auto-approves removes control T06 for
  its user (never for anyone else: T02 and T04 still hold).
- The demo login lets anyone pick a persona. It is disabled with `CAMPUS_DEMO_LOGIN=0` once sign-in is configured.
- Conversations are sent to the Claude API. What is sent is limited to what the signed-in user may see.
- The image scan accepts one finding, listed with its reason and an expiry date in `.trivyignore`: a setuptools
  advisory reported without any file behind it, on code the running agent never imports.
