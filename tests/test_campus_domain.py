"""Business rules of the campus agent, checked without any language model."""

from __future__ import annotations

from pathlib import Path

import pytest

from campus_agent.domain import Conflict, InvalidRequest, NotFound, PermissionDenied
from campus_agent.domain import academics, calendar, messaging, people

STUDENT, CLASSMATE, OTHER_STUDENT = "stu-0001", "stu-0002", "stu-0003"   # 3A-IA, 3A-IA, 1A-TD2
TEACHER, OTHER_TEACHER, STAFF = "tch-001", "tch-008", "stf-01"           # IA301 lead, MA101 lead


def count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------- generated school

def test_school_has_no_double_booking(conn):
    assert count(conn, """SELECT COUNT(*) FROM events a JOIN events b ON a.room_id = b.room_id AND a.id < b.id
                          AND a.start < b.end AND b.start < a.end""") == 0
    assert count(conn, """SELECT COUNT(*) FROM event_groups x JOIN event_groups y ON x.group_id = y.group_id
                          AND x.event_id < y.event_id JOIN events a ON a.id = x.event_id JOIN events b ON b.id = y.event_id
                          WHERE a.start < b.end AND b.start < a.end""") == 0
    assert count(conn, """SELECT COUNT(*) FROM event_people x JOIN event_people y ON x.person_id = y.person_id
                          AND x.event_id < y.event_id JOIN events a ON a.id = x.event_id JOIN events b ON b.id = y.event_id
                          WHERE a.start < b.end AND b.start < a.end""") == 0


def test_school_is_deterministic(tmp_path):
    from campus_agent.clock import parse_dt
    from campus_agent.seed import create_database

    a = create_database(tmp_path / "a.db", parse_dt("2026-11-16T09:00"), seed=7)
    b = create_database(tmp_path / "b.db", parse_dt("2026-11-16T09:00"), seed=7)
    assert a == b and a["students"] == 290 and a["teachers"] == 30


def test_no_class_on_wednesday_afternoon(conn):
    assert count(conn, """SELECT COUNT(*) FROM events WHERE kind IN ('cours','td','tp')
                          AND strftime('%w', start) = '3' AND substr(start, 12, 5) >= '12:30'""") == 0


def test_personas(conn):
    row = conn.execute("SELECT first_name, last_name, group_id FROM people WHERE id = ?", (STUDENT,)).fetchone()
    assert tuple(row) == ("Inès", "Haddad", "3A-IA")
    assert count(conn, "SELECT COUNT(*) FROM absences WHERE student_id = ? AND status = 'non_justifiee'", STUDENT) >= 1


# ---------------------------------------------------------------- directory

def test_students_do_not_see_other_students_emails(ctx_for):
    res = people.search_people(ctx_for(STUDENT), "Le Corre")
    assert res["results"][0]["id"] == CLASSMATE and "email" not in res["results"][0]
    res = people.search_people(ctx_for(TEACHER), "Le Corre")
    assert "email" in res["results"][0]


def test_search_people_is_accent_and_case_insensitive(ctx_for):
    assert people.search_people(ctx_for(STUDENT), "ines haddad")["results"][0]["id"] == STUDENT


# ---------------------------------------------------------------- schedules and visibility

def test_own_schedule_is_detailed(ctx_for):
    res = calendar.get_schedule(ctx_for(STUDENT), "2026-11-16", "2026-11-20")
    assert res["view"] == "full" and len(res["events"]) >= 9


def test_teacher_schedule_seen_by_student_is_busy_only_with_office_hours(ctx_for):
    res = calendar.get_schedule(ctx_for(STUDENT), "2026-11-16", "2026-11-20", person_id=TEACHER)
    assert res["view"] == "busy" and "events" not in res
    assert res["permanences"] and res["permanences"][0]["lieu"].startswith("Bureau")


def test_student_cannot_read_another_groups_student_or_group(ctx_for):
    with pytest.raises(PermissionDenied):
        calendar.get_schedule(ctx_for(STUDENT), "2026-11-16", person_id=OTHER_STUDENT)
    with pytest.raises(PermissionDenied):
        calendar.get_schedule(ctx_for(STUDENT), "2026-11-16", group_id="1A-TD2")


def test_range_is_capped(ctx_for):
    with pytest.raises(InvalidRequest):
        calendar.get_schedule(ctx_for(STUDENT), "2026-11-01", "2026-12-31")


# ---------------------------------------------------------------- appointments

