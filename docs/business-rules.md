# Business rules

Every rule the code enforces, where it is written for users (`campus_agent/data/docs/`) and where it is
coded. A rule is enforced by the business layer whatever the model says; "refused" means the tool returns
an error the agent explains.

## Timetable and rooms (`domain/calendar.py`; règlement des études §1, §6; réservation des salles)

- Classes run Monday to Friday on four slots; none on Wednesday afternoon; nothing at weekends or in the past.
- No double booking of a room, a person or a group; the capacity of the room is checked.
- A teacher schedules, moves or cancels only sessions of their own groups.
- Students are told of every change at the moment it is confirmed.
- A student books study rooms only, two hours at most, up to fourteen days ahead, between 8:00 and 19:00.
- Lecture halls are booked by teachers and the office.

## Appointments (`domain/calendar.py`; rendez-vous enseignants)

- A student meets a teacher inside the teacher's office hours, for 15 to 30 minutes, in a free part of them.
- The teacher is told.

## Absences (`domain/academics.py`; procédure de justification des absences)

- A student justifies their own absences within five working days of the session.
- The office accepts or refuses; a refusal needs a comment; the student is told.
- A teacher sees the absences of their own courses only.

## Grades (`domain/academics.py`, `domain/grading.py`; règlement des études §3)

- A student sees only their own published grades; a teacher those of their courses; the office all.
- A teacher enters grades for students of their groups, between 0 and 20, once per assessment and student.
- Entered grades are unpublished. Publishing makes them visible and tells the students, without the grade in the message.
- A course is passed at 10/20.

## Make-up session (`domain/grading.py`; règlement des études §4, §5)

- Open to a student whose average in the course is below 10/20, or whose absence at the exam was excused.
- One registration per course. The make-up grade replaces the final exam grade, capped at 12/20.

## Internship agreements (`domain/internships.py`; stages)

- Filed by the student at least three weeks before the start, with company, subject, dates and both tutors.
- Length by year: 1A at least 4 weeks, 2A 10 to 14, 3A 22 to 26.
- One request in progress at a time.
- The school tutor validates, then the office signs; either may refuse, with a reason. The student is told at each step.
- A teacher sees only the requests they tutor.

## Messages and tickets (`domain/messaging.py`)

- A teacher writes to their own groups and students; the office to anyone; fifty recipients listed at most, or whole groups.
- Anyone opens a ticket; a user sees their own, the office all.

## Certificates (`domain/academics.py`; attestations et documents)

- A student obtains their own enrolment certificate as a PDF; the office for any student. Each has a reference.

## Out of scope

Tuition fees, juries, degree requirements, and exam scheduling by the office are not implemented; the
agent says so and offers a ticket.
