# Organiser une séance de rattrapage (enseignant)

1. Identifie le cours et le groupe avec `search_courses` ; l'enseignant ne peut agir que sur ses groupes.
2. Cherche un créneau commun avec `find_free_slots` (groupe, durée, type de salle, période demandée).
3. Crée la séance avec `create_event` (type td ou tp, le groupe, la salle). Les étudiants sont prévenus par la création.
4. Pour un rattrapage d'examen, c'est la scolarité qui convoque : l'étudiant s'inscrit avec `register_makeup`,
   la note est plafonnée à 12/20 (règlement des études, « Rattrapages »).
