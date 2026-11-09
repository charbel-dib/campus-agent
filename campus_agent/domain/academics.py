"""Grades, absences and school certificates, each scoped to what the actor is entitled to see."""

from __future__ import annotations

import secrets
from pathlib import Path
from statistics import mean

from ..clock import fmt_dt, human_dt, parse_dt
from . import Context, InvalidRequest, NotFound, PermissionDenied, add_working_days, teacher_course_groups
from .messaging import notify

JUSTIFY_WORKING_DAYS = 5


def _student(ctx: Context, student_id: str):
    row = ctx.conn.execute("SELECT * FROM people WHERE id = ? AND role = 'student'", (student_id,)).fetchone()
    if row is None:
        raise NotFound(f"étudiant inconnu : {student_id}")
    return row


def _scope_student(ctx: Context, student_id: str | None) -> str | None:
    """Return the student whose data may be read, or raise. None means 'course-level view'."""
    a = ctx.actor
    if a.is_student:
        if student_id not in (None, a.id):
            raise PermissionDenied("un étudiant ne consulte que ses propres données")
        return a.id
    if student_id is None:
        return None
    s = _student(ctx, student_id)
    if a.is_teacher and not any(g == s["group_id"] for _c, g in teacher_course_groups(ctx.conn, a.id)):
        raise PermissionDenied("cet étudiant ne suit aucun de tes enseignements")
    return student_id


def _teacher_courses_for_group(ctx: Context, group_id: str) -> set[str]:
    return {c for c, g in teacher_course_groups(ctx.conn, ctx.actor.id) if g == group_id}


def get_grades(ctx: Context, student_id: str | None = None, course_id: str | None = None) -> dict:
    sid = _scope_student(ctx, student_id)
    a = ctx.actor
    if sid is not None:
        s = _student(ctx, sid)
        sql = ("SELECT g.*, c.title, c.ects FROM grades g JOIN courses c ON c.id = g.course_id "
               "WHERE g.student_id = ?")
        params: list = [sid]
        if a.is_student:
            sql += " AND g.published = 1"
        if course_id:
            sql += " AND g.course_id = ?"
            params.append(course_id)
        rows = ctx.conn.execute(sql + " ORDER BY g.course_id, g.date", params).fetchall()
        if a.is_teacher:
            allowed = _teacher_courses_for_group(ctx, s["group_id"])
            rows = [r for r in rows if r["course_id"] in allowed]
        courses: dict[str, dict] = {}
        for r in rows:
            c = courses.setdefault(r["course_id"], {"course_id": r["course_id"], "title": r["title"],
                                                    "ects": r["ects"], "grades": []})
            c["grades"].append({"label": r["label"], "score": r["score"], "coefficient": r["coefficient"],
                                "date": r["date"], **({} if r["published"] else {"published": False})})
        for c in courses.values():
            pub = [g for g in c["grades"] if g.get("published", True)]
            weight = sum(g["coefficient"] for g in pub)
            if weight:
                c["provisional_average"] = round(sum(g["score"] * g["coefficient"] for g in pub) / weight, 2)
        return {"student": f"{s['first_name']} {s['last_name']}", "group": s["group_id"],
                "courses": list(courses.values()),
                "note": "Moyennes provisoires : l'examen final n'a pas encore eu lieu."}

    # Course-level view (teachers for their courses, staff for any course)
    if not course_id:
        raise InvalidRequest("indique un cours (course_id) ou un étudiant (student_id)")
    groups = [r[0] for r in ctx.conn.execute("SELECT group_id FROM course_sections WHERE course_id = ?", (course_id,))]
    if not groups:
        raise NotFound(f"cours inconnu : {course_id}")
    if a.is_teacher:
        groups = [g for g in groups if course_id in _teacher_courses_for_group(ctx, g)]
        if not groups:
            raise PermissionDenied(f"tu n'interviens pas dans le cours {course_id}")
    rows = ctx.conn.execute(
        "SELECT g.*, p.first_name, p.last_name, p.group_id FROM grades g JOIN people p ON p.id = g.student_id "
        "WHERE g.course_id = ? AND p.group_id IN (%s)" % ",".join("?" * len(groups)), (course_id, *groups)).fetchall()
    by_label: dict[str, list[float]] = {}
    per_student: dict[str, dict] = {}
    for r in rows:
        by_label.setdefault(r["label"], []).append(r["score"])
        st = per_student.setdefault(r["student_id"], {"id": r["student_id"],
                                                      "name": f"{r['first_name']} {r['last_name']}",
                                                      "group": r["group_id"], "_w": 0.0, "_s": 0.0})
        st["_w"] += r["coefficient"]
        st["_s"] += r["score"] * r["coefficient"]
    for st in per_student.values():
        st["average"] = round(st.pop("_s") / st.pop("_w"), 2)
    struggling = sorted((s for s in per_student.values() if s["average"] < 10), key=lambda s: s["average"])
    return {"course_id": course_id, "groups": groups, "students": len(per_student),
            "assessments": [{"label": k, "count": len(v), "mean": round(mean(v), 2), "min": min(v), "max": max(v)}
                            for k, v in by_label.items()],
            "students_below_10": struggling[:25]}


