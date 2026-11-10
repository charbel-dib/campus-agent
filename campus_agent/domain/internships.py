"""Internship agreements: the student files a request, the school tutor validates it, the office signs it.

Rules (data/docs/stages.md): a request is filed at least three weeks before the start; the length
depends on the year (1A at least 4 weeks, 2A 10 to 14 weeks, 3A 22 to 26 weeks); no internship
starts without a signed agreement.

    deposee ──(school tutor)──▶ validee_tuteur ──(office)──▶ signee
        └───────────────(tutor or office, with a comment)──▶ refusee
"""

from __future__ import annotations

from datetime import timedelta

from ..clock import fmt_dt, parse_date
from . import Conflict, Context, InvalidRequest, NotFound, PermissionDenied, person_label
from .messaging import notify

NOTICE_DAYS = 21
WEEKS_BY_YEAR = {"1A": (4, 16), "2A": (10, 14), "3A": (22, 26)}
ACTIVE = ("deposee", "validee_tuteur")


def _view(ctx: Context, r) -> dict:
    return {"id": r["id"], "student": person_label(ctx.conn, r["student_id"]), "student_id": r["student_id"],
            "company": r["company"], "subject": r["subject"], "start_date": r["start_date"], "end_date": r["end_date"],
            "company_tutor": r["company_tutor"], "school_tutor": person_label(ctx.conn, r["school_tutor_id"]),
            "status": r["status"], **({"comment_utilisateur": r["comment"]} if r["comment"] else {}),
            "updated_at": r["updated_at"]}


def create_internship_request(ctx: Context, company: str, subject: str, start_date: str, end_date: str,
                              company_tutor: str, school_tutor_id: str, dry_run: bool = False) -> dict:
    a = ctx.actor
    if not a.is_student:
        raise PermissionDenied("seul un étudiant dépose une demande de convention de stage")
    company, subject, company_tutor = company.strip(), subject.strip(), company_tutor.strip()
    if not (2 <= len(company) <= 120 and 5 <= len(subject) <= 300 and 3 <= len(company_tutor) <= 120):
        raise InvalidRequest("entreprise, sujet et tuteur en entreprise sont obligatoires")
    start, end = parse_date(start_date), parse_date(end_date)
    if start < ctx.now().date() + timedelta(days=NOTICE_DAYS):
        raise InvalidRequest(f"la demande se dépose au moins trois semaines avant le début du stage "
                             f"(au plus tôt le {(ctx.now().date() + timedelta(days=NOTICE_DAYS)).isoformat()})")
    weeks = (end - start).days / 7
    low, high = WEEKS_BY_YEAR.get((a.group_id or "")[:2], (4, 26))
    if end <= start or not low <= round(weeks) <= high:
        raise InvalidRequest(f"durée de {weeks:.0f} semaine(s) : pour ton année, un stage dure de {low} à {high} semaines")
    tutor = ctx.conn.execute("SELECT role FROM people WHERE id = ?", (school_tutor_id,)).fetchone()
    if tutor is None or tutor["role"] != "teacher":
        raise NotFound("le tuteur à l'école doit être un enseignant (cherche son identifiant avec search_people)")
    if ctx.conn.execute(f"SELECT 1 FROM internships WHERE student_id = ? AND status IN {ACTIVE}", (a.id,)).fetchone():
        raise Conflict("une demande de convention est déjà en cours pour toi")
    summary = (f"Déposer une demande de convention de stage chez {company}, du {start_date} au {end_date} "
               f"({weeks:.0f} semaines), tuteur à l'école : {person_label(ctx.conn, school_tutor_id)}")
    if dry_run:
        return {"summary": summary}
    now = fmt_dt(ctx.now())
    cur = ctx.conn.execute(
        "INSERT INTO internships(student_id, company, subject, start_date, end_date, company_tutor, school_tutor_id, "
        "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (a.id, company, subject, start_date, end_date, company_tutor, school_tutor_id, "deposee", now, now))
    notify(ctx, "Convention de stage à valider",
           f"{a.name} a déposé une demande de convention de stage chez {company} ({start_date} au {end_date}). "
           "Elle attend votre validation en tant que tuteur à l'école.", [], [school_tutor_id])
    ctx.conn.commit()
    return {"summary": summary, "internship_id": cur.lastrowid, "status": "deposee",
            "next": "validation par le tuteur à l'école, puis signature par la scolarité"}


def get_internship_status(ctx: Context, status: str | None = None) -> dict:
    a = ctx.actor
    sql, params = "SELECT * FROM internships", []
    where = []
    if a.is_student:
        where.append("student_id = ?")
        params.append(a.id)
    elif a.is_teacher:
        where.append("school_tutor_id = ?")
        params.append(a.id)
    if status:
        where.append("status = ?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = ctx.conn.execute(sql + " ORDER BY id DESC LIMIT 30", params).fetchall()
    return {"internships": [_view(ctx, r) for r in rows]}


def review_internship(ctx: Context, internship_id: int, decision: str, comment: str = "", dry_run: bool = False) -> dict:
    a = ctx.actor
    r = ctx.conn.execute("SELECT * FROM internships WHERE id = ?", (int(internship_id),)).fetchone()
    if r is None or (a.is_teacher and r["school_tutor_id"] != a.id) or a.is_student:
        raise NotFound("demande de convention inconnue")           # same answer whether it exists or not
    if decision == "refuser":
        if r["status"] not in ACTIVE:
            raise InvalidRequest(f"cette demande est déjà {r['status']}")
        if len(comment.strip()) < 5:
            raise InvalidRequest("un refus doit être motivé par un commentaire")
        new = "refusee"
    elif decision == "valider":
        if not a.is_teacher or r["status"] != "deposee":
            raise InvalidRequest("seul le tuteur à l'école valide une demande déposée")
        new = "validee_tuteur"
    else:
        if not a.is_staff or r["status"] != "validee_tuteur":
            raise InvalidRequest("la scolarité signe une demande déjà validée par le tuteur à l'école")
        new = "signee"
    student = person_label(ctx.conn, r["student_id"])
    summary = f"{decision.capitalize()} la convention de stage de {student} chez {r['company']} (nouvel état : {new})"
    if dry_run:
        return {"summary": summary}
    ctx.conn.execute("UPDATE internships SET status = ?, comment = ?, updated_at = ? WHERE id = ?",
                     (new, comment.strip(), fmt_dt(ctx.now()), r["id"]))
    text = {"refusee": f"Votre demande de convention chez {r['company']} est refusée : {comment.strip()}",
            "validee_tuteur": f"Votre tuteur à l'école a validé votre demande de convention chez {r['company']}. "
                              "Elle part à la scolarité pour signature.",
            "signee": f"Votre convention de stage chez {r['company']} est signée par l'école."}[new]
    notify(ctx, "Convention de stage", text, [], [r["student_id"]])
    ctx.conn.commit()
    return {"summary": summary, "status": new}
