"""School time: one clock for the whole app, parsing and French formatting.

The clock can be pinned (CAMPUS_NOW) so that the demo and the evaluation see the same
"today" every time; otherwise it follows the real local time.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

FMT = "%Y-%m-%dT%H:%M"

# Teaching slots of the school day (start, end). Wednesday afternoon is kept free.
SLOTS = [(time(8, 15), time(10, 15)), (time(10, 30), time(12, 30)),
         (time(13, 45), time(15, 45)), (time(16, 0), time(18, 0))]

DAY_START = time(8, 0)
DAY_END = time(19, 0)

WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]


SCHOOL_TZ = "Europe/Paris"


def _school_zone():
    """The school's time zone, whatever the server's (hosts often run in UTC)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(SCHOOL_TZ)
    except Exception:  # noqa: BLE001 - no time zone database: fall back to the server's local time
        return None


class Clock:
    def __init__(self, pinned: str = ""):
        self._pinned = parse_dt(pinned) if pinned else None
        self._zone = _school_zone()

    def now(self) -> datetime:
        """Naive school time (Europe/Paris), like every time stored in the database."""
        if self._pinned is not None:
            return self._pinned
        current = datetime.now(self._zone) if self._zone else datetime.now()
        return current.replace(tzinfo=None, second=0, microsecond=0)

    def today(self) -> date:
        return self.now().date()


def parse_dt(value: str) -> datetime:
    """Accept "YYYY-MM-DDTHH:MM", with optional seconds or a space instead of T."""
    text = str(value).strip().replace(" ", "T")
    if len(text) == 10:
        raise ValueError(f"heure manquante dans « {value} » (format attendu AAAA-MM-JJTHH:MM)")
    for fmt in (FMT, "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19] if fmt.endswith("%S") else text[:16], fmt)
        except ValueError:
            continue
    raise ValueError(f"date-heure invalide « {value} » (format attendu AAAA-MM-JJTHH:MM)")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date invalide « {value} » (format attendu AAAA-MM-JJ)") from exc


def fmt_dt(value: datetime) -> str:
    return value.strftime(FMT)


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def human_dt(value: datetime) -> str:
    """'jeudi 19 novembre 2026, 14:00'."""
    return (f"{WEEKDAYS_FR[value.weekday()]} {value.day} {MONTHS_FR[value.month - 1]} "
            f"{value.year}, {value:%H:%M}")


def human_range(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{human_dt(start)}–{end:%H:%M}"
    return f"{human_dt(start)} → {human_dt(end)}"


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end