def _absence_row(ctx: Context, absence_id: int):
    row = ctx.conn.execute("""SELECT a.*, e.title, e.start, e.end, e.course_id, p.group_id, p.first_name, p.last_name
                              FROM absences a JOIN events e ON e.id = a.event_id JOIN people p ON p.id = a.student_id
                              WHERE a.id = ?""", (int(absence_id),)).fetchone()
    if row is None:
        raise NotFound(f"absence inconnue : {absence_id}")
    return row


def _deadline(row) -> str:
    return add_working_days(parse_dt(row["end"]).date(), JUSTIFY_WORKING_DAYS).isoformat()


def _absence_view(row) -> dict:
    out = {"id": row["id"], "student": f"{row['first_name']} {row['last_name']}", "student_id": row["student_id"],
           "session": row["title"], "date": row["start"], "when": human_dt(parse_dt(row["start"])),
           "course_id": row["course_id"], "status": row["status"]}
    if row["reason"]:
        out["reason"] = row["reason"]
    if row["submitted_at"]:
        out["submitted_at"] = row["submitted_at"]
    if row["review_comment"]:
        out["review_comment"] = row["review_comment"]
    if row["status"] == "non_justifiee":
        out["justify_before"] = _deadline(row)
    return out


def get_absences(ctx: Context, student_id: str | None = None, status: str | None = None,
                 course_id: str | None = None, order: str = "recent", limit: int = 30) -> dict:
    sid = _scope_student(ctx, student_id)
    sql = """SELECT a.*, e.title, e.start, e.end, e.course_id, p.group_id, p.first_name, p.last_name
             FROM absences a JOIN events e ON e.id = a.event_id JOIN people p ON p.id = a.student_id WHERE 1=1"""
    params: list = []
    if sid:
        sql += " AND a.student_id = ?"
        params.append(sid)
    if status:
        if status not in ("non_justifiee", "en_attente", "justifiee", "refusee"):
            raise InvalidRequest("statut : non_justifiee, en_attente, justifiee ou refusee")
        sql += " AND a.status = ?"
        params.append(status)
    if course_id:
        sql += " AND e.course_id = ?"
        params.append(course_id)
    if order not in ("recent", "oldest"):
        raise InvalidRequest("ordre : recent ou oldest")
    rows = ctx.conn.execute(sql + (" ORDER BY e.start DESC" if order == "recent" else " ORDER BY e.start ASC"),
                            params).fetchall()
    if ctx.actor.is_teacher:
        pairs = teacher_course_groups(ctx.conn, ctx.actor.id)
        rows = [r for r in rows if (r["course_id"], r["group_id"]) in pairs]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    limit = max(1, min(int(limit), 50))
    return {"total": len(rows), "by_status": counts, "order": "plus récentes d'abord" if order == "recent"
            else "plus anciennes d'abord", "absences": [_absence_view(r) for r in rows[:limit]],
            **({"truncated": True} if len(rows) > limit else {})}


def justify_absence(ctx: Context, absence_id: int, reason: str, dry_run: bool = False) -> dict:
    row = _absence_row(ctx, absence_id)
    if not ctx.actor.is_student or row["student_id"] != ctx.actor.id:
        raise PermissionDenied("seul l'étudiant concerné justifie son absence")
    if row["status"] != "non_justifiee":
        raise InvalidRequest(f"cette absence est déjà au statut « {row['status']} »")
    deadline = _deadline(row)
    if ctx.now().date().isoformat() > deadline:
        raise InvalidRequest(f"délai dépassé : la justification devait être déposée avant le {deadline} "
                             "(cinq jours ouvrés, procédure de justification des absences)")
    reason = reason.strip()
    if len(reason) < 5:
        raise InvalidRequest("précise le motif et le justificatif (au moins quelques mots)")
    summary = f"Justifier l'absence à « {row['title']} » du {human_dt(parse_dt(row['start']))} — motif : {reason}"
    if dry_run:
        return {"summary": summary}
    ctx.conn.execute("UPDATE absences SET status='en_attente', reason=?, submitted_at=? WHERE id=?",
                     (reason[:500], fmt_dt(ctx.now()), row["id"]))
    ctx.conn.commit()
    return {"summary": summary, "status": "en_attente",
            "next": "La scolarité examine la demande ; le justificatif original peut être demandé."}


