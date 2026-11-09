"""Deterministic generator of a fictional engineering school, "École d'ingénieurs Ker Avel".

Everything here is invented: people, rooms, courses, timetable, grades and absences. The same
seed and the same "today" always produce the same school, which keeps the evaluation
reproducible. The semester is placed around "today" (today falls in week 10 of 17), so a demo
reset on any date has a past (grades, absences) and a future (classes to move, rooms to book).

    python -m campus_agent.seed                      # uses CAMPUS_NOW or the real date
    python -m campus_agent.seed --now 2026-11-16T09:00 --db campus_data/campus.db
"""

from __future__ import annotations

import argparse
import random
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db as dbm
from .clock import SLOTS, Clock, fmt_dt, monday_of, parse_dt
from .config import load_settings

SCHOOL_NAME = "École d'ingénieurs Ker Avel"
SCHOOL_SHORT = "Ker Avel"
EMAIL_DOMAIN = "ker-avel.example"   # .example is reserved: no real mailbox can be reached

TEACHING_WEEKS = 16          # weeks 0..15, week VACATION_WEEK has no classes
VACATION_WEEK = 7
MIDTERM_WEEK = 6
EXAM_WEEK = 16
TODAY_WEEK = 9               # the week of "today" inside the semester

# ---------------------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------------------

GROUPS = (
    [(f"1A-TD{i}", f"1re année, groupe TD{i}", "1A", None) for i in range(1, 5)]
    + [(f"2A-TD{i}", f"2e année, groupe TD{i}", "2A", None) for i in range(1, 5)]
    + [("3A-IA", "3e année, parcours Intelligence artificielle", "3A", "IA"),
       ("3A-ROB", "3e année, parcours Robotique", "3A", "ROB"),
       ("3A-SE", "3e année, parcours Systèmes embarqués", "3A", "SE")]
)
GROUP_SIZE = {"1A": 25, "2A": 25, "3A": 30}

TEACHERS = [
    ("tch-001", "Yann", "Le Goff", "Informatique et IA", "Maître de conférences"),
    ("tch-002", "Sophie", "Durand", "Informatique et IA", "Professeure"),
    ("tch-003", "Karim", "Benali", "Informatique et IA", "Maître de conférences"),
    ("tch-004", "Nathalie", "Rousseau", "Informatique et IA", "Professeure"),
    ("tch-005", "Thomas", "Kerjean", "Informatique et IA", "Ingénieur d'études"),
    ("tch-006", "Julien", "Moreau", "Informatique et IA", "Maître de conférences"),
    ("tch-007", "Amélie", "Tanguy", "Informatique et IA", "Maîtresse de conférences"),
    ("tch-008", "Hugo", "Lefèvre", "Mathématiques", "Professeur"),
    ("tch-009", "Camille", "Petit", "Mathématiques", "Professeure agrégée"),
    ("tch-010", "Marc", "Guillou", "Mathématiques", "Maître de conférences"),
    ("tch-011", "Élise", "Fontaine", "Mathématiques", "Maîtresse de conférences"),
    ("tch-012", "Rachid", "Amrani", "Mathématiques", "Professeur agrégé"),
    ("tch-013", "Pierre", "Le Bihan", "Électronique et signal", "Professeur"),
    ("tch-014", "Laura", "Gicquel", "Électronique et signal", "Ingénieure de recherche"),
    ("tch-015", "Antoine", "Riou", "Électronique et signal", "Maître de conférences"),
    ("tch-016", "Mélanie", "Cozic", "Systèmes embarqués", "Maîtresse de conférences"),
    ("tch-017", "Fabien", "Lagadec", "Systèmes embarqués", "Professeur"),
    ("tch-018", "Nadia", "Cherif", "Systèmes embarqués", "Maîtresse de conférences"),
    ("tch-019", "Olivier", "Pennec", "Systèmes embarqués", "Ingénieur de recherche"),
    ("tch-020", "Isabelle", "Roux", "Physique", "Professeure"),
    ("tch-021", "Mathieu", "Caradec", "Physique", "Professeur agrégé"),
    ("tch-022", "Gaëlle", "Le Meur", "Robotique", "Maîtresse de conférences"),
    ("tch-023", "Samir", "Haddou", "Robotique", "Maître de conférences"),
    ("tch-024", "Bruno", "Quéré", "Robotique", "Professeur"),
    ("tch-025", "Emma", "Wilson", "Langues et sciences humaines", "Professeure certifiée"),
    ("tch-026", "David", "O'Connor", "Langues et sciences humaines", "Professeur certifié"),
    ("tch-027", "Hélène", "Prigent", "Langues et sciences humaines", "Professeure agrégée"),
    ("tch-028", "François", "Le Gall", "Langues et sciences humaines", "Professeur associé"),
    ("tch-029", "Céline", "Morin", "Informatique et IA", "Ingénieure d'études"),
    ("tch-030", "Vincent", "Le Floch", "Systèmes embarqués", "Ingénieur d'études"),
]