def _first_rdv_slot(ctx):
    res = calendar.find_free_slots(ctx, 20, "2026-11-16", "2026-11-27", person_ids=[TEACHER])
    assert res["slots"], res
    return res["slots"][0]


def test_appointment_slots_are_inside_office_hours(ctx_for, conn):
    ctx = ctx_for(STUDENT)
    res = calendar.find_free_slots(ctx, 30, "2026-11-16", "2026-11-27", person_ids=[TEACHER])
    perms = conn.execute("SELECT start, end FROM events WHERE kind='permanence' AND organizer_id=?", (TEACHER,)).fetchall()
    for slot in res["slots"]:
        assert any(p["start"] <= slot["start"] and slot["end"] <= p["end"] for p in perms)


def test_book_appointment_then_clash(ctx_for, conn):
    slot = _first_rdv_slot(ctx_for(STUDENT))
    preview = calendar.create_event(ctx_for(STUDENT), "Projet", "rendez_vous", slot["start"], slot["end"],
                                    person_ids=[TEACHER], description="Choix du sujet", dry_run=True)
    assert "Yann Le Goff" in preview["summary"]
    assert count(conn, "SELECT COUNT(*) FROM events WHERE kind='rendez_vous'") == 0     # dry run wrote nothing
    calendar.create_event(ctx_for(STUDENT), "Projet", "rendez_vous", slot["start"], slot["end"],
                          person_ids=[TEACHER], description="Choix du sujet")
    with pytest.raises(Conflict):
        calendar.create_event(ctx_for(CLASSMATE), "Projet", "rendez_vous", slot["start"], slot["end"],
                              person_ids=[TEACHER], description="Autre sujet")
    # the teacher was notified
    assert count(conn, "SELECT COUNT(*) FROM message_recipients WHERE person_id = ?", TEACHER) == 1


def test_appointment_outside_office_hours_or_too_long_is_refused(ctx_for):
    ctx = ctx_for(STUDENT)
    with pytest.raises(InvalidRequest, match="permanence"):
        calendar.create_event(ctx, "RDV", "rendez_vous", "2026-11-18T10:30", "2026-11-18T10:50", person_ids=[TEACHER])
    slot = _first_rdv_slot(ctx)
    with pytest.raises(InvalidRequest, match="15 à 30"):
        calendar.create_event(ctx, "RDV", "rendez_vous", slot["start"], slot["start"][:11] + "17:00",
                              person_ids=[TEACHER])


# ---------------------------------------------------------------- study rooms

def test_student_study_room_rules(ctx_for):
    ctx = ctx_for(STUDENT)
    with pytest.raises(PermissionDenied):
        calendar.create_event(ctx, "Révisions", "reservation", "2026-11-18T08:00", "2026-11-18T10:00", room_id="B-101")
    with pytest.raises(InvalidRequest, match="deux heures"):
        calendar.create_event(ctx, "Révisions", "reservation", "2026-11-18T13:00", "2026-11-18T16:00", room_id="BU-S1")
    calendar.create_event(ctx, "Révisions", "reservation", "2026-11-18T13:00", "2026-11-18T15:00", room_id="BU-S1")
    with pytest.raises(InvalidRequest, match="une seule"):
        calendar.create_event(ctx, "Révisions bis", "reservation", "2026-11-18T16:00", "2026-11-18T17:00", room_id="BU-S2")
    with pytest.raises(InvalidRequest, match="quatorze"):
        calendar.create_event(ctx, "Plus tard", "reservation", "2026-12-15T13:00", "2026-12-15T14:00", room_id="BU-S1")


def test_study_room_during_a_class_is_a_conflict(ctx_for):
    with pytest.raises(Conflict) as err:
        calendar.create_event(ctx_for(STUDENT), "Révisions", "reservation", "2026-11-17T08:30", "2026-11-17T09:30",
                              room_id="BU-S2")
    assert err.value.details[0]["type"] == "personne"


def test_no_planning_in_the_past_or_at_weekends(ctx_for):
    ctx = ctx_for(STUDENT)
    with pytest.raises(InvalidRequest, match="passé"):
        calendar.create_event(ctx, "X", "autre", "2026-11-13T10:00", "2026-11-13T11:00")
    with pytest.raises(InvalidRequest, match="week-end"):
        calendar.create_event(ctx, "X", "autre", "2026-11-21T10:00", "2026-11-21T11:00")


# ---------------------------------------------------------------- teachers