def review_absence(ctx: Context, absence_id: int, decision: str, comment: str = "", dry_run: bool = False) -> dict:
    if not ctx.actor.is_staff:
        raise PermissionDenied("seule la scolarité examine les justifications d'absence")
    row = _absence_row(ctx, absence_id)
    if row["status"] != "en_attente":
        raise InvalidRequest(f"cette absence n'est pas en attente (statut : {row['status']})")
    if decision not in ("justifiee", "refusee"):
        raise InvalidRequest("décision : justifiee ou refusee")
    if decision == "refusee" and len(comment.strip()) < 5:
        raise InvalidRequest("un refus est toujours motivé : ajoute un commentaire")
    who = f"{row['first_name']} {row['last_name']}"
    verdict = "Accepter" if decision == "justifiee" else "Refuser"
    summary = f"{verdict} la justification de {who} pour « {row['title']} » (motif : {row['reason'] or '—'})"
    if dry_run:
        return {"summary": summary}
    ctx.conn.execute("UPDATE absences SET status=?, reviewed_by=?, reviewed_at=?, review_comment=? WHERE id=?",
                     (decision, ctx.actor.id, fmt_dt(ctx.now()), comment.strip(), row["id"]))
    word = "acceptée" if decision == "justifiee" else "refusée"
    notify(ctx, f"Justification d'absence {word}",
           f"Ta justification pour « {row['title']} » du {human_dt(parse_dt(row['start']))} a été {word}."
           + (f" Commentaire : {comment.strip()}" if comment.strip() else ""), [], [row["student_id"]])
    ctx.conn.commit()
    return {"summary": summary, "status": decision}


def generate_certificate(ctx: Context, student_id: str | None = None) -> dict:
    a = ctx.actor
    if a.is_teacher:
        raise PermissionDenied("les attestations de scolarité sont délivrées à l'étudiant ou par la scolarité")
    sid = a.id if a.is_student else student_id
    if a.is_student and student_id not in (None, a.id):
        raise PermissionDenied("un étudiant ne peut obtenir que sa propre attestation")
    if not sid:
        raise InvalidRequest("indique l'étudiant (student_id)")
    s = _student(ctx, sid)
    group = ctx.conn.execute("SELECT * FROM groups WHERE id = ?", (s["group_id"],)).fetchone()
    ref = f"KA-{ctx.now():%Y%m%d}-{secrets.token_hex(3).upper()}"
    out_dir = Path(ctx.runtime_dir or ".") / "certificates"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ref}.pdf"
    _write_certificate_pdf(path, ref, f"{s['first_name']} {s['last_name']}", s["id"], group["name"],
                           ctx.conn.execute("SELECT value FROM meta WHERE key='semester_start'").fetchone()[0],
                           ctx.now())
    ctx.conn.execute("INSERT INTO certificates(id, student_id, requested_by, path, created_at) VALUES (?,?,?,?,?)",
                     (ref, sid, a.id, str(path), fmt_dt(ctx.now())))
    ctx.conn.commit()
    return {"reference": ref, "student": f"{s['first_name']} {s['last_name']}",
            "download_url": f"/api/certificates/{ref}", "format": "PDF"}


def _write_certificate_pdf(path: Path, ref: str, name: str, student_id: str, group_name: str,
                           semester_start: str, now) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    year = int(semester_start[:4])
    academic_year = f"{year}-{year + 1}" if int(semester_start[5:7]) >= 8 else f"{year - 1}-{year}"
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setTitle(f"Attestation de scolarité {ref}")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2.5 * cm, h - 3 * cm, "École d'ingénieurs Ker Avel")
    c.setFont("Helvetica", 9)
    c.drawString(2.5 * cm, h - 3.6 * cm, "Établissement fictif — document de démonstration sans valeur officielle")
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(w / 2, h - 7 * cm, "ATTESTATION DE SCOLARITÉ")
    c.setFont("Helvetica", 12)
    lines = [
        "La responsable de la scolarité de l'École d'ingénieurs Ker Avel atteste que",
        "",
        f"{name}  (n° étudiant {student_id})",
        "",
        f"est régulièrement inscrit(e) pour l'année universitaire {academic_year},",
        f"en {group_name.lower()}.",
    ]
    y = h - 9.5 * cm
    for line in lines:
        if line.startswith(name):
            c.setFont("Helvetica-Bold", 13)
            c.drawCentredString(w / 2, y, line)
            c.setFont("Helvetica", 12)
        else:
            c.drawString(2.5 * cm, y, line)
        y -= 0.8 * cm
    c.drawString(2.5 * cm, y - 1.2 * cm, f"Fait le {now:%d/%m/%Y}, pour servir et valoir ce que de droit.")
    c.setFont("Helvetica", 9)
    c.drawString(2.5 * cm, 3 * cm, f"Référence : {ref} — vérifiable auprès de scolarite@ker-avel.example")
    c.showPage()
    c.save()


def certificate_path(ctx: Context, ref: str) -> Path:
    row = ctx.conn.execute("SELECT * FROM certificates WHERE id = ?", (ref,)).fetchone()
    if row is None:
        raise NotFound("attestation inconnue")
    if not (ctx.actor.is_staff or row["student_id"] == ctx.actor.id):
        raise PermissionDenied("attestation d'un autre étudiant")
    return Path(row["path"])
