"""Grade entry and publication by teachers, and registration to the make-up session.

Rules (data/docs/reglement-des-etudes.md, sections 3 and 5): grades are entered unpublished and
become visible to students when the teacher publishes them; a course is passed at 10/20; the make-up
session is open to students below 10 and to those whose absence at the exam was excused; the make-up
grade replaces the final exam grade, capped at 12/20.
"""

from __future__ import annotations

from ..clock import fmt_dt, parse_date
from . import Conflict, Context, InvalidRequest, NotFound, PermissionDenied, teacher_course_groups
from .messaging import notify

MAKEUP_CAP = 12.0
PASS_MARK = 10.0


def _my_groups(ctx: Context, course_id: str) -> set[str]:
    if ctx.conn.execute("SELECT 1 FROM courses WHERE id = ?", (course_id,)).fetchone() is None:
        raise NotFound(f"cours inconnu : {course_id}")
    if ctx.actor.is_staff:
        return {r[0] for r in ctx.conn.execute("SELECT group_id FROM course_sections WHERE course_id = ?", (course_id,))}
    groups = {g for c, g in teacher_course_groups(ctx.conn, ctx.actor.id) if c == course_id}
    if not groups:
        raise PermissionDenied(f"tu n'interviens pas dans le cours {course_id}")
    return groups


def enter_grades(ctx: Context, course_id: str, label: str, coefficient_percent: int, date: str, grades: list,
                 dry_run: bool = False) -> dict:
    """Enter one assessment for several students. The grades stay unpublished until publish_grades."""
    groups = _my_groups(ctx, course_id)
    label = label.strip()
    if not 2 <= len(label) <= 60 or not 1 <= coefficient_percent <= 100:
        raise InvalidRequest("un intitulé d'épreuve et un coefficient de 1 à 100 % sont attendus")
    parse_date(date)
    seen, clean = set(), []
    for g in grades:
        sid, score = str(g.get("student_id", "")), g.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 20:
            raise InvalidRequest(f"note invalide pour {sid} : un nombre de 0 à 20 est attendu")
        s = ctx.conn.execute("SELECT group_id FROM people WHERE id = ? AND role = 'student'", (sid,)).fetchone()
        if s is None or s["group_id"] not in groups:
            raise PermissionDenied(f"{sid} n'est pas dans un de tes groupes pour {course_id}")
        if sid in seen or ctx.conn.execute("SELECT 1 FROM grades WHERE student_id = ? AND course_id = ? AND label = ?",
                                           (sid, course_id, label)).fetchone():
            raise Conflict(f"{sid} a déjà une note « {label} » en {course_id}")
        seen.add(sid)
        clean.append((sid, round(float(score), 2)))
    if not clean:
        raise InvalidRequest("aucune note à saisir")
    summary = (f"Saisir {len(clean)} note(s) « {label} » (coefficient {coefficient_percent} %) en {course_id}, "
               "non publiées pour l'instant")
    if dry_run:
        return {"summary": summary}
    ctx.conn.executemany(
        "INSERT INTO grades(student_id, course_id, label, score, coefficient, date, published) VALUES (?,?,?,?,?,?,0)",
        [(sid, course_id, label, score, coefficient_percent / 100, date) for sid, score in clean])
    ctx.conn.commit()
    return {"summary": summary, "entered": len(clean), "published": False,
            "next": "publish_grades rend ces notes visibles aux étudiants"}


def publish_grades(ctx: Context, course_id: str, label: str, dry_run: bool = False) -> dict:
    groups = _my_groups(ctx, course_id)
    rows = ctx.conn.execute(
        "SELECT g.id, g.student_id FROM grades g JOIN people p ON p.id = g.student_id WHERE g.course_id = ? "
        "AND g.label = ? AND g.published = 0 AND p.group_id IN (%s)" % ",".join("?" * len(groups)),
        (course_id, label.strip(), *groups)).fetchall()
    if not rows:
        raise NotFound(f"aucune note non publiée « {label} » en {course_id} pour tes groupes")
    summary = f"Publier {len(rows)} note(s) « {label} » en {course_id} et prévenir les étudiants"
    if dry_run:
        return {"summary": summary}
    ctx.conn.executemany("UPDATE grades SET published = 1 WHERE id = ?", [(r["id"],) for r in rows])
    # The message says that a grade is out, never the grade itself: each student reads their own.
    notify(ctx, f"Note publiée en {course_id}", f"Votre note « {label} » en {course_id} est publiée. "
           "Vous pouvez la consulter auprès de l'assistant.", [], [r["student_id"] for r in rows])
    ctx.conn.commit()
    return {"summary": summary, "published": len(rows)}


