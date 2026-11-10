"""iCalendar (RFC 5545): subscription feeds out, timetable exports in.

Export: one feed per person, group or room, which Google Calendar, Outlook and Apple Calendar can
subscribe to. Times are written in UTC, which every client reads the same way.

Import: reads the VEVENTs of a timetable export (the format HyperPlanning, ADE Campus and Celcat
produce) and creates the classes of a group. Recurrence rules are not expanded: those tools export
one VEVENT per session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..clock import SCHOOL_TZ, fmt_dt, parse_dt

PRODID = "-//Ker Avel//Campus Agent//FR"
KIND_BY_CATEGORY = {"cm": "cours", "cours": "cours", "td": "td", "tp": "tp", "examen": "examen", "exam": "examen"}


def _escape(text: str) -> str:
    text = str(text).replace(chr(92), chr(92) * 2).replace(";", chr(92) + ";").replace(",", chr(92) + ",")
    return text.replace("\r\n", chr(92) + "n").replace("\n", chr(92) + "n")


def _unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append("\n" if text[i + 1] in "nN" else text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _fold(line: str) -> str:
    """Lines are limited to 75 octets; longer ones continue on the next line after a space."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, chunk = [], b""
    for ch in line:
        b = ch.encode()
        if len(chunk) + len(b) > (75 if not parts else 74):
            parts.append(chunk.decode())
            chunk = b""
        chunk += b
    parts.append(chunk.decode())
    return "\r\n ".join(parts)


def _utc(local: str) -> str:
    return parse_dt(local).replace(tzinfo=ZoneInfo(SCHOOL_TZ)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_to_vevent(row, room_name: str = "") -> list[str]:
    lines = ["BEGIN:VEVENT", f"UID:event-{row['id']}@ker-avel.example", f"DTSTAMP:{_utc(row['updated_at'])}",
             f"DTSTART:{_utc(row['start'])}", f"DTEND:{_utc(row['end'])}", f"SUMMARY:{_escape(row['title'])}",
             f"CATEGORIES:{row['kind'].upper()}", f"STATUS:{'CANCELLED' if row['status'] == 'cancelled' else 'CONFIRMED'}"]
    if row["room_id"]:
        lines.append(f"LOCATION:{_escape(room_name or row['room_id'])}")
    lines.append("END:VEVENT")
    return lines


def calendar(vevents: list[list[str]], name: str = "Ker Avel") -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_escape(name)}"]
    for v in vevents:
        lines += v
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def feed(conn, *, person_id: str | None = None, group_id: str | None = None, room_id: str | None = None) -> str:
    """The confirmed events of a person (their own, their invitations and their group's), a group or a room.

    Descriptions are left out: they are free text written by other people.
    """
    if person_id:
        row = conn.execute("SELECT group_id, first_name, last_name FROM people WHERE id = ?", (person_id,)).fetchone()
        name = f"{row['first_name']} {row['last_name']}"
        sql = ("SELECT DISTINCT e.* FROM events e LEFT JOIN event_people p ON p.event_id = e.id "
               "LEFT JOIN event_groups g ON g.event_id = e.id "
               "WHERE e.status = 'confirmed' AND (e.organizer_id = ? OR p.person_id = ? OR g.group_id = ?)")
        params = (person_id, person_id, row["group_id"] or "")
    elif group_id:
        name = group_id
        sql = ("SELECT e.* FROM events e JOIN event_groups g ON g.event_id = e.id "
               "WHERE e.status = 'confirmed' AND g.group_id = ?")
        params = (group_id,)
    else:
        name = room_id or ""
        sql, params = "SELECT e.* FROM events e WHERE e.status = 'confirmed' AND e.room_id = ?", (room_id,)
    rooms = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM rooms")}
    rows = conn.execute(sql + " ORDER BY e.start", params).fetchall()
    return calendar([event_to_vevent(r, rooms.get(r["room_id"], "")) for r in rows], f"Ker Avel, {name}")


# ---------------------------------------------------------------------------------------- import

def _local(value: str, params: dict) -> str | None:
    """A DTSTART/DTEND value as school-local "YYYY-MM-DDTHH:MM"; None for all-day values."""
    if "T" not in value:
        return None
    naive = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        naive = naive.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(SCHOOL_TZ)).replace(tzinfo=None)
    elif params.get("TZID") and params["TZID"] != SCHOOL_TZ:
        naive = naive.replace(tzinfo=ZoneInfo(params["TZID"])).astimezone(ZoneInfo(SCHOOL_TZ)).replace(tzinfo=None)
    return fmt_dt(naive)


def parse(text: str) -> list[dict]:
    """The VEVENTs of an iCalendar text as dicts: uid, summary, start, end, location, categories, description."""
    unfolded: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += line[1:]
        elif line:
            unfolded.append(line)
    events, current = [], None
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            if current.get("start") and current.get("end"):
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *raw = head.split(";")
            params = dict(p.split("=", 1) for p in raw if "=" in p)
            key = name.upper()
            if key in ("DTSTART", "DTEND"):
                current["start" if key == "DTSTART" else "end"] = _local(value, params)
            elif key in ("UID", "SUMMARY", "LOCATION", "CATEGORIES", "DESCRIPTION"):
                current[key.lower()] = _unescape(value)
    return events


def import_events(conn, text: str, organizer_id: str, group_id: str | None, now: datetime) -> dict:
    """Create the classes of a timetable export for one group. Idempotent: an identical session is skipped."""
    rooms = {r["id"].lower(): r["id"] for r in conn.execute("SELECT id FROM rooms")}
    rooms.update({r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM rooms")})
    created = skipped = 0
    stamp = fmt_dt(now)
    for ev in parse(text):
        title = (ev.get("summary") or "Séance").strip()[:200]
        kind = KIND_BY_CATEGORY.get((ev.get("categories") or "").split(",")[0].strip().lower(), "cours")
        room = rooms.get((ev.get("location") or "").strip().lower())
        if conn.execute("SELECT 1 FROM events WHERE title = ? AND start = ? AND organizer_id = ?",
                        (title, ev["start"], organizer_id)).fetchone():
            skipped += 1
            continue
        cur = conn.execute(
            "INSERT INTO events(title, kind, start, end, room_id, organizer_id, description, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (title, kind, ev["start"], ev["end"], room, organizer_id, "", stamp, stamp))
        if group_id:
            conn.execute("INSERT INTO event_groups(event_id, group_id) VALUES (?, ?)", (cur.lastrowid, group_id))
        created += 1
    conn.commit()
    return {"created": created, "skipped": skipped}
