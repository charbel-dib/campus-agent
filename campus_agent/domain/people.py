"""Directory: people and courses. What each role may see follows the data-minimisation rule."""

from __future__ import annotations

from . import Context, InvalidRequest, fold, teacher_groups


def _person_view(ctx: Context, row) -> dict:
    """Students see other students' name and group only; staff and teachers see contact details."""
    out = {"id": row["id"], "name": f"{row['first_name']} {row['last_name']}", "role": row["role"]}
    if row["role"] == "student":
        out["group"] = row["group_id"]
        if not ctx.actor.is_student or row["id"] == ctx.actor.id:
            out["email"] = row["email"]
    else:
        out.update(department=row["department"], title=row["title"], email=row["email"])
    return out


def search_people(ctx: Context, query: str, role: str | None = None, limit: int = 8) -> dict:
    words = fold(query).split()
    if not words:
        raise InvalidRequest("la recherche est vide")
    if role and role not in ("student", "teacher", "staff"):
        raise InvalidRequest("rôle inconnu : student, teacher ou staff")
    rows = ctx.conn.execute("SELECT * FROM people" + (" WHERE role = ?" if role else ""),
                            (role,) if role else ()).fetchall()
    scored = []
    for row in rows:
        hay = fold(f"{row['first_name']} {row['last_name']} {row['email']} {row['id']} {row['department'] or ''}")
        tokens = hay.split()
        # every query word must prefix-match a word of the record ("le goff", "yann", "goff")
        if all(any(t.startswith(w) for t in tokens) for w in words):
            exact = sum(w in tokens for w in words)
            scored.append((-exact, row["last_name"], row))
    scored.sort(key=lambda x: (x[0], x[1]))
    results = [_person_view(ctx, r) for *_k, r in scored[:limit]]
    return {"count": len(scored), "results": results}


def search_courses(ctx: Context, query: str | None = None, group_id: str | None = None,
                   teacher_id: str | None = None) -> dict:
    """Course catalogue (public within the school). With no filter, a student gets their own courses."""
    if not query and not group_id and not teacher_id:
        if ctx.actor.is_student:
            group_id = ctx.actor.group_id
        elif ctx.actor.is_teacher:
            teacher_id = ctx.actor.id
        else:
            raise InvalidRequest("précise un nom de cours, un groupe ou un enseignant")
    sql = """SELECT cs.id AS section_id, c.id AS course_id, c.title, c.ects, c.year, c.track,
                    cs.group_id, p.id AS teacher_id, p.first_name, p.last_name
             FROM course_sections cs JOIN courses c ON c.id = cs.course_id
             JOIN people p ON p.id = cs.teacher_id WHERE 1=1"""
    params: list = []
    if group_id:
        sql += " AND cs.group_id = ?"
        params.append(group_id)
    if teacher_id:
        taught = teacher_groups(ctx.conn, teacher_id)
        sql += " AND (cs.teacher_id = ? OR c.id IN (SELECT course_id FROM events WHERE organizer_id = ? " \
               "AND kind = 'cours'))"
        params += [teacher_id, teacher_id]
        if not taught:
            return {"count": 0, "courses": []}
    rows = ctx.conn.execute(sql + " ORDER BY c.id, cs.group_id", params).fetchall()
    if query:
        q = fold(query)
        rows = [r for r in rows if q in fold(f"{r['course_id']} {r['title']}")]
    courses: dict[str, dict] = {}
    for r in rows:
        c = courses.setdefault(r["course_id"], {
            "course_id": r["course_id"], "title": r["title"], "ects": r["ects"], "year": r["year"],
            "track": r["track"], "lead_teacher": _lead(ctx, r["course_id"]), "sections": []})
        c["sections"].append({"group": r["group_id"], "teacher": f"{r['first_name']} {r['last_name']}",
                              "teacher_id": r["teacher_id"]})
    return {"count": len(courses), "courses": list(courses.values())}


def _lead(ctx: Context, course_id: str) -> dict | None:
    row = ctx.conn.execute("""SELECT p.id, p.first_name, p.last_name FROM events e JOIN people p ON p.id = e.organizer_id
                              WHERE e.course_id = ? AND e.kind = 'cours' LIMIT 1""", (course_id,)).fetchone()
    if row is None:
        row = ctx.conn.execute("""SELECT p.id, p.first_name, p.last_name FROM course_sections cs
                                  JOIN people p ON p.id = cs.teacher_id WHERE cs.course_id = ? LIMIT 1""",
                               (course_id,)).fetchone()
    return {"id": row["id"], "name": f"{row['first_name']} {row['last_name']}"} if row else None
