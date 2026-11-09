"""Messages (announcements and automatic notices) and support tickets.

Messages land in an internal inbox (table ``messages``): nothing leaves the school, which
suits a demo. In production the same function would hand the message to a mail or chat
connector after it is stored.
"""

from __future__ import annotations

import json

from ..clock import fmt_dt
from . import Context, InvalidRequest, NotFound, PermissionDenied, person_label, teacher_groups

TICKET_CATEGORIES = ("informatique", "salles", "scolarite", "assistant", "autre")


def _expand(ctx: Context, group_ids: list[str], person_ids: list[str]) -> list[str]:
    members: dict[str, None] = {}
    for g in group_ids:
        if ctx.conn.execute("SELECT 1 FROM groups WHERE id = ?", (g,)).fetchone() is None:
            raise NotFound(f"groupe inconnu : {g}")
        for r in ctx.conn.execute("SELECT id FROM people WHERE group_id = ? ORDER BY id", (g,)):
            members[r[0]] = None
    for p in person_ids:
        if ctx.conn.execute("SELECT 1 FROM people WHERE id = ?", (p,)).fetchone() is None:
            raise NotFound(f"personne inconnue : {p}")
        members[p] = None
    members.pop(ctx.actor.id, None)
    return list(members)


def notify(ctx: Context, subject: str, body: str, group_ids: list[str], person_ids: list[str],
           event_id: int | None = None) -> int:
    """Store a message for every member of the groups and every listed person. No commit here."""
    recipients = _expand(ctx, group_ids, person_ids)
    if not recipients:
        return 0
    cur = ctx.conn.execute(
        "INSERT INTO messages(sender_id, subject, body, recipients_json, recipient_count, event_id, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (ctx.actor.id, subject, body, json.dumps({"groups": group_ids, "people": person_ids}),
         len(recipients), event_id, fmt_dt(ctx.now())))
    ctx.conn.executemany("INSERT INTO message_recipients(message_id, person_id) VALUES (?,?)",
                         [(cur.lastrowid, r) for r in recipients])
    return len(recipients)


def send_announcement(ctx: Context, subject: str, body: str, group_ids: list[str] | None = None,
                      person_ids: list[str] | None = None, dry_run: bool = False) -> dict:
    group_ids, person_ids = list(group_ids or []), list(person_ids or [])
    subject, body = subject.strip(), body.strip()
    if ctx.actor.is_student:
        raise PermissionDenied("les annonces sont réservées aux enseignants et à la scolarité ; "
                               "un étudiant peut ouvrir un ticket ou écrire à un enseignant")
    if not 3 <= len(subject) <= 150 or not 10 <= len(body) <= 3000:
        raise InvalidRequest("objet de 3 à 150 caractères et message de 10 à 3000 caractères")
    if not group_ids and not person_ids:
        raise InvalidRequest("indique au moins un groupe ou une personne destinataire")
    if ctx.actor.is_teacher:
        taught = teacher_groups(ctx.conn, ctx.actor.id)
        foreign = [g for g in group_ids if g not in taught]
        if foreign:
            raise PermissionDenied(f"tu n'enseignes pas aux groupes : {', '.join(foreign)}")
        for p in person_ids:
            row = ctx.conn.execute("SELECT role, group_id FROM people WHERE id = ?", (p,)).fetchone()
            if row and row["role"] == "student" and row["group_id"] not in taught:
                raise PermissionDenied("tu ne peux écrire qu'aux étudiants de tes groupes")
    recipients = _expand(ctx, group_ids, person_ids)
    if not recipients:
        raise InvalidRequest("aucun destinataire")
    target = ", ".join(group_ids + [person_label(ctx.conn, p) for p in person_ids][:5])
    summary = f"Envoyer le message « {subject} » à {len(recipients)} personne(s) ({target})"
    if dry_run:
        return {"summary": summary, "recipient_count": len(recipients), "subject": subject, "body": body}
    count = notify(ctx, subject, body, group_ids, person_ids)
    ctx.conn.commit()
    return {"summary": summary, "recipient_count": count, "status": "envoyé"}


def get_messages(ctx: Context, limit: int = 10, unread_only: bool = False) -> dict:
    rows = ctx.conn.execute("""
        SELECT m.* FROM messages m JOIN message_recipients r ON r.message_id = m.id
        WHERE r.person_id = ? ORDER BY m.created_at DESC, m.id DESC LIMIT ?""",
                            (ctx.actor.id, max(1, min(int(limit), 30)))).fetchall()
    sent = ctx.conn.execute("SELECT COUNT(*) FROM messages WHERE sender_id = ?", (ctx.actor.id,)).fetchone()[0]
    return {"received": [{"id": r["id"], "from": person_label(ctx.conn, r["sender_id"]), "subject": r["subject"],
                          "body": r["body"], "date": r["created_at"]} for r in rows],
            "sent_count": sent}


def create_ticket(ctx: Context, category: str, subject: str, body: str, dry_run: bool = False) -> dict:
    if category not in TICKET_CATEGORIES:
        raise InvalidRequest(f"catégorie inconnue ; choisir parmi {', '.join(TICKET_CATEGORIES)}")
    subject, body = subject.strip(), body.strip()
    if not 3 <= len(subject) <= 150 or not 10 <= len(body) <= 3000:
        raise InvalidRequest("objet de 3 à 150 caractères et description de 10 à 3000 caractères")
    summary = f"Ouvrir un ticket {category} : « {subject} »"
    if dry_run:
        return {"summary": summary}
    cur = ctx.conn.execute("INSERT INTO tickets(person_id, category, subject, body, created_at) VALUES (?,?,?,?,?)",
                           (ctx.actor.id, category, subject, body, fmt_dt(ctx.now())))
    ctx.conn.commit()
    return {"summary": summary, "ticket_id": cur.lastrowid, "status": "ouvert",
            "delai": "prise en charge sous un jour ouvré"}


def list_tickets(ctx: Context, status: str | None = None, limit: int = 20) -> dict:
    sql, params = "SELECT * FROM tickets", []
    clauses = []
    if not ctx.actor.is_staff:
        clauses.append("person_id = ?")
        params.append(ctx.actor.id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    rows = ctx.conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, int(limit))).fetchall()
    # The description is written by users: it is labelled as such for the model, like event notes.
    return {"tickets": [{"id": r["id"], "by": person_label(ctx.conn, r["person_id"]), "category": r["category"],
                         "subject": r["subject"], "description_utilisateur": r["body"][:500], "status": r["status"],
                         "created_at": r["created_at"]} for r in rows]}
