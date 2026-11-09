"""Calendars, rooms and events: who may see what, free slots, and checked changes.

Every write goes through ``_validate`` (rules of the school and conflicts), so a change made
by the agent, through MCP or through the API obeys exactly the same rules. Writes accept
``dry_run``: the agent first validates and shows a summary, the user confirms, then the same
function runs again for real, re-checking everything in case the calendar changed meanwhile.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from ..clock import SLOTS, fmt_dt, human_dt, human_range, overlaps, parse_date, parse_dt
from . import (Conflict, Context, InvalidRequest, NotFound, PermissionDenied, person_label,
               teacher_course_groups, teacher_groups)
from .messaging import notify

KINDS = ("cours", "td", "tp", "examen", "reunion", "permanence", "rendez_vous", "reservation", "autre")
CLASS_KINDS = ("cours", "td", "tp", "examen")
STUDENT_KINDS = ("reservation", "rendez_vous", "autre")
BUILDING_OPEN, BUILDING_CLOSE = time(7, 30), time(20, 0)
LIBRARY_OPEN, LIBRARY_CLOSE = time(8, 0), time(19, 0)
WEDNESDAY_AFTERNOON = time(12, 30)
MAX_RANGE_DAYS = 31
STUDY_MAX_MINUTES = 120
STUDY_DAYS_AHEAD = 14
RDV_MIN, RDV_MAX = 15, 30


# ---------------------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------------------

def _person(ctx: Context, person_id: str):
    row = ctx.conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        raise NotFound(f"personne inconnue : {person_id}. Utilise search_people pour trouver son identifiant.")
    return row


def _group(ctx: Context, group_id: str):
    row = ctx.conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise NotFound(f"groupe inconnu : {group_id}")
    return row


def _room(ctx: Context, room_id: str):
    row = ctx.conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if row is None:
        raise NotFound(f"salle inconnue : {room_id}. Utilise find_available_rooms pour voir les salles.")
    return row


def _event(ctx: Context, event_id: int):
    row = ctx.conn.execute("SELECT * FROM events WHERE id = ?", (int(event_id),)).fetchone()
    if row is None:
        raise NotFound(f"événement inconnu : {event_id}")
    return row


def group_size(ctx: Context, group_id: str) -> int:
    return ctx.conn.execute("SELECT COUNT(*) FROM people WHERE group_id = ?", (group_id,)).fetchone()[0]


def _events_for_person(ctx: Context, person_id: str, start: datetime, end: datetime) -> list:
    return ctx.conn.execute("""
        SELECT DISTINCT e.* FROM events e
        LEFT JOIN event_people ep ON ep.event_id = e.id
        LEFT JOIN event_groups eg ON eg.event_id = e.id
        WHERE e.status = 'confirmed' AND e.start < ? AND e.end > ?
          AND (ep.person_id = ? OR eg.group_id = (SELECT group_id FROM people WHERE id = ?))
        ORDER BY e.start""", (fmt_dt(end), fmt_dt(start), person_id, person_id)).fetchall()


def _events_for_group(ctx: Context, group_id: str, start: datetime, end: datetime) -> list:
    return ctx.conn.execute("""
        SELECT e.* FROM events e JOIN event_groups eg ON eg.event_id = e.id
        WHERE e.status = 'confirmed' AND eg.group_id = ? AND e.start < ? AND e.end > ?
        ORDER BY e.start""", (group_id, fmt_dt(end), fmt_dt(start))).fetchall()


def _events_for_room(ctx: Context, room_id: str, start: datetime, end: datetime) -> list:
    return ctx.conn.execute("""
        SELECT * FROM events WHERE status = 'confirmed' AND room_id = ? AND start < ? AND end > ?
        ORDER BY start""", (room_id, fmt_dt(end), fmt_dt(start))).fetchall()


def event_dict(ctx: Context, row) -> dict:
    groups = [r[0] for r in ctx.conn.execute("SELECT group_id FROM event_groups WHERE event_id = ?", (row["id"],))]
    people = [r[0] for r in ctx.conn.execute("SELECT person_id FROM event_people WHERE event_id = ?", (row["id"],))]
    room = None
    if row["room_id"]:
        r = ctx.conn.execute("SELECT id, name FROM rooms WHERE id = ?", (row["room_id"],)).fetchone()
        room = {"id": r["id"], "name": r["name"]}
    out = {
        "id": row["id"], "title": row["title"], "kind": row["kind"],
        "start": row["start"], "end": row["end"], "when": human_range(parse_dt(row["start"]), parse_dt(row["end"])),
        "room": room, "course_id": row["course_id"], "organizer": person_label(ctx.conn, row["organizer_id"]),
        "organizer_id": row["organizer_id"], "groups": groups,
        "participants": [{"id": p, "name": person_label(ctx.conn, p)} for p in people[:12]],
        "participant_count": len(people), "status": row["status"],
    }
    if row["description"]:
        # Free text written by users: data for the model to read, never instructions to follow.
        out["description_utilisateur"] = row["description"][:400]
    return out


def _merge_busy(rows: list, skip=lambda r: False) -> list[dict]:
    intervals = sorted((parse_dt(r["start"]), parse_dt(r["end"])) for r in rows if not skip(r))
    merged: list[list[datetime]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [{"start": fmt_dt(s), "end": fmt_dt(e)} for s, e in merged]


# ---------------------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------------------

def _person_view_mode(ctx: Context, person) -> str:
    a = ctx.actor
    if person["id"] == a.id or a.is_staff:
        return "full"
    if a.is_teacher:
        return "busy"
    # student
    if person["role"] in ("teacher", "staff"):
        return "busy"
    if person["group_id"] == a.group_id:
        return "busy"
    raise PermissionDenied("un étudiant ne peut pas consulter l'agenda d'étudiants d'un autre groupe")


def _group_view_mode(ctx: Context, group_id: str) -> str:
    a = ctx.actor
    if a.is_staff:
        return "full"
    if a.is_teacher:
        return "full" if group_id in teacher_groups(ctx.conn, a.id) else "busy"
    if group_id == a.group_id:
        return "full"
    raise PermissionDenied("un étudiant ne peut consulter que l'emploi du temps de son propre groupe")


def _room_view_mode(ctx: Context) -> str:
    return "busy" if ctx.actor.is_student else "full"


def _date_range(start_date: str, end_date: str | None) -> tuple[datetime, datetime]:
    d1 = parse_date(start_date)
    d2 = parse_date(end_date) if end_date else d1
    if d2 < d1:
        raise InvalidRequest("la date de fin précède la date de début")
    if (d2 - d1).days > MAX_RANGE_DAYS:
        raise InvalidRequest(f"période trop longue : {MAX_RANGE_DAYS} jours au plus")
    return datetime.combine(d1, time.min), datetime.combine(d2 + timedelta(days=1), time.min)


# ---------------------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------------------

def get_schedule(ctx: Context, start_date: str, end_date: str | None = None, person_id: str | None = None,
                 group_id: str | None = None, room_id: str | None = None) -> dict:
    if sum(x is not None for x in (person_id, group_id, room_id)) > 1:
        raise InvalidRequest("indique une seule cible : person_id, group_id ou room_id")
    start, end = _date_range(start_date, end_date)
    if group_id:
        g = _group(ctx, group_id)
        mode, rows, target = _group_view_mode(ctx, group_id), _events_for_group(ctx, group_id, start, end), g["name"]
    elif room_id:
        r = _room(ctx, room_id)
        mode, rows, target = _room_view_mode(ctx), _events_for_room(ctx, room_id, start, end), r["name"]
    else:
        p = _person(ctx, person_id or ctx.actor.id)
        mode = _person_view_mode(ctx, p)
        rows = _events_for_person(ctx, p["id"], start, end)
        target = f"{p['first_name']} {p['last_name']}"
    out: dict[str, Any] = {"target": target, "from": fmt_dt(start)[:10], "to": fmt_dt(end - timedelta(days=1))[:10],
                           "view": mode}
    if mode == "full":
        out["events"] = [event_dict(ctx, r) for r in rows]
    else:
        out["busy"] = _merge_busy(rows, skip=lambda r: r["kind"] == "permanence")
        perms = [r for r in rows if r["kind"] == "permanence"]
        if perms:
            out["permanences"] = [{"event_id": r["id"], "start": r["start"], "end": r["end"],
                                   "when": human_range(parse_dt(r["start"]), parse_dt(r["end"])),
                                   "lieu": (r["description"] or "").split(",")[1].split(".")[0].strip()
                                   if "," in (r["description"] or "") else ""} for r in perms]
        out["note"] = "Vue disponibilités : le détail des événements d'autrui n'est pas communiqué (RGPD)."
    return out


def find_available_rooms(ctx: Context, start: str, end: str, min_capacity: int | None = None,
                         kind: str | None = None) -> dict:
    s, e = parse_dt(start), parse_dt(end)
    if e <= s:
        raise InvalidRequest("l'heure de fin doit suivre l'heure de début")
    kinds = _bookable_room_kinds(ctx)
    if kind:
        if kind not in kinds:
            raise PermissionDenied(f"les salles de type « {kind} » ne sont pas réservables avec ton profil "
                                   f"(autorisées : {', '.join(kinds)})")
        kinds = (kind,)
    q = "SELECT * FROM rooms WHERE kind IN (%s)" % ",".join("?" * len(kinds))
    params: list = list(kinds)
    if min_capacity:
        q += " AND capacity >= ?"
        params.append(int(min_capacity))
    rooms = ctx.conn.execute(q + " ORDER BY capacity, id", params).fetchall()
    free = [{"id": r["id"], "name": r["name"], "kind": r["kind"], "capacity": r["capacity"], "equipment": r["equipment"]}
            for r in rooms if not _events_for_room(ctx, r["id"], s, e)]
    return {"when": human_range(s, e), "count": len(free), "rooms": free[:15]}


def _bookable_room_kinds(ctx: Context) -> tuple[str, ...]:
    if ctx.actor.is_student:
        return ("study",)
    if ctx.actor.is_teacher:
        return ("amphi", "td", "tp_info", "labo", "meeting")
    return ("amphi", "td", "tp_info", "labo", "meeting", "study")


def find_free_slots(ctx: Context, duration_minutes: int, date_from: str, date_to: str,
                    person_ids: list[str] | None = None, group_ids: list[str] | None = None,
                    include_me: bool = True, room_kind: str | None = None, min_capacity: int | None = None,
                    earliest: str = "08:00", latest: str = "19:00", limit: int = 6) -> dict:
    """Common free slots for people and groups, optionally with a free room.

    When a student looks for a slot with a teacher, the search is limited to that teacher's
    office hours (the only time students can book), as the school rules say.
    """
    person_ids = list(dict.fromkeys(person_ids or []))
    group_ids = list(dict.fromkeys(group_ids or []))
    duration = int(duration_minutes)
    if not 15 <= duration <= 240:
        raise InvalidRequest("durée entre 15 et 240 minutes")
    d1, d2 = parse_date(date_from), parse_date(date_to)
    if d2 < d1 or (d2 - d1).days > MAX_RANGE_DAYS:
        raise InvalidRequest(f"période invalide (au plus {MAX_RANGE_DAYS} jours)")
    t_min, t_max = time.fromisoformat(earliest), time.fromisoformat(latest)
    if include_me and ctx.actor.id not in person_ids:
        person_ids.insert(0, ctx.actor.id)

    teachers_for_rdv: list[str] = []
    for pid in person_ids:
        p = _person(ctx, pid)
        _person_view_mode(ctx, p)                       # raises if the actor may not see this person
        if ctx.actor.is_student and p["role"] == "teacher":
            teachers_for_rdv.append(pid)
    for gid in group_ids:
        _group(ctx, gid)
        _group_view_mode(ctx, gid)
    if ctx.actor.is_student and group_ids:
        raise PermissionDenied("un étudiant ne peut pas planifier pour un groupe entier")
    if teachers_for_rdv and not RDV_MIN <= duration <= RDV_MAX:
        raise InvalidRequest(f"un rendez-vous étudiant avec un enseignant dure de {RDV_MIN} à {RDV_MAX} minutes, "
                             "pendant sa permanence")

    window_start = datetime.combine(d1, time.min)
    window_end = datetime.combine(d2 + timedelta(days=1), time.min)
    busy: list[tuple[datetime, datetime]] = []
    permanences: list[tuple[datetime, datetime]] = []
    for pid in person_ids:
        for r in _events_for_person(ctx, pid, window_start, window_end):
            if pid in teachers_for_rdv and r["kind"] == "permanence" and r["organizer_id"] == pid:
                permanences.append((parse_dt(r["start"]), parse_dt(r["end"])))
                continue
            busy.append((parse_dt(r["start"]), parse_dt(r["end"])))
    for gid in group_ids:
        busy += [(parse_dt(r["start"]), parse_dt(r["end"])) for r in _events_for_group(ctx, gid, window_start, window_end)]

    need_room = bool(room_kind or min_capacity or group_ids)
    capacity = int(min_capacity or 0) or (sum(group_size(ctx, g) for g in group_ids) + len(person_ids))
    if need_room:
        kinds = _bookable_room_kinds(ctx)
        if room_kind:
            if room_kind not in kinds:
                raise PermissionDenied(f"type de salle non réservable avec ton profil : {room_kind}")
            kinds = (room_kind,)
        rooms = ctx.conn.execute("SELECT * FROM rooms WHERE kind IN (%s) AND capacity >= ? ORDER BY capacity, id"
                                 % ",".join("?" * len(kinds)), (*kinds, capacity)).fetchall()
        if not rooms:
            raise InvalidRequest(f"aucune salle de ce type pour {capacity} personnes")
    step = timedelta(minutes=15)
    earliest_start = ctx.now() + timedelta(minutes=30)
    slot_starts = {s for s, _e in SLOTS}

    found: list[dict] = []
    per_day: dict[date, list[datetime]] = {}
    day = d1
    while day <= d2 and len(found) < limit:
        if day.weekday() < 5:
            t = datetime.combine(day, t_min)
            last = datetime.combine(day, t_max) - timedelta(minutes=duration)
            while t <= last and len(found) < limit:
                cand_end = t + timedelta(minutes=duration)
                ok = t >= earliest_start
                if ok and group_ids:
                    ok = t.time() in slot_starts and not (day.weekday() == 2 and t.time() >= WEDNESDAY_AFTERNOON)
                if ok and teachers_for_rdv:
                    ok = any(ps <= t and cand_end <= pe for ps, pe in permanences)
                if ok:
                    ok = not any(overlaps(t, cand_end, bs, be) for bs, be in busy)
                if ok and any(abs((t - c).total_seconds()) < duration * 60 for c in per_day.get(day, [])):
                    ok = False
                if ok and len(per_day.get(day, [])) >= 2:
                    ok = False
                if ok:
                    free_rooms = []
                    if need_room:
                        free_rooms = [{"id": r["id"], "name": r["name"], "capacity": r["capacity"]}
                                      for r in rooms if not _events_for_room(ctx, r["id"], t, cand_end)][:3]
                    if not need_room or free_rooms:
                        found.append({"start": fmt_dt(t), "end": fmt_dt(cand_end), "when": human_range(t, cand_end),
                                      **({"rooms": free_rooms} if need_room else {})})
                        per_day.setdefault(day, []).append(t)
                t += step
        day += timedelta(days=1)

    result: dict[str, Any] = {"duration_minutes": duration, "participants": [person_label(ctx.conn, p) for p in person_ids],
                              "groups": group_ids, "slots": found}
    if teachers_for_rdv:
        result["note"] = "Créneaux limités aux permanences de l'enseignant (règle des rendez-vous)."
    if group_ids:
        result["note_groupes"] = ("Créneaux de cours standard, hors mercredi après-midi. Seuls les emplois du temps "
                                  "de groupe sont pris en compte, pas les réservations personnelles des étudiants.")
    if not found:
        result["message"] = "Aucun créneau libre sur cette période avec ces contraintes."
    return result


# ---------------------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------------------

def _validate(ctx: Context, data: dict, existing=None) -> dict:
    """Check every rule for an event (new or modified). Returns the normalised event."""
    a = ctx.actor
    start, end = parse_dt(data["start"]), parse_dt(data["end"])
    kind = data["kind"]
    title = (data.get("title") or "").strip()
    room_id = data.get("room_id") or None
    person_ids = list(dict.fromkeys(data.get("person_ids") or []))
    group_ids = list(dict.fromkeys(data.get("group_ids") or []))
    course_id = data.get("course_id") or None
    description = (data.get("description") or "").strip()
    minutes = int((end - start).total_seconds() // 60)

    if kind not in KINDS:
        raise InvalidRequest(f"type d'événement inconnu : {kind}")
    if not 1 <= len(title) <= 120:
        raise InvalidRequest("le titre doit faire entre 1 et 120 caractères")
    if len(description) > 1000:
        raise InvalidRequest("description trop longue (1000 caractères au plus)")
    if end <= start:
        raise InvalidRequest("l'heure de fin doit suivre l'heure de début")
    if start.date() != end.date():
        raise InvalidRequest("un événement commence et finit le même jour")
    if start < ctx.now():
        raise InvalidRequest(f"impossible de planifier dans le passé (nous sommes le {human_dt(ctx.now())})")
    if start.weekday() >= 5:
        raise InvalidRequest("l'école est fermée le week-end")
    if start.time() < BUILDING_OPEN or end.time() > BUILDING_CLOSE:
        raise InvalidRequest("les bâtiments sont ouverts de 7 h 30 à 20 h 00")
    if minutes > (480 if a.is_staff else 240):
        raise InvalidRequest("événement trop long")
    if group_ids and kind in ("cours", "td", "tp") and start.weekday() == 2 and start.time() >= WEDNESDAY_AFTERNOON:
        raise InvalidRequest("aucun cours n'est programmé le mercredi après-midi (règlement des études, section 1)")

    room = _room(ctx, room_id) if room_id else None
    people = [_person(ctx, p) for p in person_ids if p != a.id]   # the other participants
    for g in group_ids:
        _group(ctx, g)
    if course_id and ctx.conn.execute("SELECT 1 FROM courses WHERE id = ?", (course_id,)).fetchone() is None:
        raise NotFound(f"cours inconnu : {course_id}")

    # ---- Role rules
    if a.is_student:
        if kind not in STUDENT_KINDS:
            raise PermissionDenied("un étudiant peut créer une réservation de salle de travail, un rendez-vous "
                                   "avec un enseignant ou un événement personnel")
        if group_ids or course_id:
            raise PermissionDenied("un étudiant ne peut pas planifier pour un groupe ou un cours")
        if kind == "reservation":
            if room is None or room["kind"] != "study":
                raise PermissionDenied("les étudiants réservent uniquement les salles de travail de la bibliothèque "
                                       "(BU-S1 à BU-S4)")
            if minutes > STUDY_MAX_MINUTES:
                raise InvalidRequest("une réservation de salle de travail dure au plus deux heures")
            if start.time() < LIBRARY_OPEN or end.time() > LIBRARY_CLOSE:
                raise InvalidRequest("les salles de travail sont réservables entre 8 h 00 et 19 h 00")
            if start.date() > ctx.now().date() + timedelta(days=STUDY_DAYS_AHEAD):
                raise InvalidRequest("réservation possible jusqu'à quatorze jours à l'avance")
            if any(p["role"] != "student" for p in people):
                raise InvalidRequest("une réservation de salle de travail ne réunit que des étudiants")
            same_day = ctx.conn.execute(
                "SELECT COUNT(*) FROM events WHERE organizer_id = ? AND kind = 'reservation' AND status = 'confirmed' "
                "AND substr(start, 1, 10) = ? AND id != ?",
                (a.id, start.date().isoformat(), existing["id"] if existing else -1)).fetchone()[0]
            if same_day:
                raise InvalidRequest("une seule réservation de salle de travail par jour et par étudiant")
        elif kind == "rendez_vous":
            teachers = [p for p in people if p["role"] == "teacher"]
            if len(teachers) != 1 or len(people) != 1:
                raise InvalidRequest("un rendez-vous réunit l'étudiant et un seul enseignant")
            if not RDV_MIN <= minutes <= RDV_MAX:
                raise InvalidRequest(f"un rendez-vous dure de {RDV_MIN} à {RDV_MAX} minutes")
            if room is not None:
                raise InvalidRequest("le rendez-vous a lieu au bureau de l'enseignant, sans réservation de salle")
            perm = ctx.conn.execute(
                "SELECT * FROM events WHERE kind = 'permanence' AND status = 'confirmed' AND organizer_id = ? "
                "AND start <= ? AND end >= ?", (teachers[0]["id"], fmt_dt(start), fmt_dt(end))).fetchone()
            if perm is None:
                raise InvalidRequest("un rendez-vous se prend pendant une permanence de l'enseignant "
                                     "(voir son agenda : get_schedule avec son identifiant)")
            if not description:
                description = (perm["description"] or "").split(".")[0]
        else:  # autre: a personal reminder in one's own calendar
            if room is not None or people:
                raise InvalidRequest("un événement personnel n'a ni salle ni autre participant")
    elif a.is_teacher:
        taught = teacher_groups(ctx.conn, a.id)
        foreign = [g for g in group_ids if g not in taught]
        if foreign:
            raise PermissionDenied(f"tu n'enseignes pas aux groupes : {', '.join(foreign)}")
        if course_id:
            pairs = teacher_course_groups(ctx.conn, a.id)
            if any((course_id, g) not in pairs for g in group_ids) or not group_ids:
                raise PermissionDenied(f"tu n'es pas responsable du cours {course_id} pour ces groupes")
        if kind in CLASS_KINDS and (not group_ids or room is None):
            raise InvalidRequest("une séance d'enseignement a au moins un groupe et une salle")
        if room is not None and room["kind"] == "study":
            raise PermissionDenied("les salles de travail de la bibliothèque sont réservées aux étudiants")
        outsiders = [p for p in people if p["role"] == "student" and p["group_id"] not in taught]
        if outsiders:
            raise PermissionDenied("tu ne peux inviter que des étudiants de tes groupes")
    # staff: no role restriction beyond the common rules

    if a.id not in person_ids:
        person_ids.insert(0, a.id)
    attendees = sum(group_size(ctx, g) for g in group_ids) + len(person_ids)
    if room is not None and attendees > room["capacity"]:
        raise InvalidRequest(f"{room['name']} : {room['capacity']} places pour {attendees} personnes")

    _check_conflicts(ctx, start, end, room_id, person_ids, group_ids, kind, existing["id"] if existing else None)
    return {"title": title, "kind": kind, "start": fmt_dt(start), "end": fmt_dt(end), "room_id": room_id,
            "person_ids": person_ids, "group_ids": group_ids, "course_id": course_id, "description": description}


def _check_conflicts(ctx, start, end, room_id, person_ids, group_ids, kind, exclude_id) -> None:
    found: list[dict] = []

    def add(kind_, who, r):
        if r["id"] != exclude_id:
            found.append({"type": kind_, "who": who, "event": r["title"], "start": r["start"], "end": r["end"]})

    if room_id:
        for r in _events_for_room(ctx, room_id, start, end):
            add("salle", room_id, r)
    for g in group_ids:
        for r in _events_for_group(ctx, g, start, end):
            add("groupe", g, r)
    for p in person_ids:
        for r in _events_for_person(ctx, p, start, end):
            # A student's appointment sits inside the teacher's office hours: those are not a clash.
            if kind == "rendez_vous" and r["kind"] == "permanence" and r["organizer_id"] == p:
                continue
            add("personne", person_label(ctx.conn, p), r)
    if found:
        seen, unique = set(), []
        for f in found:
            key = (f["type"], f["who"], f["event"], f["start"])
            if key not in seen:
                seen.add(key)
                unique.append(f)
        first = unique[0]
        raise Conflict(f"conflit d'agenda ({len(unique)}) : {first['type']} {first['who']} déjà pris par "
                       f"« {first['event']} » ({first['start'][11:]}–{first['end'][11:]}). "
                       "Cherche un autre créneau avec find_free_slots.", details=unique[:10])


def _insert(ctx: Context, ev: dict) -> int:
    now = fmt_dt(ctx.now())
    cur = ctx.conn.execute(
        "INSERT INTO events(title, kind, start, end, room_id, course_id, organizer_id, description, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ev["title"], ev["kind"], ev["start"], ev["end"], ev["room_id"], ev["course_id"], ctx.actor.id,
         ev["description"], now, now))
    eid = cur.lastrowid
    ctx.conn.executemany("INSERT INTO event_people(event_id, person_id) VALUES (?,?)", [(eid, p) for p in ev["person_ids"]])
    ctx.conn.executemany("INSERT INTO event_groups(event_id, group_id) VALUES (?,?)", [(eid, g) for g in ev["group_ids"]])
    return eid


def _describe(ctx: Context, ev: dict) -> str:
    where = ""
    if ev["room_id"]:
        where = f", {_room(ctx, ev['room_id'])['name']}"
    who = []
    if ev["group_ids"]:
        who.append("groupes " + ", ".join(ev["group_ids"]))
    others = [person_label(ctx.conn, p) for p in ev["person_ids"] if p != ctx.actor.id]
    if others:
        who.append("avec " + ", ".join(others[:5]) + ("…" if len(others) > 5 else ""))
    return (f"« {ev['title']} » ({ev['kind']}), {human_range(parse_dt(ev['start']), parse_dt(ev['end']))}{where}"
            + (f", {' ; '.join(who)}" if who else ""))


def create_event(ctx: Context, title: str, kind: str, start: str, end: str, room_id: str | None = None,
                 person_ids: list[str] | None = None, group_ids: list[str] | None = None,
                 course_id: str | None = None, description: str = "", notify_participants: bool = True,
                 dry_run: bool = False) -> dict:
    ev = _validate(ctx, {"title": title, "kind": kind, "start": start, "end": end, "room_id": room_id,
                         "person_ids": person_ids, "group_ids": group_ids, "course_id": course_id,
                         "description": description})
    summary = "Créer " + _describe(ctx, ev)
    recipients = _recipient_count(ctx, ev["group_ids"], ev["person_ids"])
    if notify_participants and recipients:
        summary += f" — et prévenir {recipients} personne(s)"
    if dry_run:
        return {"summary": summary, "event": ev}
    eid = _insert(ctx, ev)
    if notify_participants and recipients:
        notify(ctx, f"Nouvel événement : {ev['title']}",
               f"{ctx.actor.name} a planifié « {ev['title']} », "
               f"{human_range(parse_dt(ev['start']), parse_dt(ev['end']))}"
               + (f", {_room(ctx, ev['room_id'])['name']}" if ev["room_id"] else "") + ".",
               ev["group_ids"], [p for p in ev["person_ids"] if p != ctx.actor.id], event_id=eid)
    ctx.conn.commit()
    return {"summary": summary, "event": event_dict(ctx, _event(ctx, eid))}


def _recipient_count(ctx: Context, group_ids: list[str], person_ids: list[str]) -> int:
    members = set()
    for g in group_ids:
        members |= {r[0] for r in ctx.conn.execute("SELECT id FROM people WHERE group_id = ?", (g,))}
    members |= set(person_ids)
    members.discard(ctx.actor.id)
    return len(members)


def _can_modify(ctx: Context, row) -> None:
    a = ctx.actor
    if row["status"] != "confirmed":
        raise InvalidRequest("cet événement est déjà annulé")
    if parse_dt(row["start"]) < ctx.now():
        raise InvalidRequest("cet événement a déjà commencé ou est passé")
    if a.is_staff or row["organizer_id"] == a.id:
        return
    if a.is_teacher and row["course_id"]:
        groups = [r[0] for r in ctx.conn.execute("SELECT group_id FROM event_groups WHERE event_id = ?", (row["id"],))]
        pairs = teacher_course_groups(ctx.conn, a.id)
        if groups and all((row["course_id"], g) in pairs for g in groups):
            return
    raise PermissionDenied("tu ne peux modifier que les événements que tu organises "
                           "(ou, pour un enseignant, les séances de ses cours)")


def update_event(ctx: Context, event_id: int, start: str | None = None, end: str | None = None,
                 room_id: str | None = None, title: str | None = None, description: str | None = None,
                 notify_participants: bool = True, dry_run: bool = False) -> dict:
    row = _event(ctx, event_id)
    _can_modify(ctx, row)
    old_start, old_end = parse_dt(row["start"]), parse_dt(row["end"])
    new_start = parse_dt(start) if start else old_start
    new_end = parse_dt(end) if end else (new_start + (old_end - old_start))
    people = [r[0] for r in ctx.conn.execute("SELECT person_id FROM event_people WHERE event_id = ?", (row["id"],))]
    groups = [r[0] for r in ctx.conn.execute("SELECT group_id FROM event_groups WHERE event_id = ?", (row["id"],))]
    data = {"title": title if title is not None else row["title"], "kind": row["kind"], "start": fmt_dt(new_start),
            "end": fmt_dt(new_end), "room_id": room_id if room_id is not None else row["room_id"],
            "person_ids": people, "group_ids": groups, "course_id": row["course_id"],
            "description": description if description is not None else row["description"]}
    # Validate as the organizer would, so that a teacher moving a section keeps the section's rules.
    ev = _validate(ctx, data, existing=row)
    changes = []
    if (new_start, new_end) != (old_start, old_end):
        changes.append(f"du {human_range(old_start, old_end)} au {human_range(new_start, new_end)}")
    if ev["room_id"] != row["room_id"]:
        changes.append(f"salle {row['room_id'] or 'aucune'} → {ev['room_id'] or 'aucune'}")
    if ev["title"] != row["title"]:
        changes.append(f"titre « {ev['title']} »")
    if not changes and ev["description"] == row["description"]:
        raise InvalidRequest("aucune modification demandée")
    summary = f"Modifier « {row['title']} » : " + ("; ".join(changes) or "description")
    recipients = [p for p in people if p != ctx.actor.id]
    count = _recipient_count(ctx, groups, recipients)
    if notify_participants and count:
        summary += f" — et prévenir {count} personne(s)"
    if dry_run:
        return {"summary": summary, "event": ev}
    ctx.conn.execute("UPDATE events SET title=?, start=?, end=?, room_id=?, description=?, updated_at=? WHERE id=?",
                     (ev["title"], ev["start"], ev["end"], ev["room_id"], ev["description"], fmt_dt(ctx.now()), row["id"]))
    if notify_participants and count:
        room_txt = f", {_room(ctx, ev['room_id'])['name']}" if ev["room_id"] else ""
        notify(ctx, f"Modification : {row['title']}",
               f"« {row['title']} » prévu le {human_range(old_start, old_end)} a lieu désormais le "
               f"{human_range(new_start, new_end)}{room_txt}. Message envoyé par {ctx.actor.name}.",
               groups, recipients, event_id=row["id"])
    ctx.conn.commit()
    return {"summary": summary, "event": event_dict(ctx, _event(ctx, row["id"]))}


def cancel_event(ctx: Context, event_id: int, reason: str = "", notify_participants: bool = True,
                 dry_run: bool = False) -> dict:
    row = _event(ctx, event_id)
    _can_modify(ctx, row)
    people = [r[0] for r in ctx.conn.execute("SELECT person_id FROM event_people WHERE event_id = ?", (row["id"],))]
    groups = [r[0] for r in ctx.conn.execute("SELECT group_id FROM event_groups WHERE event_id = ?", (row["id"],))]
    recipients = [p for p in people if p != ctx.actor.id]
    count = _recipient_count(ctx, groups, recipients)
    when = human_range(parse_dt(row["start"]), parse_dt(row["end"]))
    summary = f"Annuler « {row['title']} », {when}" + (f" (motif : {reason})" if reason else "")
    if notify_participants and count:
        summary += f" — et prévenir {count} personne(s)"
    if dry_run:
        return {"summary": summary, "event_id": row["id"]}
    ctx.conn.execute("UPDATE events SET status='cancelled', updated_at=? WHERE id=?", (fmt_dt(ctx.now()), row["id"]))
    if notify_participants and count:
        notify(ctx, f"Annulation : {row['title']}",
               f"« {row['title']} » prévu le {when} est annulé." + (f" Motif : {reason}." if reason else "")
               + f" Message envoyé par {ctx.actor.name}.", groups, recipients, event_id=row["id"])
    ctx.conn.commit()
    return {"summary": summary, "event": event_dict(ctx, _event(ctx, row["id"]))}