STAFF = [
    ("stf-01", "Claire", "Morvan", "Scolarité", "Responsable de la scolarité"),
    ("stf-02", "Romain", "Jaouen", "Scolarité", "Gestionnaire de scolarité 1A et 2A"),
    ("stf-03", "Aïcha", "Diallo", "Scolarité", "Gestionnaire de scolarité 3A et stages"),
]

# The first students are fixed so the demo personas keep the same names.
FIXED_STUDENTS = [
    ("Inès", "Haddad", "3A-IA"),
    ("Lucas", "Le Corre", "3A-IA"),
    ("Maël", "Guivarc'h", "1A-TD2"),
    ("Sarah", "Nguyen", "2A-TD3"),
    ("Tom", "Bernard", "3A-ROB"),
]

FIRST_NAMES = [
    "Adam", "Alice", "Amine", "Anaïs", "Antoine", "Arthur", "Axel", "Baptiste", "Bastien", "Benjamin",
    "Camille", "Charlotte", "Chloé", "Clara", "Clément", "Damien", "Dylan", "Elias", "Elsa", "Emma",
    "Enzo", "Erwan", "Eva", "Fatima", "Florian", "Gabriel", "Gwenaëlle", "Hamza", "Hugo", "Inès",
    "Jade", "Jules", "Julie", "Kenza", "Killian", "Léa", "Léna", "Léo", "Lina", "Loïc",
    "Louis", "Louise", "Lucie", "Malo", "Manon", "Margaux", "Marie", "Mathis", "Maxime", "Mehdi",
    "Morgane", "Nathan", "Noah", "Nolwenn", "Océane", "Paul", "Pierre", "Quentin", "Rayan", "Romane",
    "Rose", "Ruben", "Salomé", "Samuel", "Sofia", "Théo", "Thibault", "Valentin", "Victor", "Yasmine",
    "Youssef", "Zoé", "Ewen", "Rania", "Karim", "Nour", "Tristan", "Solène", "Ilyes", "Maëlys",
]
LAST_NAMES = [
    "Abgrall", "Alaoui", "André", "Bellec", "Benoît", "Bertrand", "Blanc", "Boucher", "Bourhis", "Brunet",
    "Cadiou", "Carré", "Chevalier", "Colin", "Coquet", "Daniel", "David", "Denis", "Dubois", "Dumont",
    "Ferré", "Fournier", "Gall", "Garnier", "Gautier", "Girard", "Gourmelon", "Guerin", "Hamon", "Henry",
    "Hervé", "Jacob", "Jégou", "Kerbrat", "Kervella", "Labbé", "Lambert", "Larvor", "Le Bras", "Le Dantec",
    "Le Gac", "Le Moal", "Leroy", "Lucas", "Madec", "Marchand", "Masson", "Mercier", "Meyer", "Michel",
    "Nicolas", "Olivier", "Pasquier", "Perrot", "Picard", "Quillévéré", "Renard", "Robert", "Roger", "Salaün",
    "Simon", "Thomas", "Traoré", "Vasseur", "Vincent", "Yildiz", "Zerrouki", "Benkirane", "Nasser", "Khoury",
    "Tran", "Pham", "Silva", "Costa", "Ferreira", "Rossi", "Müller", "Kowalski", "Haddad", "Mansour",
]