def test_teacher_can_only_schedule_their_groups(ctx_for):
    with pytest.raises(PermissionDenied):
        calendar.create_event(ctx_for(TEACHER), "Rattrapage", "cours", "2026-11-23T10:30", "2026-11-23T12:30",
                              room_id="B-105", group_ids=["1A-TD1"])


def test_no_class_on_wednesday_afternoon_rule(ctx_for):
    with pytest.raises(InvalidRequest, match="mercredi"):
        calendar.create_event(ctx_for(TEACHER), "Rattrapage", "cours", "2026-11-25T13:45", "2026-11-25T15:45",
                              room_id="C-202", group_ids=["3A-IA"], course_id="IA301")


def test_teacher_moves_a_session_and_students_are_notified(ctx_for, conn):
    ctx = ctx_for(TEACHER)
    ev = conn.execute("SELECT id FROM events WHERE course_id='IA301' AND kind='tp' AND start >= '2026-11-20' "
                      "ORDER BY start LIMIT 1").fetchone()["id"]
    slot = calendar.find_free_slots(ctx, 120, "2026-11-23", "2026-11-27", group_ids=["3A-IA"],
                                    room_kind="tp_info")["slots"][0]
    res = calendar.update_event(ctx, ev, start=slot["start"], room_id=slot["rooms"][0]["id"])
    assert res["event"]["start"] == slot["start"]
    assert count(conn, "SELECT COUNT(*) FROM message_recipients WHERE person_id = ?", STUDENT) == 1
    inbox = messaging.get_messages(ctx_for(STUDENT))["received"]
    assert inbox[0]["subject"].startswith("Modification")


def test_teacher_cannot_create_a_clash_for_the_group(ctx_for):
    with pytest.raises(Conflict):
        calendar.create_event(ctx_for(TEACHER), "Rattrapage", "cours", "2026-11-17T08:15", "2026-11-17T10:15",
                              room_id="B-106", group_ids=["3A-IA"], course_id="IA301")


def test_student_cannot_cancel_a_class_and_nobody_edits_the_past(ctx_for, conn):
    ev = conn.execute("SELECT id FROM events WHERE course_id='IA301' AND start > '2026-11-17' LIMIT 1").fetchone()["id"]
    with pytest.raises(PermissionDenied):
        calendar.cancel_event(ctx_for(STUDENT), ev)
    past = conn.execute("SELECT id FROM events WHERE course_id='IA301' AND start < '2026-11-16' LIMIT 1").fetchone()["id"]
    with pytest.raises(InvalidRequest):
        calendar.cancel_event(ctx_for(TEACHER), past)


def test_staff_room_capacity_is_enforced(ctx_for):
    with pytest.raises(InvalidRequest, match="places"):
        calendar.create_event(ctx_for(STAFF), "Réunion de rentrée", "reunion", "2026-11-23T12:30", "2026-11-23T13:30",
                              room_id="E-012", group_ids=["2A-TD1"])


# ---------------------------------------------------------------- grades and absences

def test_student_sees_only_published_grades(ctx_for, conn):
    res = academics.get_grades(ctx_for(STUDENT))
    labels = {g["label"] for c in res["courses"] for g in c["grades"]}
    assert "Contrôle continu" not in labels                      # not published yet
    teacher_view = academics.get_grades(ctx_for(TEACHER), student_id=STUDENT)
    assert {c["course_id"] for c in teacher_view["courses"]} <= {"IA301", "PR301"}
    assert any(g.get("published") is False for c in teacher_view["courses"] for g in c["grades"])


def test_grade_access_is_scoped(ctx_for):
    with pytest.raises(PermissionDenied):
        academics.get_grades(ctx_for(STUDENT), student_id=CLASSMATE)
    with pytest.raises(PermissionDenied):
        academics.get_grades(ctx_for(OTHER_TEACHER), student_id=STUDENT)
    with pytest.raises(PermissionDenied):
        academics.get_grades(ctx_for(OTHER_TEACHER), course_id="IA301")
    stats = academics.get_grades(ctx_for(TEACHER), course_id="IA301")
    assert stats["students"] == 30 and stats["assessments"]


