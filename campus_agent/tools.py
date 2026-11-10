"""Tool registry: one definition per tool, used by the agent, the MCP server and the evaluation.

Each tool has a JSON schema (what the model sees), the roles allowed to use it, and whether it
writes. Writes never run straight from a model's request: ``execute`` validates them, stores a
*pending action* with a human-readable summary, and only ``confirm_pending`` (called by the
user's click, not by the model) carries them out, after checking everything again.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from .clock import fmt_dt, parse_dt
from .domain import Context, DomainError, InvalidRequest, NotFound, PermissionDenied
from .domain import academics, audit, calendar, grading, internships, messaging, people
from .domain.documents import DocumentIndex

log = logging.getLogger("campus_agent.tools")

ALL = ("student", "teacher", "staff")
DATE = {"type": "string", "description": "Date AAAA-MM-JJ"}
DATETIME = {"type": "string", "description": "Date et heure locales AAAA-MM-JJTHH:MM (Europe/Paris)"}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: tuple = ()
    handler: Callable[..., dict] | None = None
    write: bool = False
    roles: tuple = ALL
    flag: str | None = None        # offered only when this feature flag is on

    def input_schema(self) -> dict:
        return {"type": "object", "properties": self.properties, "required": list(self.required),
                "additionalProperties": False}

    def api_definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema()}


@dataclass
class ToolResult:
    content: dict
    is_error: bool = False
    pending_action: dict | None = None

    def as_text(self) -> str:
        return json.dumps(self.content, ensure_ascii=False)


# ---------------------------------------------------------------------------------------
# Handlers that need more than the context (the document index is shared, loaded once)
# ---------------------------------------------------------------------------------------

_DOC_INDEX: DocumentIndex | None = None


def set_document_index(index: DocumentIndex) -> None:
    global _DOC_INDEX
    _DOC_INDEX = index


# Feature flags, set once at start from the environment (runtime.boot): switch an optional tool on,
# or take any tool out of service without a deployment of new code (CAMPUS_DISABLED_TOOLS).
_FLAGS: dict[str, bool] = {}
_DISABLED: set[str] = set()


def configure(flags: dict[str, bool] | None = None, disabled: set[str] | None = None) -> None:
    _FLAGS.clear()
    _FLAGS.update(flags or {})
    _DISABLED.clear()
    _DISABLED.update(disabled or set())


def flag(name: str) -> bool:
    return bool(_FLAGS.get(name))


def _ask_user(ctx: Context, question: str, options: list) -> dict:
    options = [str(o).strip()[:80] for o in options if str(o).strip()]
    if not 2 <= len(options) <= 5 or len(question.strip()) < 5:
        raise InvalidRequest("une question et de deux à cinq choix courts sont attendus")
    return {"question": question.strip(), "options": options,
            "instruction": "La question est affichée avec ses choix. Attends la réponse de l'utilisateur."}


_PROVIDERS = None


def set_providers(providers) -> None:
    global _PROVIDERS
    _PROVIDERS = providers


def _get_assignments(ctx: Context) -> dict:
    if _PROVIDERS is None or _PROVIDERS.assignments is None:
        raise NotFound("la plateforme de cours en ligne n'est pas reliée")
    from datetime import datetime

    items = [{"course": a["course"], "name": a["name"],
              "due": datetime.fromtimestamp(a["due"]).strftime("%Y-%m-%dT%H:%M") if a["due"] else None}
             for a in _PROVIDERS.assignments.upcoming()]
    return {"assignments": items[:30], "source": "Moodle"}


def _load_skill(ctx: Context, name: str) -> dict:
    from .agent import SKILLS_DIR

    path = SKILLS_DIR / f"{name}.md"
    if not name.replace("-", "").isalnum() or not path.is_file():
        raise NotFound(f"procédure inconnue : {name}")
    return {"name": name, "procedure": path.read_text(encoding="utf-8")}


def _search_documents(ctx: Context, query: str, k: int = 4) -> dict:
    if _DOC_INDEX is None:
        raise RuntimeError("document index not loaded")
    return _DOC_INDEX.search(query, k)


# ---------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool("search_people",
         "Cherche une personne de l'école (étudiant, enseignant, scolarité) par nom, prénom ou e-mail et "
         "renvoie son identifiant (stu-…, tch-…, stf-…). À utiliser avant tout outil qui demande un identifiant : "
         "ne jamais deviner un identifiant.",
         {"query": {"type": "string", "description": "Nom, prénom ou e-mail, même partiel"},
          "role": {"type": "string", "enum": ["student", "teacher", "staff"]}},
         ("query",), people.search_people),
    Tool("search_courses",
         "Catalogue des enseignements : code (ex. IA301), titre, crédits, groupes et enseignant de chaque groupe. "
         "Sans argument : les cours de l'utilisateur (son groupe s'il est étudiant, ses cours s'il enseigne).",
         {"query": {"type": "string", "description": "Code ou mot du titre, ex. « signal »"},
          "group_id": {"type": "string", "description": "Groupe, ex. 3A-IA, 1A-TD2"},
          "teacher_id": {"type": "string"}},
         (), people.search_courses),
    Tool("get_schedule",
         "Agenda sur une période (31 jours au plus) : celui de l'utilisateur par défaut, ou d'une personne, "
         "d'un groupe ou d'une salle. Selon les droits, renvoie le détail des événements ou seulement les plages "
         "occupées (RGPD). Pour un enseignant vu par un étudiant, renvoie aussi ses permanences.",
         {"start_date": DATE, "end_date": DATE, "person_id": {"type": "string"}, "group_id": {"type": "string"},
          "room_id": {"type": "string"}},
         ("start_date",), calendar.get_schedule),
    Tool("find_free_slots",
         "Trouve des créneaux libres communs à plusieurs personnes et/ou groupes, avec une salle libre si besoin. "
         "L'utilisateur est inclus d'office. Avec des groupes, ne propose que les créneaux de cours standard hors "
         "mercredi après-midi. Pour un étudiant qui cherche un rendez-vous avec un enseignant, ne propose que des "
         "créneaux pendant la permanence de l'enseignant (15 à 30 minutes).",
         {"duration_minutes": {"type": "integer", "minimum": 15, "maximum": 240},
          "date_from": DATE, "date_to": DATE,
          "person_ids": {"type": "array", "items": {"type": "string"}},
          "group_ids": {"type": "array", "items": {"type": "string"}},
          "room_kind": {"type": "string", "enum": ["amphi", "td", "tp_info", "labo", "meeting", "study"]},
          "min_capacity": {"type": "integer", "minimum": 1},
          "earliest": {"type": "string", "description": "Heure HH:MM, défaut 08:00"},
          "latest": {"type": "string", "description": "Heure HH:MM, défaut 19:00"}},
         ("duration_minutes", "date_from", "date_to"), calendar.find_free_slots),
    Tool("find_available_rooms",
         "Salles libres sur un créneau précis, filtrables par type et capacité. Types : amphi, td, tp_info (salle "
         "informatique), labo, meeting (réunion), study (salle de travail de la bibliothèque, seule réservable par "
         "les étudiants).",
         {"start": DATETIME, "end": DATETIME, "min_capacity": {"type": "integer", "minimum": 1},
          "kind": {"type": "string", "enum": ["amphi", "td", "tp_info", "labo", "meeting", "study"]}},
         ("start", "end"), calendar.find_available_rooms),
    Tool("get_grades",
         "Notes. Étudiant : ses notes publiées et ses moyennes provisoires. Enseignant : les notes de ses cours "
         "(par étudiant, ou statistiques d'un cours avec la liste des étudiants sous 10). Scolarité : tout.",
         {"student_id": {"type": "string"}, "course_id": {"type": "string"}},
         (), academics.get_grades),
    Tool("get_absences",
         "Absences avec leur statut (non_justifiee, en_attente, justifiee, refusee) et, pour une absence non "
         "justifiée, la date limite pour la justifier. Étudiant : les siennes. Enseignant : celles de ses cours. "
         "Scolarité : toutes, par exemple les demandes en_attente à examiner.",
         {"student_id": {"type": "string"},
          "status": {"type": "string", "enum": ["non_justifiee", "en_attente", "justifiee", "refusee"]},
          "course_id": {"type": "string"},
          "order": {"type": "string", "enum": ["recent", "oldest"],
                    "description": "recent (défaut) : plus récentes d'abord ; oldest : plus anciennes d'abord"},
          "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
         (), academics.get_absences),
    Tool("search_documents",
         "Recherche dans le règlement et les procédures officielles de l'école (absences, examens, rattrapages, "
         "salles, rendez-vous, stages, attestations, usage de l'IA, vie pratique). Chaque passage a une référence "
         "« ref » à citer entre crochets dans la réponse. Si rien ne correspond, dire que l'information n'est pas "
         "dans les documents.",
         {"query": {"type": "string"}, "k": {"type": "integer", "minimum": 1, "maximum": 8}},
         ("query",), _search_documents),
    Tool("get_messages",
         "Messages reçus par l'utilisateur : annonces, changements d'emploi du temps, réponses de la scolarité.",
         {"limit": {"type": "integer", "minimum": 1, "maximum": 30}},
         (), messaging.get_messages),
    Tool("list_tickets",
         "Tickets de support : les siens, ou tous pour la scolarité.",
         {"status": {"type": "string", "enum": list(messaging.TICKET_STATUSES)}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
         (), messaging.list_tickets),
    Tool("generate_certificate",
         "Génère une attestation de scolarité PDF et renvoie son lien de téléchargement. Étudiant : la sienne. "
         "Scolarité : celle d'un étudiant (student_id).",
         {"student_id": {"type": "string"}},
         (), academics.generate_certificate, roles=("student", "staff")),
    # ---- Writes: each call is held for the user's confirmation
    Tool("create_event",
         "Crée un événement. Étudiant : « reservation » d'une salle de travail (study, 2 h max, une par jour), "
         "« rendez_vous » avec un enseignant pendant sa permanence (15 à 30 min, person_ids = [enseignant], "
         "description = objet), ou « autre » (rappel personnel). Enseignant : séance (cours, td, tp, examen) pour "
         "ses groupes avec une salle, réunion, rendez-vous. Vérifie les conflits. L'action est soumise à la "
         "confirmation de l'utilisateur.",
         {"title": {"type": "string"},
          "kind": {"type": "string", "enum": list(calendar.KINDS)},
          "start": DATETIME, "end": DATETIME,
          "room_id": {"type": "string"},
          "person_ids": {"type": "array", "items": {"type": "string"}, "description": "Autres participants"},
          "group_ids": {"type": "array", "items": {"type": "string"}},
          "course_id": {"type": "string"},
          "description": {"type": "string"},
          "notify_participants": {"type": "boolean", "description": "Prévenir les participants (défaut : oui)"}},
         ("title", "kind", "start", "end"), calendar.create_event, write=True),
    Tool("update_event",
         "Déplace ou modifie un événement existant (identifiant numérique « id » vu dans get_schedule) : nouvel "
         "horaire, salle, titre ou description. Sans « end », la durée est conservée. Vérifie droits et conflits, "
         "prévient les participants. Soumis à confirmation.",
         {"event_id": {"type": "integer"}, "start": DATETIME, "end": DATETIME, "room_id": {"type": "string"},
          "title": {"type": "string"}, "description": {"type": "string"},
          "notify_participants": {"type": "boolean"}},
         ("event_id",), calendar.update_event, write=True),
    Tool("cancel_event",
         "Annule un événement (identifiant numérique) et prévient les participants. Soumis à confirmation.",
         {"event_id": {"type": "integer"}, "reason": {"type": "string"},
          "notify_participants": {"type": "boolean"}},
         ("event_id",), calendar.cancel_event, write=True),
    Tool("justify_absence",
         "L'étudiant justifie une de ses absences non justifiées (identifiant vu dans get_absences), dans les "
         "cinq jours ouvrés. Le motif précise le justificatif. Soumis à confirmation.",
         {"absence_id": {"type": "integer"}, "reason": {"type": "string"}},
         ("absence_id", "reason"), academics.justify_absence, write=True, roles=("student",)),
    Tool("review_absence",
         "La scolarité accepte (justifiee) ou refuse (refusee, avec un commentaire obligatoire) une justification "
         "en attente. L'étudiant est prévenu. Soumis à confirmation.",
         {"absence_id": {"type": "integer"}, "decision": {"type": "string", "enum": ["justifiee", "refusee"]},
          "comment": {"type": "string"}},
         ("absence_id", "decision"), academics.review_absence, write=True, roles=("staff",)),
    Tool("send_announcement",
         "Envoie un message aux étudiants de groupes et/ou à des personnes (enseignant : ses groupes uniquement). "
         "Rédiger un objet clair et un message complet et poli. Soumis à confirmation.",
         {"subject": {"type": "string"}, "body": {"type": "string"},
          "group_ids": {"type": "array", "items": {"type": "string"}},
          "person_ids": {"type": "array", "items": {"type": "string"}}},
         ("subject", "body"), messaging.send_announcement, write=True, roles=("teacher", "staff")),
    Tool("create_ticket",
         "Ouvre un ticket de support (informatique, salles, scolarite, assistant, autre), par exemple pour un "
         "équipement en panne ou une question que l'assistant ne peut pas traiter. Soumis à confirmation.",
         {"category": {"type": "string", "enum": list(messaging.TICKET_CATEGORIES)},
          "subject": {"type": "string"}, "body": {"type": "string"}},
         ("category", "subject", "body"), messaging.create_ticket, write=True),
]

TOOLS += [
    Tool("create_internship_request",
         "L'étudiant dépose sa demande de convention de stage (au moins trois semaines avant le début ; durée selon "
         "l'année). Le tuteur à l'école est prévenu. Soumis à confirmation.",
         {"company": {"type": "string"}, "subject": {"type": "string"}, "start_date": DATE, "end_date": DATE,
          "company_tutor": {"type": "string", "description": "Nom du tuteur en entreprise"},
          "school_tutor_id": {"type": "string", "description": "Identifiant de l'enseignant tuteur (search_people)"}},
         ("company", "subject", "start_date", "end_date", "company_tutor", "school_tutor_id"),
         internships.create_internship_request, write=True, roles=("student",)),
    Tool("get_internship_status",
         "Demandes de convention de stage : la sienne (étudiant), celles dont on est tuteur (enseignant), "
         "toutes (scolarité).",
         {"status": {"type": "string", "enum": ["deposee", "validee_tuteur", "signee", "refusee"]}},
         (), internships.get_internship_status),
    Tool("review_internship",
         "Le tuteur à l'école valide une demande déposée ; la scolarité signe une demande validée ; l'un ou l'autre "
         "peut refuser avec un commentaire. L'étudiant est prévenu. Soumis à confirmation.",
         {"internship_id": {"type": "integer"}, "decision": {"type": "string", "enum": ["valider", "signer", "refuser"]},
          "comment": {"type": "string"}},
         ("internship_id", "decision"), internships.review_internship, write=True, roles=("teacher", "staff")),
    Tool("enter_grades",
         "L'enseignant saisit les notes d'une épreuve pour des étudiants de ses groupes. Les notes restent non "
         "publiées (invisibles aux étudiants) jusqu'à publish_grades. Soumis à confirmation.",
         {"course_id": {"type": "string"}, "label": {"type": "string", "description": "Intitulé, ex. « TP noté 2 »"},
          "coefficient_percent": {"type": "integer", "minimum": 1, "maximum": 100}, "date": DATE,
          "grades": {"type": "array", "items": {"type": "object"},
                     "description": "Liste de {student_id, score} ; score de 0 à 20"}},
         ("course_id", "label", "coefficient_percent", "date", "grades"),
         grading.enter_grades, write=True, roles=("teacher", "staff")),
    Tool("publish_grades",
         "Publie les notes saisies d'une épreuve : elles deviennent visibles et les étudiants sont prévenus "
         "(sans la note dans le message). Soumis à confirmation.",
         {"course_id": {"type": "string"}, "label": {"type": "string"}},
         ("course_id", "label"), grading.publish_grades, write=True, roles=("teacher", "staff")),
    Tool("register_makeup",
         "L'étudiant s'inscrit au rattrapage d'un cours : ouvert si sa moyenne est inférieure à 10/20 ou si son "
         "absence à l'examen a été excusée. Soumis à confirmation.",
         {"course_id": {"type": "string"}}, ("course_id",), grading.register_makeup, write=True, roles=("student",)),
    Tool("get_makeups",
         "Inscriptions au rattrapage : les siennes (étudiant) ou celles d'un cours (enseignant, scolarité).",
         {"course_id": {"type": "string"}}, (), grading.get_makeups),
    Tool("get_assignments",
         "Devoirs à rendre et leurs dates limites, lus sur la plateforme de cours en ligne (Moodle).",
         {}, (), _get_assignments, flag="moodle"),
    Tool("ask_user",
         "Pose une question à choix quand une information manque et que les réponses possibles sont peu nombreuses "
         "(quel cours, quel créneau, quelle personne). L'utilisateur répond d'un clic.",
         {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}},
         ("question", "options"), _ask_user, flag="clarify"),
    Tool("load_skill",
         "Charge la procédure détaillée d'une démarche listée dans « Procédures ».",
         {"name": {"type": "string"}}, ("name",), _load_skill, flag="skills"),
]

BY_NAME = {t.name: t for t in TOOLS}


def tools_for(role: str) -> list[Tool]:
    return [t for t in TOOLS if role in t.roles and t.name not in _DISABLED and (t.flag is None or flag(t.flag))]


# ---------------------------------------------------------------------------------------
# Argument validation (a small JSON-schema subset: enough for these schemas, no dependency)
# ---------------------------------------------------------------------------------------

_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def validate_args(tool: Tool, args: Any) -> dict:
    if not isinstance(args, dict):
        raise InvalidRequest("les arguments doivent être un objet JSON")
    unknown = set(args) - set(tool.properties)
    if unknown:
        raise InvalidRequest(f"arguments inconnus pour {tool.name} : {', '.join(sorted(unknown))}")
    missing = [k for k in tool.required if args.get(k) in (None, "")]
    if missing:
        raise InvalidRequest(f"arguments manquants pour {tool.name} : {', '.join(missing)}")
    clean = {}
    for key, value in args.items():
        if value is None:
            continue
        spec = tool.properties[key]
        expected = _TYPES[spec["type"]]
        if spec["type"] == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)          # models sometimes quote numbers
        if spec["type"] == "integer" and isinstance(value, bool) or not isinstance(value, expected):
            raise InvalidRequest(f"« {key} » doit être de type {spec['type']}")
        if "enum" in spec and value not in spec["enum"]:
            raise InvalidRequest(f"« {key} » doit valoir : {', '.join(spec['enum'])}")
        if spec["type"] == "integer":
            if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
                raise InvalidRequest(f"« {key} » hors limites")
        if spec["type"] == "array":
            item_type = _TYPES[spec["items"]["type"]]
            if not all(isinstance(v, item_type) for v in value) or len(value) > 50:
                raise InvalidRequest(f"« {key} » doit être une liste de {spec['items']['type']} (50 au plus)")
        if spec["type"] == "string" and len(value) > 4000:
            raise InvalidRequest(f"« {key} » est trop long")
        clean[key] = value
    return clean


# ---------------------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------------------

def _error(ctx: Context, name: str, args: dict, exc: Exception) -> ToolResult:
    if isinstance(exc, DomainError):
        audit.log(ctx, name, args, exc.code, exc.message)
        body = {"error": exc.message, "code": exc.code}
        if exc.details:
            body["details"] = exc.details
        return ToolResult(body, is_error=True)
    if isinstance(exc, ValueError):          # bad dates and similar user-input errors
        audit.log(ctx, name, args, "invalid", str(exc))
        return ToolResult({"error": str(exc), "code": "invalid"}, is_error=True)
    log.exception("tool %s failed", name)
    audit.log(ctx, name, args, "error", type(exc).__name__)
    return ToolResult({"error": "erreur interne de l'outil ; réessayer plus tard ou ouvrir un ticket",
                       "code": "error"}, is_error=True)


def execute(ctx: Context, name: str, args: Any, *, confirm_writes: bool = True) -> ToolResult:
    """Run a tool for ``ctx.actor``.

    With ``confirm_writes`` (the agent's mode), a write is validated in dry-run and stored as a
    pending action; nothing changes until the user confirms. Without it (MCP clients, which ask
    their user to approve each call themselves), the write runs immediately.
    """
    tool = BY_NAME.get(name)
    raw_args = args if isinstance(args, dict) else {}
    try:
        if tool is None:
            raise NotFound(f"outil inconnu : {name}")
        if ctx.actor.role not in tool.roles:
            raise PermissionDenied(f"l'outil {name} n'est pas disponible pour le profil {ctx.actor.role}")
        if name in _DISABLED or (tool.flag and not flag(tool.flag)):
            raise PermissionDenied(f"l'outil {name} est désactivé")
        clean = validate_args(tool, args)
        if not tool.write:
            result = tool.handler(ctx, **clean)
            audit.log(ctx, name, clean, "ok")
            return ToolResult(result)
        if not confirm_writes:
            result = tool.handler(ctx, **clean, dry_run=False)
            audit.log(ctx, name, clean, "executed", result.get("summary", ""))
            return ToolResult(result)
        preview = tool.handler(ctx, **clean, dry_run=True)
        action = _store_pending(ctx, name, clean, preview["summary"])
        audit.log(ctx, name, clean, "pending", f"action {action['id']}: {preview['summary']}")
        return ToolResult({"status": "en_attente_de_confirmation", "action_id": action["id"],
                           "summary": preview["summary"],
                           "instruction": "Rien n'est encore fait. L'utilisateur voit une demande de confirmation "
                                          "avec ce résumé. Présente-lui l'action en une phrase et attends sa décision ; "
                                          "ne prétends pas qu'elle est réalisée."},
                          pending_action=action)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a tool error the model can read
        ctx.conn.rollback()
        return _error(ctx, name, raw_args, exc)


def _store_pending(ctx: Context, name: str, args: dict, summary: str) -> dict:
    now = fmt_dt(ctx.now())
    cur = ctx.conn.execute(
        "INSERT INTO pending_actions(actor_id, session_id, tool, args_json, summary, created_at) VALUES (?,?,?,?,?,?)",
        (ctx.actor.id, ctx.session_id, name, json.dumps(args, ensure_ascii=False), summary, now))
    ctx.conn.commit()
    return {"id": cur.lastrowid, "tool": name, "summary": summary, "created_at": now}


def list_pending(ctx: Context, session_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM pending_actions WHERE actor_id = ? AND status = 'pending'"
    params: list = [ctx.actor.id]
    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)
    return [{"id": r["id"], "tool": r["tool"], "summary": r["summary"], "created_at": r["created_at"]}
            for r in ctx.conn.execute(sql + " ORDER BY id", params)]


def _pending(ctx: Context, action_id: int):
    row = ctx.conn.execute("SELECT * FROM pending_actions WHERE id = ?", (int(action_id),)).fetchone()
    if row is None or row["actor_id"] != ctx.actor.id:
        raise NotFound("action inconnue")
    if row["status"] != "pending":
        raise InvalidRequest(f"cette action est déjà traitée (statut : {row['status']})")
    return row


def confirm_pending(ctx: Context, action_id: int, ttl_minutes: int = 15) -> ToolResult:
    """Carry out a pending action after the user's explicit confirmation. Everything is re-checked."""
    try:
        row = _pending(ctx, action_id)
        if parse_dt(row["created_at"]) + timedelta(minutes=ttl_minutes) < ctx.now():
            ctx.conn.execute("UPDATE pending_actions SET status='expired', decided_at=? WHERE id=?",
                             (fmt_dt(ctx.now()), row["id"]))
            ctx.conn.commit()
            raise InvalidRequest("demande expirée : relance la demande pour vérifier à nouveau les disponibilités")
    except Exception as exc:  # noqa: BLE001
        return _error(ctx, "confirm_action", {"action_id": action_id}, exc)
    tool = BY_NAME[row["tool"]]
    args = json.loads(row["args_json"])
    try:
        if ctx.actor.role not in tool.roles:
            raise PermissionDenied("action non autorisée pour ce profil")
        result = tool.handler(ctx, **args, dry_run=False)
    except Exception as exc:  # noqa: BLE001 - e.g. the slot was taken between proposal and confirmation
        ctx.conn.rollback()
        ctx.conn.execute("UPDATE pending_actions SET status='failed', decided_at=?, result_json=? WHERE id=?",
                         (fmt_dt(ctx.now()), json.dumps({"error": str(exc)}, ensure_ascii=False), row["id"]))
        ctx.conn.commit()
        return _error(ctx, row["tool"], args, exc)
    ctx.conn.execute("UPDATE pending_actions SET status='executed', decided_at=?, result_json=? WHERE id=?",
                     (fmt_dt(ctx.now()), json.dumps(result, ensure_ascii=False, default=str), row["id"]))
    ctx.conn.commit()
    audit.log(ctx, row["tool"], args, "executed", f"action {row['id']} confirmée")
    return ToolResult({"action": "exécutée", **result})


def cancel_pending(ctx: Context, action_id: int) -> ToolResult:
    try:
        row = _pending(ctx, action_id)
    except Exception as exc:  # noqa: BLE001
        return _error(ctx, "cancel_action", {"action_id": action_id}, exc)
    ctx.conn.execute("UPDATE pending_actions SET status='cancelled', decided_at=? WHERE id=?",
                     (fmt_dt(ctx.now()), row["id"]))
    ctx.conn.commit()
    audit.log(ctx, row["tool"], json.loads(row["args_json"]), "cancelled", f"action {row['id']} refusée")
    return ToolResult({"status": "annulé", "summary": row["summary"]})