ROOMS = (
    [("A-AMPHI1", "Amphi Iroise", "A", 180, "amphi", "vidéoprojecteur, sonorisation, captation vidéo"),
     ("A-AMPHI2", "Amphi Armor", "A", 140, "amphi", "vidéoprojecteur, sonorisation")]
    + [(f"B-1{i:02d}", f"Salle B-1{i:02d}", "B", 36 if i <= 6 else 40, "td", "vidéoprojecteur, tableau blanc")
       for i in range(1, 11)]
    + [(f"C-20{i}", f"Salle informatique C-20{i}", "C", 32, "tp_info",
        "32 postes" + (", serveurs GPU" if i >= 4 else "")) for i in range(1, 6)]
    + [("D-LAB1", "Laboratoire d'électronique", "D", 32, "labo", "oscilloscopes, générateurs, cartes FPGA"),
       ("D-LAB2", "Laboratoire de robotique", "D", 32, "labo", "robots mobiles, bras robotisé, arène"),
       ("D-LAB3", "Laboratoire systèmes embarqués", "D", 32, "labo", "cartes STM32, Raspberry Pi, capteurs"),
       ("E-012", "Salle de réunion E-012", "E", 12, "meeting", "écran, visioconférence"),
       ("A-CONSEIL", "Salle du conseil", "A", 40, "meeting", "visioconférence"),
       ("BU-S1", "Bibliothèque, salle de travail 1", "BU", 6, "study", "écran"),
       ("BU-S2", "Bibliothèque, salle de travail 2", "BU", 6, "study", "écran"),
       ("BU-S3", "Bibliothèque, salle de travail 3", "BU", 8, "study", "écran, tableau blanc"),
       ("BU-S4", "Bibliothèque, salle de travail 4", "BU", 10, "study", "écran, tableau blanc")]
)


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    ects: float
    year: str
    track: str | None
    has_cm: bool             # lecture for the whole year (or the whole track in 3A)
    group_kind: str | None   # 'td' or 'tp' session for each group, None if lectures only
    group_room: str | None   # room kind for the group session
    lead: str
    second: str | None = None   # teaches the sessions of groups TD3 and TD4


COURSES = [
    Course("MA101", "Analyse", 5, "1A", None, True, "td", "td", "tch-008", "tch-009"),
    Course("MA102", "Algèbre linéaire", 4, "1A", None, True, "td", "td", "tch-010", "tch-009"),
    Course("IN101", "Programmation Python", 5, "1A", None, True, "tp", "tp_info", "tch-004", "tch-005"),
    Course("EL101", "Électronique numérique", 4, "1A", None, True, "tp", "labo", "tch-013", "tch-014"),
    Course("PH101", "Ondes et propagation", 4, "1A", None, True, "td", "td", "tch-020", "tch-021"),
    Course("LA101", "Anglais", 2, "1A", None, False, "td", "td", "tch-025", "tch-026"),
    Course("MA201", "Probabilités et statistiques", 5, "2A", None, True, "td", "td", "tch-011", "tch-012"),
    Course("IN201", "Algorithmique et structures de données", 5, "2A", None, True, "tp", "tp_info", "tch-006", "tch-005"),
    Course("SI201", "Traitement du signal", 5, "2A", None, True, "td", "td", "tch-015", "tch-014"),
    Course("IN202", "Bases de données", 4, "2A", None, True, "tp", "tp_info", "tch-007", "tch-029"),
    Course("SE201", "Systèmes embarqués", 4, "2A", None, True, "tp", "labo", "tch-016", "tch-030"),
    Course("LA201", "Anglais", 2, "2A", None, False, "td", "td", "tch-026", "tch-027"),
    Course("HS301", "Gestion de projet et droit de l'ingénieur", 3, "3A", None, True, None, None, "tch-028"),
    Course("LA301", "Anglais", 2, "3A", None, False, "td", "td", "tch-025"),
    Course("IA301", "Apprentissage automatique", 5, "3A", "IA", True, "tp", "tp_info", "tch-001"),
    Course("IA302", "Apprentissage profond", 5, "3A", "IA", True, "tp", "tp_info", "tch-002"),
    Course("IA303", "Traitement du langage et grands modèles de langage", 5, "3A", "IA", True, "tp", "tp_info", "tch-003"),
    Course("PR301", "Projet intelligence artificielle", 6, "3A", "IA", False, "td", "td", "tch-001"),
    Course("RO301", "Robotique mobile", 5, "3A", "ROB", True, "tp", "labo", "tch-022"),
    Course("RO302", "Vision par ordinateur", 5, "3A", "ROB", True, "tp", "tp_info", "tch-023"),
    Course("RO303", "Commande avancée", 5, "3A", "ROB", True, "td", "td", "tch-024"),
    Course("PR302", "Projet robotique", 6, "3A", "ROB", False, "td", "td", "tch-022"),
    Course("SE301", "Systèmes temps réel", 5, "3A", "SE", True, "tp", "labo", "tch-017"),
    Course("SE302", "Conception sur FPGA", 5, "3A", "SE", True, "tp", "labo", "tch-018"),
    Course("SE303", "Internet des objets", 5, "3A", "SE", True, "tp", "tp_info", "tch-019"),
    Course("PR303", "Projet systèmes embarqués", 6, "3A", "SE", False, "td", "td", "tch-019"),
]