def _average(ctx: Context, student_id: str, course_id: str) -> float | None:
    rows = ctx.conn.execute("SELECT score, coefficient FROM grades WHERE student_id = ? AND course_id = ? AND published = 1",
                            (student_id, course_id)).fetchall()
    weight = sum(r["coefficient"] for r in rows)
    return round(sum(r["score"] * r["coefficient"] for r in rows) / weight, 2) if weight else None


def register_makeup(ctx: Context, course_id: str, dry_run: bool = False) -> dict:
    a = ctx.actor
    if not a.is_student:
        raise PermissionDenied("seul un étudiant s'inscrit au rattrapage")
    if ctx.conn.execute("SELECT 1 FROM course_sections WHERE course_id = ? AND group_id = ?",
                        (course_id, a.group_id)).fetchone() is None:
        raise NotFound(f"tu ne suis pas le cours {course_id}")
    excused = ctx.conn.execute(
        "SELECT 1 FROM absences ab JOIN events e ON e.id = ab.event_id WHERE ab.student_id = ? AND e.course_id = ? "
        "AND e.kind = 'examen' AND ab.status = 'justifiee'", (a.id, course_id)).fetchone()
    average = _average(ctx, a.id, course_id)
    if excused:
        reason = "absence_justifiee"
    elif average is not None and average < PASS_MARK:
        reason = "moyenne_insuffisante"
    else:
        raise InvalidRequest("le rattrapage est ouvert aux étudiants dont la moyenne est inférieure à 10/20 ou dont "
                             f"l'absence à l'examen a été excusée ; ta moyenne en {course_id} est "
                             f"{'non disponible' if average is None else average}")
    if ctx.conn.execute("SELECT 1 FROM makeup_registrations WHERE student_id = ? AND course_id = ?",
                        (a.id, course_id)).fetchone():
        raise Conflict(f"tu es déjà inscrit au rattrapage de {course_id}")
    summary = f"T'inscrire au rattrapage de {course_id} (motif : {reason.replace('_', ' ')})"
    if dry_run:
        return {"summary": summary}
    ctx.conn.execute("INSERT INTO makeup_registrations(student_id, course_id, reason, created_at) VALUES (?,?,?,?)",
                     (a.id, course_id, reason, fmt_dt(ctx.now())))
    ctx.conn.commit()
    return {"summary": summary, "reason": reason,
            "rule": f"la note de rattrapage remplace celle de l'examen final, dans la limite de {MAKEUP_CAP:.0f}/20"}


def get_makeups(ctx: Context, course_id: str | None = None) -> dict:
    a = ctx.actor
    if a.is_student:
        rows = ctx.conn.execute("SELECT * FROM makeup_registrations WHERE student_id = ?", (a.id,)).fetchall()
    else:
        if not course_id:
            raise InvalidRequest("indique un cours (course_id)")
        groups = _my_groups(ctx, course_id)
        rows = ctx.conn.execute(
            "SELECT m.* FROM makeup_registrations m JOIN people p ON p.id = m.student_id WHERE m.course_id = ? "
            "AND p.group_id IN (%s)" % ",".join("?" * len(groups)), (course_id, *groups)).fetchall()
    return {"registrations": [{"student_id": r["student_id"], "course_id": r["course_id"], "reason": r["reason"],
                               "registered_at": r["created_at"]} for r in rows],
            "rule": f"note de rattrapage plafonnée à {MAKEUP_CAP:.0f}/20"}
