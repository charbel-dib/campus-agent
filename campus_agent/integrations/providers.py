"""Interfaces to outside systems, each with an in-memory double and a real implementation.

The real ones speak the protocol of a system a school is likely to run: SMTP for mail, CalDAV for
personal calendars (Radicale, Nextcloud, and the CalDAV endpoints of Google and Apple), the GLPI REST
API for the helpdesk, Moodle web services for assignments. ``docs/integrations.md`` explains how to
point each at a real server; ``docker-compose.integrations.yml`` starts local ones.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import urlparse

TIMEOUT = 10


class MailProvider(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class CalendarProvider(Protocol):
    def put(self, uid: str, ics: str) -> None: ...
    def delete(self, uid: str) -> None: ...


class TicketProvider(Protocol):
    def create(self, subject: str, body: str, requester: str, category: str) -> str: ...


class AssignmentsProvider(Protocol):
    def upcoming(self) -> list[dict]: ...


# ---------------------------------------------------------------- in memory (tests, default)

@dataclass
class MemoryMail:
    sent: list = field(default_factory=list)

    def send(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})


@dataclass
class MemoryCalendar:
    items: dict = field(default_factory=dict)

    def put(self, uid, ics):
        self.items[uid] = ics

    def delete(self, uid):
        self.items.pop(uid, None)


@dataclass
class MemoryTickets:
    created: list = field(default_factory=list)

    def create(self, subject, body, requester, category):
        self.created.append({"subject": subject, "body": body, "requester": requester, "category": category})
        return str(len(self.created))


# ---------------------------------------------------------------- real systems

class SmtpMail:
    """``smtp://host:port`` (Mailpit, a relay) or ``smtps://user:password@host:465``."""

    def __init__(self, url: str, sender: str):
        self.url, self.sender = urlparse(url), sender

    def send(self, to, subject, body):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        cls = smtplib.SMTP_SSL if self.url.scheme == "smtps" else smtplib.SMTP
        with cls(self.url.hostname, self.url.port or (465 if self.url.scheme == "smtps" else 25), timeout=TIMEOUT) as s:
            if self.url.username:
                s.login(self.url.username, self.url.password or "")
            s.send_message(msg)


class CalDavCalendar:
    """One calendar collection, e.g. ``http://user:password@localhost:5232/user/agenda/`` (Radicale)."""

    def __init__(self, url: str, session=None):
        import requests

        u = urlparse(url)
        self.base = f"{u.scheme}://{u.hostname}{':' + str(u.port) if u.port else ''}{u.path.rstrip('/')}/"
        self.http = session or requests.Session()
        if u.username:
            self.http.auth = (u.username, u.password or "")

    def put(self, uid, ics):
        r = self.http.put(f"{self.base}{uid}.ics", data=ics.encode(), timeout=TIMEOUT,
                          headers={"content-type": "text/calendar; charset=utf-8"})
        r.raise_for_status()

    def delete(self, uid):
        r = self.http.delete(f"{self.base}{uid}.ics", timeout=TIMEOUT)
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()


class GlpiTickets:
    """GLPI REST API (``/apirest.php``): open a session, create the ticket, close the session."""

    def __init__(self, url: str, app_token: str, user_token: str, session=None):
        import requests

        self.url, self.app_token, self.user_token = url.rstrip("/"), app_token, user_token
        self.http = session or requests.Session()

    def create(self, subject, body, requester, category):
        base = {"App-Token": self.app_token, "Content-Type": "application/json"}
        r = self.http.get(f"{self.url}/initSession", headers={**base, "Authorization": f"user_token {self.user_token}"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        headers = {**base, "Session-Token": r.json()["session_token"]}
        try:
            r = self.http.post(f"{self.url}/Ticket", headers=headers, timeout=TIMEOUT, json={"input": {
                "name": subject, "content": f"{body}\n\nDemandeur : {requester}\nCatégorie : {category}", "type": 2}})
            r.raise_for_status()
            return str(r.json()["id"])
        finally:
            self.http.get(f"{self.url}/killSession", headers=headers, timeout=TIMEOUT)


class MoodleAssignments:
    """Moodle web services (``mod_assign_get_assignments``): assignments and due dates of the token's courses."""

    def __init__(self, url: str, token: str, session=None):
        import requests

        self.url, self.token = url.rstrip("/"), token
        self.http = session or requests.Session()

    def upcoming(self):
        r = self.http.get(f"{self.url}/webservice/rest/server.php", timeout=TIMEOUT, params={
            "wstoken": self.token, "wsfunction": "mod_assign_get_assignments", "moodlewsrestformat": "json"})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("exception"):
            raise RuntimeError(data.get("message", "Moodle error"))
        out = []
        for course in data.get("courses", []):
            for a in course.get("assignments", []):
                out.append({"course": course.get("fullname", ""), "name": a.get("name", ""), "due": a.get("duedate", 0)})
        return sorted(out, key=lambda a: a["due"])


@dataclass
class Providers:
    mail: MailProvider | None = None
    calendar: CalendarProvider | None = None
    tickets: TicketProvider | None = None
    assignments: AssignmentsProvider | None = None

    def any(self) -> bool:
        return any((self.mail, self.calendar, self.tickets))


def from_settings(s) -> Providers:
    return Providers(
        mail=SmtpMail(s.smtp_url, s.mail_from) if s.smtp_url else None,
        calendar=CalDavCalendar(s.caldav_url) if s.caldav_url else None,
        tickets=GlpiTickets(s.glpi_url, s.glpi_app_token, s.glpi_user_token) if s.glpi_url else None,
        assignments=MoodleAssignments(s.moodle_url, s.moodle_token) if s.moodle_url else None)