# Teacher offices, used for office hours (permanences)
OFFICES = {t[0]: f"Bureau E-1{i + 1:02d}" for i, t in enumerate(TEACHERS)}


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------

def ascii_slug(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(c for c in norm.lower() if c.isalnum() or c in "-.").strip("-.")


def email_for(first: str, last: str, taken: set[str]) -> str:
    base = f"{ascii_slug(first)}.{ascii_slug(last.replace(' ', '-').replace(chr(39), ''))}"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}{n}", n + 1
    taken.add(candidate)
    return f"{candidate}@{EMAIL_DOMAIN}"


def course_groups(course: Course) -> list[str]:
    return [g[0] for g in GROUPS if g[2] == course.year and (course.track is None or g[3] == course.track)]


def section_teacher(course: Course, group_id: str) -> str:
    if course.second and group_id.endswith(("TD3", "TD4")):
        return course.second
    return course.lead


@dataclass
class Activity:
    course: Course
    kind: str               # cours, td, tp
    groups: list[str]
    teacher: str
    room_kind: str
    size: int
    title: str
    cell: tuple[int, int] | None = None
    room: str | None = None


@dataclass
class Plan:
    activities: list[Activity] = field(default_factory=list)
    office_hours: dict[str, tuple[int, int]] = field(default_factory=dict)


def weekly_activities() -> list[Activity]:
    acts: list[Activity] = []
    for c in COURSES:
        groups = course_groups(c)
        size = GROUP_SIZE[c.year]
        if c.has_cm:
            room_kind = "amphi" if len(groups) > 1 else "td"
            acts.append(Activity(c, "cours", groups, c.lead, room_kind, size * len(groups), f"{c.title} — cours"))
        if c.group_kind:
            label = "TP" if c.group_kind == "tp" else ("séance projet" if c.id.startswith("PR") else "TD")
            for g in groups:
                title = f"{c.title} — {label}" + (f" ({g})" if len(groups) > 1 else "")
                acts.append(Activity(c, c.group_kind, [g], section_teacher(c, g), c.group_room or "td", size, title))
    return acts


def build_plan(rng: random.Random) -> Plan:
    """Place each weekly activity on a (day, slot) cell with no group, teacher or room clash.

    Greedy placement, scarcest resources first (big lectures, then labs and computer rooms),
    avoiding the same course twice in a day for a group. Wednesday afternoon stays free.
    """
    cells = [(d, s) for d in range(5) for s in range(len(SLOTS)) if not (d == 2 and s >= 2)]
    rooms_by_kind: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for rid, _name, _b, cap, kind, _eq in ROOMS:
        rooms_by_kind[kind].append((rid, cap))

    priority = {"amphi": 0, "labo": 1, "tp_info": 2, "td": 3}
    acts = weekly_activities()
    rng.shuffle(acts)
    acts.sort(key=lambda a: (priority[a.room_kind], -a.size))

    busy_group: dict[str, set] = defaultdict(set)
    busy_teacher: dict[str, set] = defaultdict(set)
    busy_room: dict[str, set] = defaultdict(set)
    course_days: dict[tuple[str, str], set] = defaultdict(set)

    for act in acts:
        candidates = cells[:]
        rng.shuffle(candidates)
        candidates.sort(key=lambda c: sum(c[0] in course_days[(g, act.course.id)] for g in act.groups))
        for cell in candidates:
            if cell in busy_teacher[act.teacher] or any(cell in busy_group[g] for g in act.groups):
                continue
            room = next((rid for rid, cap in rooms_by_kind[act.room_kind]
                         if cap >= act.size and cell not in busy_room[rid]), None)
            if room is None:
                continue
            act.cell, act.room = cell, room
            busy_teacher[act.teacher].add(cell)
            busy_room[room].add(cell)
            for g in act.groups:
                busy_group[g].add(cell)
                course_days[(g, act.course.id)].add(cell[0])
            break
        else:
            raise RuntimeError(f"timetable: no room/time for {act.title}")

    plan = Plan(activities=acts)
    for tid, *_ in TEACHERS:
        free = [c for c in cells if c not in busy_teacher[tid]]
        plan.office_hours[tid] = rng.choice(free)
    return plan


