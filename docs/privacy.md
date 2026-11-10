# Personal data

The school in this repository is fictional, but the assistant is built as if it were not. This page is the
record of processing a school would keep for it.

## What the assistant adds to the school's own records

| Data | Purpose | Kept for | Who can read it |
|---|---|---|---|
| Conversations (the user's messages, the replies) | Continue a conversation; investigate a reported problem | 30 days (`CAMPUS_RETENTION_DAYS`) | The user; nobody else through the interface |
| Traces (durations, token counts, tool names, outcomes; no text) | Run the service: cost, latency, errors | 30 days | The office (aggregates), the operator |
| Feedback (thumb, optional reason) | Improve the assistant | 30 days | The office |
| Pending and executed actions | Show what was confirmed and when | 30 days once decided | The user |
| Audit trail (who called which tool, when, with what outcome; arguments truncated to 200 characters) | Answer for decisions taken through the assistant | 1 year (`CAMPUS_AUDIT_RETENTION_DAYS`) | The office; each user for their own lines |

Grades, absences, timetable and messages belong to the school's systems and follow their own rules; the
assistant reads them with the rights of the person signed in.

## Sent outside the school

The conversation and the tool results of the signed-in user go to the Claude API to produce a reply. The
assistant sends only what that user is entitled to see. Nothing is sent to the trace collector but identifiers and numbers.

## Minimisation, in the code

- Other people's calendars are returned as busy/free, never with titles or notes.
- A student sees classmates' names and groups, not their e-mail address.
- A published-grade notice says that a grade is out, not the grade.
- Calendar feeds leave out descriptions written by other people.

## Rights

- **Access and portability**: "Exporter mes données" in the interface (`GET /api/me/data`), or `cli export`.
- **Erasure**: "Effacer mes conversations" (`DELETE /api/me/data`), or `cli erase`. Conversations, feedback,
  traces and undecided actions are deleted; audit lines keep who, what and when, without arguments.
- **Retention** is applied by `cli purge`, to be scheduled daily.

## Not covered here

Consent screens, the legal basis and the contract with the model provider are the school's to decide.
