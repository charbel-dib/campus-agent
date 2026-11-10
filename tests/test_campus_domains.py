"""Internship agreements, grade entry and publication, make-up registration."""

from __future__ import annotations

import pytest

from campus_agent import tools
from campus_agent.domain import Conflict, InvalidRequest, NotFound, PermissionDenied, grading, internships

REQUEST = dict(company="Naval Group", subject="Détection d'anomalies sur des signaux sonar", start_date="2027-02-01",
               end_date="2027-07-16", company_tutor="Anne Riou", school_tutor_id="tch-001")


def test_internship_goes_from_request_to_signature(ctx_for, conn):
    student, tutor, office = ctx_for("stu-0001"), ctx_for("tch-001"), ctx_for("stf-01")
    iid = internships.create_internship_request(student, **REQUEST)["internship_id"]
    assert conn.execute("SELECT COUNT(*) FROM message_recipients WHERE person_id = 'tch-001' AND message_id = "
                        "(SELECT MAX(id) FROM messages)").fetchone()[0] == 1          # the tutor is told
    with pytest.raises(InvalidRequest):
        internships.review_internship(office, iid, "signer")                         # not before the tutor
    assert internships.review_internship(tutor, iid, "valider")["status"] == "validee_tuteur"
    assert internships.review_internship(office, iid, "signer")["status"] == "signee"
    assert internships.get_internship_status(student)["internships"][0]["status"] == "signee"


def test_internship_rules(ctx_for):
    student = ctx_for("stu-0001")
    with pytest.raises(InvalidRequest, match="trois semaines"):
        internships.create_internship_request(student, **{**REQUEST, "start_date": "2026-11-23", "end_date": "2027-05-10"})
    with pytest.raises(InvalidRequest, match="22 à 26"):
        internships.create_internship_request(student, **{**REQUEST, "end_date": "2027-03-01"})
    with pytest.raises(NotFound):
        internships.create_internship_request(student, **{**REQUEST, "school_tutor_id": "stu-0002"})
    with pytest.raises(PermissionDenied):
        internships.create_internship_request(ctx_for("tch-001"), **REQUEST)
    internships.create_internship_request(student, **REQUEST)
    with pytest.raises(Conflict):
        internships.create_internship_request(student, **REQUEST)


def test_internship_review_is_scoped_and_refusal_needs_a_reason(ctx_for):
    iid = internships.create_internship_request(ctx_for("stu-0001"), **REQUEST)["internship_id"]
    with pytest.raises(NotFound):
        internships.review_internship(ctx_for("tch-008"), iid, "valider")           # not this student's tutor
    assert internships.get_internship_status(ctx_for("tch-008"))["internships"] == []
    assert internships.get_internship_status(ctx_for("stu-0002"))["internships"] == []
    with pytest.raises(InvalidRequest, match="motivé"):
        internships.review_internship(ctx_for("tch-001"), iid, "refuser")
    assert internships.review_internship(ctx_for("tch-001"), iid, "refuser", "Sujet trop éloigné de la formation")["status"] == "refusee"


def test_grades_are_entered_unpublished_then_published(ctx_for, conn):
    teacher, student = ctx_for("tch-001"), ctx_for("stu-0001")
    entry = dict(course_id="IA301", label="TP noté 2", coefficient_percent=10, date="2026-11-13",
                 grades=[{"student_id": "stu-0001", "score": 15.5}, {"student_id": "stu-0002", "score": 9}])
    assert grading.enter_grades(teacher, **entry)["entered"] == 2
    from campus_agent.domain import academics

    seen = [g["label"] for c in academics.get_grades(student)["courses"] for g in c["grades"]]
    assert "TP noté 2" not in seen                                                   # not visible yet
    before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert grading.publish_grades(teacher, "IA301", "TP noté 2")["published"] == 2
    seen = [g["label"] for c in academics.get_grades(student)["courses"] for g in c["grades"]]
    assert "TP noté 2" in seen
    msg = conn.execute("SELECT body FROM messages ORDER BY id DESC LIMIT 1").fetchone()["body"]
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == before + 1 and "15" not in msg
    with pytest.raises(Conflict):
        grading.enter_grades(teacher, **entry)                                       # no second grade for the same test


def test_grade_entry_is_limited_to_the_teachers_students_and_valid_scores(ctx_for):
    base = dict(course_id="IA301", label="Oral", coefficient_percent=10, date="2026-11-13")
    with pytest.raises(PermissionDenied):
        grading.enter_grades(ctx_for("tch-008"), **base, grades=[{"student_id": "stu-0001", "score": 12}])
    with pytest.raises(PermissionDenied):
        grading.enter_grades(ctx_for("tch-001"), **base, grades=[{"student_id": "stu-0003", "score": 12}])
    with pytest.raises(InvalidRequest):
        grading.enter_grades(ctx_for("tch-001"), **base, grades=[{"student_id": "stu-0001", "score": 21}])
    res = tools.execute(ctx_for("stu-0001"), "enter_grades", {**base, "grades": [{"student_id": "stu-0001", "score": 20}]})
    assert res.is_error and res.content["code"] == "denied"                          # a student never enters a grade


def test_makeup_registration_follows_the_rules(ctx_for, conn):
    weak, strong = ctx_for("stu-0002"), ctx_for("stu-0001")
    out = grading.register_makeup(weak, "IA301")
    assert out["reason"] == "moyenne_insuffisante" and "12/20" in out["rule"]
    with pytest.raises(Conflict):
        grading.register_makeup(weak, "IA301")
    with pytest.raises(InvalidRequest, match="inférieure à 10"):
        grading.register_makeup(strong, "IA301")
    with pytest.raises(NotFound):
        grading.register_makeup(strong, "AN101")
    assert [r["student_id"] for r in grading.get_makeups(ctx_for("tch-001"), "IA301")["registrations"]] == ["stu-0002"]
    with pytest.raises(PermissionDenied):
        grading.get_makeups(ctx_for("tch-008"), "IA301")


def test_new_writes_wait_for_confirmation(ctx_for, conn):
    ctx = ctx_for("stu-0002", channel="agent")
    res = tools.execute(ctx, "register_makeup", {"course_id": "IA301"})
    assert res.pending_action and conn.execute("SELECT COUNT(*) FROM makeup_registrations").fetchone()[0] == 0
    assert not tools.confirm_pending(ctx, res.pending_action["id"]).is_error
    assert conn.execute("SELECT COUNT(*) FROM makeup_registrations").fetchone()[0] == 1