# ---------------------------------------------------------------------------------------
# Database population
# ---------------------------------------------------------------------------------------

class Writer:
    def __init__(self, conn, created_at: str):
        self.conn = conn
        self.created_at = created_at

    def event(self, title, kind, start: datetime, end: datetime, organizer, *, room=None, course=None,
              people=(), groups=(), description="") -> int:
        cur = self.conn.execute(
            "INSERT INTO events(title, kind, start, end, room_id, course_id, organizer_id, description, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (title, kind, fmt_dt(start), fmt_dt(end), room, course, organizer, description,
             self.created_at, self.created_at))
        eid = cur.lastrowid
        self.conn.executemany("INSERT INTO event_people(event_id, person_id) VALUES (?,?)",
                              [(eid, p) for p in dict.fromkeys(people)])
        self.conn.executemany("INSERT INTO event_groups(event_id, group_id) VALUES (?,?)",
                              [(eid, g) for g in groups])
        return eid


def slot_times(day: date, slot: int) -> tuple[datetime, datetime]:
    s, e = SLOTS[slot]
    return datetime.combine(day, s), datetime.combine(day, e)


def generate(conn, now: datetime, seed: int = 2026) -> dict:
    """Fill an empty database. Returns a short summary (counts, semester dates)."""
    rng = random.Random(seed)
    today = now.date()
    semester_start = monday_of(today) - timedelta(weeks=TODAY_WEEK)
    created_at = fmt_dt(datetime.combine(semester_start - timedelta(days=14), datetime.min.time()))
    w = Writer(conn, created_at)
    taken: set[str] = set()

    dbm.set_meta(conn, "school_name", SCHOOL_NAME)
    dbm.set_meta(conn, "semester_start", semester_start.isoformat())
    dbm.set_meta(conn, "semester_end", (semester_start + timedelta(weeks=EXAM_WEEK, days=4)).isoformat())
    dbm.set_meta(conn, "vacation_week_start", (semester_start + timedelta(weeks=VACATION_WEEK)).isoformat())
    dbm.set_meta(conn, "seeded_for", fmt_dt(now))
    dbm.set_meta(conn, "seed", str(seed))

    conn.executemany("INSERT INTO groups(id, name, year, track) VALUES (?,?,?,?)", GROUPS)
    conn.executemany("INSERT INTO rooms(id, name, building, capacity, kind, equipment) VALUES (?,?,?,?,?,?)", ROOMS)
    conn.executemany("INSERT INTO courses(id, title, ects, year, track) VALUES (?,?,?,?,?)",
                     [(c.id, c.title, c.ects, c.year, c.track) for c in COURSES])

    for pid, first, last, dept, title in TEACHERS:
        conn.execute("INSERT INTO people(id, role, first_name, last_name, email, department, title) "
                     "VALUES (?,?,?,?,?,?,?)", (pid, "teacher", first, last, email_for(first, last, taken), dept, title))
    for pid, first, last, dept, title in STAFF:
        conn.execute("INSERT INTO people(id, role, first_name, last_name, email, department, title) "
                     "VALUES (?,?,?,?,?,?,?)", (pid, "staff", first, last, email_for(first, last, taken), dept, title))

    # Students: fixed personas first, then unique random names, filling each group
    students_by_group: dict[str, list[str]] = defaultdict(list)
    used_names = {(f, l) for f, l, _ in FIXED_STUDENTS}
    queue = list(FIXED_STUDENTS)
    n = 0
    for gid, _name, year, _track in GROUPS:
        target = GROUP_SIZE[year]
        while len(students_by_group[gid]) < target:
            fixed = next((s for s in queue if s[2] == gid), None)
            if fixed:
                queue.remove(fixed)
                first, last = fixed[0], fixed[1]
            else:
                while True:
                    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
                    if (first, last) not in used_names:
                        used_names.add((first, last))
                        break
            n += 1
            sid = f"stu-{n:04d}"
            conn.execute("INSERT INTO people(id, role, first_name, last_name, email, group_id) VALUES (?,?,?,?,?,?)",
                         (sid, "student", first, last, email_for(first, last, taken), gid))
            students_by_group[gid].append(sid)
    # Keep fixed personas at the lowest ids for readability: stu-0001 is Inès Haddad, etc.
    _renumber_fixed_students(conn)
    students_by_group = defaultdict(list)
    for row in conn.execute("SELECT id, group_id FROM people WHERE role='student' ORDER BY id"):
        students_by_group[row["group_id"]].append(row["id"])

    for c in COURSES:
        for g in course_groups(c):
            conn.execute("INSERT INTO course_sections(id, course_id, group_id, teacher_id) VALUES (?,?,?,?)",
                         (f"{c.id}/{g}", c.id, g, section_teacher(c, g)))

    # Timetable
    plan = build_plan(rng)
    course_sessions: list[tuple[int, Activity, datetime, datetime]] = []
    for week in range(TEACHING_WEEKS):
        if week == VACATION_WEEK:
            continue
        monday = semester_start + timedelta(weeks=week)
        for act in plan.activities:
            day, slot = act.cell
            start, end = slot_times(monday + timedelta(days=day), slot)
            if week == MIDTERM_WEEK and act.kind == "cours":
                w.event(f"Partiel — {act.course.title}", "examen", start, end, act.teacher, room=act.room,
                        course=act.course.id, people=[act.teacher], groups=act.groups,
                        description="Partiel de mi-semestre. Documents non autorisés sauf mention contraire.")
                continue
            eid = w.event(act.title, act.kind, start, end, act.teacher, room=act.room, course=act.course.id,
                          people=[act.teacher], groups=act.groups)
            course_sessions.append((eid, act, start, end))
        for tid, (day, slot) in plan.office_hours.items():
            start = datetime.combine(monday + timedelta(days=day), SLOTS[slot][0])
            w.event("Permanence", "permanence", start, start + timedelta(hours=1), tid, people=[tid],
                    description=f"Permanence hebdomadaire, {OFFICES[tid]}. Rendez-vous de 15 à 30 minutes.")
        # Weekly scolarité meeting, monthly department meetings (Wednesday afternoon)
        start = datetime.combine(monday, datetime.min.time()) + timedelta(hours=9)
        w.event("Point hebdomadaire de la scolarité", "reunion", start, start + timedelta(minutes=45), "stf-01",
                room="E-012", people=[s[0] for s in STAFF])
        if week % 4 == 2:
            depts = defaultdict(list)
            for pid, _f, _l, dept, _t in TEACHERS:
                depts[dept].append(pid)
            for i, (dept, members) in enumerate(sorted(depts.items())):
                start = datetime.combine(monday + timedelta(days=2), datetime.min.time()) + timedelta(hours=14)
                room = "A-CONSEIL" if i % 2 == 0 else "E-012"
                start += timedelta(hours=i // 2)
                w.event(f"Réunion du département {dept}", "reunion", start, start + timedelta(hours=1),
                        members[0], room=room, people=members)

    # Final exams: one per course, lecture courses in amphitheatres, the rest in their usual rooms
    exam_monday = semester_start + timedelta(weeks=EXAM_WEEK)
    for year in ("1A", "2A", "3A"):
        common = [(d, s) for d in range(5) for s in (0, 2)]
        # 1A and 3A share the main amphitheatre: 3A takes its common exams from the end of the week
        cells = iter(reversed(common) if year == "3A" else common)
        tracks_cells: dict[str | None, object] = defaultdict(lambda: iter([(d, s) for d in range(5) for s in (1, 3)]))
        for c in [c for c in COURSES if c.year == year]:
            groups = course_groups(c)
            if c.track is None:
                day, slot = next(cells)
                room = "A-AMPHI1" if year != "2A" else "A-AMPHI2"
            else:
                day, slot = next(tracks_cells[c.track])
                room = {"IA": "B-107", "ROB": "B-108", "SE": "B-109"}[c.track]
            start, end = slot_times(exam_monday + timedelta(days=day), slot)
            w.event(f"Examen final — {c.title}", "examen", start, end, c.lead, room=room, course=c.id,
                    people=[c.lead], groups=groups, description="Examen final. Se présenter 15 minutes avant.")

    _grades(conn, rng, semester_start, today, students_by_group)
    _absences(conn, rng, now, course_sessions, students_by_group)
    _life(conn, w, rng, now, plan, students_by_group)
    conn.commit()
    return summary(conn)


def _renumber_fixed_students(conn) -> None:
    """Give the fixed personas ids stu-0001.. in FIXED_STUDENTS order, swapping with whoever had them."""
    for i, (first, last, gid) in enumerate(FIXED_STUDENTS, start=1):
        target = f"stu-{i:04d}"
        row = conn.execute("SELECT id FROM people WHERE first_name=? AND last_name=? AND group_id=?",
                           (first, last, gid)).fetchone()
        if row["id"] == target:
            continue
        conn.execute("UPDATE people SET id='tmp-swap' WHERE id=?", (target,))
        conn.execute("UPDATE people SET id=? WHERE id=?", (target, row["id"]))
        conn.execute("UPDATE people SET id=? WHERE id='tmp-swap'", (row["id"],))


def _grades(conn, rng, semester_start: date, today: date, students_by_group) -> None:
    ability = {sid: rng.gauss(12.5, 2.0) for sids in students_by_group.values() for sid in sids}
    # Demo personas get plausible, stable profiles
    ability.update({"stu-0001": 14.5, "stu-0002": 9.0, "stu-0003": 12.0, "stu-0004": 13.0, "stu-0005": 11.0})

    def score(sid: str) -> float:
        raw = ability[sid] + rng.gauss(0, 2.5)
        return round(max(0.0, min(20.0, raw)) * 4) / 4

    def day(week: int, weekday: int) -> date:
        return semester_start + timedelta(weeks=week, days=weekday)

    rows = []
    for c in COURSES:
        labels = []
        if c.has_cm:
            labels.append(("Partiel", 0.4, day(MIDTERM_WEEK, 4), 1))
        if c.group_kind == "tp":
            labels.append(("TP noté", 0.2, day(4, 3), 1))
        if c.id.startswith("LA"):
            labels.append(("Oral", 0.3, day(5, 2), 1))
        if c.id.startswith("PR"):
            labels.append(("Soutenance intermédiaire", 0.3, day(8, 3), 1))
        if c.has_cm and c.group_kind:
            labels.append(("Contrôle continu", 0.2, day(8, 4), 0))   # not published yet
        for g in course_groups(c):
            for sid in students_by_group[g]:
                for label, coef, when, published in labels:
                    if when < today:
                        rows.append((sid, c.id, label, score(sid), coef, when.isoformat(), published))
    conn.executemany("INSERT INTO grades(student_id, course_id, label, score, coefficient, date, published) "
                     "VALUES (?,?,?,?,?,?,?)", rows)


REASONS = ["Certificat médical", "Convocation administrative (préfecture)", "Panne de train, justificatif SNCF",
           "Compétition sportive universitaire", "Rendez-vous médical", "Obsèques d'un proche"]


def _absences(conn, rng, now: datetime, sessions, students_by_group) -> None:
    proneness = {sid: (0.06 if rng.random() < 0.12 else 0.012)
                 for sids in students_by_group.values() for sid in sids}
    rows = []
    for eid, act, start, end in sessions:
        if end > now:
            continue
        age_days = (now - end).days
        for g in act.groups:
            for sid in students_by_group[g]:
                if rng.random() >= proneness[sid]:
                    continue
                if age_days > 21:
                    status = rng.choices(["justifiee", "non_justifiee", "refusee"], [0.6, 0.3, 0.1])[0]
                elif age_days > 3:
                    status = rng.choices(["en_attente", "non_justifiee", "justifiee"], [0.35, 0.45, 0.2])[0]
                else:
                    status = "non_justifiee"
                reason = rng.choice(REASONS) if status != "non_justifiee" else ""
                submitted = fmt_dt(end + timedelta(days=1)) if reason else None
                reviewed_by = "stf-02" if status in ("justifiee", "refusee") and g[0] in "12" else (
                    "stf-03" if status in ("justifiee", "refusee") else None)
                reviewed_at = fmt_dt(end + timedelta(days=3)) if reviewed_by else None
                comment = "Justificatif non recevable." if status == "refusee" else ""
                rows.append((sid, eid, status, reason, submitted, reviewed_by, reviewed_at, comment))
    conn.executemany("INSERT INTO absences(student_id, event_id, status, reason, submitted_at, reviewed_by, "
                     "reviewed_at, review_comment) VALUES (?,?,?,?,?,?,?,?)", rows)
    # The student persona always has a recent unexcused absence to justify in the demo:
    # the most recent past session of her group, still inside the five-working-day window.
    recent = max((s for s in sessions if "3A-IA" in s[1].groups and s[3] <= now), key=lambda s: s[3], default=None)
    if recent:
        conn.execute("DELETE FROM absences WHERE student_id='stu-0001' AND event_id=?", (recent[0],))
        conn.execute("INSERT INTO absences(student_id, event_id, status) VALUES ('stu-0001', ?, 'non_justifiee')",
                     (recent[0],))


def _life(conn, w: Writer, rng, now: datetime, plan: Plan, students_by_group) -> None:
    """A few personal events in the coming weeks so calendars look lived-in."""
    monday = monday_of(now.date())
    # A study-room booking and a club meeting for the student persona next week
    start = datetime.combine(monday + timedelta(days=8), SLOTS[2][0])
    if _group_free(conn, "3A-IA", start, start + timedelta(hours=2)):
        w.event("Révisions apprentissage profond", "reservation", start, start + timedelta(hours=2), "stu-0001",
                room="BU-S3", people=["stu-0001", "stu-0002"])
    # Student bookings in study rooms
    for i in range(12):
        sid = rng.choice(students_by_group[rng.choice(list(students_by_group))])
        day = monday + timedelta(days=rng.randint(0, 11))
        if day.weekday() >= 5:
            continue
        start = datetime.combine(day, SLOTS[rng.choice([1, 3])][0])
        room = f"BU-S{rng.randint(1, 4)}"
        if not _room_free(conn, room, start, start + timedelta(hours=2)):
            continue
        w.event("Travail en groupe", "reservation", start, start + timedelta(hours=2), sid, room=room, people=[sid])
    # Two support tickets already open
    created = fmt_dt(now - timedelta(days=2))
    conn.execute("INSERT INTO tickets(person_id, category, subject, body, created_at) VALUES (?,?,?,?,?)",
                 ("stu-0004", "informatique", "Accès au wifi", "Je n'arrive pas à me connecter au wifi du bâtiment C.",
                  created))
    conn.execute("INSERT INTO tickets(person_id, category, subject, body, created_at) VALUES (?,?,?,?,?)",
                 ("tch-023", "salles", "Vidéoprojecteur B-104", "Le vidéoprojecteur de la salle B-104 ne s'allume plus.",
                  created))


def _group_free(conn, group_id: str, start: datetime, end: datetime) -> bool:
    row = conn.execute("SELECT 1 FROM events e JOIN event_groups g ON g.event_id=e.id WHERE g.group_id=? "
                       "AND e.status='confirmed' AND e.start < ? AND e.end > ? LIMIT 1",
                       (group_id, fmt_dt(end), fmt_dt(start))).fetchone()
    return row is None


def _room_free(conn, room: str, start: datetime, end: datetime) -> bool:
    row = conn.execute("SELECT 1 FROM events WHERE room_id=? AND status='confirmed' AND start < ? AND end > ? LIMIT 1",
                       (room, fmt_dt(end), fmt_dt(start))).fetchone()
    return row is None


def summary(conn) -> dict:
    count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "school": dbm.get_meta(conn, "school_name"),
        "semester_start": dbm.get_meta(conn, "semester_start"),
        "semester_end": dbm.get_meta(conn, "semester_end"),
        "students": count("SELECT COUNT(*) FROM people WHERE role='student'"),
        "teachers": count("SELECT COUNT(*) FROM people WHERE role='teacher'"),
        "staff": count("SELECT COUNT(*) FROM people WHERE role='staff'"),
        "rooms": count("SELECT COUNT(*) FROM rooms"),
        "courses": count("SELECT COUNT(*) FROM courses"),
        "events": count("SELECT COUNT(*) FROM events"),
        "grades": count("SELECT COUNT(*) FROM grades"),
        "absences": count("SELECT COUNT(*) FROM absences"),
    }


def create_database(path: Path, now: datetime, seed: int = 2026) -> dict:
    """(Re)create the database file from scratch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()
    conn = dbm.connect(path)
    try:
        dbm.init_schema(conn)
        return generate(conn, now, seed)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Generate the fictional school database.")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--now", default=settings.now_override, help="school clock, e.g. 2026-11-16T09:00")
    parser.add_argument("--seed", type=int, default=settings.seed)
    args = parser.parse_args(argv)
    now = parse_dt(args.now) if args.now else Clock().now()
    info = create_database(args.db, now, args.seed)
    print(f"{info['school']}: {args.db}")
    for key, value in info.items():
        if key != "school":
            print(f"  {key:15} {value}")


if __name__ == "__main__":
    main()
