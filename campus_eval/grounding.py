"""Groundedness: every checkable fact in a reply must come from what the agent was given.

The agent's replies state dates, times, rooms, course codes, grades and citations. Each of these is
extracted with a pattern, normalised, and looked up in the *evidence*: the tool results of the run,
the user's own messages and the context the agent receives (today's date, the teaching slots).
A fact absent from the evidence is an unsupported claim, which is how hallucinations show up in an
agent that is supposed to answer from its tools.

Two more checks need no evidence: a weekday must match its date ("jeudi 18 novembre" is wrong in
2026; a date written with its right weekday is calendar arithmetic and counts as supported), and a citation ``[doc#n]`` must name an existing passage that a search returned in this run.

This is deliberately conservative: numbers that are not grades (counts, durations, coefficients below 1) are not checked,
so a reply can still be wrong in ways this does not see. The false-positive rate is measured by
reviewing the flagged claims by hand (see the evaluation report).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

MONTHS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre",
          "novembre", "decembre"]
MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
             "october", "november", "december"]
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
WEEKDAYS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

ROOM = re.compile(r"\b(A-AMPHI[12]|A-CONSEIL|B-1(?:0[1-9]|10)|C-20[1-5]|D-LAB[1-3]|E-012|E-1\d\d|BU-S[1-4])\b",
                  re.IGNORECASE)
COURSE = re.compile(r"\b([A-Z]{2}[1-3]0[1-3])\b")
CITATION = re.compile(r"\[([a-z0-9-]+#\d+)\]")
GRADE = re.compile(r"(?<![\d/:.,])(\d{1,2})[,.](\d{1,2})(?![\d/:]|\s*(?:%|centime|cent|\$|€|s\b|ects))", re.IGNORECASE)
TIME_COLON = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
TIME_H = re.compile(r"(?<![\d.,])([01]?\d|2[0-3])\s?h\s?([0-5]\d)?(?![\d])", re.IGNORECASE)
DAY_MONTH = re.compile(r"\b(\d{1,2})(?:er)?\s+(" + "|".join(MONTHS + MONTHS_EN) + r")\b")
MONTH_DAY_EN = re.compile(r"\b(" + "|".join(MONTHS_EN) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b")
ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)")
SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
WEEKDAY_DATE = re.compile(r"\b(" + "|".join(WEEKDAYS + WEEKDAYS_EN) + r")\s+(\d{1,2})(?:er|st|nd|rd|th)?\s+("
                          + "|".join(MONTHS + MONTHS_EN) + r")\b")


DASHES = re.compile(r"[\u2010-\u2015\u2212]")


def fold(text: str) -> str:
    # Dashes become spaced hyphens first: "16 h–17 h" must not fold into "16 h17 h".
    text = DASHES.sub(" - ", text).replace("\u202f", " ").replace("\xa0", " ")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return text.lower()


@dataclass
class Claims:
    times: set
    dates: set
    rooms: set
    courses: set
    grades: set
    citations: list
    weekday_dates: list


def _month(name: str) -> int:
    return (MONTHS.index(name) if name in MONTHS else MONTHS_EN.index(name)) + 1


def extract(text: str, *, evidence: bool = False) -> Claims:
    """Extract facts. In evidence mode, structured forms (ISO dates, JSON times) count too."""
    raw = text
    t = fold(text)
    times = {(int(h), int(m)) for h, m in TIME_COLON.findall(t)}
    for h, m in TIME_H.findall(t):
        hour = int(h)
        if 7 <= hour <= 20:          # a time of day; "2 h" is a duration and is not checked
            times.add((hour, int(m) if m else 0))
    dates = {(int(d), _month(mo)) for d, mo in DAY_MONTH.findall(t) if 1 <= int(d) <= 31}
    dates |= {(int(d), _month(mo)) for mo, d in MONTH_DAY_EN.findall(t) if 1 <= int(d) <= 31}
    if evidence:
        dates |= {(int(d), int(mo)) for _y, mo, d in ISO_DATE.findall(t)}
        dates |= {(int(d), int(mo)) for d, mo in SLASH_DATE.findall(t) if 1 <= int(mo) <= 12}
    rooms = {r.upper() for r in ROOM.findall(raw)}
    courses = set(COURSE.findall(raw))
    grades = set()
    for whole, frac in GRADE.findall(t):
        value = float(f"{whole}.{frac}")
        if 1 <= value <= 20:          # below 1 it is a coefficient ("0,4"), not a grade
            grades.add(round(value, 2))
    if evidence:
        grades |= {round(float(x), 2) for x in re.findall(r"(?<![\d-])(\d{1,2}(?:\.\d{1,2})?)(?![\d-])", t)
                   if float(x) <= 20}
    citations = CITATION.findall(raw)
    weekday_dates = [(wd, int(d), _month(mo)) for wd, d, mo in WEEKDAY_DATE.findall(t)]
    return Claims(times, dates, rooms, courses, grades, citations, weekday_dates)


def _year_for(month: int, semester_start: date) -> int:
    # The semester runs from late summer to winter: months before the start month are next year.
    return semester_start.year + (1 if month < semester_start.month else 0)


def _weekday_checked(claims: Claims, semester_start: date) -> set:
    """Dates written with their weekday, when the weekday is right: calendar arithmetic, not a guess.

    "Jeudi 19 novembre" for "jeudi" is computed from today's date, which the agent is given. The
    weekday check below verifies such a date, so it is not reported a second time as unsupported.
    """
    ok = set()
    for wd, d, mo in claims.weekday_dates:
        try:
            real = date(_year_for(mo, semester_start), mo, d).weekday()
        except ValueError:
            continue
        names = WEEKDAYS if wd in WEEKDAYS else WEEKDAYS_EN
        if names.index(wd) == real:
            ok.add((d, mo))
    return ok


def unsupported(reply: str, evidence: str, semester_start: date, known_refs: set[str],
                retrieved_refs: set[str]) -> list[str]:
    """Facts of ``reply`` that the evidence does not support, as readable strings."""
    claims = extract(reply)
    ev = extract(evidence, evidence=True)
    problems: list[str] = []
    for h, m in sorted(claims.times - ev.times):
        problems.append(f"heure {h:02d}:{m:02d}")
    for d, mo in sorted(claims.dates - ev.dates - _weekday_checked(claims, semester_start)):
        problems.append(f"date {d}/{mo}")
    for r in sorted(claims.rooms - ev.rooms):
        problems.append(f"salle {r}")
    for c in sorted(claims.courses - ev.courses):
        problems.append(f"cours {c}")
    for g in sorted(claims.grades - ev.grades):
        problems.append(f"note {g}")
    for ref in claims.citations:
        if ref not in known_refs:
            problems.append(f"citation inexistante [{ref}]")
        elif ref not in retrieved_refs:
            problems.append(f"citation non récupérée [{ref}]")
    for wd, d, mo in claims.weekday_dates:
        try:
            real = date(_year_for(mo, semester_start), mo, d).weekday()
        except ValueError:
            problems.append(f"date impossible {d}/{mo}")
            continue
        names = WEEKDAYS if wd in WEEKDAYS else WEEKDAYS_EN
        if names.index(wd) != real:
            problems.append(f"jour faux : {wd} {d}/{mo}")
    return problems


def retrieved_refs(tool_outputs: list[str]) -> set[str]:
    return set(re.findall(r'"ref":\s*"([a-z0-9-]+#\d+)"', "\n".join(tool_outputs)))