def test_absence_justification_flow(ctx_for, conn):
    ab = academics.get_absences(ctx_for(STUDENT), status="non_justifiee")["absences"][0]
    assert ab["justify_before"] >= "2026-11-16"
    with pytest.raises(PermissionDenied):
        academics.justify_absence(ctx_for(CLASSMATE), ab["id"], "Certificat médical")
    academics.justify_absence(ctx_for(STUDENT), ab["id"], "Certificat médical, Dr Martin")
    with pytest.raises(InvalidRequest):
        academics.justify_absence(ctx_for(STUDENT), ab["id"], "Certificat médical")   # already pending
    with pytest.raises(PermissionDenied):
        academics.review_absence(ctx_for(TEACHER), ab["id"], "justifiee")
    with pytest.raises(InvalidRequest, match="motivé"):
        academics.review_absence(ctx_for(STAFF), ab["id"], "refusee")
    academics.review_absence(ctx_for(STAFF), ab["id"], "justifiee")
    status = conn.execute("SELECT status FROM absences WHERE id = ?", (ab["id"],)).fetchone()[0]
    assert status == "justifiee"
    assert messaging.get_messages(ctx_for(STUDENT))["received"][0]["subject"].startswith("Justification")


def test_absence_deadline_is_enforced(ctx_for):
    later = ctx_for(STUDENT, now="2026-12-01T09:00")
    ab = academics.get_absences(later, status="non_justifiee")["absences"][0]
    with pytest.raises(InvalidRequest, match="délai"):
        academics.justify_absence(later, ab["id"], "Certificat médical")


def test_teacher_sees_absences_of_own_courses_only(ctx_for):
    res = academics.get_absences(ctx_for(TEACHER))
    assert all(a["course_id"] in ("IA301", "PR301") for a in res["absences"])


def test_certificate_pdf(ctx_for):
    res = academics.generate_certificate(ctx_for(STUDENT))
    path = academics.certificate_path(ctx_for(STUDENT), res["reference"])
    assert Path(path).read_bytes()[:4] == b"%PDF"
    with pytest.raises(PermissionDenied):
        academics.certificate_path(ctx_for(CLASSMATE), res["reference"])
    with pytest.raises(PermissionDenied):
        academics.generate_certificate(ctx_for(TEACHER), STUDENT)
    assert academics.certificate_path(ctx_for(STAFF), res["reference"]).exists()


# ---------------------------------------------------------------- messages, tickets

def test_announcement_rules(ctx_for):
    with pytest.raises(PermissionDenied):
        messaging.send_announcement(ctx_for(STUDENT), "Info", "Un message aux autres.", group_ids=["3A-IA"])
    with pytest.raises(PermissionDenied):
        messaging.send_announcement(ctx_for(TEACHER), "Info", "Un message aux autres.", group_ids=["1A-TD1"])
    res = messaging.send_announcement(ctx_for(TEACHER), "TP de vendredi", "Pensez à apporter vos données.",
                                      group_ids=["3A-IA"])
    assert res["recipient_count"] == 30


def test_ticket(ctx_for):
    with pytest.raises(InvalidRequest):
        messaging.create_ticket(ctx_for(STUDENT), "cafeteria", "Café", "La machine à café est en panne.")
    res = messaging.create_ticket(ctx_for(STUDENT), "salles", "Prise B-104", "Les prises de la salle B-104 ne marchent plus.")
    assert res["ticket_id"]
    assert len(messaging.list_tickets(ctx_for(STUDENT))["tickets"]) == 1
    assert len(messaging.list_tickets(ctx_for(STAFF))["tickets"]) == 3


# ---------------------------------------------------------------- documents

def test_document_search(doc_index):
    top = doc_index.search("combien de temps pour justifier une absence")["results"][0]
    assert top["ref"] == "procedure-absences#1"
    assert doc_index.search("réserver une salle de travail")["results"][0]["ref"].startswith("reservation-salles")
    assert doc_index.search("quelle est la capitale du Pérou")["results"] == []


def test_unknown_ids_are_reported(ctx_for):
    with pytest.raises(NotFound):
        calendar.get_schedule(ctx_for(STAFF), "2026-11-16", person_id="tch-999")


def test_absences_can_be_listed_oldest_first(ctx_for):
    recent = academics.get_absences(ctx_for(STAFF), status="en_attente")["absences"]
    oldest = academics.get_absences(ctx_for(STAFF), status="en_attente", order="oldest")["absences"]
    assert oldest[0]["date"] == min(a["date"] for a in recent) and oldest[0]["id"] == recent[-1]["id"]
    assert oldest[0]["when"].split()[0] in ("lundi", "mardi", "mercredi", "jeudi", "vendredi")
    assert "submitted_at" in oldest[0]
