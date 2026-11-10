"""Adapters to the systems a school already runs. Each one sits behind a small interface with an
in-memory implementation for tests and a real one speaking the system's protocol:

- ``ical``      iCalendar (RFC 5545) export feeds and import of a timetable export (HyperPlanning, ADE, Celcat)
- ``providers`` mail over SMTP, personal calendars over CalDAV, helpdesk tickets in GLPI, assignments from Moodle
- ``outbox``    delivery of the agent's changes to those systems, with retries
- ``oidc``      single sign-on and bearer tokens checked against the identity provider's keys
"""
